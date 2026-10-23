from test_codex_formula_context import translator_stub


def long_sentence():
    return (
        "We initialized the interacting photons in a superposition of local states, "
        "then varied the synthetic magnetic field and the coupling parameters to "
        "compare the measured populations and persistent currents with the "
        "calculated dynamics, while the prepared local states were not eigenstates "
        "of the Hamiltonian."
    )


def test_batch_size_target_cannot_sever_normal_scientific_sentence():
    translator = translator_stub()
    source = long_sentence()
    assert translator.MAX_ITEM_CHARS < len(source) < translator.MAX_BATCH_CHARS
    assert translator._split_long_text(source) == [source]
    assert translator._split_formula_context_text(source) == [source]
    assert translator._split_long_text(source + " Next we measure the currents.") == [
        source + " ",
        "Next we measure the currents.",
    ]


def test_extreme_sentence_still_has_bounded_requests_and_exact_reassembly():
    translator = translator_stub()
    source = (
        "The measured population "
        + "is compared with the calibration " * 220
        + "and {v0}."
    )
    parts = translator._split_long_text(source)
    assert len(parts) > 1
    assert "".join(parts) == source
    assert max(map(len, parts)) <= translator.MAX_BATCH_CHARS
    assert sum("{v0}" in part for part in parts) == 1


def test_only_old_potentially_severed_sentence_caches_get_new_key():
    translator = translator_stub()
    long = long_sentence()
    short = "The states are not eigenstates of the Hamiltonian."
    assert translator._prose_cache_key(short) == short
    assert translator._prose_cache_key(long) != long
    assert "sentence_chunking" in translator._formula_context_cache_key(long, {})
    assert "sentence_chunking" not in translator._formula_context_cache_key(short, {})


def test_legacy_broken_long_sentence_is_retranslated_as_a_complete_unit():
    translator = translator_stub()
    source = long_sentence()
    translator.cache.set(source, "这些局域态并不是本征态。哈密顿量。")
    good = "我们初始化了相互作用光子的局域态叠加，随后改变合成磁场和耦合参数，将测得的布居与持续电流同计算的动力学比较，而制备的局域态并非哈密顿量的本征态。"
    calls = []

    def backend(texts, **kwargs):
        calls.extend(texts)
        return [good for text in texts]

    translator._run_batch_translation = backend
    assert translator.translate_batch([source]) == [good]
    assert calls == [source]
    assert translator.cache.get(translator._prose_cache_key(source)) == good
