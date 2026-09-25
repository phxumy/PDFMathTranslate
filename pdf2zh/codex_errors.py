"""Separate CLI execution failures from recoverable response-shape errors."""

import re


class CodexExecutionError(RuntimeError):
    """The CLI failed; splitting the source cannot repair its output format."""


class CodexPermanentError(CodexExecutionError):
    """Execution requires a user/environment change before another attempt."""


class CodexTimeoutError(RuntimeError):
    """A smaller request may complete within the configured timeout."""


class CodexOutputError(RuntimeError):
    """The CLI succeeded but returned an invalid JSON/schema payload."""


def codex_cli_error(returncode: int, detail: str) -> CodexExecutionError:
    """Classify only a terminal CLI error line, never words in its echoed prompt."""

    lines = [line.strip() for line in detail.splitlines() if line.strip()]
    terminal = (
        re.fullmatch(r"ERROR:\s*(.*)", lines[-1], re.IGNORECASE) if lines else None
    )
    message = terminal.group(1).casefold() if terminal else ""
    permanent = any(
        reason in message
        for reason in (
            "you've hit your usage limit",
            "usage limit exceeded",
            "quota exceeded",
            "not logged in",
            "authentication failed",
            "invalid api key",
            "unauthorized",
        )
    )
    error_type = CodexPermanentError if permanent else CodexExecutionError
    return error_type(f"Codex translator failed with exit code {returncode}: {detail}")
