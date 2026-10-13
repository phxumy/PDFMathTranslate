"""Reference-title regressions from SHARPNESS's actual rejected candidates."""

from __future__ import annotations

import pytest

from pdf2zh.translation_policy import ExactReplacement
from pdf2zh.translator import CodexTranslator

# Keep the original title, proposed translation, and bibliographic tail together:
# accepting a title must leave every author/venue/page/link byte untouched.
CASES = (
    (
        "H. Attouch, J. Bolte, P. Redont, and A. Soubeyran",
        "Proximal alternating minimiza- tion and projection methods for nonconvex problems: An approach based on the Kurdyka- {v42}Lojasiewicz inequality",
        "非凸问题的近端交替最小化与投影方法：一种基于Kurdyka- {v42}Lojasiewicz不等式的方法",
        "Math. Oper. Res., 35 (2010), pp. 438--457, [[PDF2ZH_FLOW_10]].",
    ),
    (
        "R. Baraniuk, M. Davenport, R. DeVore, and M. Wakin",
        "A simple proof of the restricted isometry property for random matrices",
        "随机矩阵的受限等距性质的一个简单证明",
        "Constr. Approx., 28 (2008), pp. 253--263, [[PDF2ZH_FLOW_11]].",
    ),
    (
        "A. Belloni, V. Chernozhukov, and L. Wang",
        "Square-root lasso: Pivotal recovery of sparse signals via conic programming",
        "平方根 lasso：通过锥规划对稀疏信号的枢轴恢复",
        "Biometrika, 98 (2011), pp. 791--806, [[PDF2ZH_FLOW_13]].",
    ),
    (
        "S. Bubeck, et al.",
        "Convex optimization: Algorithms and complexity",
        "凸优化: 算法与复杂性",
        "Found. Trends Mach. Learn., 8 (2015), pp. 231--357, [[PDF2ZH_FLOW_15]].",
    ),
    (
        "E. J. Candès and X. Li",
        "Solving quadratic equations via phaselift when there are about as many equations as unknowns",
        "当方程数量约等于未知数数量时通过phaselift求解二次方程",
        "Found. Comput. Math., 14 (2014), pp. 1017--1026, [[PDF2ZH_FLOW_17]].",
    ),
    (
        "E. J. Candes, X. Li, and M. Soltanolkotabi",
        "Phase retrieval from coded diffraction patterns",
        "从编码衍射图样中进行相位恢复",
        "Appl. Comput. Harmon. Anal., 39 (2015), pp. 277--299, [[PDF2ZH_FLOW_18]]. 2014.09.004.",
    ),
    (
        "V. Chandrasekaran, B. Recht, P. A. Parrilo, and A. S. Willsky",
        "The convex geometry of linear inverse problems",
        "线性逆问题的凸几何",
        "Found. Comput. Math., 12 (2012), pp. 805--849, [[PDF2ZH_FLOW_24]].",
    ),
    (
        "V. Charisopoulos, Y. Chen, D. Davis, M. Díaz, L. Ding, and D. Drusvyatskiy",
        "Low-{v7} matrix recovery with composite optimization: Good conditioning and rapid convergence",
        "低-{v7}矩阵恢复与复合优化：良好条件性与快速收敛",
        "Found. Comput. Math., 21 (2021), pp. 1505--1593, [[PDF2ZH_FLOW_25]].",
    ),
    (
        "V. Charisopoulos, D. Davis, M. Díaz, and D. Drusvyatskiy",
        "Composite optimiza- tion for robust {v9} one bilinear sensing",
        "用于鲁棒 {v9} 单一双线性传感的复合优化",
        "Inform. Inference, 10 (2021), pp. 333--396, [[PDF2ZH_FLOW_26]].",
    ),
    (
        "D. Davis, D. Drusvyatskiy, K. J. MacPhee, and C. Paquette",
        "Subgradient methods for sharp weakly convex functions",
        "尖锐弱凸函数的次梯度方法",
        "J. Optim. Theory Appl., 179 (2018), pp. 962--982, [[PDF2ZH_FLOW_28]].",
    ),
    (
        "L. Ding and M. Udell",
        "A strict complementarity approach to error bound and sen-sitivity of solution of conic programs",
        "一种用于锥规划解的误差界与敏感性的严格互补方法",
        "Optim. Lett., 17 (2023), pp. 1551--1574., [[PDF2ZH_FLOW_29]].",
    ),
)


def translator_stub():
    translator = CodexTranslator.__new__(CodexTranslator)
    translator.lang_out = "zh-cn"
    return translator


