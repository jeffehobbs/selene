"""The model light and the ctrl+k model picker."""

import asyncio

import httpx

from selene import settings
from selene.app import Selene, parse_args
from selene.files import ModelScreen, model_row

MODELS = [
    {"name": "clef-flash:latest", "size": 10_932_739_373,
     "details": {"parameter_size": "9.1B", "quantization_level": "Q8_0"}},
    {"name": "gemma4:12b-mlx", "size": 7_714_091_504, "details": {}},
    {"name": "gemma4:31b-mlx", "size": 19_424_434_885, "details": {}},
]


class FakeOllama:
    def __init__(self, model="gemma4:31b-mlx"):
        self.model = self.requested = model
        self.up = True
        self.in_memory = {"gemma4:31b-mlx"}
        self.warmed: list[str] = []
        self.warm_gate = asyncio.Event()
        self.warm_gate.set()

    async def resolve_model(self):
        if not self.up:
            raise httpx.ConnectError("refused")
        if self.model not in {m["name"] for m in MODELS}:
            raise LookupError(f"model {self.model!r} not installed")
        return self.model

    async def installed(self):
        if not self.up:
            raise httpx.ConnectError("refused")
        return MODELS

    async def loaded(self):
        return set(self.in_memory)

    async def warm(self):
        await self.warm_gate.wait()
        self.warmed.append(self.model)
        self.in_memory.add(self.model)

    async def close(self):
        pass


async def wait_for(cond, timeout=5, step=0.05):
    for _ in range(int(timeout / step)):
        if cond():
            return True
        await asyncio.sleep(step)
    return False


def app_with(fake, *argv):
    # No GHCi: these tests are about Ollama, and a missing ghci boots in no time.
    app = Selene(parse_args(["--ghci", "/nonexistent/ghci", *argv]))
    app.ollama = fake
    return app


def log_text(app):
    return "\n".join(line.text for line in app.query_one("#log").lines)


def test_model_row_shows_size_details_and_memory():
    row = model_row(MODELS[0], 18, current=True, in_memory=True).plain
    assert row.startswith("● clef-flash:latest")
    assert "10.9 GB" in row and "9.1B Q8_0" in row and row.endswith("in memory")
    assert model_row(MODELS[1], 18, current=False, in_memory=False).plain.startswith("  gemma4")


async def test_light_follows_ollama_and_logs_changes_once():
    fake = FakeOllama()
    app = app_with(fake)
    async with app.run_test(size=(120, 34)):
        assert await wait_for(lambda: app.state["ollama"] == "ready")
        bar = str(app.query_one("#status").render())
        assert "● gemma4:31b-mlx" in bar

        fake.up = False
        await app._check_model()
        await app._check_model()
        assert app.state["ollama"] == "down"
        assert "unreachable" in str(app.query_one("#status").render())
        assert log_text(app).count("can't reach Ollama") == 1

        fake.up = True
        await app._check_model()
        assert app.state["ollama"] == "ready"
        assert "gemma4:31b-mlx is back" in log_text(app)


async def test_picker_switches_loads_and_remembers():
    fake = FakeOllama()
    app = app_with(fake)
    async with app.run_test(size=(120, 34)) as pilot:
        assert await wait_for(lambda: app.state["ollama"] == "ready")
        await pilot.press("ctrl+k")
        assert await wait_for(lambda: isinstance(app.screen, ModelScreen))
        options = app.screen.query_one("#models")
        assert options.option_count == 3
        assert options.highlighted == 2  # the current model
        assert "in memory" in str(options.get_option_at_index(2).prompt)

        fake.warm_gate.clear()  # hold the load so we can see the light while it loads
        await pilot.press("up", "enter")
        assert await wait_for(lambda: app.state["ollama"] == "loading")
        assert app.ollama.model == "gemma4:12b-mlx"
        assert "loading" in str(app.query_one("#status").render())
        fake.warm_gate.set()
        assert await wait_for(lambda: app.state["ollama"] == "ready")
        assert fake.warmed == ["gemma4:12b-mlx"]
        assert settings.load()["model"] == "gemma4:12b-mlx"

        # /model with no name opens the picker too; esc leaves the model alone.
        app.command("model")
        assert await wait_for(lambda: isinstance(app.screen, ModelScreen))
        await pilot.press("escape")
        assert await wait_for(lambda: not app._modal())
        assert app.ollama.model == "gemma4:12b-mlx" and fake.warmed == ["gemma4:12b-mlx"]

    # The next launch starts with the picked model; --model still wins.
    assert Selene(parse_args([])).ollama.model == "gemma4:12b-mlx"
    assert Selene(parse_args(["--model", "clef-flash"])).ollama.model == "clef-flash"


async def test_switching_to_a_missing_model_goes_red_and_isnt_remembered():
    fake = FakeOllama()
    app = app_with(fake)
    async with app.run_test(size=(120, 34)):
        assert await wait_for(lambda: app.state["ollama"] == "ready")
        app.command("model nope:7b")
        assert await wait_for(lambda: app.state["ollama"] == "missing")
        assert "not installed" in str(app.query_one("#status").render())
        assert fake.warmed == [] and settings.load()["model"] == ""


async def test_clear_empties_the_ghci_log():
    app = app_with(FakeOllama())
    async with app.run_test(size=(120, 34)):
        app.log_line("some noise")
        assert "some noise" in log_text(app)
        app.command("clear")
        assert app.query_one("#log").lines == []
        app.log_line("after")
        assert log_text(app) == "after"
