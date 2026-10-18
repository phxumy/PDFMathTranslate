"""Sequential queue, cancellation and resumed downloads at the GUI boundary."""

import ast
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock
import uuid

import pytest

from pdf2zh.translation_queue import queue_files, queue_signature, queue_summary


@pytest.fixture
def ui(tmp_path):
    source = Path(__file__).parents[1] / "pdf2zh/gui.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    names = {"translate_queue_stream", "preview_uploaded_queue", "stop_translate_file"}
    tree.body = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in names
    ]
    ns = dict(
        Path=Path,
        Event=Event,
        uuid=uuid,
        queue_files=queue_files,
        queue_signature=queue_signature,
        queue_summary=queue_summary,
        cancellation_event_map={},
        update_manager=Mock(),
        logger=Mock(),
        cancel_chatgpt_wait=Mock(),
        ChatGPTTranslator=SimpleNamespace(name="chatgpt-browser"),
        gr=SimpleNamespace(update=lambda **kw: kw, Progress=Mock, Error=RuntimeError),
    )
    exec(compile(tree, str(source), "exec"), ns)
    ns["files"] = []
    for name in ("first.pdf", "second.pdf", "third.pdf"):
        file = tmp_path / name
        file.write_bytes(name.encode())
        ns["files"].append(str(file))
    ns["state"] = {"session_id": None}
    ns["calls"] = []

    def translate(*args):
        file = args[1]
        ns["calls"].append(file)
        yield (*[{} for _ in range(7)], "正在翻译", args[15])
        mono = tmp_path / (Path(file).stem + "-mono.pdf")
        dual = tmp_path / (Path(file).stem + "-dual.pdf")
        mono.write_bytes(b"translated")
        dual.write_bytes(b"bilingual")
        yield (str(mono), {}, str(dual), {}, {}, {}, {}, "翻译完成", args[15])

    ns["translate_file_stream"] = translate
    return ns


def stream(ui, **overrides):
    values = dict(
        file_type="File",
        file_input=ui["files"],
        link_input=None,
        service="Codex",
        lang_from="English",
        lang_to="Simplified Chinese",
        page_range="First",
        page_input=None,
        prompt=None,
        threads="1",
        skip_subset_fonts=False,
        ignore_cache=False,
        vfont=None,
        use_babeldoc=False,
        recaptcha_response=None,
        state=ui["state"],
        chatgpt_model=None,
        chatgpt_effort=None,
        codex_fast=False,
        codex_speed="standard",
        codex_parallel=1,
    )
    values.update(overrides)
    return ui["translate_queue_stream"](**values)


def test_files_run_serially_in_upload_order_and_keep_all_downloads(ui):
    iterator = stream(ui)
    next(iterator)
    assert ui["calls"] == []
    first = next(iterator)
    assert ui["calls"] == ui["files"][:1]
    assert "1/3" in first[7]
    first_done = next(iterator)
    assert len(first_done[9]["value"]) == 2
    assert ui["calls"] == ui["files"][:1]
    updates = list(iterator)
    assert ui["calls"] == ui["files"]
    assert len(updates[-1][9]["value"]) == 6
    assert "3/3" in updates[-1][7]
    assert not ui["cancellation_event_map"]
    assert ui["state"]["queue_id"] is None
    ui["update_manager"].begin_translation.assert_called_once()
    ui["update_manager"].end_translation.assert_called_once()


def test_failure_preserves_completed_files_and_retry_starts_at_failed_file(ui):
    normal = ui["translate_file_stream"]

    def fail_second(*args):
        if args[1] == ui["files"][1]:
            ui["calls"].append(args[1])
            raise RuntimeError("provider unavailable")
        yield from normal(*args)

    ui["translate_file_stream"] = fail_second
    updates = []
    with pytest.raises(RuntimeError, match="provider unavailable"):
        for update in stream(ui):
            updates.append(update)
    assert ui["calls"] == ui["files"][:2]
    assert len(updates[-1][9]["value"]) == 2
    assert "失败" in updates[-1][10]
    ui["translate_file_stream"] = normal
    ui["calls"].clear()
    assert "3/3" in list(stream(ui))[-1][7]
    assert ui["calls"] == ui["files"][1:]


