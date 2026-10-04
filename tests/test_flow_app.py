"""Flow as a mode in the app: off at launch, toggled by ctrl+f, glides home."""

import asyncio

import pytest
from conftest import TEST_BOOT, needs_ghci

import selene.app as app_module
from selene.app import Selene, parse_args

CODE = 'setcps 1\nd1 $ s "bd*8"\nd2 $ s "cp*8" # gain 0.8'


async def wait_for(cond, timeout: float, step: float = 0.1) -> bool:
    for _ in range(int(timeout / step)):
        if cond():
            return True
        await asyncio.sleep(step)
    return False


async def window(fake_dirt, seconds: float = 1.0) -> list[dict]:
    await asyncio.sleep(0.3)
    fake_dirt.clear()
    await asyncio.sleep(seconds)
    return fake_dirt.events()


@needs_ghci
async def test_flow_toggles_and_glides_home(fake_dirt, monkeypatch):
    monkeypatch.setattr(app_module, "FLOW_SPEED", 20.0)  # 20 s of Flow per second
    app = Selene(parse_args(["--boot", str(TEST_BOOT)]))
    async with app.run_test(size=(120, 30)) as pilot:
        assert await wait_for(lambda: app.state["ghci"] == "ready", 60)
        app.evaluate(CODE, source="editor")
        events = await window(fake_dirt)
        # Off at launch: plain code, no Flow controls anywhere.
        assert app.flow is None and events
        assert all("djf" not in e for e in events)

        await pilot.press("ctrl+f")
        assert app.flow_on and app.state["flow"] == "flow"
        events = await window(fake_dirt)
        # Wrapped, and at the very start everything is still neutral.
        assert events and all(e.get("djf") is not None for e in events)

        # Let Flow run a few simulated minutes: the controls move.
        await asyncio.sleep(12)
        events = await window(fake_dirt, 3)
        assert {round(e["djf"], 2) for e in events} != {0.5}, "tone never moved"
        gains = {e["s"]: e["gain"] for e in events}
        assert gains and (abs(gains.get("bd", 1) - 1) > 0.01 or abs(gains.get("cp", .8) - .8) > 0.01)

        await pilot.press("ctrl+f")  # off: glide home over 12 simulated seconds
        assert not app.flow_on and app.state["flow"] == "easing out"
        assert await wait_for(lambda: app.flow is None, 5)
        events = await window(fake_dirt)
        for e in events:
            assert abs(e["djf"] - 0.5) < 1e-6
            assert abs(e["gain"] - (1.0 if e["s"] == "bd" else 0.8)) < 1e-6
            assert e.get("room", 0) < 1e-6

        # The next evaluation is plain again.
        app.evaluate(CODE, source="editor")
        events = await window(fake_dirt)
        assert events and all("djf" not in e for e in events)
        await app.action_quit()


@needs_ghci
async def test_hush_turns_flow_off(fake_dirt, monkeypatch):
    monkeypatch.setattr(app_module, "FLOW_SPEED", 20.0)
    app = Selene(parse_args(["--boot", str(TEST_BOOT)]))
    async with app.run_test(size=(120, 30)) as pilot:
        assert await wait_for(lambda: app.state["ghci"] == "ready", 60)
        app.evaluate(CODE, source="editor")
        await asyncio.sleep(0.5)
        await pilot.press("ctrl+f")
        await asyncio.sleep(3)
        await pilot.press("escape")
        assert await wait_for(lambda: app.flow is None and app.state["flow"] == "", 5)
        assert not app.flow_on
        await app.action_quit()
