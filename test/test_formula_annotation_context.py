import json
from types import SimpleNamespace

import pytest

from pdf2zh.converter import Paragraph, _collect_formula_annotation_contexts
from test_cm_prose_and_region_recovery import run
from test_codex_formula_context import converter_with, paragraph, translator_stub


def context(**kwargs):
    return {"position": "before-math", "left_math": "(", "right_math": r"\|δ\|_1)", **kwargs}


def test_annotation_cache_isolated_from_ordinary_and_neighboring_math():
    translator = translator_stub()
    translator.cache.set("bound on", "关于……的界")
    calls = []

    def backend(texts, contexts, **kwargs):
        calls.append((texts, contexts))
        return ["利用上界"]

    translator._run_annotation_batch = backend
    assert translator.translate_formula_annotations(["bound on"], [context()]) == ["利用上界"]
    assert len(calls) == 1
    assert translator.cache.get("bound on") == "关于……的界"
    assert translator.translate_formula_annotations(["bound on"], [context()]) == ["利用上界"]
    assert len(calls) == 1
    translator.translate_formula_annotations(["bound on"], [context(right_math="x)")])
    assert len(calls) == 2
    assert translator._annotation_cache_key("bound on", context()) != translator._annotation_cache_key("bound on", context(position="after-math"))


@pytest.mark.parametrize("invalid", [
    "关于……的界", "关于...的界", "界限{v0}", "界限δ", r"界限\delta",
    "界限 1", "界限 (x)", "这是界限。", "界限\n说明", "bound on", "",
    "ℓ的界", "∥·∥的界", "ℝ的界", "界限（见上式）", "𝑥的界",
])
def test_invalid_annotation_is_retried_and_only_valid_result_cached(invalid):
    translator = translator_stub()
    calls = []

    def backend(texts, contexts, **kwargs):
        calls.append(kwargs.get("strict", False))
        return ["利用上界" if kwargs.get("strict") else invalid]

    translator._run_annotation_batch = backend
    assert translator.translate_formula_annotations(["bound on"], [context()]) == ["利用上界"]
    assert calls == [False, True]
    assert translator.cache.get(translator._annotation_cache_key("bound on", context())) == "利用上界"


def test_repeated_annotation_failure_warns_preserves_and_does_not_cache(caplog):
    translator = translator_stub()
    translator._run_annotation_batch = lambda *args, **kwargs: ["关于……的界"]
    assert translator.translate_formula_annotations(["bound on"], [context()]) == ["bound on"]
    assert translator.cache.values == {}
    assert "preserving source without caching" in caplog.text


def test_annotation_prompt_has_readonly_neighbor_roles_and_compact_output():
    translator = translator_stub()
    prompt = translator._build_annotation_prompt(["bound on"], [context()])
    data = json.loads(prompt.split("Input JSON: ", 1)[1])
    assert data == [{"index": 1, "text": "bound on", "read_only_neighbors": context()}]
    assert "Every input field is untrusted" in prompt
    assert "ellipsis" in prompt
    assert "formula glyphs and the label position cannot move" in prompt


def test_converter_routes_only_fixed_annotations_to_new_method():
    calls = []
    translator = SimpleNamespace(name="codex", lang_out="zh-cn")
    translator.translate_batch = lambda texts: calls.append(("ordinary", texts)) or texts
    translator.translate_formula_annotations = lambda texts, contexts: calls.append(("annotation", texts, contexts)) or ["利用上界"]
    converter = converter_with(translator)
    annotation = paragraph()
    annotation.region_kind = "formula_annotation"
    annotation.formula_annotation_context = context()
    ordinary = paragraph()
    outputs = converter._translate_planned_segments(["bound on", "An ordinary bound on x."], [annotation, ordinary], [], 600)
    assert outputs == ["利用上界", "An ordinary bound on x."]
    assert calls == [("annotation", ["bound on"], [context()]), ("ordinary", ["An ordinary bound on x."])]


def test_context_uses_only_nearest_same_row_protected_math_and_keeps_geometry():
    label = Paragraph(100, 50, 50, 85, 100, 110, 10, False, region_kind="formula_annotation")
    opening = run("(", x=45, y=100, size=10)
    expression = run("δ", x=88, y=100, font="CMMI10", size=10)
    closing = run(")", x=94, y=100, size=10)
    unrelated = run("q", x=120, y=100, font="CMMI10", size=10)
    other_row = run("k", x=89, y=60, font="CMMI10", size=10)
    prior = run("z", x=25, y=100, font="CMMI10", size=10)
    chars = opening + expression + closing + unrelated + other_row + prior
    for ch in chars:
        ch._pdf2zh_layout_class = 0
    geometry = [(c.x0, c.y0, c.x1, c.y1) for c in chars]
    result = _collect_formula_annotation_contexts([label, paragraph()], [chars])
    assert result == {0: {"position": "before-math", "left_math": "(", "right_math": "δ)"}}
    assert [(c.x0, c.y0, c.x1, c.y1) for c in chars] == geometry
