from pdf2zh.translator import CodexTranslator
from pdf2zh.translation_policy import ExactReplacement
from pdf2zh.translation_quality import has_omitted_scientific_content


def test_extreme_title_load_repartition_keeps_all_translated_text():
    tr = CodexTranslator.__new__(CodexTranslator)
    tr.lang_out = "zh"
    before = (
        ExactReplacement(
            "Designing energy-efficient",
            "通过预测并降低低温电子设备的冷却需求来设计高能效",
        ),
        ExactReplacement(
            "quantum computers through prediction and reduction of cooling requirements for cryogenic electronics",
            "量子计算机",
        ),
    )
    after = tr._rebalance_reference_continuation(before)
    assert "".join(x.translated for x in after) == "".join(x.translated for x in before)
    assert len(after[0].translated) < len(before[0].translated) / 2
    assert [x.source for x in after] == [x.source for x in before]


def test_extraction_predicate_must_survive_cross_column_translation():
    source = "The Chern number can be extracted from the conductivity tensor, so current measurements are valuable."
    assert has_omitted_scientific_content(
        source, "陈数可以从电导张量，因此电流测量很有价值。"
    )
    assert not has_omitted_scientific_content(
        source, "陈数可由电导张量求得，因此电流测量很有价值。"
    )
