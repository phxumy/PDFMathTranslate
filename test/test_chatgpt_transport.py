"""No real browser/account/model calls; exercise durable transport boundaries."""

import hashlib
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from pdf2zh import chatgpt as cg
from pdf2zh.translator import CodexTranslator, TranslationAbortedError


@pytest.fixture
def translator(tmp_path, monkeypatch):
    monkeypatch.setenv("PDF2ZH_CHATGPT_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(
        cg.ChatGPTTranslator,
        "set_envs",
        lambda self, envs: setattr(self, "envs", {**type(self).envs, **(envs or {})}),
    )
    monkeypatch.setattr(
        cg,
        "refresh_chatgpt_options",
        lambda *args, **kwargs: {
            "ok": True,
            "model": "actual-web-model",
            "effort": "actual-effort",
        },
    )
    return cg.ChatGPTTranslator(
        "en",
        "zh",
        envs={"CHATGPT_MODEL": "actual-web-model", "CHATGPT_EFFORT": "actual-effort"},
    )


def load(path):
    result = json.loads(Path(path).read_text(encoding="utf-8"))
    if (
        not isinstance(result.get("translations"), list)
        or len(result["translations"]) != 1
    ):
        raise ValueError("bad count")
    return result["translations"]


def execute(translator):
    return translator._execute_codex_request("one batch", {"type": "object"}, load)


def test_initialization_never_probes_codex(translator, monkeypatch):
    monkeypatch.setattr(
        CodexTranslator, "_probe_cli", Mock(side_effect=AssertionError("Codex invoked"))
    )
    other = cg.ChatGPTTranslator("en", "zh")
    other.set_concurrency(99)
    assert other.max_concurrency == 1
    assert not hasattr(other, "codex_bin")
    assert other.cache.params["model"] == "actual-web-model"
    assert other.cache.params["reasoning_effort"] == "actual-effort"


def test_ui_language_change_preserves_pending_request_and_target_language(translator, monkeypatch):
    selection = {"ok": True, "model": "actual-web-model", "effort": "即時"}
    monkeypatch.setattr(cg, "refresh_chatgpt_options", lambda *a, **kw: selection)
    old = cg.ChatGPTTranslator("en", "fr", task_id="locale-stable", envs={"CHATGPT_EFFORT": "即時"})
    bridge = Mock(side_effect=[
        {"ok": False, "not_submitted": False, "message": "interrupted"},
        {"ok": True, "answer": '{"translations":["traduction"]}'},
    ])
    monkeypatch.setattr(cg, "_invoke_bridge", bridge)
    with pytest.raises(TranslationAbortedError):
        execute(old)
    original = bridge.call_args.args[0]
    selection["effort"] = "即时"
    new = cg.ChatGPTTranslator("en", "fr", task_id="locale-stable", envs={"CHATGPT_EFFORT": "即时"})
    assert new.lang_out == "fr"
    assert new.task_id == old.task_id
    assert execute(new) == ["traduction"]
    resumed = bridge.call_args.args[0]
    assert resumed["op"] == "recover"
    assert resumed["request_dir"] == original["request_dir"]
    selection["effort"] = "高"
    with pytest.raises(TranslationAbortedError):
        cg.ChatGPTTranslator("en", "fr", task_id="locale-stable", envs={"CHATGPT_EFFORT": "即时"})


def test_completed_answer_reused_without_resubmission(translator, monkeypatch):
    bridge = Mock(
        return_value={
            "ok": True,
            "answer": '{"translations":["译文"]}',
            "session_id": "own-session",
        }
    )
    monkeypatch.setattr(cg, "_invoke_bridge", bridge)
    assert execute(translator) == ["译文"]
    assert execute(translator) == ["译文"]
    assert bridge.call_count == 1
    sent = Path(bridge.call_args.args[0]["prompt_file"]).read_text(encoding="utf-8")
    assert "Required output JSON schema" in sent
    assert json.loads(sent.splitlines()[-1]) == {"type": "object"}


