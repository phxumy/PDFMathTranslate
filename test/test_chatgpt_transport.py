"""No real browser/account/model calls; exercise durable transport boundaries."""

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