@pytest.mark.parametrize("authors,title,target,tail", CASES)
def test_rejected_math_candidates_have_safe_exact_title_boundaries(
    authors, title, target, tail
):
    entry = f"[1] {authors}, {title}, {tail} "
    result, accepted = translator_stub()._apply_reference_title_replacements(
        entry, [ExactReplacement(title, target)]
    )
    assert accepted
    assert result == entry.replace(
        title, translator_stub()._normalize_translation_output(target)
    )


@pytest.mark.parametrize(
    "venue",
    [
        "Constr. Approx.",
        "Found. Trends Mach. Learn.",
        "Found. Comput. Math.",
        "Appl. Comput. Harmon. Anal.",
        "Inform. Inference",
        "Biometrika",
        "J. Optim. Theory Appl.",
        "Optim. Lett.",
    ],
)
@pytest.mark.parametrize(
    "leftover",
    [
        "Quantum Methods. ",
        "Optimization Methods, ",
        "Recovery of signals. ",
        "A Complete Framework: ",
    ],
)
def test_title_prose_before_math_venue_is_never_metadata(venue, leftover):
    tail = f"{leftover}{venue}, 28 (2008), pp. 253--263."
    assert not CodexTranslator._reference_metadata_tail_is_safe(tail)
    entry = f"[1] A. Smith, First title sentence. {tail}"
    assert not CodexTranslator._reference_title_boundary_is_safe(
        entry, "First title sentence"
    )


@pytest.mark.parametrize(
    "source,target",
    [
        (
            "An approach based on the Kurdyka- {v42}Lojasiewicz inequality",
            "基于Kurdyka- Lojasiewicz不等式的{v42}方法",
        ),
        (
            "An approach based on the Kurdyka- {v42}Lojasiewicz inequality",
            "基于Kurdyka- {v42}Lojasiewicz inequality的方法",
        ),
        (
            "Kurdyka-Lojasiewicz inequality for convergence and optimization",
            "用于 convergence and optimization 的Kurdyka-Lojasiewicz不等式",
        ),
        (
            "Strong-Convex inequality for optimization",
            "用于优化的Strong-Convex inequality",
        ),
        (
            "Solving equations via phaselift when data is noisy",
            "数据有噪声时通过phaselift solving equations",
        ),
        (
            "Square-root lasso for sparse recovery",
            "用于 sparse recovery 的平方根 lasso",
        ),
        ("Low-rank matrix recovery", "低-rank矩阵恢复"),
        (
            "Composite optimization for robust rank-one bilinear sensing",
            "用于鲁棒 rank 单一双线性传感的复合优化",
        ),
        ("Matrix recovery via convex optimization", "通过凸 optimization 恢复矩阵"),
        ("Sparse recovery via programming", "通过programming稀疏恢复"),
    ],
)
def test_names_never_exempt_moved_accents_or_ordinary_english(source, target):
    assert CodexTranslator._reference_title_has_unsafe_residue(source, target)


@pytest.mark.parametrize("name", ["lasso", "LASSO", "phaselift", "PhaseLift"])
def test_only_the_same_conventional_algorithm_name_may_remain(name):
    source = f"Signal recovery via {name}"
    assert not CodexTranslator._reference_title_has_unsafe_residue(
        source, f"通过{name}恢复信号"
    )
    assert not CodexTranslator._reference_preserves_named_optimization_method(
        "Signal recovery via another method", f"通过{name}恢复信号"
    )


@pytest.mark.parametrize("name", ["Strong-Convex", "Sharp-Error", "Low-Rank"])
def test_inequality_modifier_is_not_a_personal_name(name):
    assert not CodexTranslator._reference_preserves_single_eponym_name(
        f"On the {name} inequality", f"论{name}不等式"
    )


@pytest.mark.parametrize("space", ["", " "])
def test_accent_stays_with_its_surname_when_wrap_whitespace_changes(space):
    assert not CodexTranslator._reference_title_has_unsafe_residue(
        "An approach based on the Kurdyka- {v42}Lojasiewicz inequality",
        f"基于Kurdyka-{space}{{v42}}Lojasiewicz不等式的方法",
    )