def test_ambiguous_browser_close_recovers_same_request(translator, monkeypatch):
    bridge = Mock(
        side_effect=[
            {
                "ok": False,
                "message": "browser closed",
                "session_id": "submitted-session",
            },
            {"ok": True, "answer": '{"translations":["恢复译文"]}'},
        ]
    )
    monkeypatch.setattr(cg, "_invoke_bridge", bridge)
    with pytest.raises(TranslationAbortedError, match="browser closed"):
        execute(translator)
    assert execute(translator) == ["恢复译文"]
    first, second = [call.args[0] for call in bridge.call_args_list]
    assert first["op"] == "translate"
    assert second["op"] == "recover"
    assert second["session_id"] == "submitted-session"
    assert first["request_dir"] == second["request_dir"]


def test_controller_crash_resumes_without_new_prompt(translator, monkeypatch):
    bridge = Mock(
        side_effect=[
            TranslationAbortedError("controller terminated"),
            {"ok": True, "answer": '{"translations":["恢复"]}'},
        ]
    )
    monkeypatch.setattr(cg, "_invoke_bridge", bridge)
    with pytest.raises(TranslationAbortedError):
        execute(translator)
    execute(translator)
    assert bridge.call_args.args[0]["op"] == "recover"


def test_proven_not_submitted_can_try_again_after_login(translator, monkeypatch):
    bridge = Mock(
        side_effect=[
            {"ok": False, "not_submitted": True, "message": "needs login"},
            {"ok": True, "answer": '{"translations":["登录后的译文"]}'},
        ]
    )
    monkeypatch.setattr(cg, "_invoke_bridge", bridge)
    with pytest.raises(TranslationAbortedError):
        execute(translator)
    execute(translator)
    assert [call.args[0]["op"] for call in bridge.call_args_list] == [
        "translate",
        "translate",
    ]


@pytest.mark.parametrize(
    "answer",
    [
        '{"translations": []}',
        'explanation: {"translations":["译文"]}',
        '{"translations":["incomplete"',
    ],
)
def test_bad_answer_aborts_without_automatic_split_or_resend(
    translator, monkeypatch, answer
):
    bridge = Mock(return_value={"ok": True, "answer": answer})
    monkeypatch.setattr(cg, "_invoke_bridge", bridge)
    with pytest.raises(TranslationAbortedError):
        execute(translator)
    assert bridge.call_count == 1
    records = list(translator._request_root.glob("*/request.json"))
    assert json.loads(records[0].read_text(encoding="utf-8"))["status"] in {
        "paused",
        "invalid",
    }


def test_ignore_cache_creates_new_attempt_but_pending_still_recovers(
    translator, monkeypatch
):
    bridge = Mock(
        side_effect=[
            {"ok": True, "answer": '{"translations":["初稿"]}'},
            {"ok": False, "message": "closed"},
            {"ok": True, "answer": '{"translations":["重译"]}'},
        ]
    )
    monkeypatch.setattr(cg, "_invoke_bridge", bridge)
    execute(translator)
    translator.ignore_cache = True
    with pytest.raises(TranslationAbortedError):
        execute(translator)
    assert execute(translator) == ["重译"]
    first, second, third = [call.args[0] for call in bridge.call_args_list]
    assert first["request_dir"] != second["request_dir"]
    assert second["request_dir"] == third["request_dir"]
    assert third["op"] == "recover"


def test_changing_effort_separates_request_cache(translator, monkeypatch):
    bridge = Mock(return_value={"ok": True, "answer": '{"translations":["译文"]}'})
    monkeypatch.setattr(cg, "_invoke_bridge", bridge)
    execute(translator)
    translator.reasoning_effort = "other-effort"
    execute(translator)
    assert (
        bridge.call_args_list[0].args[0]["request_dir"]
        != bridge.call_args_list[1].args[0]["request_dir"]
    )


def test_fenced_json_is_accepted_but_surrounding_prose_is_not():
    assert cg._clean_json_answer('```json\n{"translation":"你好"}\n```') == {
        "translation": "你好"
    }
    with pytest.raises(TranslationAbortedError):
        cg._clean_json_answer('Here is the result: {"translation":"你好"}')


