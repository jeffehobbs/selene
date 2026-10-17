"""MIDI keyboard capture: played notes -> a Tidal layer on the cycle grid."""

import asyncio
import random
import re
import time

import mido
from conftest import TEST_BOOT, needs_ghci

from selene import blocks, midi
from selene.app import Selene, parse_args
from selene.ghci import Ghci
from selene.midi import MidiInput, Note, NoteBuffer, transcribe

CPS = 0.5625


def N(cycle, note, velocity=100, seconds=0.3):
    return Note(cycle, note, velocity, seconds)


def code(notes, orbit="d1"):
    return transcribe(notes, "superpiano", CPS, orbit).code


def pattern(notes):
    """The n pattern as one mini-notation string, whether on one line or in cat."""
    out = code(notes)
    if out.startswith("d1 $ n (cat ["):
        cycles = re.findall(r'^\s+"([^"]*)",?$', out.split("])", 1)[0], re.M)
        return midi._join(cycles)
    return out.split('n "', 1)[1].split('"', 1)[0]


def test_note_names_follow_tidal():
    assert midi.note_name(60) == "c5" and midi.note_name(61) == "cs5"
    assert midi.note_name(48) == "c4" and midi.note_name(71) == "b5"


def test_straight_eighths_with_human_timing():
    random.seed(4)
    notes = [N(3 + i / 8 + random.uniform(-0.012, 0.012), 60 + 2 * i) for i in range(8)]
    assert pattern(notes) == "[c5 d5] [e5 fs5] [gs5 as5] [c6 d6]"


def test_rests_and_groups():
    notes = [N(10.0, 60), N(10.5, 64), N(10.625, 67)]
    assert pattern(notes) == "c5 ~ [e5 g5] ~"


def test_chord_is_one_step():
    notes = [N(3, 60), N(3.006, 67), N(3.012, 64), N(3.5, 62)]
    assert pattern(notes) == "[c5,e5,g5] ~ d5 ~"


def test_triplets_when_clearly_meant():
    assert pattern([N(3 + i / 12, 60) for i in range(12)]) == \
        "[c5 c5 c5] [c5 c5 c5] [c5 c5 c5] [c5 c5 c5]"
    assert pattern([N(3 + i / 3, 60) for i in range(3)]) == "c5 c5 c5"


def test_light_swing_stays_on_eighths():
    notes = [N(3 + i / 8 + (0.025 if i % 2 else 0), 60) for i in range(8)]
    assert pattern(notes) == "[c5 c5] [c5 c5] [c5 c5] [c5 c5]"


def test_two_cycles_alternate_and_early_downbeat_counts():
    notes = [N(4.985, 60), N(5.5, 64), N(6.25, 67)]  # first note 1/100 cycle early
    cap = transcribe(notes, "superpiano", CPS)
    assert cap.cycles == 2 and cap.start == 5
    assert pattern(notes) == "<[c5 ~ e5 ~] [~ g5 ~ ~]>"


def test_three_cycles_round_up_to_four():
    cap = transcribe([N(1, 60), N(2, 62), N(3, 64)], "superpiano", CPS)
    assert cap.cycles == 4 and "<c5 d5 e5 ~>" in cap.code


def test_long_phrases_keep_the_end():
    cap = transcribe([N(c, 60 + c) for c in range(1, 8)], "superpiano", CPS)
    assert cap.trimmed and cap.start == 4 and "<e5 f5 fs5 g5>" in cap.code


def test_gain_only_when_velocity_varies():
    assert "gain" not in code([N(3, 60, 90), N(3.5, 62, 100)])
    loud_soft = code([N(3, 60, 120), N(3.5, 62, 40)])
    assert '# gain "1.17 ~ 0.86 ~"' in loud_soft


def test_sustain_is_the_median_hold():
    out = code([N(3, 60, seconds=0.2), N(3.25, 62, seconds=0.5), N(3.5, 64, seconds=0.6)])
    assert out.endswith("# sustain 0.5")


def test_last_phrase_starts_after_a_gap():
    notes = [N(1, 50), N(1.5, 52), N(4, 60), N(4.25, 62)]
    assert [n.note for n in midi.last_phrase(notes)] == [60, 62]


def test_buffer_pairs_offs_and_pedal_holds_them():
    buf = NoteBuffer()
    buf.note_on(60, 100, 1.0)
    buf.note_on(60, 0, 1.4)  # velocity 0 is an off
    assert buf.notes[0].off == 1.4
    buf.sustain(True, 2.0)
    buf.note_on(62, 90, 2.0)
    buf.note_off(62, 2.2)
    assert buf.notes[1].off is None  # the pedal holds it
    buf.sustain(False, 3.0)
    assert buf.notes[1].off == 3.0


def test_keyboards_skip_virtual_buses():
    assert midi.keyboards(["IAC Driver Bus 1", "Midi Through Port-0", "Keystation 49"]) == \
        ["Keystation 49"]
    # A Launchkey's DAW port lists first on some systems; its keys are the MIDI port.
    assert midi.keyboards(["Launchkey MK3 49 LKMK3 DAW Out", "Launchkey MK3 49 LKMK3 MIDI Out"]) \
        == ["Launchkey MK3 49 LKMK3 MIDI Out"]


