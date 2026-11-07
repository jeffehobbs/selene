"""Pause freezes Tidal's clock (tempo 0) and resume carries on; checked against
what reaches SuperDirt."""

import asyncio

from conftest import TEST_BOOT, needs_ghci

from selene.app import Selene, parse_args, parse_cps
from selene.flow import TidalClock


async def wait_for(cond, timeout: float, step: float = 0.1) -> bool:
    for _ in range(int(timeout / step)):
        if cond():
            return True
        await asyncio.sleep(step)
    return False


def test_parse_cps():
    assert parse_cps("setcps 0.5") == 0.5
    assert parse_cps("setcps (120/60/4)") == 0.5
    assert parse_cps("setcps (bpm/60)") is None
    assert parse_cps("setcps 0") is None
    assert parse_cps("setcps (__import__('os'))") is None


def test_clock_freezes_and_resumes():
    clock = TidalClock()
    clock.observe(100.0, 100.3, 10.0, 1.0)
    clock.pause(101.3)
    assert clock.cycle_at(101.3) == clock.cycle_at(500.0) == 11.0
    clock.observe(102.0, 102.3, 99.0, 1.0)  # an event already in flight: ignored
    assert clock.cycle_at(200.0) == 11.0
    clock.resume(200.0, 2.0)
    assert clock.cycle_at(201.0) == 13.0


@needs_ghci
async def test_pause_and_resume(fake_dirt):
    app = Selene(parse_args(["--boot", str(TEST_BOOT)]))
    async with app.run_test(size=(120, 30)) as pilot:
        assert await wait_for(lambda: app.state["ghci"] == "ready", 60)
        app.evaluate('setcps 1\nd1 $ s "bd*4"', source="editor")
        assert await wait_for(lambda: bool(fake_dirt.plays()), 10)

        await pilot.press("ctrl+p")
        assert await wait_for(lambda: app.clock.paused, 1)
        await asyncio.sleep(0.8)  # let what was already scheduled drain
        frozen = app.clock.cycle_at(__import__("time").time())
        fake_dirt.clear()
        await asyncio.sleep(1.5)
        assert not fake_dirt.plays(), "events still arriving while paused"
        assert app.clock.cycle_at(__import__("time").time()) == frozen

        # A tempo change while paused waits for the resume.
        app.evaluate("setcps 2", source="raw")
        assert await wait_for(lambda: app.paused_cps == 2.0, 5)
        await asyncio.sleep(0.5)
        assert not fake_dirt.plays()

        await pilot.press("ctrl+p")
        assert await wait_for(lambda: app.paused_cps is None, 5)
        assert await wait_for(lambda: bool(fake_dirt.plays()), 5), "no sound after resume"
        assert not app.clock.paused

        # Hush while paused ends the pause, so the next pattern sounds.
        await pilot.press("ctrl+p")
        assert await wait_for(lambda: app.paused_cps is not None, 5)
        await pilot.press("escape")
        assert await wait_for(lambda: app.paused_cps is None, 5)
        fake_dirt.clear()
        app.evaluate('setcps 1\nd1 $ s "cp*4"', source="editor")
        assert await wait_for(lambda: bool(fake_dirt.plays()), 10)