def test_child_environment_cannot_inherit_api_or_oracle_routing(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "test-not-a-real-key")
    monkeypatch.setenv("ORACLE_ENGINE", "api")
    monkeypatch.setenv("ORACLE_REMOTE_HOST", "unwanted-host")
    monkeypatch.setenv("CODEX_HOME", "unwanted-codex-config")
    env = cg._bridge_environment(tmp_path)
    assert env["ORACLE_ENGINE"] == "browser"
    assert "OPENAI_API_KEY" not in env
    assert "ORACLE_REMOTE_HOST" not in env
    assert "CODEX_HOME" not in env
    assert env["ORACLE_HOME_DIR"] == str(tmp_path / "oracle")


def test_unverified_model_or_effort_fails_closed(translator, monkeypatch):
    with pytest.raises(TranslationAbortedError, match="模型"):
        cg.ChatGPTTranslator("en", "zh", envs={"CHATGPT_MODEL": "wrong-model"})
    with pytest.raises(TranslationAbortedError, match="推理"):
        cg.ChatGPTTranslator("en", "zh", envs={"CHATGPT_EFFORT": "wrong-effort"})


def test_invalid_manifest_prevents_duplicate_submission(translator, monkeypatch):
    bridge = Mock(return_value={"ok": False, "message": "closed"})
    monkeypatch.setattr(cg, "_invoke_bridge", bridge)
    with pytest.raises(TranslationAbortedError):
        execute(translator)
    record = next(translator._request_root.glob("*/request.json"))
    record.write_text("broken", encoding="utf-8")
    with pytest.raises(TranslationAbortedError, match="记录无法读取"):
        execute(translator)
    assert bridge.call_count == 1


def test_invalid_completed_response_has_explicit_retry_path(translator, monkeypatch):
    bridge = Mock(
        side_effect=[
            {"ok": True, "answer": "invalid JSON"},
            {"ok": True, "answer": '{"translations":["重新回答"]}'},
        ]
    )
    monkeypatch.setattr(cg, "_invoke_bridge", bridge)
    with pytest.raises(TranslationAbortedError, match="Ignore cache"):
        execute(translator)
    translator.ignore_cache = True
    assert execute(translator) == ["重新回答"]
    first, second = [call.args[0] for call in bridge.call_args_list]
    assert first["request_dir"] != second["request_dir"]
    assert second["op"] == "translate"


def test_cancel_during_model_check_is_not_cleared(translator, monkeypatch):
    def selection(*args, **kwargs):
        cg.cancel_chatgpt_wait()
        return {"ok": True, "model": "actual-web-model", "effort": "actual-effort"}

    monkeypatch.setattr(cg, "refresh_chatgpt_options", selection)
    with pytest.raises(TranslationAbortedError, match="已取消"):
        cg.ChatGPTTranslator("en", "zh")
    cg._cancel_wait.clear()


def test_request_lock_rejects_concurrent_same_batch_and_releases(tmp_path):
    path = tmp_path / "request.lock"
    with cg._request_lock(path):
        with pytest.raises(TranslationAbortedError, match="同一批次"):
            with cg._request_lock(path):
                pytest.fail("a duplicate submission got the lock")
    with cg._request_lock(path):
        pass


def test_document_task_continues_across_batches_and_translator_instances(
    translator, monkeypatch
):
    bridge = Mock(return_value={"ok": True, "answer": '{"translations":["译文"]}'})
    monkeypatch.setattr(cg, "_invoke_bridge", bridge)
    first = cg.ChatGPTTranslator("en", "zh", task_id="document-a")
    resumed = cg.ChatGPTTranslator("en", "zh", task_id="document-a")
    execute(first)
    first._execute_codex_request("second batch", {"type": "object"}, load)
    resumed._execute_codex_request("third batch", {"type": "object"}, load)
    payloads = [call.args[0] for call in bridge.call_args_list]
    assert len({payload["task_id"] for payload in payloads}) == 1
    assert len({payload["request_dir"] for payload in payloads}) == 3
    for payload in payloads:
        manifest = Path(payload["request_dir"]).parent / "request.json"
        assert (
            json.loads(manifest.read_text(encoding="utf-8"))["task_id"] == first.task_id
        )


