import fitz
from pdf2zh.high_level import _recover_publisher_cover_lines
from pdf2zh.translation_policy import (
    DocumentTranslationPolicy,
    SourceSegment,
    ROLE_PRESERVE,
    ROLE_TRANSLATE,
)


def test_recommendation_recovers_linked_title_without_next_author_row():
    doc = fitz.open()
    page = doc.new_page(width=600, height=800)
    page.insert_text((400, 100), "You may also like", fontsize=10)
    page.insert_text((420, 130), "Integer Quantum Hall Effect", fontsize=7)
    page.insert_text((420, 138), "K. Ziegler", fontsize=7)
    page.insert_link(
        {
            "kind": fitz.LINK_URI,
            "from": fitz.Rect(420, 123, 515, 131),
            "uri": "https://example.com/paper",
        }
    )
    page = doc.reload_page(page)
    boxes = _recover_publisher_cover_lines([], page, 800, 600)
    titles = [box for box in boxes if box.class_name == "publisher_text"]
    assert len(titles) == 1
    x0, y0, x1, y1 = titles[0].bounds
    assert y0 > 800 - 138
    assert y1 >= 800 - 123
    doc.close()


def test_citation_metadata_is_preserved_and_recommendation_is_translated():
    policy = DocumentTranslationPolicy()
    plan = policy.plan_segment(
        SourceSegment(
            0,
            "To cite this article: A. Author et al 2011 J. Phys. 23 156002",
            region_kind="publisher_text",
        )
    )
    assert [part.role for part in plan.parts] == [ROLE_TRANSLATE, ROLE_PRESERVE]
    assert plan.parts[1].text == "A. Author et al 2011 J. Phys. 23 156002"
