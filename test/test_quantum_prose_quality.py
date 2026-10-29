from pdf2zh.translation_quality import (
    has_suspicious_physics_translation,
    has_suspicious_english_residue,
)
from pdf2zh.translation_quality import has_incomplete_scientific_caption
from pdf2zh.translation_quality import is_math_expression_only


def test_bare_panel_a_is_not_translated_as_an_indefinite_article():
    source = "Fig. 2. The results. a Qubit lifetime versus bias. b Comparison of the measurements."
    assert has_incomplete_scientific_caption(
        source, "图2。结果。一个Qubit寿命与偏置的关系。b 测量对比。"
    )
    assert not has_incomplete_scientific_caption(
        source, "图2。结果。a 量子比特寿命与偏置的关系。b 测量对比。"
    )


def test_table_measurement_values_are_not_prose():
    for value in ("7.83 GHz", "5.56 GHz", "8 µs", "23.4 GHz", "0.08 µm²", "285 nA/µm2"):
        assert is_math_expression_only(value), value
    for value in (
        "7.83 GHz resonator frequency",
        "5.56 GHz at zero flux",
        "8 seconds later",
    ):
        assert not is_math_expression_only(value), value


def test_electric_field_is_not_magnetic_field():
    source = "Simulated electric field strength. The field decays horizontally."
    assert has_suspicious_physics_translation(
        source, "模拟电场强度。该磁场沿水平方向衰减。"
    )
    assert not has_suspicious_physics_translation(
        source, "模拟电场强度。该电场沿水平方向衰减。"
    )


def test_coined_qubit_name_is_not_a_transition_between_energy_levels():
    source = "Relaxation times of fixed-frequency transmon qubits."
    assert has_suspicious_physics_translation(
        source, "固定频率跨能级量子比特的弛豫时间。"
    )
    assert has_suspicious_physics_translation(
        source.replace("transmon", "trans- mon"), "跨能级量子比特。"
    )
    assert not has_suspicious_physics_translation(
        source, "固定频率 transmon 量子比特的弛豫时间。"
    )
    assert not has_suspicious_physics_translation(
        "Transitions between energy levels.", "跨能级跃迁。"
    )


def test_named_fabricator_is_not_split_at_the_initial():
    from pdf2zh.source_segmentation import scientific_sentence_parts

    source = "The sample was fabricated by A. Smith as described in detail."
    assert scientific_sentence_parts(source) == [source]
    assert has_suspicious_physics_translation(source, "样品由 A. 制备。Smith。")
    assert not has_suspicious_physics_translation(
        source, "样品由 A. Smith 制备，详见说明。"
    )


def test_comparison_abbreviation_keeps_its_second_operand():
    from pdf2zh.source_segmentation import scientific_sentence_parts

    source = "b Comparison of optimized bias vs. zero applied field."
    assert scientific_sentence_parts(source) == [source]
    assert not is_math_expression_only("a qubit")


def test_panel_cases_are_not_connection_destinations():
    source = "In Figure S2 the gate is connected a via an RF line, or b via a DC line."
    assert has_suspicious_physics_translation(
        source, "栅极通过射频线连接到a，或者通过直流线连接到b。"
    )
    assert not has_suspicious_physics_translation(
        source, "情形a中栅极通过射频线连接；情形b中通过直流线连接。"
    )
    assert not has_suspicious_physics_translation(
        "The gate is connected to node a via an RF line.", "栅极经射频线连接到a。"
    )


def test_lifetime_fluctuations_remain_the_measured_object():
    source = "Fluctuations of {v0} times are shown. The fluctuations were 17% higher for the optimized E-field."
    assert has_suspicious_physics_translation(source, "优化后的电场波动高出17%。")
    assert not has_suspicious_physics_translation(
        source, "优化电场下，弛豫时间的波动高出17%。"
    )
    assert has_suspicious_physics_translation(
        source.replace("fluctuations were", "fluc- tuations were"),
        "优化后的电场波动高出17%。",
    )


def test_area_variable_cannot_become_the_name_of_the_plotted_function():
    source = "The values are plotted vs. the area {v0} of the global gate."
    assert has_suspicious_physics_translation(
        source, "这些值绘制为面积的函数{v0}全局栅极的面积。"
    )
    assert not has_suspicious_physics_translation(
        source, "这些值绘制为全局栅极面积{v0}的函数。"
    )


def test_peak_width_in_a_response_curve_does_not_become_a_field_criterion():
    source = "We include the width of a peak in {v0} vs. E-field as a criterion next to peak height."
    assert has_suspicious_physics_translation(
        source, "将峰宽纳入{v0}与将电场作为与峰高并列的判据。"
    )
    assert not has_suspicious_physics_translation(
        source, "除了峰高，还将{v0}随电场变化曲线中的峰宽作为判据。"
    )
    assert not has_suspicious_physics_translation(
        "We use the applied field as a criterion.", "将电场作为判据。"
    )


def test_named_effect_citation_cannot_move_to_the_following_claim():
    source = "We use the AC-stark shift{v0}. This helps as shown by Smith{v1}."
    assert has_suspicious_physics_translation(
        source, "利用 AC-Stark 频移。这有帮助，正如 Smith 所示{v0}{v1}。"
    )
    assert not has_suspicious_physics_translation(
        source, "利用 AC-Stark 频移{v0}。这有帮助，正如 Smith 所示{v1}。"
    )
    assert not has_suspicious_physics_translation(
        source, "利用交流斯塔克位移{v0}。这有帮助{v1}。"
    )


def test_multiplicative_enhancement_is_not_an_additive_increase():
    source = "The density is enhanced by a factor of {v0}."
    assert has_suspicious_physics_translation(source, "密度提高了{v0}倍。")
    assert not has_suspicious_physics_translation(source, "密度变为原来的{v0}倍。")
    assert has_suspicious_physics_translation(
        source.replace("{v0}", "2"), "密度增加了2倍。"
    )
    assert not has_suspicious_physics_translation(
        "The density increased by 200 percent.", "密度增加了2倍。"
    )


def test_threshold_binds_the_range_not_the_circuit_location():
    source = "They occur on the ground plane in the whole range of reasonable values {v0} V/m."
    assert has_suspicious_physics_translation(
        source, "在整个合理取值范围内出现在接地平面上{v0} V/m。"
    )
    assert has_suspicious_physics_translation(
        source, "在整个合理取值范围内，都会在接地平面上观测到{v0} V/m。"
    )
    assert not has_suspicious_physics_translation(
        source, "它们位于接地平面上，这在整个合理范围{v0} V/m内成立。"
    )


def test_measurement_shots_are_not_shooting_events():
    source = "The shot budget limits the quantum measurement precision."
    assert has_suspicious_physics_translation(
        source, "射击次数预算限制了量子测量精度。"
    )
    assert not has_suspicious_physics_translation(
        source, "测量次数预算限制了量子测量精度。"
    )
    assert not has_suspicious_physics_translation(
        "Shots were fired at the target.", "向目标进行了射击。"
    )


def test_aggregate_quality_factor_is_not_each_electrodes_quality_factor():
    source = "The resulting loaded quality factor for all four electrodes is {v0}."
    assert has_suspicious_physics_translation(
        source, "四个电极的所得加载品质因数均为{v0}。"
    )
    assert not has_suspicious_physics_translation(
        source, "四个电极共同作用得到的加载品质因数为{v0}。"
    )


def test_large_number_word_does_not_remain_untranslated():
    source = "The quality factor is {v0} million."
    assert has_suspicious_english_residue(source, "品质因数为{v0} million。")
    assert not has_suspicious_english_residue(source, "品质因数为{v0}百万。")
