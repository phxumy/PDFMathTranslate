"""ChatGPT's signed-in browser transport, with durable, non-resubmitting recovery.

The companion attaches to Chrome through its user-approved local connection.
It never copies browser cookies or Codex credentials, and never calls a model
API. Scientific translation and PDF layout keep the existing shared rules.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from contextlib import contextmanager

from pdf2zh.translator import (
    ScientificTranslator,
    TranslationAbortedError,
    codex_subprocess_window_kwargs,
)

RESUME_MESSAGE = (
    "已暂停。重新连接 ChatGPT 后，对同一 PDF 再次点击翻译；"
    "已完成批次会复用，已发送请求会先尝试取回，请勿在翻译窗口重复发送。"
)
_browser_lock = threading.Lock()
_cancel_wait = threading.Event()


@contextmanager
def _request_lock(path: Path):
    """OS-owned lock; survives controller crashes without leaving stale locks."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise TranslationAbortedError(
                "另一个翻译窗口正在处理同一批次，请先等待或停止该任务。"
            ) from exc
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def chatgpt_state_dir() -> Path:
    override = os.environ.get("PDF2ZH_CHATGPT_STATE_DIR")
    if override:
        return Path(override).expanduser().resolve()
    base = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / ".local" / "share"))
    return base / "PDFMathTranslate" / "ChatGPT"


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary.replace(path)


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("expected an object")
        return data
    except (ValueError, OSError) as exc:
        raise TranslationAbortedError(
            f"ChatGPT 任务记录无法读取，已停止以避免重复提交：{path.name}。"
        ) from exc


def _find_node() -> str:
    explicit = os.environ.get("PDF2ZH_NODE_BIN")
    candidates = [explicit] if explicit else []
    roots = [Path(sys.executable).resolve().parent, Path(__file__).resolve().parents[2]]
    if os.environ.get("PYSTAND_HOME"):
        roots.insert(0, Path(os.environ["PYSTAND_HOME"]))
    for root in roots:
        candidates.extend([root / "node" / "node.exe", root / "node.exe"])
    candidates.extend(
        [
            shutil.which("node"),
            Path(os.environ.get("ProgramFiles", "C:/Program Files"))
            / "nodejs"
            / "node.exe",
        ]
    )
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return str(Path(candidate).resolve())
    raise TranslationAbortedError(
        "未找到 Node.js 24 或以上版本，ChatGPT 浏览器组件未就绪。"
    )


def _bridge_environment(state_dir: Path) -> dict[str, str]:
    # Deliberately leave user-wide environment/configuration untouched.
    env = os.environ.copy()
    for key in list(env):
        upper = key.upper()
        if upper.endswith("API_KEY") or upper.startswith(("ORACLE_", "CODEX_")):
            env.pop(key, None)
    env["ORACLE_ENGINE"] = "browser"
    env["ORACLE_HOME_DIR"] = str(state_dir / "oracle")
    return env