def test_stop_between_files_keeps_completed_output_and_starts_no_next_file(ui):
    iterator = stream(ui)
    next(iterator)
    next(iterator)
    next(iterator)
    ui["stop_translate_file"](ui["state"])
    final = list(iterator)[-1]
    assert ui["calls"] == ui["files"][:1]
    assert "队列已停止" in final[7]
    assert len(final[9]["value"]) == 2


def test_completed_queue_replay_shows_translation_preview_without_provider_calls(ui):
    list(stream(ui))
    ui["calls"].clear()
    updates = list(stream(ui))
    previews = [
        row[1].get("value")
        for row in updates
        if isinstance(row[1], dict) and row[1].get("value")
    ]
    assert previews == [pair[0] for pair in ui["state"]["queue_results"]]
    assert ui["calls"] == []


def test_stop_before_first_file_and_disconnect_release_queue_guard(ui):
    iterator = stream(ui)
    next(iterator)
    ui["stop_translate_file"](ui["state"])
    assert "队列已停止" in list(iterator)[-1][7]
    assert ui["calls"] == []
    iterator = stream(ui)
    next(iterator)
    iterator.close()
    assert not ui["cancellation_event_map"]
    assert ui["update_manager"].end_translation.call_count == 2


def test_stop_cancels_current_worker_and_never_starts_later_pdf(ui):
    active = Event()
    session = uuid.uuid4()

    def current(*args):
        ui["calls"].append(args[1])
        args[15]["session_id"] = session
        args[15]["service"] = "codex"
        ui["cancellation_event_map"][session] = active
        try:
            yield (*[{} for _ in range(7)], "正在翻译", args[15])
            assert active.is_set()
            raise RuntimeError("Translation cancelled")
        finally:
            ui["cancellation_event_map"].pop(session, None)

    ui["translate_file_stream"] = current
    iterator = stream(ui)
    next(iterator)
    next(iterator)
    ui["stop_translate_file"](ui["state"])
    assert active.is_set()
    assert "队列已停止" in list(iterator)[-1][7]
    assert ui["calls"] == ui["files"][:1]
    assert not ui["cancellation_event_map"]


@pytest.mark.parametrize(
    "change", ["settings", "content", "missing_output", "ignore_cache"]
)
def test_changed_inputs_or_missing_outputs_are_not_reused(ui, change):
    list(stream(ui))
    ui["calls"].clear()
    options = {}
    if change == "settings":
        options["lang_to"] = "Japanese"
    elif change == "content":
        Path(ui["files"][0]).write_bytes(b"changed PDF")
    elif change == "missing_output":
        Path(ui["state"]["queue_results"][0][0]).unlink()
    else:
        options["ignore_cache"] = True
    list(stream(ui, **options))
    assert ui["calls"] == (
        ui["files"][:1] if change == "missing_output" else ui["files"]
    )


def test_empty_selection_and_invalid_later_file_do_not_start_provider(ui):
    for files in ([], [ui["files"][0], "missing.pdf"]):
        with pytest.raises(RuntimeError):
            list(stream(ui, file_input=files))
    assert ui["calls"] == []


def test_preview_uses_first_file_and_escapes_names():
    assert queue_files("a.pdf") == ["a.pdf"]
    summary = queue_summary(["<img>|evil.pdf"], ["待翻译"])
    assert "<img>" not in summary and "&#124;" in summary


def test_queue_signature_does_not_conflate_same_named_different_pdfs(tmp_path):
    one = tmp_path / "one" / "same.pdf"
    two = tmp_path / "two" / "same.pdf"
    one.parent.mkdir()
    two.parent.mkdir()
    one.write_bytes(b"one")
    two.write_bytes(b"two")
    assert queue_signature([one, two], []) != queue_signature([two, one], [])
