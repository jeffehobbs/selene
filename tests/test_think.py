"""Think mode: the model's reasoning streams into a strip above the editor while
a prompt is interpreted, and the strip goes away once the code starts."""

import asyncio

from selene import settings
from selene.app import Selene, parse_args

REPLY = '```haskell\nd1 $ s "bd*4"\n```'


class ThinkingModel:
    model = "fake"

    def __init__(self, app):
        self.app = app
        self.strip_while_thinking = ""
        self.thoughts_arg = []

    async def resolve_model(self):
        return "fake"

    async def chat(self, messages, thoughts=None):
        self.thoughts_arg.append(thoughts is not None)
        if thoughts:
            thoughts("Style: slow and dark.\nPick 60 BPM.")
            await asyncio.sleep(0.05)
            strip = self.app.query_one("#thoughts")
            self.strip_while_thinking = f"{strip.has_class('on')}|{self.app.thought}"
        yield REPLY

    async def warm(self):
        pass

    async def close(self):
        pass


async def test_think_mode_shows_reasoning_then_clears_it():
    app = Selene(parse_args(["--ghci", "/nonexistent/ghci", "--fix-attempts", "0"]))
    model = app.ollama = ThinkingModel(app)
    assert not app.think
    async with app.run_test(size=(120, 30)) as pilot:
        app.command("think")
        assert app.think and settings.load()["think"] is True
        app.generate("slow dark drone")
        for _ in range(100):
            await pilot.pause(0.05)
            if app.model_text:
                break
        assert model.thoughts_arg[0] is True
        assert model.strip_while_thinking == "True|Style: slow and dark.\nPick 60 BPM."
        assert not app.query_one("#thoughts").has_class("on") and app.thought == ""

        app.command("think")
        assert not app.think and settings.load()["think"] is False
        await app.action_quit()
