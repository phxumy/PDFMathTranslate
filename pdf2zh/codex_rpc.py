"""Small bounded, metadata-only client for the official Codex app-server."""

from __future__ import annotations

import json
from queue import Empty, Full, Queue
import subprocess
from threading import Thread
import time

from pdf2zh.codex_runtime import CodexRuntime


class CodexRpcError(Exception):
    """Deliberately excludes raw CLI responses and credentials."""


class CodexAppServer:
    def __init__(self, runtime: CodexRuntime, profile=None, *, timeout=12.0):
        self.runtime = runtime
        self.profile = profile
        self.deadline = time.monotonic() + timeout
        self.process = None
        self.reader = None
        self.responses = Queue(maxsize=32)
        self.request_id = 0

    def __enter__(self):
        from pdf2zh.codex_account import _window_kwargs

        args = ["--profile", str(self.profile)] if self.profile else []
        args += ["app-server", "--listen", "stdio://"]
        try:
            self.process = subprocess.Popen(
                self.runtime.command(*args),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                **self.runtime.subprocess_kwargs(account_operation=True),
                **_window_kwargs(),
            )
            self.reader = Thread(target=self._read, daemon=True)
            self.reader.start()
            self.request(
                "initialize",
                {"clientInfo": {"name": "pdf2zh_account", "version": "1"}},
            )
            self._send({"method": "initialized", "params": {}})
            return self
        except Exception:
            self.close()
            raise

    def _read(self):
        try:
            while line := self.process.stdout.readline(1024 * 1024 + 1):
                if len(line) > 1024 * 1024:
                    break
                try:
                    response = json.loads(line)
                except ValueError:
                    continue
                if isinstance(response, dict) and isinstance(response.get("id"), int):
                    self.responses.put_nowait(response)
        except (OSError, ValueError, Full):
            pass
        finally:
            try:
                self.responses.put_nowait(None)
            except Full:
                pass

    def _send(self, message):
        self.process.stdin.write(json.dumps(message) + "\n")
        self.process.stdin.flush()

    def request(self, method, params=None):
        self.request_id += 1
        self._send({"id": self.request_id, "method": method, "params": params or {}})
        while True:
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError()
            try:
                response = self.responses.get(timeout=remaining)
            except Empty as exc:
                raise TimeoutError() from exc
            if response is None:
                raise CodexRpcError("CLI exited")
            if response.get("id") != self.request_id:
                continue
            if "error" in response or not isinstance(response.get("result"), dict):
                raise CodexRpcError("RPC failed")
            return response["result"]

    def close(self):
        from pdf2zh.codex_account import _terminate_process

        if self.process is not None:
            _terminate_process(self.process)
            if self.reader is not None:
                self.reader.join(timeout=1)
            for stream in (self.process.stdin, self.process.stdout):
                if stream is not None:
                    stream.close()
            self.process = None

    def __exit__(self, *_):
        self.close()