def test_different_documents_do_not_share_identical_batch_request(
    translator, monkeypatch
):
    bridge = Mock(return_value={"ok": True, "answer": '{"translations":["译文"]}'})
    monkeypatch.setattr(cg, "_invoke_bridge", bridge)
    execute(cg.ChatGPTTranslator("en", "zh", task_id="document-a"))
    execute(cg.ChatGPTTranslator("en", "zh", task_id="document-b"))
    first, second = [call.args[0] for call in bridge.call_args_list]
    assert first["task_id"] != second["task_id"]
    assert first["request_dir"] != second["request_dir"]


def test_explicit_task_restores_pending_request_after_new_instance(
    translator, monkeypatch
):
    bridge = Mock(
        side_effect=[
            {"ok": False, "message": "closed after submission"},
            {"ok": True, "answer": '{"translations":["恢复"]}'},
        ]
    )
    monkeypatch.setattr(cg, "_invoke_bridge", bridge)
    with pytest.raises(TranslationAbortedError):
        execute(cg.ChatGPTTranslator("en", "zh", task_id="document-a"))
    execute(cg.ChatGPTTranslator("en", "zh", task_id="document-a"))
    first, resumed = [call.args[0] for call in bridge.call_args_list]
    assert resumed["op"] == "recover"
    assert resumed["task_id"] == first["task_id"]
    assert resumed["request_dir"] == first["request_dir"]


def test_task_configuration_and_implicit_tasks_have_distinct_identities(
    translator, monkeypatch
):
    first = cg.ChatGPTTranslator("en", "zh", task_id="document-a")
    same = cg.ChatGPTTranslator(
        "en", "zh", task_id="document-a", envs={"CHATGPT_TIMEOUT": "120"}
    )
    assert first.task_id == same.task_id
    assert (
        cg.ChatGPTTranslator("en", "zh").task_id
        != cg.ChatGPTTranslator("en", "zh").task_id
    )
    monkeypatch.setattr(
        cg,
        "refresh_chatgpt_options",
        lambda *args, **kwargs: {
            "ok": True,
            "model": "other-web-model",
            "effort": "actual-effort",
        },
    )
    assert (
        cg.ChatGPTTranslator("en", "zh", task_id="document-a").task_id != first.task_id
    )
    monkeypatch.setattr(
        cg,
        "refresh_chatgpt_options",
        lambda *args, **kwargs: {
            "ok": True,
            "model": "actual-web-model",
            "effort": "other-effort",
        },
    )
    assert (
        cg.ChatGPTTranslator("en", "zh", task_id="document-a").task_id != first.task_id
    )


@pytest.mark.parametrize("task_id", ["", "../outside", "含中文", "a" * 129, 123])
def test_invalid_task_identity_is_rejected_before_browser_access(
    translator, monkeypatch, task_id
):
    refresh = Mock(side_effect=AssertionError("must not open browser"))
    monkeypatch.setattr(cg, "refresh_chatgpt_options", refresh)
    with pytest.raises(TranslationAbortedError, match="任务标识"):
        cg.ChatGPTTranslator("en", "zh", task_id=task_id)
    refresh.assert_not_called()


