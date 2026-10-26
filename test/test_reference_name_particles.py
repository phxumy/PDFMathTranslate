import pytest

from pdf2zh.translation_policy import looks_like_reference_title_prefix


@pytest.mark.parametrize("surname", ["deBeaumont", "vanDijk", "d’Arcy"])
def test_attached_lowercase_surname_particle_is_preserved_as_an_author(surname):
    assert looks_like_reference_title_prefix(
        f"(16) McDonald, E. R.; {surname}, R. S.; Sellers, W. R. "
    )


def test_lowercase_prose_is_not_an_author_particle():
    assert not looks_like_reference_title_prefix("(16) Smith, A.; demonstrated, B. ")
