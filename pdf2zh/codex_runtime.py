"""Select credential scope once for every use of a Codex executable."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import sys
import subprocess

_BUNDLED_CLI = Path("codex-cli/x86_64-pc-windows-msvc/bin/codex.exe")
_AUTH_ENV = {
    "OPENAI_API_KEY",
    "CODEX_API_KEY",
    "CODEX_ACCESS_TOKEN",
    "OPENAI_IDENTITY_TOKEN",
    "OPENAI_IDENTITY_TOKEN_FILE",
    "OPENAI_IDENTITY_PROVIDER_ID",
    "OPENAI_FEDERATION_RULE_ID",
    "OPENAI_WORKLOAD_IDENTITY_CONTEXT",
    "OPENAI_BASE_URL",
    "CODEX_SQLITE_HOME",
}


def bundled_codex_candidates():
    roots = []
    if os.environ.get("PYSTAND"):
        roots.append(Path(os.environ["PYSTAND"]).expanduser().resolve().parent)
    if os.environ.get("PYSTAND_HOME"):
        roots.append(Path(os.environ["PYSTAND_HOME"]).expanduser().resolve())
    executable = Path(sys.executable).resolve()
    roots.extend([executable.parent, executable.parent.parent])
    roots.extend(Path(__file__).resolve().parents[:4])
    return list(
        dict.fromkeys(
            candidate.resolve()
            for root in roots
            for candidate in (root / _BUNDLED_CLI, root / "build" / _BUNDLED_CLI)
        )
    )


def is_bundled_codex(binary: str) -> bool:
    path = Path(binary).resolve()
    if path in bundled_codex_candidates():
        return True
    # A saved absolute CODEX_BIN may point to another installed copy. Recognize
    # the application layout, not merely a binary named codex.exe.
    if tuple(p.casefold() for p in path.parts[-4:]) != tuple(
        p.casefold() for p in _BUNDLED_CLI.parts
    ):
        return False
    root = path.parents[3]
    return (root / "PDFMathTranslate-Codex.exe").is_file() and (
        root / "site-packages" / "pdf2zh"
    ).is_dir()


def application_codex_home() -> Path:
    return Path.home() / ".config" / "PDFMathTranslate" / "codex"


@dataclass(frozen=True)
class CodexRuntime:
    binary: str
    isolated: bool

    @classmethod
    def for_binary(cls, binary: str):
        return cls(binary, is_bundled_codex(binary))

    @property
    def scope_message(self):
        if self.isolated:
            return "内置 Codex · 软件独立账号（与桌面端、个人 CLI 隔离）"
        return "用户 Codex · 沿用用户登录（登录或切换会改变该 CLI 的账号）"

    def command(self, *args):
        # Force file storage even when a profile selects the OS credential store.
        overrides = ["-c", 'cli_auth_credentials_store="file"'] if self.isolated else []
        return [self.binary, *overrides, *args]

    def subprocess_kwargs(self, *, account_operation=False):
        if not self.isolated:
            return {}
        directory = application_codex_home()
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        environment = {
            k: v for k, v in os.environ.items() if k.upper() not in _AUTH_ENV
        }
        environment["CODEX_HOME"] = str(directory)
        # Change only the child's environment. Never copy or alter the user's
        # original credentials, global CODEX_HOME, or config.toml.
        options = {"env": environment}
        if account_operation:
            options["cwd"] = str(directory)
        return options


def login_bundled_cli(binary):
    """Compatibility entry point for Login-Codex.cmd, using the same scope."""
    runtime = CodexRuntime.for_binary(binary)
    if not runtime.isolated:
        raise RuntimeError("The bundled Codex CLI could not be identified.")
    return subprocess.call(
        runtime.command("login"), **runtime.subprocess_kwargs(account_operation=True)
    )
