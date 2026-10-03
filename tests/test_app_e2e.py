"""Full loop: type a prompt in the TUI -> Ollama -> GHCi -> OSC on 57120.

Slow (real model). Skipped unless Ollama is up with a gemma4 model.
"""

import asyncio

import httpx
import pytest
from conftest import TEST_BOOT, needs_ghci

from selene.app import Selene, parse_args


def ollama_has(family: str) -> bool:
    try:
        tags = httpx.get("http://localhost:11434/api/tags", timeout=2).json()["models"]
    except Exception:  # noqa: BLE001
        return False
    return any(m["name"].split(":")[0] == family for m in tags)


def log_text(app) -> str:
    return "\n".join(strip.text for strip in app.query_one("#log").lines)


async def wait_for(cond, timeout: float, step: float = 0.25) -> bool:
    for _ in range(int(timeout / step)):
        if cond():
            return True
        await asyncio.sleep(step)
    return False


@needs_ghci
@pytest.mark.skipif(not ollama_has("gemma4"), reason="no gemma4 in Ollama")
async def test_prompt_to_sound(fake_dirt):
    app = Selene(parse_args(["--boot", str(TEST_BOOT)]))
    async with app.run_test(size=(140, 40)) as pilot:
        assert await wait_for(lambda: app.state["ghci"] == "ready", 60), app.state
        assert app.state["model"] == "gemma4:31b-mlx"

        await pilot.click("#prompt")
        await pilot.press(*"slow dubby techno with a deep kick and offbeat hats")
        await pilot.press("enter")
        assert await wait_for(lambda: bool(app.playing_code), 240), \
            f"nothing played; editor:\n{app.query_one('#code').text}\nlog:\n{log_text(app)}"
        print("\n--- generated ---\n" + app.playing_code)
        assert await wait_for(lambda: bool(fake_dirt.plays()), 10)

        # A follow-up revises what's playing.
        first = app.playing_code
        await pilot.press(*"add a slow minor-key pad on d2")
        await pilot.press("enter")
        assert await wait_for(lambda: app.playing_code not in ("", first), 240), log_text(app)
        print("\n--- revised ---\n" + app.playing_code)
        assert "d2" in app.playing_code

        await pilot.press("escape")
        assert await wait_for(lambda: app.playing_code == "", 5)
        await app.action_quit()


@needs_ghci
async def test_screenshot(tmp_path):
    """Render the idle UI for a visual check (no model needed)."""
    app = Selene(parse_args(["--boot", str(TEST_BOOT)]))
    async with app.run_test(size=(120, 32)) as pilot:
        assert await wait_for(lambda: app.state["ghci"] == "ready", 60)
        app.query_one("#code").text = 'setcps (124/60/4)\n\nd1 $ s "bd*4" # room 0.2\n\nd2 $ n "0 3 7" # s "superpiano"'
        app.playing_code = app.query_one("#code").text
        app._code_state("playing")
        app._render_bar()
        await pilot.pause()
        app.save_screenshot(filename="screenshot.svg", path=str(tmp_path.parent.parent))
        await app.action_quit()
