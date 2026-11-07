"""Microtonal mode (/tuning 19|24|31): opt-in per session, and while it's
off, nothing anyone else sees changes."""

import asyncio

from conftest import TEST_BOOT, needs_ghci

from selene import blocks, tuning
from selene.app import Selene, parse_args
from selene.ghci import Ghci
from selene.lanes import LaneModel
from selene.llm import build_system_prompt, evolve_message


# ── off means off ─────────────────────────────────────────────────────────

def test_off_leaves_the_prompt_exactly_as_it_was():
    plain = build_system_prompt(["bd"], ["superpiano"], "- prefs", "sounds")
    assert build_system_prompt(["bd"], ["superpiano"], "- prefs", "sounds", "") == plain
    for word in ("edo", "EDO", "escale", "Microtonal"):
        assert word not in plain
    assert "edo" not in evolve_message('d1 $ n "0 3" # s "superpiano"', "d1", "evolve")


def test_a_default_session_is_off():
    app = Selene(parse_args([]))
    assert app.tuning == tuning.OFF
    assert "Microtonal" not in app.system_prompt
    assert not app.tuning_defined


def test_lanes_draw_ordinary_notes_as_before():
    lanes = LaneModel(synths={"superpiano"})
    assert lanes._look({"s": "superpiano", "note": 2.0})[::3] == ("D", False)
    # A fractional note without the edo tag (a player's own `note 0.5`) isn't marked.
    assert lanes._look({"s": "superpiano", "note": 0.5})[3] is False


# ── the pieces ────────────────────────────────────────────────────────────

def test_parse():
    assert tuning.parse("31") == 31
    assert tuning.parse("19-EDO") == 19
    assert tuning.parse("24edo") == 24
    assert tuning.parse("off") == tuning.OFF
    assert tuning.parse("12") == tuning.OFF
    assert tuning.parse("17") is None
    assert tuning.parse("lots") is None


def test_prompt_section_names_only_that_edos_scales():
    text = tuning.prompt_section(31)
    assert "31-EDO" in text and '"mohajira"' in text and "edo 31" in text
    assert '"rast"' not in text  # a 24-EDO scale


def test_every_scale_fits_its_octave():
    for k, scales in tuning.SCALES.items():
        for name, steps in scales.items():
            assert steps[0] == 0 and steps == sorted(set(steps)) and steps[-1] < k, (k, name)


def test_unknown_scale_names_are_caught():
    assert tuning.unknown_scales('edo 31 (escale 31 "major" "0 .. 7")') == set()
    assert tuning.unknown_scales('edo 31 (escale 31 "<major mohajira>" "0 2")') == set()
    assert tuning.unknown_scales('edo 31 (escale 31 "rast" "0 2")') == {'31 "rast"'}
    assert tuning.unknown_scales('edo 19 (escale 19 "<minor dorianish>" "0")') == {'19 "dorianish"'}
    assert tuning.unknown_scales('n (scale "minor" "0 .. 7")') == set()


def test_flow_keeps_an_edo_scale_as_the_key():
    assert blocks.scales('edo 31 (escale 31 "mohajira" "0 2")') == {"31-EDO mohajira"}
    assert blocks.scales('n (scale "minor" "0")') == {"minor"}


def test_file_header_round_trip():
    text = 'd1 $ edo 31 "0 5"'
    edo, body = tuning.split_header(tuning.header(31) + "\n" + text)
    assert (edo, body) == (31, text)
    assert tuning.split_header(text) == (None, text)
    assert tuning.split_header("-- selene: tuning 17\nd1 $ s \"bd\"")[0] is None
    # Code says what it's in even without a header (a session log, a file saved after /tuning off).
    assert tuning.edo_of('d1 $ s "bd"\nd2 $ edo 19 (escale 19 "minor" "0")') == 19
    assert tuning.edo_of('d1 $ n "0 3" # s "superpiano"') is None
    assert tuning.edo_of('d1 $ edo 17 "0"') is None


