"""Read model metadata through the selected official CLI, without reading tokens."""

from __future__ import annotations

import json
from queue import Empty, Full, Queue
import re
import subprocess
from threading import Thread
import time

from pdf2zh.codex_account import _cli, _terminate_process, _window_kwargs
from pdf2zh.codex_runtime import CodexRuntime


class CatalogError(Exception):
    pass


def _models_from_page(result):
    rows = result.get("data")
    if not isinstance(rows, list):
        raise CatalogError("invalid model page")
    models = []
    for item in rows:
        if not isinstance(item, dict) or item.get("hidden") is True:
            continue
        model = item.get("model") or item.get("id")
        if not isinstance(model, str) or not re.fullmatch(
            r"[A-Za-z0-9._:/-]{1,160}", model
        ):
            continue
        label = item.get("displayName")
        if not isinstance(label, str) or not label.strip():
            label = model
        efforts = []
        options = item.get("supportedReasoningEfforts")
        for option in options if isinstance(options, list) else []:
            effort = option.get("reasoningEffort") if isinstance(option, dict) else None
            if (
                isinstance(effort, str)
                and re.fullmatch(r"[a-z]{1,20}", effort)
                and effort not in efforts
            ):
                efforts.append(effort)
        default = item.get("defaultReasoningEffort")
        models.append(
            {
                "id": model,
                "label": label[:200],
                "efforts": efforts,
                "default_effort": default if default in efforts else None,
            }
        )
    return models


def get_codex_models(configured_bin=None, profile=None, *, timeout=12.0):
    """Query model/list with a bounded lifetime; raw CLI output never escapes.

    This is a catalog, which the CLI can serve from cache. It is not an
    inference entitlement check. No thread, turn, login, or model override is
    created by this metadata request.
    """
    binary = _cli(configured_bin)
    if not binary:
        return {
            "ok": False,
            "models": [],
            "message": "未找到 Codex CLI；仍可手动填写模型和推理强度。",
        }
    runtime = CodexRuntime.for_binary(binary)
    command = runtime.command()
    if profile:
        command += ["--profile", str(profile)]
    command += ["app-server", "--listen", "stdio://"]
    process = None
    reader = None
    responses = Queue(maxsize=16)
    deadline = time.monotonic() + timeout
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            **runtime.subprocess_kwargs(account_operation=True),
            **_window_kwargs(),
        )

        def read_responses():
            try:
                while line := process.stdout.readline(1024 * 1024 + 1):
                    if len(line) > 1024 * 1024:
                        break
                    try:
                        value = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(value, dict) and isinstance(value.get("id"), int):
                        try:
                            responses.put_nowait(value)
                        except Full:
                            break
            except (OSError, ValueError):
                pass
            finally:
                try:
                    responses.put_nowait(None)
                except Full:
                    pass

        reader = Thread(target=read_responses, daemon=True)
        reader.start()

        def send(message):
            process.stdin.write(json.dumps(message) + "\n")
            process.stdin.flush()

        def request(request_id, method, params):
            send({"id": request_id, "method": method, "params": params})
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError()
                try:
                    response = responses.get(timeout=remaining)
                except Empty as exc:
                    raise TimeoutError() from exc
                if response is None:
                    raise CatalogError("CLI exited")
                if response.get("id") != request_id:
                    continue
                if "error" in response or not isinstance(response.get("result"), dict):
                    raise CatalogError("RPC failed")
                return response["result"]

        request(
            1,
            "initialize",
            {"clientInfo": {"name": "pdf2zh_model_picker", "version": "1"}},
        )
        send({"method": "initialized", "params": {}})
        models, model_ids, cursors = [], set(), set()
        cursor = None
        for page in range(20):
            params = {"limit": 100, "includeHidden": False}
            if cursor is not None:
                params["cursor"] = cursor
            result = request(page + 2, "model/list", params)
            for model in _models_from_page(result):
                if model["id"] not in model_ids:
                    models.append(model)
                    model_ids.add(model["id"])
            cursor = result.get("nextCursor")
            if cursor is None:
                break
            if not isinstance(cursor, str) or cursor in cursors:
                raise CatalogError("invalid pagination")
            cursors.add(cursor)
        else:
            raise CatalogError("too many pages")
        if not models:
            return {
                "ok": False,
                "models": [],
                "message": "Codex 未返回可选模型；仍可手动填写。",
            }
        return {
            "ok": True,
            "models": models,
            "message": f"已读取 {len(models)} 个模型。可从列表选择，也可手动填写新型号；实际可用性以翻译请求结果为准。",
        }
    except TimeoutError:
        return {
            "ok": False,
            "models": [],
            "message": "读取模型列表超时；保留当前设置，可重试或手动填写。",
        }
    except (OSError, ValueError, TypeError, CatalogError):
        return {
            "ok": False,
            "models": [],
            "message": "无法读取模型列表，请检查所选 Codex CLI 和 profile；当前设置保留，仍可手动填写。",
        }
    finally:
        if process is not None:
            _terminate_process(process)
            if reader is not None:
                reader.join(timeout=1)
            for stream in (process.stdin, process.stdout):
                if stream is not None:
                    stream.close()


def model_choices(models):
    return [("自动（由 CLI 选择）", "")] + [
        (f"{m['label']} · {m['id']}", m["id"]) for m in models
    ]


def effort_choices(models, model, allowed):
    entry = next((item for item in models if item["id"] == model), None)
    return (
        [effort for effort in entry["efforts"] if effort in allowed]
        if entry
        else [
            effort
            for effort in ("none", "low", "medium", "high", "xhigh", "max")
            if effort in allowed
        ]
    )
