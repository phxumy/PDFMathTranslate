"""Isolated Next worker. Only framed requests and progress use the parent pipe."""

import asyncio
from concurrent.futures import Future
from contextlib import aclosing
import json
import os
from pathlib import Path
import sys
from threading import Lock, Thread


class ParentChannel:
    def __init__(self, output):
        self.output = output
        self.lock = Lock()
        self.pending = {}
        self.sequence = 0
        self.reader = None
        self.replies = os.fdopen(os.dup(sys.stdin.fileno()), "r", encoding="utf-8")
        self.closed = False

    def send(self, **event):
        with self.lock:
            print(json.dumps(event, ensure_ascii=True), file=self.output, flush=True)

    def request(self, kind, text):
        future = Future()
        with self.lock:
            if self.closed:
                raise RuntimeError("Translation service disconnected")
            if self.reader is None:
                self.reader = Thread(target=self.receive, daemon=True)
                self.reader.start()
            self.sequence += 1
            number = self.sequence
            self.pending[number] = future
        self.send(type=kind, id=number, text=text)
        return future.result()

    def receive(self):
        try:
            for line in self.replies:
                reply = json.loads(line)
                with self.lock:
                    future = self.pending.pop(reply["id"])
                future.set_result(reply["text"])
        finally:
            with self.lock:
                self.closed = True
                for future in self.pending.values():
                    future.set_exception(
                        RuntimeError("Translation service disconnected")
                    )


async def render(job, channel):
    from importlib.metadata import version

    if version("pdf2zh-next") != "2.10.2" or version("babeldoc") != "0.7.1":
        raise RuntimeError("Precise 需要 pdf2zh-next 2.10.2 和 BabelDOC 0.7.1。")
    from babeldoc.format.pdf.high_level import async_translate
    from pdf2zh_next.config.model import SettingsModel
    from pdf2zh_next import high_level as next_engine
    from pdf2zh_next.translator.utils import get_rate_limiter
    from pdf2zh_next.translator.base_translator import BaseTranslator

    class DesktopTranslator(BaseTranslator):
        name = "pdf2zh-desktop"

        def translate(self, text, ignore_cache=False, rate_limit_params=None):
            return self.do_translate(text)

        def do_translate(self, text, rate_limit_params=None):
            return channel.request("translate", text)

        def llm_translate(self, text, ignore_cache=False, rate_limit_params=None):
            return self.do_llm_translate(text)

        def do_llm_translate(self, text, rate_limit_params=None):
            if not job["llm"]:
                raise NotImplementedError
            if text is not None:
                return channel.request("llm", text)

    settings = SettingsModel(
        translate_engine_settings=None,
        translation=dict(
            lang_in=job["lang_in"],
            lang_out=job["lang_out"],
            output=job["output"],
            qps=job["workers"],
            pool_max_workers=job["workers"],
            ignore_cache=job["ignore_cache"],
            no_auto_extract_glossary=not job["llm"],
            term_qps=job["workers"],
        ),
        pdf=dict(
            pages=job["pages"],
            formular_font_pattern=job["vfont"],
            formular_char_pattern=job["vchar"],
            skip_clean=job["skip_subset_fonts"],
            disable_rich_text_translate=not job["llm"],
        ),
    )
    # Next 2.10.2 resolves translators through two module-level factories;
    # its built-in factories also make live health-check requests. Bind both
    # to the chosen desktop service before creating the config in this worker.
    service = DesktopTranslator(settings, get_rate_limiter(job["workers"]))
    next_engine.get_translator = lambda settings: service
    next_engine.get_term_translator = lambda settings: service
    config = next_engine.create_babeldoc_config(settings, Path(job["source"]))
    result = None
    async with aclosing(async_translate(config)) as events:
        async for event in events:
            kind = event.get("type")
            if kind in ("progress_start", "progress_update", "progress_end"):
                channel.send(
                    type="progress",
                    stage=event.get("stage", ""),
                    percent=event.get("overall_progress", 0),
                )
            elif kind == "error":
                raise RuntimeError(str(event.get("error") or "Next translation failed"))
            elif kind == "finish":
                result = event["translate_result"]
                break
    if result is None:
        raise RuntimeError("Next 退出前没有生成译文。")
    channel.send(
        type="result", mono=str(result.mono_pdf_path), dual=str(result.dual_pdf_path)
    )


def main():
    # Redirect native-library output before importing any PDF or model extensions.
    output = os.fdopen(os.dup(sys.stdout.fileno()), "w", encoding="utf-8")
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    channel = ParentChannel(output)
    try:
        job = json.loads(sys.stdin.readline())
        # Native PDF saving starts its own multiprocessing helpers on Windows.
        # Keep their standard handles separate from the desktop protocol.
        with open(os.devnull, "rb") as null:
            os.dup2(null.fileno(), sys.stdin.fileno())
        if os.name == "nt":
            import multiprocessing

            pythonw = Path(sys.executable).with_name("pythonw.exe")
            if pythonw.is_file():
                multiprocessing.set_executable(str(pythonw))
        bundled = Path(sys.executable).resolve().parent.parent
        if (bundled / "site-packages" / "pdf2zh_next").is_dir():
            sys.path.insert(0, str(bundled / "site-packages"))
            from babeldoc.assets.assets import restore_offline_assets_package

            if any(bundled.glob("offline_assets_*.zip")):
                restore_offline_assets_package(bundled)
        asyncio.run(render(job, channel))
        return 0
    except Exception as exc:
        channel.send(type="error", message=str(exc) or type(exc).__name__)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
