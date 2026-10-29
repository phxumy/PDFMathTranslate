import ast
from io import StringIO
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from pdf2zh import codex_models


def model(name="gpt-6.1-terra", efforts=("low", "max", "ultra")):
    return {
        "id": name,
        "model": name,
        "displayName": "Example model",
        "supportedReasoningEfforts": [{"reasoningEffort": x} for x in efforts],
        "defaultReasoningEffort": "low",
    }


def process_fixture(monkeypatch, replies):
    calls = []

    class Input(StringIO):
        def close(self):
            self.saved = self.getvalue()
            super().close()

    process = SimpleNamespace(
        stdin=Input(),
        stdout=StringIO("\n".join(json.dumps(r) for r in replies) + "\n"),
        terminated=False,
    )

    def popen(command, **kwargs):
        calls.append((command, kwargs))
        return process

    monkeypatch.setattr(codex_models, "_cli", lambda value: value or "codex")
    monkeypatch.setattr(codex_models.subprocess, "Popen", popen)
    monkeypatch.setattr(
        codex_models, "_terminate_process", lambda p: setattr(p, "terminated", True)
    )
    return process, calls


def test_catalog_paginates_with_selected_cli_and_profile_and_cleans_up(monkeypatch):
    process, calls = process_fixture(
        monkeypatch,
        [
            {"id": 1, "result": {}},
            {"id": 2, "result": {"data": [model()], "nextCursor": "next"}},
            {
                "id": 3,
                "result": {"data": [model(), model("new-model")], "nextCursor": None},
            },
        ],
    )
    result = codex_models.get_codex_models("C:/custom/codex.exe", "work")
    assert result["ok"]
    assert [m["id"] for m in result["models"]] == ["gpt-6.1-terra", "new-model"]
    assert calls[0][0] == [
        "C:/custom/codex.exe",
        "--profile",
        "work",
        "app-server",
        "--listen",
        "stdio://",
    ]
    requests = [json.loads(line) for line in process.stdin.saved.splitlines()]
    assert [r["method"] for r in requests] == [
        "initialize",
        "initialized",
        "model/list",
        "model/list",
    ]
    assert requests[-1]["params"]["cursor"] == "next"
    assert process.terminated and process.stdout.closed


@pytest.mark.parametrize(
    "failure",
    [
        {"id": 2, "error": {"message": "token=private-do-not-expose"}},
        {"id": 2, "result": {"data": "bad"}},
        {"id": 2, "result": {"data": [], "nextCursor": 12}},
    ],
)
def test_catalog_errors_are_sanitized_and_leave_manual_fallback(monkeypatch, failure):
    process, _ = process_fixture(monkeypatch, [{"id": 1, "result": {}}, failure])
    result = codex_models.get_codex_models()
    assert not result["ok"] and not result["models"]
    assert "private-do-not-expose" not in json.dumps(result)
    assert "手动" in result["message"] and process.terminated


def test_missing_cli_does_not_start_a_process(monkeypatch):
    monkeypatch.setattr(codex_models, "_cli", lambda _: None)
    assert not codex_models.get_codex_models()["ok"]


def test_picker_uses_catalog_efforts_and_keeps_manual_new_model_path():
    models = codex_models._models_from_page({"data": [model()]})
    allowed = {"none", "low", "medium", "high", "xhigh", "max"}
    assert codex_models.effort_choices(models, "gpt-6.1-terra", allowed) == [
        "low",
        "max",
    ]
    assert "max" in codex_models.effort_choices(models, "future-id", allowed)
    assert codex_models.model_choices(models)[0][1] == ""


@pytest.fixture
def callbacks():
    path = Path(__file__).parents[1] / "pdf2zh/gui.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    tree.body = [
        n
        for n in tree.body
        if isinstance(n, ast.FunctionDef)
        and n.name in {"refresh_codex_model_catalog", "select_codex_catalog_model"}
    ]
    ns = {
        "gr": SimpleNamespace(update=lambda **kw: kw),
        "CodexTranslator": SimpleNamespace(
            SUPPORTED_REASONING_EFFORTS={
                "none",
                "low",
                "medium",
                "high",
                "xhigh",
                "max",
            }
        ),
        "get_codex_models": lambda *args: {
            "ok": True,
            "models": codex_models._models_from_page({"data": [model()]}),
            "message": "loaded",
        },
        "model_choices": codex_models.model_choices,
        "effort_choices": codex_models.effort_choices,
    }
    exec(compile(tree, str(path), "exec"), ns)
    return ns


def test_refresh_preserves_manual_values_and_restores_controls(callbacks):
    updates = list(
        callbacks["refresh_codex_model_catalog"](
            "Codex", "codex", "", "future-id", "max"
        )
    )
    assert len(updates) == 2 and all(len(row) == 8 for row in updates)
    assert all(not update["interactive"] for update in updates[0][2:])
    assert updates[-1][5]["value"] == "future-id" and updates[-1][6]["value"] == "max"
    assert all(update["interactive"] for update in updates[-1][2:])
    callbacks["get_codex_models"] = lambda *args: {"ok": False, "message": "failed"}
    result = list(
        callbacks["refresh_codex_model_catalog"]("Codex", "", "", "future-id", "max")
    )[-1]
    assert result[5]["value"] == "future-id" and result[6]["value"] == "max"


def test_selecting_model_limits_effort_but_unknown_model_stays_editable(callbacks):
    models = codex_models._models_from_page(
        {"data": [model(efforts=("low", "medium"))]}
    )
    choose = callbacks["select_codex_catalog_model"]
    assert choose("Codex", models, "gpt-6.1-terra", "max")["value"] == "low"
    assert choose("Codex", models, "future-id", "max")["value"] == "max"
    assert choose("Google", models, "gpt-6.1-terra", "max") == {}


def test_unexpected_catalog_error_restores_controls_without_raw_output(callbacks):
    def fail(*args):
        raise RuntimeError("token=do-not-display")

    callbacks["get_codex_models"] = fail
    result = list(
        callbacks["refresh_codex_model_catalog"]("Codex", "", "", "future-id", "max")
    )[-1]
    assert all(v["interactive"] for v in result[2:])
    assert "do-not-display" not in str(result)
