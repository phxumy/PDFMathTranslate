"""Exercise the Precise job protocol without network or the optional Next runtime."""

from asyncio import CancelledError
from pathlib import Path
import sys
from threading import Event
from types import SimpleNamespace

import pymupdf
import pytest

from pdf2zh import precise
from pdf2zh.high_level import translate_stream

WORKER = """
import json, pathlib, shutil, sys
job = json.loads(sys.stdin.readline())
folder = pathlib.Path(job["output"])
assert job["pages"] == "2" and job["lang_out"] == "zh"
assert "envs" not in job and "api_key" not in job
print(json.dumps({"type":"progress","stage":"fixture","percent":20}),flush=True)
print(json.dumps({"type":"translate","text":"hello","id":1}),flush=True)
reply=json.loads(sys.stdin.readline())
assert reply=={"id":1,"text":"你好"}
m,d=folder/"mono.pdf",folder/"dual.pdf"
shutil.copyfile(job["source"],m)
shutil.copyfile(job["source"],d)
print(json.dumps({"type":"result","mono":str(m),"dual":str(d)}),flush=True)
"""


@pytest.fixture
def fixture_worker(tmp_path, monkeypatch):
    path = tmp_path / "precise_worker.py"
    path.write_text(WORKER, encoding="utf8")
    monkeypatch.setattr(precise, "__file__", str(tmp_path / "precise.py"))
    monkeypatch.setattr(precise, "precise_python", lambda: sys.executable)
    with pymupdf.open() as doc:
        doc.new_page()
        doc.new_page()
        source = doc.tobytes()
    return source, path


def test_precise_returns_verified_outputs_and_uses_selected_service(
    fixture_worker, monkeypatch
):
    source, _ = fixture_worker
    seen = []

    def factory(service, li, lo, envs, prompt, ignore_cache):
        seen.append((service, envs["TEST_SETTING"]))
        return SimpleNamespace(name="codex", translate=lambda text: "你好")

    monkeypatch.setattr(precise, "create_translation_service", factory)
    progress = []
    mono, dual = translate_stream(
        source,
        pages=[1],
        mode="precise",
        service="codex",
        lang_in="en",
        lang_out="zh",
        envs={"TEST_SETTING": "private"},
        progress_callback=progress.append,
    )
    assert seen == [("codex", "private")] and mono == source and dual == source
    assert progress[-1].phase == "complete" and progress[-1].completed_pages == 1
    assert any("fixture" in e.phase for e in progress)


def test_precise_provider_failure_is_terminal(fixture_worker, monkeypatch):
    source, _ = fixture_worker

    def fail(text):
        raise RuntimeError("provider rejected request")

    monkeypatch.setattr(
        precise,
        "create_translation_service",
        lambda *args: SimpleNamespace(name="codex", translate=fail),
    )
    with pytest.raises(RuntimeError, match="provider rejected"):
        translate_stream(
            source, pages=[1], mode="precise", service="codex", lang_out="zh"
        )


def test_precise_cancellation_terminates_worker(fixture_worker, monkeypatch):
    source, path = fixture_worker
    path.write_text(
        'import json,sys,time\njson.loads(sys.stdin.readline())\nprint(json.dumps({"type":"progress","stage":"ready","percent":0}),flush=True)\ntime.sleep(30)\n',
        encoding="utf8",
    )
    cancelled = Event()
    monkeypatch.setattr(
        precise,
        "create_translation_service",
        lambda *args: SimpleNamespace(name="codex"),
    )
    with pytest.raises(CancelledError):
        translate_stream(
            source,
            mode="precise",
            service="codex",
            cancellation_event=cancelled,
            progress_callback=lambda event: (
                cancelled.set() if "ready" in event.phase else None
            ),
        )


def test_precise_rejects_conflicting_modes_or_unknown_pages(fixture_worker):
    source, _ = fixture_worker
    with pytest.raises(ValueError, match="ultrafast"):
        translate_stream(source, mode="precise", ultrafast=True)
    with pytest.raises(ValueError, match="页码"):
        translate_stream(source, mode="precise", pages=[2])
