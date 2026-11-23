"""Run PDFMathTranslate-next with an isolated Python and the selected desktop service."""

from asyncio import CancelledError
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
import json
import logging
import os
import signal
from pathlib import Path
from queue import Empty, Queue
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Event, Thread
from time import monotonic

from filelock import FileLock

from pdf2zh import openai_access
from pdf2zh.converter import create_translation_service
from pdf2zh.translation_history import safe_error
from pdf2zh.translation_progress import TranslationProgressTracker

logger = logging.getLogger(__name__)
_PACKAGES = ["pdf2zh-next==2.10.2", "babeldoc==0.7.1"]
_HIDDEN = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def precise_python():
    configured = os.environ.get("PDF2ZH_PRECISE_PYTHON")
    if configured:
        python = Path(configured).expanduser().resolve()
        if not python.is_file():
            raise ValueError("找不到 PDF2ZH_PRECISE_PYTHON 指定的 Python。")
        return python
    bundle = (
        Path(__file__).resolve().parents[2]
        / "engines"
        / "precise"
        / "runtime"
        / "python.exe"
    )
    if bundle.is_file():
        return bundle
    if os.name == "nt" and Path(sys.executable).name.lower() in {
        "pdf2zh.exe",
        "pdfmathtranslate-codex.exe",
    }:
        raise RuntimeError("安装目录缺少 Precise 组件，请重新运行本版安装程序。")
    root = (
        Path.home()
        / ".cache"
        / "pdf2zh"
        / "engines"
        / f"next-2.10.2-babeldoc-0.7.1-py{sys.version_info.major}{sys.version_info.minor}"
    )
    root.parent.mkdir(parents=True, exist_ok=True)
    python = root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    with FileLock(str(root) + ".lock", timeout=600):
        marker = root / ".ready"
        if not (python.is_file() and marker.is_file()):
            logger.info("Preparing the isolated PDFMathTranslate-next environment")
            subprocess.run(
                [sys.executable, "-m", "venv", str(root)],
                check=True,
                creationflags=_HIDDEN,
            )
            subprocess.run(
                [
                    str(python),
                    "-m",
                    "pip",
                    "install",
                    "--disable-pip-version-check",
                    "--no-input",
                    *_PACKAGES,
                ],
                check=True,
                creationflags=_HIDDEN,
            )
            marker.write_text("\n".join(_PACKAGES), encoding="utf-8")
    return python


