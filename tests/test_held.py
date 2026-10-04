"""Layers the new code doesn't mention keep playing (DJ-style) and stay controllable."""

import asyncio

from conftest import TEST_BOOT, needs_ghci

from selene.app import Selene, parse_args
from selene.llm import user_message

A = 'setcps 2\nd1 $ s "bd*4"\nd2 $ s "hh*4"\nd3 $ s "cp*4"\nd4 $ s "arpy*4"'
B = 'setcps 2\nd1 $ s "bd*2"\nd2 $ s "hh*8"'
SOUNDS = ("bd", "hh", "cp", "arpy", "tabla")


async def wait_for(cond, timeout, step=0.05):
    for _ in range(int(timeout / step)):
        if cond():
            return True
        await asyncio.sleep(step)
    return False


async def heard(fake_dirt) -> set[str]:
    await asyncio.sleep(0.4)
    fake_dirt.clear()
    await asyncio.sleep(1.0)
    return {e["s"] for e in fake_dirt.events() if e.get("gain", 1) > 1e-3} & set(SOUNDS)


def test_model_is_told_about_held_layers():
    msg = user_message("add a bassline", 'd1 $ s "bd"', held='d3 $ s "cp*4"')
    assert "held layers" in msg and 'd3 $ s "cp*4"' in msg and 'd1 $ s "bd"' in msg
    assert "held" not in user_message("add a bassline", 'd1 $ s "bd"')


@needs_ghci
async def test_held_layers(tmp_path, fake_dirt):
    (tmp_path / "a.tidal").write_text(A)
    (tmp_path / "b.tidal").write_text(B)
    app = Selene(parse_args(["--boot", str(TEST_BOOT), "--dir", str(tmp_path)]))
    async with app.run_test(size=(120, 40)) as pilot:
        assert await wait_for(lambda: app.state["ghci"] == "ready", 60)
        lanes = app.query_one("#lanes")
        app.command("open a")
        await pilot.press("ctrl+e")
        assert await wait_for(lambda: len(lanes.chips) == 4, 10)
        app.command("open b")
        await pilot.press("ctrl+e")

        # d3 and d4 keep playing, and keep their lanes, marked held.
        assert await wait_for(lambda: set(app.held) == {"d3", "d4"}, 10)
        assert [c.orbit for c in lanes.chips] == ["d1", "d2", "d3", "d4"]
        assert [c.has_class("held") for c in lanes.chips] == [False, False, True, True]
        assert "a.tidal" in str(lanes.chips[2].tooltip)
        assert await heard(fake_dirt) == {"bd", "hh", "cp", "arpy"}
        # The editor is file b, unedited: not "dirty" despite the held layers.
        assert not app._editor_dirty()

        # Held lanes mute like any other.
        await pilot.click(lanes.chips[3])
        assert await wait_for(lambda: app.muted == {"d4"}, 3)
        assert await heard(fake_dirt) == {"bd", "hh", "cp"}
        await pilot.click(lanes.chips[3])
        assert await wait_for(lambda: not app.muted, 3)

        # /take brings d3's code into the editor; it's current again.
        app.command("take 3")
        assert await wait_for(lambda: "d3" not in app.held, 3)
        assert 'd3 $ s "cp*4"' in app.query_one("#code").text
        assert not app._editor_dirty()

        # /fade crossfades d4 out over 8 cycles (4 s at cps 2), then it's gone.
        app.command("fade 4")
        assert await wait_for(lambda: "d4" not in app.held and "d4" not in app._all_orbits(), 8)
        assert "arpy" not in await heard(fake_dirt)

        # Something started behind selene's back still gets a (held) lane.
        await app.ghci.eval('d7 $ s "tabla*4"')
        assert await wait_for(lambda: "d7" in app.held, 6)
        assert app.held["d7"].stmt is None
        app.command("stop held")
        assert await wait_for(lambda: "d7" not in app._all_orbits(), 6)
        assert "tabla" not in await heard(fake_dirt)
        await asyncio.sleep(2.5)  # in-flight events don't resurrect it
        assert "d7" not in app._all_orbits()

        # A new file leaves layers held; hush clears everything.
        app.command("open a")
        assert await wait_for(lambda: app.query_one("#code").text.startswith("setcps 2\nd1 $ s \"bd*4\""), 3)
        await pilot.press("ctrl+e")
        app.command("open b")
        await pilot.press("ctrl+e")
        assert await wait_for(lambda: set(app.held) == {"d3", "d4"}, 10)
        await pilot.press("escape")
        assert await wait_for(lambda: not app.held and not app._all_orbits(), 5)
        await asyncio.sleep(2.5)
        assert not app._all_orbits(), "hushed layers came back"
        await app.action_quit()


def log_text(app):
    return "\n".join(strip.text for strip in app.query_one("#log").lines)


@needs_ghci
async def test_fade_all(tmp_path, fake_dirt):
    from test_flow_app import make_app
    (tmp_path / "a.tidal").write_text(A)
    (tmp_path / "b.tidal").write_text(B)
    app = make_app("--dir", str(tmp_path))
    async with app.run_test(size=(120, 40)) as pilot:
        assert await wait_for(lambda: app.state["ghci"] == "ready", 60)
        app.command("open a")
        await pilot.press("ctrl+e")
        await asyncio.sleep(0.5)
        app.command("open b")
        await pilot.press("ctrl+e")
        assert await wait_for(lambda: set(app.held) == {"d3", "d4"}, 10)
        await pilot.press("ctrl+f")
        assert app.flow_on

        # Everything, current and held, fades over 4 cycles (2 s at cps 2).
        app.command("fade all 4")
        await asyncio.sleep(0.3)
        assert not app.flow_on  # Flow steps aside for the fade
        assert set(app.fading) == {"d1", "d2", "d3", "d4"}
        assert await heard(fake_dirt), "should still be audible mid-fade"
        assert await wait_for(lambda: not app._all_orbits(), 4)
        assert await heard(fake_dirt) == set()
        assert "faded out" in log_text(app)
        assert app.query_one("#code").text == B  # the code is still there...
        await pilot.press("ctrl+e")  # ...to bring it back
        assert await wait_for(lambda: app.playing_code == B, 5)
        assert await heard(fake_dirt) == {"bd", "hh"}

        # Playing something mid-fade cancels the hush at the end.
        app.command("fade all 4")
        await asyncio.sleep(0.5)
        await pilot.press("ctrl+e")
        await asyncio.sleep(2.5)  # past where the fade would have ended
        assert app.playing_code == B
        assert await heard(fake_dirt) == {"bd", "hh"}

        # Cycle counts on single fades.
        app.command("fade 1 16")
        assert await wait_for(lambda: "fading d1 out over 16 cycles" in log_text(app), 3)
        app.command("fade d1 d2 3")
        assert await wait_for(lambda: "fading d1 d2 out over 3 cycles" in log_text(app), 3)
        await app.action_quit()
