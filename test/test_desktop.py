import socket
import unittest
from unittest.mock import patch

from pdf2zh.desktop import (
    DesktopOptions,
    DesktopRuntime,
    _error_html,
    choose_desktop_port,
    setup_desktop,
)
from pdf2zh.pdf2zh import create_parser


class EventHook:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self


class FakeWindow:
    def __init__(self):
        self.events = type("Events", (), {"closed": EventHook()})()
        self.scripts = []
        self.urls = []
        self.html = []

    def evaluate_js(self, script):
        self.scripts.append(script)

    def load_url(self, url):
        self.urls.append(url)

    def load_html(self, html):
        self.html.append(html)


class FakeDemo:
    def __init__(self):
        self.launch_kwargs = None
        self.close_count = 0

    def launch(self, **kwargs):
        self.launch_kwargs = kwargs
        return object(), f"http://127.0.0.1:{kwargs['server_port']}/", None

    def close(self, verbose=True):
        self.close_count += 1


class FakeWebview:
    def __init__(self):
        self.settings = {}
        self.window = FakeWindow()
        self.create_kwargs = None
        self.start_kwargs = None

    def create_window(self, title, **kwargs):
        self.create_kwargs = {"title": title, **kwargs}
        return self.window

    def start(self, func, args, **kwargs):
        self.start_kwargs = kwargs
        func(args)


class DesktopPortTests(unittest.TestCase):
    def test_explicit_busy_port_is_rejected(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
            with self.assertRaisesRegex(RuntimeError, "already in use"):
                choose_desktop_port(port)

    def test_invalid_explicit_port_is_rejected(self):
        for port in (0, 65536):
            with self.subTest(port=port):
                with self.assertRaisesRegex(ValueError, "between 1 and 65535"):
                    choose_desktop_port(port)

    def test_automatic_port_is_valid_and_available(self):
        port = choose_desktop_port()
        self.assertGreaterEqual(port, 1)
        self.assertLessEqual(port, 65535)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", port))


class DesktopRuntimeTests(unittest.TestCase):
    def test_runtime_uses_loopback_and_stops_gradio_once(self):
        demo = FakeDemo()
        window = FakeWindow()
        runtime = DesktopRuntime(
            DesktopOptions(server_port=8765),
            demo_loader=lambda _: demo,
        )
        with patch("pdf2zh.desktop.choose_desktop_port", return_value=8765):
            runtime.start(window)

        self.assertEqual(window.urls, ["http://127.0.0.1:8765/"])
        self.assertEqual(demo.launch_kwargs["server_name"], "127.0.0.1")
        self.assertFalse(demo.launch_kwargs["inbrowser"])
        self.assertFalse(demo.launch_kwargs["share"])
        self.assertTrue(demo.launch_kwargs["prevent_thread_lock"])

        runtime.stop()
        runtime.stop()
        self.assertEqual(demo.close_count, 1)

    def test_startup_failure_is_rendered_inside_the_window(self):
        window = FakeWindow()

        def fail_loader(_):
            raise RuntimeError("model <missing>")

        runtime = DesktopRuntime(DesktopOptions(), demo_loader=fail_loader)
        with self.assertLogs("pdf2zh.desktop", level="ERROR"):
            runtime.start(window)

        self.assertEqual(window.urls, [])
        self.assertEqual(len(window.html), 1)
        self.assertIn("model &lt;missing&gt;", window.html[0])

    def test_error_page_escapes_untrusted_exception_text(self):
        html = _error_html(RuntimeError("<script>alert(1)</script>"))
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)


class DesktopEntryTests(unittest.TestCase):
    def test_parser_exposes_desktop_without_changing_browser_mode(self):
        parser = create_parser()
        desktop_args = parser.parse_args(["--desktop"])
        browser_args = parser.parse_args(["--interactive"])
        self.assertTrue(desktop_args.desktop)
        self.assertFalse(desktop_args.interactive)
        self.assertFalse(browser_args.desktop)
        self.assertTrue(browser_args.interactive)

    def test_setup_desktop_configures_downloads_and_edge_renderer(self):
        webview = FakeWebview()
        demo = FakeDemo()
        with patch("pdf2zh.desktop.choose_desktop_port", return_value=8877):
            result = setup_desktop(
                server_port=8877,
                webview_module=webview,
                demo_loader=lambda _: demo,
            )

        self.assertEqual(result, 0)
        self.assertTrue(webview.settings["ALLOW_DOWNLOADS"])
        self.assertTrue(webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"])
        self.assertEqual(webview.start_kwargs["gui"], "edgechromium")
        self.assertFalse(webview.start_kwargs["private_mode"])
        self.assertIn("icon", webview.start_kwargs)
        self.assertEqual(webview.create_kwargs["min_size"], (1040, 700))
        self.assertEqual(demo.close_count, 1)


if __name__ == "__main__":
    unittest.main()
