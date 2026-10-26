"""Local task history. No provider credentials or prompts are stored here."""

from contextvars import ContextVar
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sqlite3
from threading import Lock
import time
import uuid

current_task = ContextVar("pdf2zh_history_task", default=None)


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def process_alive(pid):
    if not isinstance(pid, int) or pid <= 0:
        return False
    if pid == os.getpid():
        return True
    if os.name == "nt":
        import ctypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.GetExitCodeProcess.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_ulong),
        ]
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return ctypes.get_last_error() == 5  # access denied is not proof of exit
        try:
            code = ctypes.c_ulong()
            return (
                not kernel.GetExitCodeProcess(handle, ctypes.byref(code))
                or code.value == 259
            )
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def safe_error(error):
    text = str(error) or type(error).__name__
    text = re.sub(
        r"(?i)(bearer\s+|(?:api[_ -]?key|token|password)[\"']?\s*[:=]\s*[\"']?)[^\s,;\"']+",
        r"\1[已隐藏]",
        text,
    )
    text = re.sub(r"\bsk-[A-Za-z0-9_-]{8,}\b", "[已隐藏]", text)
    return text[:8000]


def article_metadata(path):
    """Use embedded/source evidence only; unknown publication fields stay blank."""
    import pymupdf

    path = Path(path)
    result = {
        "title": path.stem,
        "authors": "",
        "journal": "",
        "year": "",
        "doi": "",
        "pages": 0,
    }
    try:
        with pymupdf.open(path) as doc:
            result["pages"] = len(doc)
            meta = doc.metadata or {}
            result["title"] = meta.get("title") or path.stem
            result["authors"] = meta.get("author") or ""
            text = "\n".join(doc[i].get_text() for i in range(min(2, len(doc))))
            text = re.sub(
                r"(Journal of Physics:)\s*\n\s*(Condensed Matter)", r"\1 \2", text
            )
            journal = re.search(
                r"(?m)^\s*((?:(?i:Journal of|Physical Review)|Nature|NATURE)\b[^\n]{0,70})",
                text,
            )
            if journal:
                result["journal"] = re.split(
                    r"\s+\d|\bDOI\b|\bVOL(?:UME)?\b|\|", journal[1], flags=re.I
                )[0].strip()
            filename = re.match(r"(.+?)\s*-\s*((?:19|20)\d{2})\s*-\s*(.+)", path.stem)
            if filename:
                result["year"] = filename[2]
                result["authors"] = result["authors"] or filename[1].strip()
            else:
                year = re.search(
                    r"(?i)(?:published(?:\s+online)?|copyright|©)[^\n]{0,60}?\b((?:19|20)\d{2})\b",
                    text,
                )
                if year:
                    result["year"] = year[1]
            doi = re.search(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+", text)
            if doi:
                result["doi"] = doi[0].rstrip(".,;)")
    except Exception:
        pass
    return result


class HistoryStore:
    def __init__(self, root=None):
        self.root = Path(root or "pdf2zh_files").resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "history.v1.sqlite3"
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute(
                "CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, created TEXT, updated TEXT, data TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, task_id TEXT NOT NULL, time TEXT NOT NULL, kind TEXT NOT NULL, detail TEXT NOT NULL)"
            )
            db.execute("CREATE INDEX IF NOT EXISTS task_events ON events(task_id,id)")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def create(self, task_id, filename, settings, input_at=None):
        data = {
            "filename": filename,
            "status": "running",
            "input_at": input_at or now(),
            "started_at": now(),
            "finished_at": None,
            "elapsed_seconds": 0,
            "progress": {},
            "cache": {},
            "article": {},
            "files": {},
            "settings": settings,
        }
        data["owner_pid"] = os.getpid()
        with self.connect() as db:
            db.execute(
                "INSERT INTO tasks VALUES (?,?,?,?)",
                (task_id, now(), now(), json.dumps(data, ensure_ascii=False)),
            )
        self.event(task_id, "input", f"输入文章：{filename}")
        self.event(task_id, "started", "开始翻译")

    def get(self, task_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            raise ValueError("翻译记录不存在。")
        record = {"id": row["id"], **json.loads(row["data"])}
        if record["status"] == "running" and not process_alive(record.get("owner_pid")):
            self.update(
                task_id,
                status="interrupted",
                error="上次进程已退出，任务未记录为完成；最后进度与缓存保留。",
            )
            self.event(task_id, "interrupted", "检测到上次进程已退出")
            record.update(
                status="interrupted",
                error="上次进程已退出，任务未记录为完成；最后进度与缓存保留。",
            )
        return record

    def update(self, task_id, **values):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT data FROM tasks WHERE id=?", (task_id,)).fetchone()
            if row is None:
                raise ValueError("翻译记录不存在。")
            data = json.loads(row["data"])
            data.update(values)
            db.execute(
                "UPDATE tasks SET data=?,updated=? WHERE id=?",
                (json.dumps(data, ensure_ascii=False), now(), task_id),
            )

    def event(self, task_id, kind, detail):
        with self.connect() as db:
            db.execute(
                "INSERT INTO events(task_id,time,kind,detail) VALUES(?,?,?,?)",
                (task_id, now(), kind, safe_error(detail)),
            )

    def list(self, limit=300):
        with self.connect() as db:
            rows = db.execute(
                "SELECT id,data FROM tasks ORDER BY created DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self.get(row["id"]) for row in rows]

    def events(self, task_id):
        with self.connect() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT time,kind,detail FROM events WHERE task_id=? ORDER BY id",
                    (task_id,),
                )
            ]

    def owned_file(self, value, task_id=None):
        if not value:
            return None
        path = Path(value).resolve()
        # Only a task UUID directory directly under the application output root.
        try:
            rel = path.relative_to(self.root)
            if len(rel.parts) != 2 or str(uuid.UUID(rel.parts[0])) != rel.parts[0]:
                return None
            if task_id is not None and rel.parts[0] != str(task_id):
                return None
        except (ValueError, OSError):
            return None
        return path if path.is_file() else None

    def files(self, task_id):
        record = self.get(task_id)
        return {
            kind: self.owned_file(path, task_id)
            for kind, path in record.get("files", {}).items()
        }

    def delete_files(self, task_id, kinds):
        record = self.get(task_id)
        if record["status"] == "running":
            raise ValueError("任务正在翻译，暂时不能清理。")
        deleted = []
        for kind in kinds:
            if kind not in {"original", "mono", "dual"}:
                raise ValueError("不支持的文件类型。")
            path = self.owned_file(record.get("files", {}).get(kind), task_id)
            if path:
                path.unlink()
                deleted.append(kind)
        self.event(task_id, "deleted", "已清理本地文件：" + ", ".join(deleted))
        return deleted


class TaskRecorder:
    def __init__(self, task_id, filename, settings, input_at=None, store=None):
        self.store = store or HistoryStore()
        self.id = str(task_id)
        self.started = time.monotonic()
        self.lock = Lock()
        self.metrics = {"hits": 0, "misses": 0, "writes": 0, "errors": 0}
        self.last_progress = {}
        self.last_phase = None
        self.store.create(self.id, filename, settings, input_at)

    def cache_event(self, kind):
        with self.lock:
            self.metrics[kind] += 1

    def source(self, path):
        self.store.update(
            self.id,
            article=article_metadata(path),
            files={"original": str(Path(path).resolve())},
        )
        self.store.event(self.id, "source_saved", "原文已保存到本地")

    def progress(self, event):
        from dataclasses import asdict

        self.last_progress = asdict(event)
        self.store.update(
            self.id,
            progress=self.last_progress,
            cache=dict(self.metrics),
            elapsed_seconds=time.monotonic() - self.started,
        )
        key = (event.phase, event.completed_pages, event.current_page)
        if key != self.last_phase:
            self.store.event(
                self.id,
                "progress",
                f"{event.phase}：完成 {event.completed_pages}/{event.total_pages} 页；当前原文页 {event.current_page or '—'}",
            )
            self.last_phase = key

    def finish(self, status, outputs=None, error=None):
        files = self.store.get(self.id)["files"]
        if outputs:
            files.update(
                mono=str(Path(outputs[0]).resolve()),
                dual=str(Path(outputs[1]).resolve()),
            )
        self.store.update(
            self.id,
            status=status,
            finished_at=now(),
            elapsed_seconds=time.monotonic() - self.started,
            files=files,
            progress=self.last_progress,
            cache=dict(self.metrics),
            error=safe_error(error) if error else None,
        )
        self.store.event(
            self.id, status, safe_error(error) if error else "翻译完成，文件已保存"
        )


def cache_event(kind):
    task = current_task.get()
    if task:
        task.cache_event(kind)
