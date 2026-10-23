import pytest

from pdf2zh.translation_quality import normalize_scientific_cross_reference_placement


@pytest.mark.parametrize(
    "opening", ["Compared with", "Compared to", "Unlike", "In contrast to"]
)
def test_comparison_clause_is_not_one_contiguous_reference_list(opening):
    source = f"{opening} figure 3(a), figures 3(b)–(d) still have similar fractal structures."
    target = "与图 3(a) 相比，图 3(b)–(d) 仍具有相似的分形结构。"
    assert normalize_scientific_cross_reference_placement(source, target) == target
    for damaged in (
        "与图 3(a) 相比，图 3(b) 仍具有相似的分形结构。",
        "与图 3(a) 相比，图 4(b)–(d) 仍具有相似的分形结构。",
        "与原图相比，图 3(b)–(d) 仍具有相似的分形结构。",
    ):
        assert normalize_scientific_cross_reference_placement(source, damaged) is None


def test_ordinary_reference_list_is_still_validated_as_one_unit():
    source = "See figures 3(a), figures 3(b)–(d) for the spectrum."
    target = "能谱见图 3(a)、图 3(b)–(d)。"
    assert normalize_scientific_cross_reference_placement(source, target) == target
