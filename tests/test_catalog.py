from selene import catalog


def test_synth_names_include_core_synths_and_skip_internals(tmp_path):
    synths = tmp_path / "SuperDirt/synths"
    library = tmp_path / "SuperDirt/library"
    synths.mkdir(parents=True)
    library.mkdir()
    (synths / "default-synths.scd").write_text(
        'SynthDef(\\gabor, {}).add;\nSynthDef(\\cyclo, {}).add;\nSynthDef("dirac", {}).add;\n'
        'SynthDef(\\debug, {}).add;\nSynthDef(\\in1, {}).add;\nSynthDef(\\dirt_from, {}).add;')
    (synths / "tutorial-synths.scd").write_text("SynthDef(\\tutorial3, {}).add;")
    (synths / "core-synths.scd").write_text("SynthDef(\\dirt_lpf, {}).add;")
    (library / "default-synths-extra.scd").write_text(
        "SynthDef(\\superpiano, {}).add;\nSynthDef(\\soskick, {}).add;")
    assert catalog.synth_names(tmp_path) == ["cyclo", "dirac", "gabor", "soskick", "superpiano"]


def test_installed_superdirt_lists_gabor():
    names = catalog.synth_names()
    if names == catalog.FALLBACK_SYNTHS:
        import pytest
        pytest.skip("SuperDirt not installed here")
    assert {"gabor", "cyclo", "imp", "psin", "pmsin", "dirac", "superpiano"} <= set(names)
    assert not [n for n in names if n.startswith("dirt_") or n in ("debug", "in", "in1", "inr")]