def test_lanes_mark_notes_between_keys():
    lanes = LaneModel(synths={"superpiano"})
    def look(note, k):
        return lanes._look({"s": "superpiano", "note": note, "edo": float(k)})[::3]

    assert look(0.5, 24) == ("C", True)  # a quarter-tone above C
    assert look(12 * 18 / 31, 31) == ("G", False)  # 31-EDO's fifth: 5 cents flat, on the key
    assert look(12 * 4 / 31, 31) == ("D", True)  # step 4: 155 cents, between keys


# ── in GHCi ───────────────────────────────────────────────────────────────

@needs_ghci
async def test_helpers_play_edo_steps(fake_dirt):
    ghci = Ghci(boot=TEST_BOOT)
    assert (await ghci.start()).ok
    defined = await ghci.define(tuning.helpers())
    assert defined.ok, defined.error_text
    steps = await ghci.eval(
        'print $ map (unNote . value) $ queryArc (escale 31 "major" "0 1 7 -1") (Arc 0 1)')
    assert steps.ok, steps.error_text
    assert "[0.0,5.0,31.0,-3.0]" in steps.error_text
    fake_dirt.clear()
    played = await ghci.eval('d1 $ edo 24 "1 2" # s "superpiano"')
    assert played.ok, played.error_text
    await asyncio.sleep(1.5)
    events = [e for e in fake_dirt.events() if e.get("s") == "superpiano"]
    assert events and {round(e["note"], 3) for e in events} <= {0.5, 1.0}
    assert all(e["edo"] == 24.0 for e in events)
    await ghci.hush()
    await ghci.stop()


# ── in the app ────────────────────────────────────────────────────────────

class Model:
    """Replies in turn; records every request's messages."""
    model = "fake"

    def __init__(self, *replies):
        self.replies = list(replies)
        self.requests = []

    async def resolve_model(self):
        return "fake"

    async def chat(self, messages, thoughts=None):
        self.requests.append([dict(m) for m in messages])
        yield f"```haskell\n{self.replies.pop(0)}\n```"

    async def close(self):
        pass


async def wait_for(cond, timeout, step=0.05):
    for _ in range(int(timeout / step)):
        if cond():
            return True
        await asyncio.sleep(step)
    return False


@needs_ghci
async def test_tuning_session(fake_dirt, tmp_path):
    app = Selene(parse_args(["--boot", str(TEST_BOOT), "--dir", str(tmp_path)]))
    model = app.ollama = Model(
        'd1 $ edo 31 (escale 31 "rast" "0 2") # s "superpiano"',  # not a 31-EDO scale
        'd1 $ edo 31 (escale 31 "mohajira" "0 2") # s "superpiano"',
        'd1 $ n "0 4" # s "superpiano"')
    async with app.run_test(size=(120, 34)):
        assert await wait_for(lambda: app.state["ghci"] == "ready", 60)
        assert "tuning" not in str(app.query_one("#status").render())

        app.command("tuning 31")
        assert await wait_for(lambda: app.tuning == 31, 10)
        assert app.tuning_defined
        assert "31-EDO" in str(app.query_one("#status").render())

        app.generate("a neutral-third melody")
        assert await wait_for(lambda: "mohajira" in app.playing_code, 15)
        first = model.requests[0]
        assert "this session is in 31-EDO" in first[0]["content"]
        assert "don't exist" in model.requests[1][-1]["content"]  # the bad name went back

        path = tmp_path / "neutral.tidal"
        app._write(path)
        assert path.read_text().startswith("-- selene: tuning 31\n")

        app.command("tuning off")
        assert await wait_for(lambda: app.tuning == tuning.OFF, 10)
        assert "Microtonal mode:" not in app.system_prompt
        app.generate("back to normal")
        assert await wait_for(lambda: app.playing_code.startswith('d1 $ n "0 4"'), 15)
        assert model.requests[2][-1]["content"].startswith("Microtonal mode is now off")
        assert "Microtonal" not in model.requests[2][0]["content"]

        # Opening the saved file brings the mode back; the header stays out of the editor.
        app._load(path)
        assert await wait_for(lambda: app.tuning == 31, 10)
        assert not app.query_one("#code").text.startswith("--")
        await app.action_quit()
