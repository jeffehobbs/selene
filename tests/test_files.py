"""Saving and opening .tidal files from the editor."""

import asyncio

from conftest import TEST_BOOT, needs_ghci

from selene.app import Selene, parse_args
from selene.files import ConfirmScreen, PathScreen, resolve


def dialog(app, kind):
    """True once a dialog of this kind is up and its widgets are mounted."""
    return isinstance(app.screen, kind) and bool(app.screen.query("Button, Input"))


async def wait_for(cond, timeout: float, step: float = 0.05) -> bool:
    for _ in range(int(timeout / step)):
        if cond():
            return True
        await asyncio.sleep(step)
    return False


def test_resolve(tmp_path):
    assert resolve("dub", tmp_path, save=True) == tmp_path / "dub.tidal"
    assert resolve("dub.txt", tmp_path, save=True) == tmp_path / "dub.txt"
    assert resolve("dub", tmp_path, save=False) == tmp_path / "dub.tidal"
    (tmp_path / "notes").mkdir()
    assert resolve("notes", tmp_path, save=False) == tmp_path / "notes"  # exists as-is
    assert resolve("~/x.tidal", tmp_path, save=True).is_absolute()


@needs_ghci
async def test_save_open_and_confirmations(tmp_path, fake_dirt):
    folder = tmp_path / "songs"
    app = Selene(parse_args(["--boot", str(TEST_BOOT), "--dir", str(folder)]))
    async with app.run_test(size=(120, 34)) as pilot:
        assert await wait_for(lambda: app.state["ghci"] == "ready", 60)
        editor = app.query_one("#code")
        editor.text = 'd1 $ s "bd*4"'

        # First save asks for a name; the folder is created.
        await pilot.press("ctrl+s")
        assert await wait_for(lambda: dialog(app, PathScreen), 2)
        await pilot.press(*"dub", "enter")
        saved = folder / "dub.tidal"
        assert await wait_for(saved.exists, 2)
        assert saved.read_text() == 'd1 $ s "bd*4"\n'
        assert editor.border_title == "dub.tidal"

        # Edits mark it; ctrl+s writes straight back.
        editor.text = 'd1 $ s "bd*2"'
        await pilot.pause()
        assert editor.border_title == "dub.tidal •"
        await pilot.press("ctrl+s")
        assert await wait_for(lambda: saved.read_text() == 'd1 $ s "bd*2"\n', 2)
        assert editor.border_title == "dub.tidal"

        # Save-as onto another existing file asks first; Cancel is the default.
        (folder / "other.tidal").write_text("d2 $ s \"cp\"\n")
        app.command("save other")
        assert await wait_for(lambda: dialog(app, ConfirmScreen), 2)
        await pilot.press("enter")  # focused button: Cancel
        assert await wait_for(lambda: not app._modal(), 2)
        assert (folder / "other.tidal").read_text() == 'd2 $ s "cp"\n'

        # Opening over unsaved, unplayed edits asks first.
        editor.text = 'd1 $ s "arpy"'
        app.command("open other")
        assert await wait_for(lambda: dialog(app, ConfirmScreen), 2)
        await pilot.press("escape")  # closes the dialog only; nothing hushed
        assert await wait_for(lambda: not app._modal(), 2)
        assert editor.text == 'd1 $ s "arpy"'
        app.command("open other")
        assert await wait_for(lambda: dialog(app, ConfirmScreen), 2)
        await pilot.click("#yes")
        assert await wait_for(lambda: editor.text == 'd2 $ s "cp"\n', 2)
        assert editor.border_title == "other.tidal"

        # Opened code isn't played until ctrl+e, and is then what plays.
        assert app.playing_code == ""
        await pilot.press("ctrl+e")
        assert await wait_for(lambda: "cp" in app.playing_code, 10)

        # The open dialog's tree lists .tidal files; picking one opens it.
        await pilot.press("ctrl+o")
        assert await wait_for(lambda: dialog(app, PathScreen), 2)
        await pilot.press(*"dub", "enter")  # a bare name finds dub.tidal
        assert await wait_for(lambda: editor.text == 'd1 $ s "bd*2"\n', 2)
        assert editor.border_title == "dub.tidal"

        # Picking a file in the tree opens it too.
        await pilot.press("ctrl+o")
        assert await wait_for(lambda: dialog(app, PathScreen), 2)
        tree = app.screen.query_one("TidalTree")
        assert await wait_for(lambda: len(tree.root.children) == 2, 3)
        assert sorted(str(n.label) for n in tree.root.children) == ["dub.tidal", "other.tidal"]
        other = next(n for n in tree.root.children if str(n.label) == "other.tidal")
        tree.select_node(other)
        assert await wait_for(lambda: editor.text == 'd2 $ s "cp"\n', 2)
        await app.action_quit()