def legacy_request(translator, status, **extra):
    schema = {"type": "object"}
    prompt = (
        "one batch\n\nRequired output JSON schema (return only the matching JSON object):\n"
        + json.dumps(schema, ensure_ascii=False, sort_keys=True)
    )
    identity = {
        "prompt": prompt,
        "schema": schema,
        "model": translator.model,
        "effort": translator.reasoning_effort,
    }
    digest = hashlib.sha256(
        json.dumps(identity, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    directory = translator._request_root / digest
    cg._write_json(
        directory / "request.json",
        {
            "status": status,
            "attempt": "attempt-2",
            "session_id": "previously-submitted",
            **extra,
        },
    )
    return directory


@pytest.mark.parametrize("status", ["pending", "paused", "invalid"])
@pytest.mark.parametrize("ignore_cache", [False, True])
def test_legacy_submitted_request_is_recovered_before_new_task_send(
    translator, monkeypatch, status, ignore_cache
):
    old = legacy_request(translator, status)
    translator.ignore_cache = ignore_cache
    bridge = Mock(
        return_value={"ok": True, "answer": '{"translations":["旧请求恢复"]}'}
    )
    monkeypatch.setattr(cg, "_invoke_bridge", bridge)
    assert execute(translator) == ["旧请求恢复"]
    payload = bridge.call_args.args[0]
    assert payload["op"] == "recover"
    assert Path(payload["request_dir"]) == old / "attempt-2"
    assert payload["task_id"] == translator.task_id
    assert payload["session_id"] == "previously-submitted"
    assert json.loads((old / "request.json").read_text(encoding="utf-8"))[
        "legacy_recovery_complete"
    ]


def test_legacy_completed_answer_can_be_reused_without_creating_a_chat(
    translator, monkeypatch
):
    old = legacy_request(translator, "complete")
    cg._write_json(old / "attempt-2" / "validated.json", {"translations": ["旧译文"]})
    bridge = Mock(
        side_effect=AssertionError("completed answer should not be submitted")
    )
    monkeypatch.setattr(cg, "_invoke_bridge", bridge)
    assert execute(translator) == ["旧译文"]
    bridge.assert_not_called()


def test_legacy_ready_uses_new_task_scoped_request(translator, monkeypatch):
    old = legacy_request(translator, "ready")
    bridge = Mock(return_value={"ok": True, "answer": '{"translations":["新译文"]}'})
    monkeypatch.setattr(cg, "_invoke_bridge", bridge)
    execute(translator)
    payload = bridge.call_args.args[0]
    assert payload["op"] == "translate"
    assert Path(payload["request_dir"]).parent != old


def test_legacy_claimed_pending_request_cannot_be_reassigned(translator, monkeypatch):
    legacy_request(translator, "paused", task_id="another-document")
    bridge = Mock()
    monkeypatch.setattr(cg, "_invoke_bridge", bridge)
    with pytest.raises(TranslationAbortedError, match="另一个文档任务"):
        execute(translator)
    bridge.assert_not_called()


def test_unreadable_legacy_manifest_cannot_be_skipped_for_new_task(
    translator, monkeypatch
):
    old = legacy_request(translator, "paused")
    (old / "request.json").write_text("broken", encoding="utf-8")
    bridge = Mock()
    monkeypatch.setattr(cg, "_invoke_bridge", bridge)
    with pytest.raises(TranslationAbortedError, match="记录无法读取"):
        execute(translator)
    bridge.assert_not_called()


def test_new_task_attempt_supersedes_legacy_completed_cache(translator, monkeypatch):
    old = legacy_request(translator, "complete")
    cg._write_json(old / "attempt-2" / "validated.json", {"translations": ["旧译文"]})
    bridge = Mock(return_value={"ok": True, "answer": '{"translations":["新译文"]}'})
    monkeypatch.setattr(cg, "_invoke_bridge", bridge)
    translator.ignore_cache = True
    assert execute(translator) == ["新译文"]
    translator.ignore_cache = False
    assert execute(translator) == ["新译文"]
    assert bridge.call_count == 1


def test_legacy_invalid_recovery_precedes_explicit_new_attempt(translator, monkeypatch):
    old = legacy_request(translator, "invalid")
    translator.ignore_cache = True
    bridge = Mock(
        side_effect=[
            {"ok": True, "answer": "still invalid"},
            {"ok": True, "answer": '{"translations":["重译"]}'},
        ]
    )
    monkeypatch.setattr(cg, "_invoke_bridge", bridge)
    with pytest.raises(TranslationAbortedError):
        execute(translator)
    assert bridge.call_args.args[0]["op"] == "recover"
    assert Path(bridge.call_args.args[0]["request_dir"]) == old / "attempt-2"
    assert execute(translator) == ["重译"]
    assert bridge.call_args.args[0]["op"] == "translate"
    assert Path(bridge.call_args.args[0]["request_dir"]).parent != old
    translator.ignore_cache = False
    assert execute(translator) == ["重译"]
    assert bridge.call_count == 2