LATER_CASES = (
    (
        "H. Xu, X.-K. Song, D. Wang, and L. Ye",
        "Quantum sensing of control errors in three-level systems by coherent control techniques",
        "利用相干控制技术对三能级系统中的控制误差进行量子传感",
        "Sci. China: Phys., Mech. Astron. 66, 240314 (2023).",
    ),
    (
        "M. C. Ferris",
        "Weak Sharp Minima and Exact Penalty Functions",
        "弱尖锐极小值与精确罚函数",
        "Technical report, Univer-sity of Wisconsin-Madison Department of Computer Sciences, 1988, [[PDF2ZH_FLOW_35]].",
    ),
    (
        "A. S. Nemirovski and Y. E. Nesterov",
        "Optimal methods of smooth convex minimization",
        "光滑凸最小化的最优方法",
        "Zhurnal Vychislitel'noi Matematiki i Matematicheskoi Fiziki, 25 (1985), pp. 356--369, [[PDF2ZH_FLOW_39]].",
    ),
    (
        "Y. Nesterov and A. Nemirovski",
        "On first-order algorithms for {v5}/nuclear norm minimiza- tion",
        "关于{v5}/核范数最小化的一阶算法",
        "Acta Numer., 22 (2013), pp. 509--575, [[PDF2ZH_FLOW_40]].",
    ),
    (
        "S. Portnoy and R. Koenker",
        "The gaussian hare and the Laplacian tortoise: Computability of squared-error versus absolute-error estimators",
        "高斯野兔与拉普拉斯乌龟：平方误差与绝对误差估计量的可计算性",
        "Statist. Sci., 12 (1997), pp. 279--300, [[PDF2ZH_FLOW_43]].",
    ),
    (
        "V. Roulet, N. Boumal, and A. d'Aspremont",
        "Computational complexity versus statistical performance on sparse recovery problems",
        "稀疏恢复问题的计算复杂度与统计性能",
        "Inform. Inference, (2020).",
    ),
    (
        "R. Vershynin",
        "Introduction to the Non-asymptotic Analysis of Random Matrices",
        "随机矩阵非渐近分析导论",
        "Cambridge University Press, Cambridge, 2012, pp. 210--268.",
    ),
    (
        "T. Yang and Q. Lin",
        "RSG: Beating subgradient method without smoothness and strong convexity",
        "RSG：无需光滑性和强凸性即可超越次梯度法",
        "J. Mach. Learn. Res., 19 (2018), pp. 236--268, [[PDF2ZH_FLOW_52]].",
    ),
)


@pytest.mark.parametrize("authors,title,target,tail", LATER_CASES)
def test_later_math_publication_formats_keep_all_metadata(authors, title, target, tail):
    entry = f"[30] {authors}, {title}, {tail} "
    translator = translator_stub()
    result, accepted = translator._apply_reference_title_replacements(
        entry, [ExactReplacement(title, target)]
    )
    assert accepted
    assert result == entry.replace(
        title, translator._normalize_translation_output(target)
    )


COLLECTION_CASES = (
    (
        "A. J. Hoffman",
        "On approximate solutions of systems of linear inequalities",
        "in Selected Papers Of Alan J Hoffman: With Commentary, World Scientific, Singapore, 2003, pp. 174--176.",
    ),
    (
        "K. Ball, E. A. Carlen, and E. H. Lieb",
        "Sharp uniform convexity and smoothness inequalities for trace norms",
        "Inequalities: Selecta of Elliott H. Lieb, (2002), pp. 171--190.",
    ),
)


@pytest.mark.parametrize("authors,title,tail", COLLECTION_CASES)
def test_anthology_requests_only_the_cited_work_title(authors, title, tail):
    entry = f"[3] {authors}, {title}, {tail} "
    translator = translator_stub()
    requested = []

    def exact_request(titles):
        requested.extend(titles)
        return ["完整的作品题名"] * len(titles)

    translator._run_exact_reference_title_batch = exact_request
    translator._run_discovered_reference_title_batch = lambda entries: pytest.fail(
        "complete anthology metadata must not ask the model to discover boundaries"
    )
    replacements = translator._run_reference_title_batch([entry])[0]
    assert requested == [title]
    result, accepted = translator._apply_reference_title_replacements(
        entry, replacements
    )
    assert accepted
    assert result == entry.replace(title, "完整的作品题名")


@pytest.mark.parametrize(
    "tail",
    [
        "Technical report on new optimization methods, 1988.",
        "Technical report, University of Example, 1988, remaining title prose.",
        "Inform. Inference, (2020) with new methods.",
        "A Complete Framework: Inform. Inference, (2020).",
        "New title prose, Sci. China: Phys., Mech. Astron. 66, 240314 (2023).",
        "Cambridge University Press methods and applications, 2012.",
        "Cambridge University Press, Cambridge, 2012, new results.",
        "Selected Papers of efficient methods, (2002), pp. 171--190.",
        "Inequalities: Selecta of Elliott H. Lieb, (2002), pp. 171--190. New methods.",
    ],
)
def test_new_metadata_formats_require_complete_catalogue_fields(tail):
    assert not CodexTranslator._reference_complete_metadata_tail_is_safe(tail)
    assert not CodexTranslator._reference_metadata_tail_is_safe(tail)


