"""Pulsar's playback keys: play the statement or line under the cursor, and
mute layers from the keyboard."""

import asyncio

from conftest import TEST_BOOT, needs_ghci

from selene.app import PatternEditor, Selene, parse_args

CODE = 'setcps 2\n-- drums\nd1 $ s "bd*4"\nd2 $ s "cp*4"\n  # gain 1\n\nd3 $ s "arpy*4"'
SOUNDS = ("bd", "cp", "arpy", "hh", "sn")


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
    return {e["s"] for e in fake_dirt.events()} & set(SOUNDS)


@needs_ghci
async def test_play_block_line_and_mute_keys(fake_dirt):
    app = Selene(parse_args(["--boot", str(TEST_BOOT)]))
    async with app.run_test(size=(120, 30)) as pilot:
        assert await wait_for(lambda: app.state["ghci"] == "ready", 60)
        editor = app.query_one(PatternEditor)
        editor.text = CODE
        app.action_play_editor()
        assert await wait_for(lambda: app.playing_code == CODE, 5)
        assert await heard(fake_dirt) == {"bd", "cp", "arpy"}

        # Change d2 and play just that statement, cursor on its second line.
        editor.text = CODE.replace('"cp*4"', '"hh*4"')
        editor.focus()
        editor.cursor_location = (4, 3)
        await pilot.press("ctrl+j")
        assert await wait_for(lambda: '"hh*4"' in app.playing_code, 5)
        assert await heard(fake_dirt) == {"bd", "hh", "arpy"}
        # Everything in the editor is playing now, so it isn't an unplayed edit,
        # and its comment and blank line are kept.
        assert app.playing_code == editor.text and not app._editor_dirty()

        # A block that leaves the editor unplayed elsewhere: d3 changed too,
        # but only d1 is played.
        editor.text = editor.text.replace('"arpy*4"', '"sn*4"').replace('"bd*4"', '"bd*2"')
        editor.cursor_location = (2, 0)
        await pilot.press("ctrl+enter")
        assert await wait_for(lambda: '"bd*2"' in app.playing_code, 5)
        assert '"arpy*4"' in app.playing_code and app._editor_dirty()
        # Layers stay in place: d1 is still before d2 and d3.
        assert app.playing_code.index("d1") < app.playing_code.index("d2")

        # The line under the cursor alone (the rest of d2's statement isn't sent).
        editor.cursor_location = (6, 0)
        await pilot.press("shift+enter")
        assert await wait_for(lambda: '"sn*4"' in app.playing_code, 5)
        assert await heard(fake_dirt) == {"bd", "hh", "sn"}

        # Nothing to play on a comment line.
        editor.cursor_location = (1, 0)
        await pilot.press("ctrl+j")
        assert app.playing_code.count("d1") == 1

        # Mutes: option+1 (Apple Terminal) and ctrl+3 (Pulsar), then unmute all.
        await pilot.press("inverted_exclamation_mark")
        await pilot.press("ctrl+3")
        assert await wait_for(lambda: app.muted == {"d1", "d3"}, 5)
        assert await heard(fake_dirt) == {"hh"}
        await pilot.press("ctrl+9")  # not playing: nothing happens
        assert app.muted == {"d1", "d3"}
        await pilot.press("º")
        assert await wait_for(lambda: app.muted == set(), 5)
        assert await heard(fake_dirt) == {"bd", "hh", "sn"}
        await app.action_quit()
