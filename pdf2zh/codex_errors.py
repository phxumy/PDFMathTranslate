"""Separate CLI execution failures from recoverable response-shape errors."""

import re
import json


class CodexExecutionError(RuntimeError):
    """The CLI failed; splitting the source cannot repair its output format."""


class CodexPermanentError(CodexExecutionError):
    """Execution requires a user/environment change before another attempt."""


class CodexTimeoutError(RuntimeError):
    """A smaller request may complete within the configured timeout."""


class CodexOutputError(RuntimeError):
    """The CLI succeeded but returned an invalid JSON/schema payload."""


class CodexTranslationValidationError(CodexOutputError):
    """Bounded content retries failed; no complete translation can be published."""


def codex_cli_error(returncode: int, detail: str) -> CodexExecutionError:
    """Classify only a terminal CLI error line, never words in its echoed prompt."""

    lines = [line.strip() for line in detail.splitlines() if line.strip()]
    terminal = (
        re.fullmatch(r"ERROR:\s*(.*)", lines[-1], re.IGNORECASE) if lines else None
    )
    message = terminal.group(1).casefold().replace("’", "'") if terminal else ""
    if terminal:
        try:
            payload = json.loads(terminal.group(1))
        except (ValueError, TypeError):
            payload = None
        if isinstance(payload, dict):
            error = payload.get("error")
            status = payload.get("status")
            if (
                isinstance(error, dict)
                and status in {400, 401, 403, 404, 422}
                and error.get("type")
                in {"invalid_request_error", "authentication_error", "permission_error"}
                and isinstance(error.get("message"), str)
            ):
                # Only the terminal CLI error is authoritative. Do not classify
                # echoed PDF text or expose the entire prompt in a UI error.
                return CodexPermanentError(
                    f"Codex 请求被拒绝（HTTP {status}）：{error['message']}"
                    " 请检查所选 CLI 版本、模型 ID、推理强度及账号权限。"
                )
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
    reason = terminal.group(1) if terminal else detail
    return error_type(f"Codex translator failed with exit code {returncode}: {reason}")