@pytest.mark.parametrize("authors,title,tail", COLLECTION_CASES)
def test_anthology_container_cannot_be_translated_as_an_additional_title(
    authors, title, tail
):
    entry = f"[3] {authors}, {title}, {tail} "
    container = tail.removeprefix("in ").split(",")[0]
    result, accepted = translator_stub()._apply_reference_title_replacements(
        entry,
        [ExactReplacement(title, "作品题名"), ExactReplacement(container, "容器题名")],
    )
    assert not accepted
    assert result == entry


def test_proceedings_volume_publisher_and_location_are_outside_exact_title():
    title = "Factor group-sparse regularization for efficient low-rank matrix recovery"
    tail = (
        "in Advances in Neural Information Processing Systems 32, "
        "Curran Associates, Red Hook, NY, 2019."
    )
    entry = f"[29] J. Fan, L. Ding, Y. Chen, and M. Udell, {title}, {tail}"
    translator = translator_stub()
    requested = []

    def exact_request(titles):
        requested.extend(titles)
        return ["用于高效低秩矩阵恢复的因子组稀疏正则化"]

    translator._run_exact_reference_title_batch = exact_request
    translator._run_discovered_reference_title_batch = lambda entries: pytest.fail(
        "complete proceedings catalogue must yield the exact work title"
    )
    replacements = translator._run_reference_title_batch([entry])[0]
    assert requested == [title]
    result, accepted = translator._apply_reference_title_replacements(
        entry, replacements
    )
    assert accepted
    assert result == entry.replace(title, "用于高效低秩矩阵恢复的因子组稀疏正则化")
    assert result.endswith(tail)

    # Actual erroneous response from the first final candidate: publisher and
    # location text survived, but the model translated the proceedings name.
    consumed_metadata = f"{title}, {tail.rsplit(', 2019.', 1)[0]}"
    result, accepted = translator._apply_reference_title_replacements(
        entry,
        [
            ExactReplacement(
                consumed_metadata,
                "用于高效低秩矩阵恢复的因子组稀疏正则化，载于《神经信息处理系统进展》"
                "第32卷，Curran Associates，Red Hook，NY",
            )
        ],
    )
    assert not accepted
    assert result == entry


@pytest.mark.parametrize(
    "tail",
    [
        "in a scalable framework, 2019.",
        "in Advances in theoretical methods 32, Curran Associates, Red Hook, NY, 2019.",
        "in Advances in Neural Information Processing Systems 32, new algorithms, 2019.",
        "in Advances in Neural Information Processing Systems 32, Curran Associates, Red Hook, NY, 2019, new applications.",
    ],
)
def test_title_prose_does_not_become_proceedings_metadata(tail):
    assert not CodexTranslator._reference_complete_metadata_tail_is_safe(tail)
    entry = f"[1] A. Smith, Learning methods {tail}"
    assert CodexTranslator._structured_reference_title_spans(entry) != (
        "Learning methods",
    )


def test_proceedings_catalogue_never_truncates_in_a_title_phrase():
    title = "Learning methods in a scalable framework"
    tail = (
        "in Advances in Neural Information Processing Systems 32, "
        "Curran Associates, Red Hook, NY, 2019."
    )
    entry = f"[1] A. Smith, {title}, {tail}"
    assert CodexTranslator._structured_reference_title_spans(entry) == (title,)
    result, accepted = translator_stub()._apply_reference_title_replacements(
        entry, [ExactReplacement("Learning methods", "学习方法")]
    )
    assert not accepted
    assert result == entry


def test_comma_author_preference_keeps_book_series_outside_the_work_title():
    title = "Convex Analysis"
    tail = (
        "Princeton Landmarks in Mathematics and Physics 11, "
        "Princeton University Press, Princeton, NJ, 1997."
    )
    entry = f"[46] R. T. Rockafellar, {title}, {tail} "
    assert CodexTranslator._structured_reference_title_spans(entry) == (title,)
    result, accepted = translator_stub()._apply_reference_title_replacements(
        entry, [ExactReplacement(title, "凸分析")]
    )
    assert accepted
    assert result == entry.replace(title, "凸分析")


def test_book_series_rule_cannot_skip_an_ordinary_second_title_clause():
    entry = (
        "[46] A. Smith, Convex Analysis, New Applications in Optimization 11, "
        "Princeton University Press, Princeton, NJ, 1997."
    )
    assert not CodexTranslator._reference_complete_metadata_tail_is_safe(
        entry.split("Convex Analysis, ")[1]
    )
    result, accepted = translator_stub()._apply_reference_title_replacements(
        entry, [ExactReplacement("Convex Analysis", "凸分析")]
    )
    assert not accepted
    assert result == entry
