"""Whatever the player has done to the pattern is what the next prompt builds on."""

import asyncio

from conftest import TEST_BOOT, needs_ghci

from selene.app import Selene, parse_args

FIRST = 'setcps 1\nd1 $ s "bd*4"\nd2 $ n "0 3" # s "superpiano"'


class Recorder:
    """Records every request; replies from a queue."""
    model = "fake"

    def __init__(self, replies):
        self.replies = list(replies)
        self.requests: list[list[dict]] = []

    async def resolve_model(self):
        return "fake"

    async def chat(self, messages):
        self.requests.append([dict(m) for m in messages])
        yield self.replies.pop(0)

    async def close(self):
        pass

    @property
    def last(self) -> str:
        """The latest request's final user message."""
        return self.requests[-1][-1]["content"]


def fenced(code):
    return f"```haskell\n{code}\n```"


async def wait_for(cond, timeout, step=0.05):
    for _ in range(int(timeout / step)):
        if cond():
            return True
        await asyncio.sleep(step)
    return False


@needs_ghci
async def test_every_kind_of_edit_reaches_the_next_prompt(tmp_path, fake_dirt):
    app = Selene(parse_args(["--boot", str(TEST_BOOT), "--fix-attempts", "0",
                             "--dir", str(tmp_path)]))
    model = app.ollama = Recorder([fenced(FIRST)] + [fenced('d1 $ s "bd*2"')] * 12)
    async with app.run_test(size=(120, 34)) as pilot:
        assert await wait_for(lambda: app.state["ghci"] == "ready", 60)
        editor = app.query_one("#code")

        async def prompt(text):
            n = len(model.requests)
            app.generate(text)
            assert await wait_for(lambda: len(model.requests) > n, 10)
            await wait_for(lambda: not editor.read_only, 10)

        await prompt("a beat with piano")
        assert await wait_for(lambda: app.playing_code == FIRST, 10)

        # 1. Edited and played (ctrl+e): it's what's playing, so it's the base.
        played = FIRST.replace("superpiano", "supervibe")
        editor.text = played
        await pilot.press("ctrl+e")
        assert await wait_for(lambda: app.playing_code == played, 10)
        await prompt("faster")
        assert "supervibe" in model.last and "superpiano" not in model.last
        assert "not played it yet" not in model.last

        # 2. Edited, not played: sent as the player's intent, with what plays.
        unplayed = app.playing_code + '\nd3 $ s "cp*2" -- my clap'
        editor.text = unplayed
        await prompt("add hats")
        assert "not played it yet" in model.last and "-- my clap" in model.last

        # 3. After ctrl+n the history goes, but unplayed edits still count.
        editor.text = app.playing_code + "\n-- keep this kick"
        await pilot.press("ctrl+n")
        await prompt("something new")
        assert "-- keep this kick" in model.last
        assert len(model.requests[-1]) == 2  # system + this message: history cleared

        # 4. The model's broken code, fixed by hand: that's an edit too.
        model.replies.insert(0, fenced('d1 $ s "bd*4" # nonsense 1'))
        await prompt("break it")
        assert await wait_for(lambda: editor.has_class("failed"), 10)
        assert not app._editor_dirty()  # untouched model text isn't an edit
        editor.text = 'd1 $ s "bd*4" # gain 0.9 -- fixed it'
        assert app._editor_dirty()
        await prompt("now add a bassline")
        assert "-- fixed it" in model.last and "not played it yet" in model.last

        # 5. With held layers playing: the edit is the base, held layers listed apart.
        (tmp_path / "six.tidal").write_text(
            "setcps 1\n" + "\n".join(f'd{i} $ s "hh*{i}"' for i in range(1, 7)))
        (tmp_path / "two.tidal").write_text('setcps 1\nd1 $ s "bd*2"\nd2 $ s "cp"')
        app.command("open six")
        await pilot.press("ctrl+e")
        await asyncio.sleep(0.5)
        app.command("open two")
        await pilot.press("ctrl+e")
        assert await wait_for(lambda: set(app.held) == {"d3", "d4", "d5", "d6"}, 10)
        editor.text = 'setcps 1\nd1 $ s "bd*2"\nd2 $ s "cp" -- edited two'
        await prompt("vary it")
        assert "-- edited two" in model.last
        assert "held layers" in model.last and 'd6 $ s "hh*6"' in model.last

        # 6. After Flow rewrote a layer into the editor, a further edit still wins.
        app._adopt('d2 $ s "cp*2"', "(flow) evolving d2")
        assert 'd2 $ s "cp*2"' in editor.text and not app._editor_dirty()
        editor.text = editor.text + "\n-- after flow"
        await prompt("brighter")
        assert "-- after flow" in model.last and 'd2 $ s "cp*2"' in model.last
        await app.action_quit()
