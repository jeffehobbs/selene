"""The quiet log: GHCi's and SuperCollider's routine output is hidden, while
green, red and selene's own lines show; /quiet brings everything back."""

from selene import settings
from selene.app import Selene, parse_args


def shown(app) -> str:
    return "\n".join(strip.text for strip in app.query_one("#log").lines)


async def test_quiet_log_hides_chatter_and_toggles():
    app = Selene(parse_args(["--ghci", "/nonexistent/ghci"]))
    assert app.quiet  # on by default
    async with app.run_test(size=(140, 30)) as pilot:
        app._ghci_line("Listening for external controls on 127.0.0.1:6010")
        app._ghci_line("Connected to SuperDirt.")
        app._ghci_line("<interactive>:3:14: error: Variable not in scope: nope")
        app._ghci_line("GHCi exited")
        app._dirt_line("Booting server 'localhost' on address 127.0.0.1:57110.")
        app._dirt_line("ERROR: Message 'foo' not understood.")
        app._dirt_line("the input device's sample rate doesn't match the output's")
        app.log_line("» a slow dub groove", "bold cyan")
        await pilot.pause()
        text = shown(app)
        for kept in ("Connected to SuperDirt", "Variable not in scope", "GHCi exited",
                     "ERROR: Message", "sample rate doesn't match", "slow dub groove"):
            assert kept in text, kept
        assert "Listening for external" not in text and "Booting server" not in text

        app.command("quiet")
        await pilot.pause()
        assert not app.quiet and settings.load()["quiet"] is False
        text = shown(app)
        assert "Listening for external" in text and "Booting server" in text  # earlier lines too
        assert text.index("Listening") < text.index("slow dub groove")  # in their places

        app.command("quiet")
        await pilot.pause()
        assert "Booting server" not in shown(app) and "slow dub groove" in shown(app)

        app.command("clear")
        app.command("quiet")
        await pilot.pause()
        assert "slow dub groove" not in shown(app)  # /clear forgets the hidden lines too
        await app.action_quit()

    assert Selene(parse_args(["--ghci", "/nonexistent/ghci"])).quiet is False  # remembered
