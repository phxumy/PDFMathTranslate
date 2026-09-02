"""Native desktop shell for the existing PDFMathTranslate WebUI.

The translation UI remains the single source of truth.  This module only owns
the local-only Gradio server, the native WebView window, and their lifecycle.
"""

from __future__ import annotations

from dataclasses import dataclass
from html import escape
import json
import logging
import os
from pathlib import Path
import socket
import sys
from threading import Event, Lock
from typing import Any, Callable

logger = logging.getLogger(__name__)

DEFAULT_DESKTOP_PORT = 7860
DESKTOP_PORT_SEARCH_LIMIT = 50
WINDOW_TITLE = "PDFMathTranslate · Codex Studio"


SPLASH_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>PDFMathTranslate · Codex Studio</title>
  <style>
    :root {
      color-scheme: dark;
      --ink: #eaf8f7;
      --muted: #8da9ad;
      --cyan: #31d7c5;
      --blue: #4b7df3;
      --panel: rgba(10, 27, 42, .82);
    }
    * { box-sizing: border-box; }
    html, body { height: 100%; margin: 0; }
    body {
      display: grid;
      place-items: center;
      overflow: hidden;
      color: var(--ink);
      font-family: Inter, "Segoe UI Variable", "Microsoft YaHei UI", sans-serif;
      background:
        radial-gradient(circle at 17% 20%, rgba(49, 215, 197, .16), transparent 33%),
        radial-gradient(circle at 83% 76%, rgba(75, 125, 243, .20), transparent 38%),
        linear-gradient(145deg, #06101b 0%, #091928 48%, #07121f 100%);
    }
    body::before {
      content: "";
      position: fixed;
      inset: 0;
      opacity: .18;
      background-image:
        linear-gradient(rgba(124, 190, 194, .16) 1px, transparent 1px),
        linear-gradient(90deg, rgba(124, 190, 194, .16) 1px, transparent 1px);
      background-size: 42px 42px;
      mask-image: linear-gradient(to bottom, black, transparent 82%);
    }
    .card {
      position: relative;
      width: min(680px, calc(100vw - 52px));
      padding: 46px 48px 40px;
      border: 1px solid rgba(130, 210, 213, .20);
      border-radius: 28px;
      background: var(--panel);
      box-shadow: 0 30px 90px rgba(0, 0, 0, .38), inset 0 1px rgba(255, 255, 255, .05);
      backdrop-filter: blur(24px);
    }
    .brand { display: flex; align-items: center; gap: 18px; }
    .mark {
      width: 66px;
      height: 66px;
      flex: none;
      filter: drop-shadow(0 0 20px rgba(49, 215, 197, .28));
    }
    .eyebrow {
      margin: 0 0 7px;
      color: var(--cyan);
      font-size: 11px;
      font-weight: 750;
      letter-spacing: .18em;
      text-transform: uppercase;
    }
    h1 { margin: 0; font-size: clamp(28px, 5vw, 42px); font-weight: 680; letter-spacing: -.04em; }
    .subtitle { margin: 9px 0 0; color: var(--muted); font-size: 15px; }
    .status-wrap { margin-top: 42px; }
    #status { margin: 0 0 9px; font-size: 15px; font-weight: 620; }
    #detail { margin: 0 0 18px; color: var(--muted); font-size: 13px; }
    .track { height: 4px; overflow: hidden; border-radius: 999px; background: rgba(142, 190, 196, .13); }
    .bar {
      width: 42%;
      height: 100%;
      border-radius: inherit;
      background: linear-gradient(90deg, var(--cyan), #74f1d4, var(--blue));
      box-shadow: 0 0 20px rgba(49, 215, 197, .45);
      animation: travel 1.55s cubic-bezier(.4, 0, .2, 1) infinite;
    }
    .footer {
      display: flex;
      justify-content: space-between;
      gap: 16px;
      margin-top: 28px;
      color: #69878d;
      font-size: 11px;
      letter-spacing: .08em;
      text-transform: uppercase;
    }
    @keyframes travel { from { transform: translateX(-115%); } to { transform: translateX(355%); } }
  </style>
</head>
<body>
  <main class="card">
    <div class="brand">
      <svg class="mark" viewBox="0 0 80 80" aria-hidden="true">
        <defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop stop-color="#79f2d4"/><stop offset="1" stop-color="#4b7df3"/></linearGradient></defs>
        <path d="M15 24 40 10l25 14v31L40 70 15 55Z" fill="none" stroke="url(#g)" stroke-width="3"/>
        <path d="m15 24 25 15 25-15M40 39v31" fill="none" stroke="url(#g)" stroke-width="2" opacity=".72"/>
        <circle cx="15" cy="24" r="5" fill="#79f2d4"/><circle cx="65" cy="24" r="5" fill="#4b7df3"/>
        <circle cx="40" cy="39" r="6" fill="url(#g)"/><circle cx="40" cy="70" r="4" fill="#54b5e9"/>
      </svg>
      <div>
        <p class="eyebrow">Unofficial Codex Edition</p>
        <h1>PDFMathTranslate</h1>
        <p class="subtitle">科研 PDF 翻译工作台</p>
      </div>
    </div>
    <section class="status-wrap" aria-live="polite">
      <p id="status">正在准备本地工作区</p>
      <p id="detail">窗口已就绪，正在载入版面识别与翻译组件…</p>
      <div class="track"><div class="bar"></div></div>
    </section>
    <div class="footer"><span>Local-only runtime</span><span>127.0.0.1</span></div>
  </main>
</body>
</html>
"""


@dataclass(frozen=True)
class DesktopOptions:
    """Configuration passed from the existing command-line parser."""

    server_port: int | None = None
    onnx_path: str | None = None
    debug: bool = False


def _port_is_available(port: int, host: str = "127.0.0.1") -> bool:
    if not 1 <= port <= 65535:
        return False
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind((host, port))
        except OSError:
            return False
    return True


def choose_desktop_port(requested: int | None = None) -> int:
    """Choose a loopback port without silently replacing an explicit choice."""

    if requested is not None:
        if not 1 <= requested <= 65535:
            raise ValueError("Desktop server port must be between 1 and 65535.")
        if not _port_is_available(requested):
            raise RuntimeError(f"Desktop server port {requested} is already in use.")
        return requested

    for port in range(
        DEFAULT_DESKTOP_PORT, DEFAULT_DESKTOP_PORT + DESKTOP_PORT_SEARCH_LIMIT
    ):
        if _port_is_available(port):
            return port

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _default_demo_loader(onnx_path: str | None) -> Any:
    from pdf2zh.doclayout import ModelInstance, OnnxModel

    ModelInstance.value = (
        OnnxModel(onnx_path) if onnx_path else OnnxModel.load_available()
    )
    os.environ["PDF2ZH_DESKTOP"] = "1"
    from pdf2zh.gui import demo

    return demo


def _set_status(window: Any, title: str, detail: str) -> None:
    script = """
        (() => {
          const status = document.getElementById('status');
          const detail = document.getElementById('detail');
          if (status) status.textContent = %s;
          if (detail) detail.textContent = %s;
        })();
    """ % (
        json.dumps(title, ensure_ascii=False),
        json.dumps(detail, ensure_ascii=False),
    )
    try:
        window.evaluate_js(script)
    except Exception:
        logger.debug("Desktop splash status update was skipped.", exc_info=True)


def _error_html(error: BaseException) -> str:
    detail = escape(str(error) or error.__class__.__name__)
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><style>
html,body{{height:100%;margin:0}} body{{display:grid;place-items:center;background:#07131f;color:#eaf8f7;font:15px 'Segoe UI','Microsoft YaHei UI',sans-serif}}
.box{{width:min(700px,calc(100vw - 48px));padding:38px;border:1px solid #28455a;border-radius:24px;background:#0d2030;box-shadow:0 24px 70px #0007}}
.tag{{color:#ffb86b;font-size:11px;font-weight:700;letter-spacing:.15em;text-transform:uppercase}} h1{{margin:10px 0 12px;font-size:30px}} p{{color:#a9bec4;line-height:1.7}}
code{{display:block;margin-top:20px;padding:16px;overflow-wrap:anywhere;border-radius:12px;background:#07131f;color:#ffcf9b}}
</style></head><body><main class="box"><div class="tag">Startup interrupted</div><h1>桌面工作台未能启动</h1>
<p>原有命令行和浏览器 WebUI 没有受到影响。你可以使用 <b>pdf2zh -i</b> 启动浏览器模式，并根据下面的信息排查桌面组件。</p>
<code>{detail}</code></main></body></html>"""


class DesktopRuntime:
    """Coordinate model loading, Gradio, and native-window shutdown."""

    def __init__(
        self,
        options: DesktopOptions,
        demo_loader: Callable[[str | None], Any] | None = None,
    ) -> None:
        self.options = options
        self._demo_loader = demo_loader or _default_demo_loader
        self._demo: Any | None = None
        self._closed = Event()
        self._close_lock = Lock()

    def start(self, window: Any) -> None:
        try:
            _set_status(
                window, "正在加载版面模型", "首次启动或离线资源恢复可能需要稍等片刻…"
            )
            demo = self._demo_loader(self.options.onnx_path)
            with self._close_lock:
                self._demo = demo
            if self._closed.is_set():
                self.stop()
                return

            port = choose_desktop_port(self.options.server_port)
            _set_status(window, "正在启动本地工作区", f"安全绑定到 127.0.0.1:{port}…")
            _, local_url, _ = demo.launch(
                server_name="127.0.0.1",
                server_port=port,
                inbrowser=False,
                share=False,
                debug=False,
                show_error=True,
                prevent_thread_lock=True,
                quiet=not self.options.debug,
            )
            if self._closed.is_set():
                self.stop()
                return
            window.load_url(local_url)
        except Exception as exc:
            logger.exception("Unable to start the PDFMathTranslate desktop shell.")
            if not self._closed.is_set():
                window.load_html(_error_html(exc))

    def stop(self, *_: Any) -> None:
        self._closed.set()
        with self._close_lock:
            demo, self._demo = self._demo, None
        if demo is not None:
            try:
                demo.close(verbose=False)
            except Exception:
                logger.debug("Gradio server shutdown reported an error.", exc_info=True)


def _import_webview() -> Any:
    try:
        import webview
    except ImportError as exc:
        raise RuntimeError(
            "Desktop mode requires pywebview. Install this checkout with "
            "`python -m pip install -e .[desktop]`, or use `pdf2zh -i` for "
            "the browser WebUI."
        ) from exc
    return webview


def _desktop_icon_path(suffix: str = ".ico") -> Path | None:
    icon = Path(__file__).with_name("assets") / f"desktop-icon{suffix}"
    return icon if icon.is_file() else None


def setup_desktop(
    server_port: int | None = None,
    onnx_path: str | None = None,
    debug: bool = False,
    webview_module: Any | None = None,
    demo_loader: Callable[[str | None], Any] | None = None,
) -> int:
    """Start the native desktop window and block until the window closes."""

    webview = webview_module or _import_webview()
    webview.settings["ALLOW_DOWNLOADS"] = True
    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
    webview.settings["OPEN_DEVTOOLS_IN_DEBUG"] = False

    options = DesktopOptions(server_port=server_port, onnx_path=onnx_path, debug=debug)
    runtime = DesktopRuntime(options, demo_loader=demo_loader)
    window = webview.create_window(
        WINDOW_TITLE,
        html=SPLASH_HTML,
        width=1480,
        height=940,
        min_size=(1040, 700),
        resizable=True,
        background_color="#07131f",
        text_select=True,
        zoomable=True,
    )
    window.events.closed += runtime.stop
    storage_path = Path.home() / ".config" / "PDFMathTranslate" / "desktop-webview"
    icon_path = _desktop_icon_path()
    try:
        webview.start(
            runtime.start,
            window,
            gui="edgechromium" if sys.platform == "win32" else None,
            debug=debug,
            private_mode=False,
            storage_path=str(storage_path),
            icon=str(icon_path) if icon_path else None,
        )
    finally:
        runtime.stop()
    return 0


def main() -> int:
    """Entry point for the optional ``pdf2zh-desktop`` console script."""

    from pdf2zh.pdf2zh import main as pdf2zh_main

    return pdf2zh_main(["--desktop", *sys.argv[1:]])


if __name__ == "__main__":
    raise SystemExit(main())
