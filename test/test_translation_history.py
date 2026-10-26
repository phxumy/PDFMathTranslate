import uuid
from pathlib import Path

import pytest
import pymupdf

from pdf2zh.translation_history import (
    HistoryStore,
    TaskRecorder,
    article_metadata,
    current_task,
    safe_error,
)
from pdf2zh.translation_progress import TranslationProgress
from pdf2zh.app_update import UpdateManager, UpdateError


def test_lowercase_nature_in_body_does_not_override_journal_header(tmp_path):
    path = tmp_path / "Article.pdf"
    with pymupdf.open() as doc:
        first = doc.new_page()
        first.insert_text(
            (50, 300), "nature of developing potent and selective ligands"
        )
        second = doc.new_page()
        second.insert_text((50, 30), "Journal of Medicinal Chemistry")
        doc.save(path)
    assert article_metadata(path)["journal"] == "Journal of Medicinal Chemistry"


@pytest.mark.parametrize("journal", ["Nature", "Nature Medicine", "NATURE PHYSICS"])
def test_nature_journal_headers_are_still_recognized(tmp_path, journal):
    path = tmp_path / "Article.pdf"
    with pymupdf.open() as doc:
        doc.new_page().insert_text((50, 30), journal)
        doc.save(path)
    assert article_metadata(path)["journal"] == journal


def test_history_survives_restart_and_missing_outputs_remain_in_record(tmp_path):
    task_id = str(uuid.uuid4())
    root = tmp_path / "files"
    folder = root / task_id
    folder.mkdir(parents=True)
    source = folder / "Author - 2024 - Paper.pdf"
    with pymupdf.open() as doc:
        doc.new_page().insert_text((50, 50), "Research paper")
        doc.set_metadata({"title": "A scientific paper", "author": "A. Author"})
        doc.save(source)
    store = HistoryStore(root)
    task = TaskRecorder(task_id, source.name, {"service": "test"}, store=store)
    task.source(source)
    task.progress(TranslationProgress("translating", 1, 2, 1, 3.0))
    mono = folder / "paper-mono.pdf"
    dual = folder / "paper-dual.pdf"
    mono.write_bytes(source.read_bytes())
    dual.write_bytes(source.read_bytes())
    task.finish("completed", [mono, dual])
    mono.unlink()
    reopened = HistoryStore(root)
    row = reopened.get(task_id)
    assert row["article"]["authors"] == "A. Author" and row["article"]["year"] == "2024"
    assert row["status"] == "completed" and row["finished_at"]
    assert row["files"]["mono"] == str(mono.resolve())
    assert reopened.files(task_id)["mono"] is None
    assert reopened.files(task_id)["original"] == source.resolve()
    assert [e["kind"] for e in reopened.events(task_id)] == [
        "input",
        "started",
        "source_saved",
        "progress",
        "completed",
    ]


def test_deletion_cannot_escape_record_task_directory(tmp_path):
    store = HistoryStore(tmp_path / "files")
    task_id = str(uuid.uuid4())
    other = str(uuid.uuid4())
    store.create(task_id, "source.pdf", {})
    outside = tmp_path / "private.pdf"
    outside.write_bytes(b"private")
    sibling = store.root / other / "keep.pdf"
    sibling.parent.mkdir()
    sibling.write_bytes(b"keep")
    store.update(
        task_id, status="failed", files={"original": str(outside), "mono": str(sibling)}
    )
    assert store.delete_files(task_id, ["original", "mono"]) == []
    assert outside.read_bytes() == b"private" and sibling.read_bytes() == b"keep"
    assert store.get(task_id)["status"] == "failed"


def test_running_tasks_and_update_guard_prevent_cleaning(tmp_path):
    store = HistoryStore(tmp_path)
    task_id = str(uuid.uuid4())
    store.create(task_id, "source.pdf", {})
    with pytest.raises(ValueError):
        store.delete_files(task_id, ["original"])
    manager = UpdateManager()
    manager.begin_translation()
    with pytest.raises(UpdateError):
        manager.maintain_storage(lambda: pytest.fail("deleted during translation"))
    manager.end_translation()
    assert manager.maintain_storage(lambda: "done") == "done"


def test_cache_telemetry_and_clear_are_isolated_to_configured_database(tmp_path):
    from pdf2zh import cache

    database = cache.init_test_db()
    task = TaskRecorder(
        str(uuid.uuid4()), "sample.pdf", {}, store=HistoryStore(tmp_path)
    )
    token = current_task.set(task)
    try:
        c = cache.TranslationCache("test")
        assert c.get("text") is None
        c.set("text", "译文")
        assert c.get("text") == "译文"
        assert task.metrics == {"hits": 1, "misses": 1, "writes": 1, "errors": 0}
        assert cache.cache_information()["healthy"]
        assert cache.clear_translation_cache() == 1
        assert cache.cache_information()["entries"] == 0
    finally:
        current_task.reset(token)
        cache.clean_test_db(database)
        cache.db.bind([cache._TranslationCache])


def test_history_errors_do_not_repeat_credentials():
    message = safe_error(
        "Authorization: Bearer abcd12345 API_KEY=secret password=hidden sk-abcdefghijk"
    )
    assert (
        "abcd12345" not in message
        and "secret" not in message
        and "hidden" not in message
        and "sk-abcdefghijk" not in message
    )


def test_dead_process_record_becomes_interrupted_without_claiming_completion(
    tmp_path, monkeypatch
):
    import pdf2zh.translation_history as history

    store = HistoryStore(tmp_path)
    task_id = str(uuid.uuid4())
    store.create(task_id, "paper.pdf", {})
    monkeypatch.setattr(history, "process_alive", lambda pid: False)
    record = store.get(task_id)
    assert record["status"] == "interrupted" and record["finished_at"] is None
    assert store.get(task_id)["status"] == "interrupted"
    assert [e["kind"] for e in store.events(task_id)].count("interrupted") == 1
