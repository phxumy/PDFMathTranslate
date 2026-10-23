import json

from test_codex_formula_context import translator_stub
from pdf2zh.translator import CodexTranslator


def test_contextual_gap_repair_preserves_reference_and_original_formula_position():
    translator = translator_stub()
    translator._run_batch_translation = lambda gaps, **kwargs: ["操作", "如图所示。"]
    calls = []

    def repair(source, context, gaps):
        calls.append((source, context, gaps))
        return ["操作", "如图 1c 所示。"]

    translator._run_formula_prose_gap_repair = repair
    source = "The operation {v0} is shown in Fig. 1c."
    context = [{"placeholder": "{v0}", "unicode_formula": "U"}]
    result = translator._retry_contextual_item_by_prose_spans(source, context)
    assert result == "操作{v0}如图 1c 所示。"
    assert calls == [(source, context, ["The operation ", " is shown in Fig. 1c."])]


def test_gap_repair_still_rejects_missing_reference_or_copied_formula():
    for repaired in (["操作", "如图所示。"], ["操作{v0}", "如图 1c 所示。"]):
        translator = translator_stub()
        translator._run_batch_translation = lambda gaps, **kwargs: [
            "操作",
            "如图所示。",
        ]
        translator._run_formula_prose_gap_repair = lambda *args: repaired
        assert (
            translator._retry_contextual_item_by_prose_spans(
                "The operation {v0} is shown in Fig. 1c.",
                [{"placeholder": "{v0}", "unicode_formula": "U"}],
            )
            is None
        )


def test_gap_prompt_marks_full_paragraph_read_only_and_requests_only_gaps():
    translator = translator_stub()
    translator.batch_output_schema = {}
    prompts = []
    translator._execute_codex_request = lambda prompt, schema, load: prompts.append(
        prompt
    ) or ["两侧"]
    result = CodexTranslator._run_formula_prose_gap_repair(
        translator,
        "The operations on either side of {v0}",
        [{"placeholder": "{v0}", "unicode_formula": "U"}],
        ["The operations on either side of "],
    )
    assert result == ["两侧"]
    assert "read-only context" in prompts[0]
    assert "Do not move meaning to a different gap" in prompts[0]
    payload = json.loads(prompts[0].split("Input JSON: ", 1)[1])
    assert payload["gaps"] == [
        {"index": 1, "text": "The operations on either side of "}
    ]