def _invoke_bridge(
    request: dict, *, timeout: float = 60, cancellable: bool = False
) -> dict:
    bridge = Path(__file__).with_name("browser_bridge") / "bridge.mjs"
    if (
        not bridge.is_file()
        or not (bridge.parent / "node_modules" / "@steipete" / "oracle").is_dir()
    ):
        raise TranslationAbortedError(
            "ChatGPT 浏览器组件尚未安装完整，请检查本地软件更新。"
        )
    state_dir = chatgpt_state_dir()
    state_dir.mkdir(parents=True, exist_ok=True)
    payload = {**request, "state_dir": str(state_dir)}
    if not _browser_lock.acquire(blocking=False):
        return {
            "ok": False,
            "status": "busy",
            "error_code": "busy",
            "not_submitted": True,
            "message": "ChatGPT 正在处理上一项操作，请稍后重试。",
        }
    process = None
    try:
        if cancellable and _cancel_wait.is_set():
            raise TranslationAbortedError("已停止等待 ChatGPT。" + RESUME_MESSAGE)
        process = subprocess.Popen(
            [_find_node(), str(bridge)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(bridge.parent),
            env=_bridge_environment(state_dir),
            **codex_subprocess_window_kwargs(),
        )
        started = time.monotonic()
        input_text = json.dumps(payload, ensure_ascii=False)
        while True:
            if cancellable and _cancel_wait.is_set():
                raise TranslationAbortedError("已停止等待 ChatGPT。" + RESUME_MESSAGE)
            if time.monotonic() - started > timeout:
                raise TranslationAbortedError(
                    "等待 ChatGPT 超时；请求可能仍在网页中运行。" + RESUME_MESSAGE
                )
            try:
                stdout, stderr = process.communicate(input=input_text, timeout=0.25)
                break
            except subprocess.TimeoutExpired:
                input_text = None
        try:
            response = json.loads(stdout.strip())
            if not isinstance(response, dict) or "ok" not in response:
                raise ValueError("missing result")
        except (ValueError, TypeError) as exc:
            # Do not expose logs, prompts, or login state in UI exceptions.
            raise TranslationAbortedError(
                "ChatGPT 浏览器组件未返回有效状态；已保留任务记录。" + RESUME_MESSAGE
            ) from exc
        return response
    except OSError as exc:
        raise TranslationAbortedError(
            "无法启动 ChatGPT 浏览器组件：" + str(exc)
        ) from exc
    finally:
        if process is not None and process.poll() is None:
            # Stop only this client. The persistent local connection worker can
            # finish collecting a submitted turn and save it for recovery.
            process.terminate()
            try:
                process.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate(timeout=3)
        _browser_lock.release()


def _ui_operation(op: str, *, cancellable=False, **kwargs) -> dict:
    try:
        result = _invoke_bridge(
            {"op": op, **kwargs},
            timeout=210 if op == "login" else 120,
            cancellable=cancellable,
        )
        result.setdefault("models", [])
        result.setdefault("efforts", [])
        return result
    except TranslationAbortedError as exc:
        return {
            "ok": False,
            "status": "unavailable",
            "message": str(exc),
            "models": [],
            "efforts": [],
        }


def login_chatgpt() -> dict:
    return _ui_operation("login")


def get_chatgpt_status() -> dict:
    return _ui_operation("status")


def refresh_chatgpt_options(model=None, effort=None, *, _cancellable=False) -> dict:
    return _ui_operation(
        "select" if model or effort else "catalog",
        model=model,
        effort=effort,
        cancellable=_cancellable,
    )


def cancel_chatgpt_wait() -> None:
    _cancel_wait.set()


def _clean_json_answer(answer: str) -> dict:
    """Accept a whole JSON response or one fenced JSON block, never an excerpt."""
    text = answer.strip().lstrip("\ufeff")
    fenced = re.fullmatch(
        r"```(?:json)?\s*\n(.*?)\n```", text, flags=re.DOTALL | re.IGNORECASE
    )
    if fenced:
        text = fenced.group(1).strip()
    try:
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("expected object")
    except (ValueError, TypeError) as exc:
        raise TranslationAbortedError(
            "ChatGPT 回答未满足 JSON 格式，原始回答已保留。" + RESUME_MESSAGE
        ) from exc
    return data


class ChatGPTTranslator(ScientificTranslator):
    name = "chatgpt-browser"
    envs = {"CHATGPT_MODEL": None, "CHATGPT_EFFORT": None, "CHATGPT_TIMEOUT": "900"}
    CustomPrompt = True

    def __init__(
        self, lang_in, lang_out, model=None, envs=None, prompt=None, ignore_cache=False
    ):
        _cancel_wait.clear()
        self.set_envs(envs)
        requested_model = model or self.envs.get("CHATGPT_MODEL")
        requested_effort = self.envs.get("CHATGPT_EFFORT")
        try:
            self.timeout = max(
                60, min(7200, int(self.envs.get("CHATGPT_TIMEOUT") or 900))
            )
        except (TypeError, ValueError) as exc:
            raise TranslationAbortedError("ChatGPT 等待时间必须为整数秒。") from exc
        result = refresh_chatgpt_options(
            requested_model, requested_effort, _cancellable=True
        )
        if _cancel_wait.is_set():
            raise TranslationAbortedError("已取消 ChatGPT 翻译。")
        if not result.get("ok") or not result.get("model"):
            raise TranslationAbortedError(
                result.get("message") or "请先登录 ChatGPT 并刷新可用模型。"
            )
        actual_model = result["model"]
        actual_effort = result.get("effort") or ""
        if requested_model and actual_model != requested_model:
            raise TranslationAbortedError(
                "网页未确认所选 ChatGPT 模型，请刷新后重新选择。"
            )
        if requested_effort and actual_effort != requested_effort:
            raise TranslationAbortedError("网页未确认所选推理强度，请刷新后重新选择。")
        super().__init__(
            lang_in, lang_out, actual_model, prompt=prompt, ignore_cache=ignore_cache
        )
        self.reasoning_effort = actual_effort
        self.add_cache_impact_parameters("reasoning_effort", actual_effort)
        self.add_cache_impact_parameters("transport", "oracle-browser-v1")
        self._request_root = chatgpt_state_dir() / "requests"

    def set_concurrency(self, value) -> None:
        self.max_concurrency = 1

    def _execute_codex_request(
        self, prompt_text, schema, response_loader, mode_override=None
    ):
        # Hook name is retained to share the mature scientific rules; no Codex
        # executable, credentials, CLI flags, or fallback are involved here.
        # Ordinary web chat has no --output-schema constraint, so supply the
        # exact contract in the message as well as validating the returned data.
        prompt_text += (
            "\n\nRequired output JSON schema (return only the matching JSON object):\n"
            + json.dumps(schema, ensure_ascii=False, sort_keys=True)
        )
        identity = {
            "prompt": prompt_text,
            "schema": schema,
            "model": self.model,
            "effort": self.reasoning_effort,
        }
        digest = hashlib.sha256(
            json.dumps(identity, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        request_dir = self._request_root / digest
        request_dir.mkdir(parents=True, exist_ok=True)
        with _request_lock(request_dir / "request.lock"):
            return self._execute_browser_request(
                request_dir, digest, prompt_text, response_loader
            )

    def _execute_browser_request(
        self, request_dir, digest, prompt_text, response_loader
    ):
        manifest_path = request_dir / "request.json"
        saved = _read_json(manifest_path)
        attempt = saved.get("attempt", "attempt-1")
        if not re.fullmatch(r"attempt-\d+", attempt):
            raise TranslationAbortedError("ChatGPT 任务记录的批次编号无效。")
        attempt_dir = request_dir / attempt
        answer_path = attempt_dir / "validated.json"
        if (
            saved.get("status") == "complete"
            and answer_path.is_file()
            and not self.ignore_cache
        ):
            return response_loader(str(answer_path))
        if saved.get("status") in {"complete", "invalid"} and self.ignore_cache:
            attempt = "attempt-" + str(int(attempt.split("-")[1]) + 1)
            attempt_dir = request_dir / attempt
            saved = {}
        attempt_dir.mkdir(parents=True, exist_ok=True)
        prompt_path = attempt_dir / "prompt.txt"
        output_path = attempt_dir / "answer.txt"
        answer_path = attempt_dir / "validated.json"
        prompt_path.write_text(prompt_text, encoding="utf-8")
        # Any controller crash after this durable marker recovers instead of
        # issuing a new prompt. The companion can prove pre-submission failures.
        recover = saved.get("status") in {"pending", "paused", "invalid"}
        record = {
            "attempt": attempt,
            "status": "pending",
            "model": self.model,
            "effort": self.reasoning_effort,
        }
        if saved.get("session_id"):
            record["session_id"] = saved["session_id"]
        _write_json(manifest_path, record)
        payload = {
            "op": "recover" if recover else "translate",
            "request_dir": str(attempt_dir),
            "request_id": digest + "-" + attempt,
            "model": self.model,
            "effort": self.reasoning_effort,
            "prompt_file": str(prompt_path),
            "output_file": str(output_path),
            "session_id": record.get("session_id"),
            "timeout_seconds": self.timeout,
        }
        try:
            result = _invoke_bridge(
                payload, timeout=self.timeout + 30, cancellable=True
            )
            if result.get("session_id"):
                record["session_id"] = result["session_id"]
            if result.get("conversation_url"):
                record["conversation_url"] = result["conversation_url"]
            if not result.get("ok"):
                # Only explicit evidence of no submission permits a new attempt.
                record["status"] = (
                    "ready" if result.get("not_submitted") is True else "paused"
                )
                _write_json(manifest_path, record)
                raise TranslationAbortedError(
                    (result.get("message") or "ChatGPT 请求中断。")
                    + " "
                    + RESUME_MESSAGE
                )
            answer = result.get("answer")
            if answer is None and output_path.is_file():
                answer = output_path.read_text(encoding="utf-8")
            if not isinstance(answer, str) or not answer.strip():
                raise TranslationAbortedError(
                    "ChatGPT 尚未返回完整回答。" + RESUME_MESSAGE
                )
            output_path.write_text(answer, encoding="utf-8")
            try:
                parsed = _clean_json_answer(answer)
                _write_json(answer_path, parsed)
                value = response_loader(str(answer_path))
            except (ValueError, TypeError, RuntimeError) as exc:
                record["status"] = "invalid"
                _write_json(manifest_path, record)
                raise TranslationAbortedError(
                    "ChatGPT 已回答，但 JSON 格式、段落数量或字段不正确，原始回答已保留。"
                    "可勾选 Ignore cache 后重新翻译以重试；尚未完成的请求仍会先恢复。"
                ) from exc
            record["status"] = "complete"
            _write_json(manifest_path, record)
            return value
        except TranslationAbortedError:
            # Preserve any richer state already saved by the result handler.
            if _read_json(manifest_path).get("status") == "pending":
                record["status"] = "paused"
                _write_json(manifest_path, record)
            raise

    def _require_translated_prose(self, sources, targets):
        """Do not report an exhausted source-preservation fallback as completion.

        References, authors, and intentionally preserved document regions use
        separate converter paths. Reuse the existing conservative prose gate,
        which excludes formula tokens, URLs, acronyms, and model identifiers.
        """
        from pdf2zh.translation_quality import has_unchanged_translatable_english

        if self._checks_english_residue():
            for source, target in zip(sources, targets, strict=True):
                if has_unchanged_translatable_english(source, target):
                    raise TranslationAbortedError(
                        "ChatGPT 未完整翻译部分正文，已停止生成结果；"
                        "已通过检查的译文和原始回答已保留。"
                        "请检查翻译会话，修正原因后重新翻译。"
                    )
        return targets

    def do_translate(self, text: str) -> str:
        translated = super().do_translate(text)
        return self._require_translated_prose([text], [translated])[0]

    def translate(self, text: str, ignore_cache: bool = False) -> str:
        translated = super().translate(text, ignore_cache=ignore_cache)
        return self._require_translated_prose([text], [translated])[0]

    def translate_batch(
        self, texts: list[str], ignore_cache: bool = False
    ) -> list[str]:
        translated = super().translate_batch(texts, ignore_cache=ignore_cache)
        return self._require_translated_prose(texts, translated)

    def translate_batch_with_formula_contexts(
        self,
        texts: list[str],
        formula_contexts: list[dict[str, str]],
        ignore_cache: bool = False,
    ) -> list[str]:
        translated = super().translate_batch_with_formula_contexts(
            texts, formula_contexts, ignore_cache=ignore_cache
        )
        return self._require_translated_prose(texts, translated)