def _run_job(python, job, translator, tracker, caller_cancelled, stopped):
    worker = Path(__file__).with_name("precise_worker.py")
    events = Queue()
    with subprocess.Popen(
        [str(python), "-B", "-X", "utf8", str(worker)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        creationflags=_HIDDEN,
        start_new_session=(os.name != "nt"),
    ) as process:

        def read_events():
            try:
                for line in process.stdout:
                    events.put(json.loads(line))
            except (ValueError, OSError) as exc:
                events.put({"type": "error", "message": str(exc)})
            finally:
                events.put({"type": "closed"})

        def translate_part(event):
            if stopped.is_set():
                raise CancelledError()
            if event["type"] == "llm":
                text = openai_access.chat_request(
                    translator.client,
                    translator.model,
                    [{"role": "user", "content": event["text"]}],
                    translator.options,
                )
            else:
                text = translator.translate(event["text"])
            return {"id": event["id"], "text": text}

        def send(message):
            process.stdin.write(json.dumps(message, ensure_ascii=True) + "\n")
            process.stdin.flush()

        pool = ThreadPoolExecutor(max_workers=job["workers"])
        thread = Thread(target=read_events, daemon=True)
        result = None
        exit_deadline = None
        try:
            thread.start()
            send(job)
            while True:
                if caller_cancelled and caller_cancelled.is_set():
                    raise CancelledError("Translation cancelled")
                if exit_deadline is not None and monotonic() >= exit_deadline:
                    raise RuntimeError("Precise 保存输出后未能正常退出。")
                try:
                    event = events.get(timeout=0.15)
                except Empty:
                    continue
                kind = event["type"]
                if kind in ("translate", "llm"):
                    if kind == "llm" and not job["llm"]:
                        raise RuntimeError("Unexpected LLM request from Precise")
                    future = pool.submit(copy_context().run, translate_part, event)
                    future.add_done_callback(
                        lambda f: events.put({"type": "reply", "future": f})
                    )
                elif kind == "reply":
                    send(event["future"].result())
                elif kind == "progress":
                    value = max(0, min(100, float(event["percent"] or 0)))
                    tracker.emit(f"Precise · {event['stage']} · {value:.0f}%")
                elif kind == "error":
                    raise RuntimeError("Precise · " + safe_error(event["message"]))
                elif kind == "result":
                    result = event
                    exit_deadline = monotonic() + 30
                    process.stdin.close()
                elif kind == "closed":
                    break
            if process.wait() or result is None:
                raise RuntimeError("Precise 进程提前退出，未生成完整的单语和双语 PDF。")
            return result
        finally:
            stopped.set()
            if process.poll() is None:
                if os.name == "nt":
                    subprocess.run(
                        ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        creationflags=_HIDDEN,
                    )
                else:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                if process.poll() is None:
                    process.kill()
            process.wait()
            thread.join(timeout=2)
            pool.shutdown(wait=False, cancel_futures=True)


def translate_precise(
    stream,
    pages,
    lang_in,
    lang_out,
    service,
    thread,
    envs,
    prompt,
    ignore_cache,
    vfont,
    vchar,
    skip_subset_fonts,
    cancellation_event,
    progress_callback,
):
    from pymupdf import Document

    if service.split(":", 1)[0] == "chatgpt-browser":
        raise ValueError("已废弃的 ChatGPT 聊天服务仅支持 Fast 标准排版。")
    with Document(stream=stream) as source:
        selected = sorted(set(pages)) if pages else list(range(len(source)))
        if not selected or selected[0] < 0 or selected[-1] >= len(source):
            raise ValueError("Precise 页码超出 PDF 范围。")
    tracker = TranslationProgressTracker(len(selected), progress_callback)
    tracker.emit("preparing")
    python = precise_python()
    if cancellation_event and cancellation_event.is_set():
        raise CancelledError("Translation cancelled")
    translator = create_translation_service(
        service, lang_in, lang_out, envs, prompt, ignore_cache
    )
    workers = max(1, int(thread or 4))
    stopped = Event()
    translator.cancellation_event = stopped
    if hasattr(translator, "set_concurrency"):
        translator.set_concurrency(workers)
    with TemporaryDirectory(prefix="pdf2zh-precise-") as temp:
        folder = Path(temp).resolve()
        input_pdf = folder / "input.pdf"
        input_pdf.write_bytes(stream)
        job = dict(
            source=str(input_pdf),
            output=str(folder),
            workers=workers,
            pages=",".join(str(n + 1) for n in selected) if pages else None,
            lang_in=lang_in,
            lang_out=lang_out,
            ignore_cache=bool(ignore_cache),
            vfont=vfont or None,
            vchar=vchar or None,
            skip_subset_fonts=bool(skip_subset_fonts),
            llm=translator.name in ("openai", "openailiked", "azure-openai")
            and not prompt,
        )
        result = _run_job(python, job, translator, tracker, cancellation_event, stopped)
        paths = [Path(result[k]).resolve() for k in ("mono", "dual")]
        if paths[0] == paths[1] or not all(
            p.parent == folder
            and p != input_pdf
            and p.suffix.lower() == ".pdf"
            and p.is_file()
            for p in paths
        ):
            raise RuntimeError("Precise 返回了无效的输出文件。")
        outputs = tuple(p.read_bytes() for p in paths)
        for data in outputs:
            with Document(stream=data) as doc:
                if not doc.is_pdf or not len(doc):
                    raise RuntimeError("Precise 生成的 PDF 无有效页面。")
        tracker.completed_pages = len(selected)
        tracker.emit("complete")
        return outputs
