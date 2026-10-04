"""Clicking the dN chips mutes/solos orbits; checked against what reaches SuperDirt."""

import asyncio

from conftest import TEST_BOOT, needs_ghci

from selene.app import OrbitChip, Selene, parse_args


async def wait_for(cond, timeout: float, step: float = 0.1) -> bool:
    for _ in range(int(timeout / step)):
        if cond():
            return True
        await asyncio.sleep(step)
    return False


async def heard(fake_dirt) -> set[str]:
    """Sounds arriving over a fresh window (after in-flight messages drain)."""
    await asyncio.sleep(0.4)
    fake_dirt.messages.clear()
    await asyncio.sleep(1.2)
    return {s for m in fake_dirt.plays() for s in m if s in ("bd", "cp", "arpy")}


def visible_chips(app) -> list[OrbitChip]:
    """The chips heading the lanes (the bar's copies are hidden meanwhile)."""
    return app.query_one("#lanes").chips


def chip(app, orbit: str) -> OrbitChip:
    return next(c for c in visible_chips(app) if c.orbit == orbit)


@needs_ghci
async def test_click_mute_solo_and_hush(fake_dirt):
    app = Selene(parse_args(["--boot", str(TEST_BOOT)]))
    async with app.run_test(size=(120, 30)) as pilot:
        assert await wait_for(lambda: app.state["ghci"] == "ready", 60)
        code = 'setcps 1\nd1 $ s "bd*4"\nd2 $ s "cp*4"\nd3 $ s "arpy*4"'
        app.evaluate(code, source="editor")
        assert await wait_for(lambda: len(visible_chips(app)) == 3, 10)
        assert await heard(fake_dirt) == {"bd", "cp", "arpy"}

        await pilot.click(chip(app, "d1"))
        assert await wait_for(lambda: chip(app, "d1").has_class("muted"), 3)
        assert await heard(fake_dirt) == {"cp", "arpy"}

        # Mute survives re-evaluating the orbit (e.g. a model revision).
        app.evaluate('setcps 1\nd1 $ s "bd*8"\nd2 $ s "cp*4"\nd3 $ s "arpy*4"', source="editor")
        assert await heard(fake_dirt) == {"cp", "arpy"}

        await pilot.click(chip(app, "d1"))
        assert await heard(fake_dirt) == {"bd", "cp", "arpy"}

        await pilot.click(chip(app, "d2"), shift=True)  # solo d2
        assert app.muted == {"d1", "d3"}
        assert await heard(fake_dirt) == {"cp"}
        await pilot.click(chip(app, "d2"), shift=True)  # un-solo
        assert app.muted == set()
        assert await heard(fake_dirt) == {"bd", "cp", "arpy"}

        # An orbit that leaves the code is unmuted so it comes back audible.
        await pilot.click(chip(app, "d3"))
        app.evaluate('setcps 1\nd1 $ s "bd*4"\nd2 $ s "cp*4"\nd3 silence', source="editor")
        assert await wait_for(lambda: "d3" not in app.muted, 5)
        app.evaluate('setcps 1\nd1 $ s "bd*4"\nd2 $ s "cp*4"\nd3 $ s "arpy*4"', source="editor")
        assert await heard(fake_dirt) == {"bd", "cp", "arpy"}

        # Hush clears mutes so the next pattern isn't silently muted.
        await pilot.click(chip(app, "d1"))
        await pilot.press("escape")
        assert await wait_for(lambda: not visible_chips(app), 5)
        assert app.muted == set()
        app.evaluate('d1 $ s "bd*4"', source="editor")
        assert await heard(fake_dirt) == {"bd"}

        await app.action_quit()
