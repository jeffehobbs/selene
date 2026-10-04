"""The player's preferences ride along at the end of the model's system prompt."""

import asyncio

from conftest import TEST_BOOT, needs_ghci

from selene import settings
from selene.app import Selene, parse_args
from selene.files import ConfirmScreen, PrefsScreen
from selene.llm import build_system_prompt


def test_preferences_close_the_system_prompt():
    plain = build_system_prompt(["bd"], ["superpiano"])
    assert "preferences" not in plain
    prompt = build_system_prompt(["bd"], ["superpiano"], "- prefer 808 kits\n")
    assert prompt.startswith(plain)
    assert prompt.rstrip().endswith("- prefer 808 kits")
    assert "follow these unless a request says otherwise" in prompt


class Recorder:
    """Records the system prompt each request is sent with."""
    model = "fake"

    def __init__(self):
        self.systems = []

    async def resolve_model(self):
        return "fake"

    async def chat(self, messages):
        self.systems.append(messages[0]["content"])
        yield '```haskell\nd1 $ s "bd*4"\n```'

    async def close(self):
        pass


async def wait_for(cond, timeout, step=0.05):
    for _ in range(int(timeout / step)):
        if cond():
            return True
        await asyncio.sleep(step)
    return False


def dialog_up(app, kind):
    return isinstance(app.screen, kind) and bool(app.screen.query("Button"))


@needs_ghci
async def test_prefs_dialog_and_prefer(private_settings, fake_dirt):
    app = Selene(parse_args(["--boot", str(TEST_BOOT)]))
    model = app.ollama = Recorder()
    async with app.run_test(size=(120, 34)) as pilot:
        assert await wait_for(lambda: app.state["ghci"] == "ready", 60)

        # /prefer appends a line, saved and in the prompt straight away.
        app.command("prefer prefer 808 kits")
        app.command("prefer keep it under 120 bpm")
        assert settings.load_preferences() == "- prefer 808 kits\n- keep it under 120 bpm\n"
        app.generate("a beat")
        assert await wait_for(lambda: model.systems, 10)
        assert model.systems[-1].rstrip().endswith("- keep it under 120 bpm")

        # /prefs edits them; ctrl+s in the dialog saves the preferences, not the pattern.
        app.command("prefs")
        assert await wait_for(lambda: dialog_up(app, PrefsScreen), 3)
        area = app.screen.query_one("#prefs")
        assert area.text.startswith("- prefer 808 kits")
        area.text = "- lots of reverb"
        await pilot.press("ctrl+s")
        assert await wait_for(lambda: not app._modal(), 3)
        assert settings.load_preferences() == "- lots of reverb\n"
        assert app.query_one("#code").border_title == "pattern"  # no file was saved

        # Esc with unsaved changes asks first; Cancel (the default) keeps editing.
        app.command("prefs")
        assert await wait_for(lambda: dialog_up(app, PrefsScreen), 3)
        app.screen.query_one("#prefs").text = "- something else"
        await pilot.press("escape")
        assert await wait_for(lambda: dialog_up(app, ConfirmScreen), 3)
        await pilot.press("enter")  # focused: Cancel
        assert await wait_for(lambda: dialog_up(app, PrefsScreen), 3)
        await pilot.press("escape")
        assert await wait_for(lambda: dialog_up(app, ConfirmScreen), 3)
        await pilot.click("#yes")  # Discard
        assert await wait_for(lambda: not app._modal(), 3)
        assert settings.load_preferences() == "- lots of reverb\n"
        assert app.playing_code  # esc in the dialogs never hushed the music

        # The footer's Prefs entry (ctrl+t) opens the same dialog.
        await pilot.press("ctrl+t")
        assert await wait_for(lambda: dialog_up(app, PrefsScreen), 3)
        await pilot.press("escape")  # nothing changed: closes without asking
        assert await wait_for(lambda: not app._modal(), 3)
        from textual.widgets._footer import FooterKey
        prefs_button = next(k for k in app.query(FooterKey) if k.key == "ctrl+t")
        assert prefs_button.description == "Prefs"
        await pilot.click(prefs_button)  # clicking it works too
        assert await wait_for(lambda: dialog_up(app, PrefsScreen), 3)
        await pilot.press("escape")
        assert await wait_for(lambda: not app._modal(), 3)

        app.generate("another beat")
        assert await wait_for(lambda: len(model.systems) == 2, 10)
        assert model.systems[-1].rstrip().endswith("- lots of reverb")
        assert "808" not in model.systems[-1].split("preferences")[-1]
        await app.action_quit()