LONG = [N(1 + i / 8, 48 + i % 12, 50 + 2 * i) for i in range(32)]  # 4 cycles of 8ths


def test_long_captures_wrap_a_cycle_a_line():
    out = code(LONG, "d3")
    lines = out.splitlines()
    assert lines[0] == "d3 $ n (cat [" and lines[1] == '    "[c4 cs4] [d4 ds4] [e4 f4] [fs4 g4]",'
    assert '  # s "superpiano"' in lines and "  # gain (cat [" in lines
    assert max(len(line) for line in lines) <= midi.WIDTH
    assert blocks.split_statements(out) == [out]  # one statement: ctrl+j plays it whole
    assert blocks.orbits_used(out) == ["d3"]


def test_short_captures_stay_on_one_line():
    assert "\n" not in code([N(3, 60), N(3.5, 62)])


TRANSCRIPTIONS = [
    LONG,
    [N(10.0, 60), N(10.5, 64), N(10.625, 67), N(11.25, 69)],
    [N(3, 60), N(3.006, 67), N(3.012, 64), N(3.5, 62, 40)],
    [N(3 + i / 12, 60 + i) for i in range(12)],
    [N(1, 48), N(2, 50), N(3, 52)],
]


@needs_ghci
async def test_transcriptions_compile_and_play_the_notes(fake_dirt):
    ghci = Ghci(boot=TEST_BOOT)
    assert (await ghci.start()).ok
    try:
        for notes in TRANSCRIPTIONS:
            result = await ghci.eval(code(notes))
            assert result.ok, (code(notes), result.error_text)
        fake_dirt.clear()
        assert (await ghci.eval(code(TRANSCRIPTIONS[2]))).ok
        await asyncio.sleep(2.5)
        heard = {round(e["n"]) for e in fake_dirt.events() if "n" in e}
        assert {0, 2, 4, 7} <= heard  # c5 d5 e5 g5
    finally:
        await ghci.hush()
        await ghci.stop()


def app_with_clock():
    app = Selene(parse_args(["--ghci", "/nonexistent/ghci"]))
    now = time.time()
    app.clock.observe(now, now, 100.0, CPS)  # cycle 100 plays now
    return app, now


def log_text(app):
    return "\n".join(line.text for line in app.query_one("#log").lines)


async def test_capture_writes_a_layer_into_the_editor():
    app, t0 = app_with_clock()
    async with app.run_test() as pilot:
        app.query_one("#code").text = 'd1 $ s "bd*4"\n'
        app.playing_code = 'd1 $ s "bd*4"'
        at = lambda cycle: t0 + (cycle - 100) / CPS  # noqa: E731
        for cycle, note in ((101, 60), (101.5, 64), (101.625, 67)):
            app.midi_message(mido.Message("note_on", note=note, velocity=100), at(cycle))
            app.midi_message(mido.Message("note_off", note=note), at(cycle) + 0.3)
        await pilot.press(*"/capture", "enter")
        await pilot.pause()
        editor = app.query_one("#code")
        assert editor.text == ('d1 $ s "bd*4"\n\n'
                               'd2 $ n "c5 ~ [e5 g5] ~" # s "supersquare" # sustain 0.3\n')
        assert editor.cursor_location == (2, 0)
        assert app.playing_code == 'd1 $ s "bd*4"'  # not played until the player plays it
        assert "captured 3 notes over 1 cycle → d2" in log_text(app)
        assert not app.played.notes  # the next capture starts fresh


async def test_capture_needs_a_beat():
    app = Selene(parse_args(["--ghci", "/nonexistent/ghci"]))
    async with app.run_test() as pilot:
        app.midi_message(mido.Message("note_on", note=60, velocity=100), time.time())
        await pilot.press(*"/capture", "enter")
        await pilot.pause()
        assert "no beat to put your notes on" in log_text(app)
        assert app.query_one("#code").text == ""


async def test_keyboard_connects_and_ctrl_y_captures(monkeypatch):
    ports = ["IAC Driver Bus 1", "Keystation 49"]
    monkeypatch.setattr(MidiInput, "available", staticmethod(lambda: list(ports)))
    monkeypatch.setattr(MidiInput, "open", lambda self, name: setattr(self, "name", name))
    app, t0 = app_with_clock()
    async with app.run_test() as pilot:
        assert app.midi.name == "Keystation 49"
        assert "midi: Keystation 49" in log_text(app)
        app.midi_message(mido.Message("note_on", note=72, velocity=90), t0 + 1 / CPS)
        await pilot.press("ctrl+y")
        await pilot.pause()
        assert app.query_one("#code").text.startswith('d1 $ n "c6"')
        ports.remove("Keystation 49")
        app._poll_midi()
        assert app.midi.name is None and "went away" in log_text(app)
