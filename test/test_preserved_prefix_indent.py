from copy import copy

from pdf2zh.converter import _paragraph_first_line_origin
from test_italic_translation import paragraph


def parts():
    prefix = paragraph()
    prefix.x = prefix.x0 = 51.4771
    prefix.x1 = 83.4244
    prefix.y = prefix.y0 = 236.126
    prefix.y1 = 245.126
    prefix.size = 9
    prefix.layout_class = 2
    body = copy(prefix)
    body.x = 85.606
    body.x1 = 291.48
    body.y0 = 166.45
    return [prefix, body]


def test_first_line_reserves_full_width_of_unscaled_formula_prefix():
    ps = parts()
    origin = _paragraph_first_line_origin(1, ["{v0}", "Hz, 1H). Chiral analysis."], ps)
    assert origin == ps[1].x
    scaled_start = origin + (ps[1].x - origin) * 0.7
    assert scaled_start > ps[0].x1


def test_ordinary_indent_or_formula_on_previous_line_still_scales_normally():
    ps = parts()
    assert _paragraph_first_line_origin(1, ["Prose", "More prose"], ps) == ps[1].x0
    ps[0].y += 15
    assert _paragraph_first_line_origin(1, ["{v0}", "Next line"], ps) == ps[1].x0


def test_different_region_cannot_claim_the_first_line_indent():
    ps = parts()
    ps[0].layout_class = 0
    assert _paragraph_first_line_origin(1, ["{v0}", "Hz, 1H)."], ps) == ps[1].x0
