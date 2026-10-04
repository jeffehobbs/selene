"""Unplayed editor edits are the base for the next prompt; model text is not."""

import asyncio

from conftest import TEST_BOOT, needs_ghci

from selene.app import Selene, parse_args


class FakeOllama:
    """Records what the app asks; answers with fixed code."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.asked: list[str] = []
        self.model = "fake"

    async def resolve_model(self):
        return "fake"

    async def chat(self, messages):
        self.asked.append(messages[-1]["content"])
        yield self.replies.pop(0)

    async def close(self):
        pass


async def wait_for(cond, timeout: float, step: float = 0.1) -> bool:
    for _ in range(int(timeout / step)):
        if cond():
            return True
        await asyncio.sleep(step)
    return False


@needs_ghci
async def test_unplayed_edits_are_the_base(fake_dirt):
    app = Selene(parse_args(["--boot", str(TEST_BOOT), "--fix-attempts", "0"]))
    app.ollama = FakeOllama([
        '```haskell\nd1 $ s "bd*4" # nonsense 1\n```',   # fails in GHCi
        '```haskell\nd1 $ s "bd*4"\nd2 $ s "cp"\n```',
        '```haskell\nd1 $ s "bd*2"\n```',
    ])
    async with app.run_test(size=(120, 30)) as pilot:
        assert await wait_for(lambda: app.state["ghci"] == "ready", 60)
        editor = app.query_one("#code")
        app.evaluate('d1 $ s "bd"', source="editor")
        assert await wait_for(lambda: app.playing_code == 'd1 $ s "bd"', 10)

        # A failed generation leaves model code in the editor: not an edit.
        app.generate("busy kick")
        assert await wait_for(lambda: editor.has_class("failed"), 10)
        assert not app._editor_dirty()
        app.generate("add claps")
        assert await wait_for(lambda: "cp" in app.playing_code, 10)
        assert "not played it yet" not in app.ollama.asked[-1]

        # The player edits without playing, then prompts: the edit is the base.
        editor.text = 'd1 $ s "bd*4"\nd2 $ s "cp*3"\nd3 $ s "arpy"'
        assert app._editor_dirty()
        app.generate("halve the kick")
        assert await wait_for(lambda: len(app.ollama.asked) == 3, 10)
        assert "not played it yet" in app.ollama.asked[-1]
        assert 'd3 $ s "arpy"' in app.ollama.asked[-1]
        await app.action_quit()
