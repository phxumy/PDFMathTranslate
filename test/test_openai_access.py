"""The model list, chat probe, and translator share the real SDK with a local transport."""

import json
import os
import sqlite3

import httpx
import openai
import pytest

from pdf2zh import openai_access as api
from pdf2zh.config import ConfigManager
from pdf2zh.converter import _retryable_translation_error
from pdf2zh.openai_keys import KEY_REF, OpenAIKeys
from pdf2zh.translator import OpenAITranslator


def completion():
    return {
        "id": "cmpl-test",
        "object": "chat.completion",
        "created": 1,
        "model": "test",
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": "译文"},
            }
        ],
    }


def sdk(handler):
    return openai.OpenAI(
        api_key="local-fixture",
        base_url="http://localhost/v1",
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


@pytest.mark.parametrize(
    "model,options",
    [
        ("gpt-4.1-mini", {"temperature": 0}),
        ("gpt-6.2-sol", {}),
        ("gpt-6.2-astra", {}),
        ("private-alias", {}),
    ],
)
def test_translation_uses_model_compatible_parameters(model, options, monkeypatch):
    calls = []

    def handler(req):
        calls.append(json.loads(req.content))
        return httpx.Response(200, json=completion())

    with sdk(handler) as client:
        monkeypatch.setattr(api, "client_for", lambda *args, **kwargs: client)
        translator = OpenAITranslator("en", "zh", model, api_key="local-fixture")
        assert translator.do_translate("sample") == "译文"
    assert len(calls) == 1
    assert {
        k: v for k, v in calls[0].items() if k not in ("model", "messages")
    } == options


@pytest.mark.parametrize(
    "status,code,count",
    [
        (400, "invalid_request_error", 1),
        (401, "invalid_api_key", 1),
        (404, "model_not_found", 1),
        (429, "insufficient_quota", 1),
        (429, "rate_limit_exceeded", 3),
        (503, "server_error", 3),
    ],
)
def test_http_errors_stop_with_bounded_attempts(status, code, count, monkeypatch):
    calls = []

    def handler(req):
        calls.append(req.url.path)
        return httpx.Response(
            status,
            json={
                "error": {
                    "code": code,
                    "message": "local-fixture private PDF text",
                    "type": "example",
                    "param": None,
                }
            },
        )

    monkeypatch.setattr(api.time, "sleep", lambda *_: None)
    with sdk(handler) as client, pytest.raises(api.OpenAIServiceError) as error:
        api.chat_request(client, "example", [], {})
    assert len(calls) == count
    assert not _retryable_translation_error(error.value)
    assert str(status) in str(error.value) and "local-fixture" not in str(error.value)


def test_catalog_and_probe_use_correct_paths_and_model(monkeypatch):
    calls = []

    def handler(req):
        calls.append((req.url.path, json.loads(req.content) if req.content else {}))
        if req.method == "GET":
            return httpx.Response(
                200,
                json={
                    "object": "list",
                    "data": [
                        {"id": s, "object": "model", "created": 1, "owned_by": "test"}
                        for s in ["z", "a", "z"]
                    ],
                },
            )
        return httpx.Response(200, json=completion())

    monkeypatch.setattr(api, "client_for", lambda *args, **kw: sdk(handler))
    assert api.model_ids("http://localhost/v1", "fixture") == ["a", "z"]
    api.test_model_connection("http://localhost/v1", "fixture", "gpt-6.2-sol")
    assert calls[-1] == (
        "/v1/chat/completions",
        {
            "model": "gpt-6.2-sol",
            "messages": [{"role": "user", "content": "OK"}],
            "max_completion_tokens": 16,
        },
    )


def test_unsupported_temperature_is_removed_once():
    bodies = []

    def handler(req):
        body = json.loads(req.content)
        bodies.append(body)
        if "temperature" in body:
            return httpx.Response(
                400,
                json={
                    "error": {
                        "message": "default only",
                        "param": "temperature",
                        "code": "unsupported_value",
                    }
                },
            )
        return httpx.Response(200, json=completion())

    with sdk(handler) as client:
        assert api.chat_request(client, "gateway", [], {"temperature": 0}) == "译文"
    assert len(bodies) == 2 and "temperature" not in bodies[1]


def test_legacy_key_migration_rename_and_restart(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    endpoint = "https://api.example.test/v1"
    secret = "local-private-fixture"
    envs = {
        "OPENAI_BASE_URL": endpoint,
        "OPENAI_API_KEY": secret,
        "OPENAI_MODEL": "my-model",
    }
    path.write_text(
        json.dumps({"translators": [{"name": "openai", "envs": envs}]}), encoding="utf8"
    )
    # Bind a fresh manager to this directory without reading a real user profile.
    monkeypatch.setattr(ConfigManager, "_instance", None)
    monkeypatch.setattr(type(path), "home", classmethod(lambda cls: tmp_path))
    ConfigManager.custome_config(path)
    vault = ConfigManager.openai_keys()
    ref = ConfigManager.get_translator_by_name("openai")["OPENAI_API_KEY"]
    assert ref.startswith(KEY_REF) and secret not in path.read_text()
    assert vault.remember(endpoint + "/", secret, "Research") == ref
    other = vault.remember(endpoint, "another-fixture", "Personal")
    assert other != ref and vault.remember(endpoint, ref, "Lab") == ref
    with pytest.raises(ValueError):
        vault.remember(endpoint, ref, "Personal")
    with pytest.raises(ValueError):
        vault.resolve("https://other.example.test/v1", ref)
    reopened = OpenAIKeys(path)
    assert (
        reopened.resolve(endpoint, ref) == secret and ("Lab", ref) in reopened.choices()
    )
    if os.name == "nt":
        with sqlite3.connect(vault.path) as db:
            assert (
                secret.encode()
                not in db.execute(
                    "select secret from profiles where id=?", (ref,)
                ).fetchone()[0]
            )
    else:
        assert vault.path.stat().st_mode & 0o077 == 0
