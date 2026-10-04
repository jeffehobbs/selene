"""The editor builds the model's code a whole line at a time, code only."""

import asyncio

from conftest import TEST_BOOT, needs_ghci

from selene.app import Selene, parse_args

REPLY = ('Here is a groove for you:\n```haskell\nsetcps (120/60/4)\n\nd1 $ s "bd*4"\n'
         '  # gain 1.0\nd2 $ s "~ hh" # gain 0.8\n```\nEnjoy the groove!')


class SlowModel:
    """Streams REPLY in 3-character chunks, snapshotting the editor between them."""

    model = "fake"

    def __init__(self, app):
        self.app = app
        self.snapshots: list[tuple[str, bool, bool]] = []

    async def resolve_model(self):
        return "fake"

    async def chat(self, messages):
        editor = self.app.query_one("#code")
        for i in range(0, len(REPLY), 3):
            yield REPLY[i:i + 3]
            await asyncio.sleep(0.005)
            self.snapshots.append((editor.text, editor.read_only, editor.has_class("drafting")))

    async def close(self):
        pass


async def wait_for(cond, timeout, step=0.05):
    for _ in range(int(timeout / step)):
        if cond():
            return True
        await asyncio.sleep(step)
    return False


@needs_ghci
async def test_streaming_builds_whole_lines_of_code_only(fake_dirt):
    app = Selene(parse_args(["--boot", str(TEST_BOOT)]))
    model = app.ollama = SlowModel(app)
    async with app.run_test(size=(120, 34)):
        assert await wait_for(lambda: app.state["ghci"] == "ready", 60)
        editor = app.query_one("#code")
        editor.text = "-- what was here before"
        app.generate("a groove")
        assert await wait_for(lambda: app.playing_code, 15)
        final = editor.text
        assert final == 'setcps (120/60/4)\n\nd1 $ s "bd*4"\n  # gain 1.0\nd2 $ s "~ hh" # gain 0.8'
        texts = [t for t, _, _ in model.snapshots]
        # The old text stays up until the first line of code is complete...
        assert texts[0] == "-- what was here before"
        drawn = [t for t in texts if t != "-- what was here before"]
        assert drawn[0].rstrip("\n") == "setcps (120/60/4)"
        # ...then the code only ever grows by whole lines: never the fence,
        # never prose, never half a line.
        for t in drawn:
            assert "```" not in t and "groove" not in t
            assert final.startswith(t)
            whole = len(t) == len(final) or final[len(t)] == "\n" or t.endswith("\n")
            assert whole, f"half a line drawn: {t!r}"
        assert len(set(drawn)) >= 4
        # Read-only and dimmed while drafting; editable again after.
        assert all(ro and dim for t, ro, dim in model.snapshots)
        assert not editor.read_only and not editor.has_class("drafting")
        assert editor.show_cursor
        await app.action_quit()
