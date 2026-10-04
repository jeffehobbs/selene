"""Flow as a mode in the app: off at launch, toggled by ctrl+f, glides home."""

import asyncio

import pytest
from conftest import TEST_BOOT, needs_ghci

import selene.app as app_module
from selene.app import Selene, parse_args

CODE = 'setcps 1\nd1 $ s "bd*8"\nd2 $ s "cp*8" # gain 0.8'


class NoModel:
    """Flow's layer rewrites have their own end-to-end test; here the model
    stays out of it so what plays is exactly what the test wrote."""
    model = "none"

    async def resolve_model(self):
        return "none"

    async def chat(self, messages):
        raise RuntimeError("no model in this test")
        yield  # pragma: no cover

    async def close(self):
        pass


def make_app(*extra):
    app = Selene(parse_args(["--boot", str(TEST_BOOT), *extra]))
    app.ollama = NoModel()
    return app


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
    app = make_app()
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
        # (Not asserting the "easing out" state: at 20x it lasts 0.6 s, and the
        # pilot's press waits for an idle screen the live lanes never give it.)
        assert not app.flow_on
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
    app = make_app()
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


@needs_ghci
async def test_deep_flow_drops_land_on_phrase_boundaries(fake_dirt, monkeypatch):
    """Depth 5: whole layers drop out on multiples of 4 cycles, as heard by SuperDirt."""
    monkeypatch.setattr(app_module, "FLOW_SPEED", 30.0)
    import selene.flow
    monkeypatch.setattr(selene.flow, "EVENT_PERIODS", {"drop": 47})  # only drops, to measure them
    app = make_app("--flow-depth", "5")
    # 64 events a cycle: even heavily thinned, a layer is never silent by chance.
    code = 'setcps 1\nd1 $ s "bd*64"\nd2 $ s "hh*64"\nd3 $ s "cp*64"'
    async with app.run_test(size=(120, 30)) as pilot:
        assert await wait_for(lambda: app.state["ghci"] == "ready", 60)
        app.evaluate(code, source="editor")
        assert await wait_for(lambda: app.clock.known, 10), "no events on the tap"
        await pilot.press("ctrl+f")
        await asyncio.sleep(0.5)
        fake_dirt.clear()
        await asyncio.sleep(40)
        events = fake_dirt.events()
        await app.action_quit()

    # A drop sends events at gain 0. A change is sent a frame (~60 ms) early
    # so it always catches the boundary, so a few events at the very end of
    # the previous cycle may already have it: a cycle counts as heard when
    # most of its events are audible.
    counts: dict[tuple[str, int], list[int]] = {}
    for e in events:
        tally = counts.setdefault((e["s"], int(e["cycle"] + 1e-6)), [0, 0])
        tally[0] += 1
        tally[1] += e.get("gain", 1) > 1e-3
    heard: dict[str, set[int]] = {}
    for (sound, cycle), (total, audible) in counts.items():
        heard.setdefault(sound, set())
        if audible > total / 2:
            heard[sound].add(cycle)
    cycles = range(min(min(c) for c in heard.values()) + 1, max(max(c) for c in heard.values()))
    drops = 0
    for sound, present in heard.items():
        run = []
        for c in cycles:
            if c not in present:
                run.append(c)
            elif run:
                assert run[0] % 4 == 0 and len(run) % 4 == 0, (sound, run)
                drops += 1
                run = []
    assert drops >= 2, f"expected drops in 40 cycles at depth 5; heard {heard}"


class SlowModel(NoModel):
    """Answers a rewrite only after a pause, so Flow can stop meanwhile."""

    async def chat(self, messages):
        await asyncio.sleep(1.0)
        yield '```haskell\nd1 $ s "bd*2"\n```'


@needs_ghci
async def test_stopping_flow_mid_rewrite_is_safe(fake_dirt):
    app = make_app()
    app.ollama = SlowModel()
    async with app.run_test(size=(120, 30)) as pilot:
        assert await wait_for(lambda: app.state["ghci"] == "ready", 60)
        app.evaluate(CODE, source="editor")
        assert await wait_for(lambda: bool(app.playing_code), 5)
        await pilot.press("ctrl+f")
        # Flow stops between scheduling a rewrite and the rewrite starting
        # (quitting did this to the end-to-end test)...
        app._flow_evolve("d1", "vary")
        app._flow_stop(log=False)
        await asyncio.sleep(0.3)
        # ...and while a rewrite is waiting on the model.
        await pilot.press("ctrl+f")
        app._flow_evolve("d2", "vary")
        await asyncio.sleep(0.2)
        await pilot.press("escape")  # hush stops Flow outright
        assert await wait_for(lambda: app.flow is None, 3)
        await asyncio.sleep(1.5)  # the model answers after Flow is gone
        assert app.playing_code == ""  # nothing was evaluated or adopted
        await app.action_quit()
    # Leaving run_test re-raises any worker crash, so getting here is the test.
