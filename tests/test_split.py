"""The divider between the log and the pattern drags, clamps, resets, and is remembered."""

import json

from conftest import TEST_BOOT, needs_ghci

from selene import settings
from selene.app import Selene, parse_args


def widths(app):
    return app.query_one("#log").outer_size.width, app.query_one("#code").outer_size.width


def test_settings_roundtrip_and_defaults(private_settings):
    assert settings.load() == settings.DEFAULTS
    settings.save(split=31.5)
    settings.save(lanes=False)
    assert json.loads(private_settings.read_text()) == {"split": 31.5, "lanes": False}
    private_settings.write_text("not json")
    assert settings.load() == settings.DEFAULTS


@needs_ghci
async def test_drag_clamp_reset_and_remember(private_settings):
    app = Selene(parse_args(["--boot", str(TEST_BOOT)]))
    async with app.run_test(size=(120, 34)) as pilot:
        await pilot.pause()
        log, code = widths(app)
        assert abs(log - code) <= 2  # starts in the middle

        # Drag the divider to column 30.
        await pilot.mouse_down("#divider")
        await pilot.hover("#main", offset=(30, 5))
        await pilot.mouse_up("#main", offset=(30, 5))
        await pilot.pause()
        log, code = widths(app)
        assert 29 <= log <= 31 and code > 80
        assert json.loads(private_settings.read_text())["split"] == app.split

        # It can't squeeze a panel below 20 columns.
        await pilot.mouse_down("#divider")
        await pilot.hover("#main", offset=(2, 5))
        await pilot.mouse_up("#main", offset=(2, 5))
        await pilot.pause()
        assert widths(app)[0] >= 20
        app.command("split 99")
        await pilot.pause()
        assert widths(app)[1] >= 20

        # /split sets a share; double-click puts it back in the middle.
        app.command("split 70")
        await pilot.pause()
        assert app.split == 70.0
        await pilot.double_click("#divider")
        await pilot.pause()
        assert app.split == 50.0
        app.command("split 35")
        await pilot.press("ctrl+l")  # lanes off: remembered too
        await pilot.pause()
        await app.action_quit()

    # A new launch comes back the same way.
    again = Selene(parse_args(["--boot", str(TEST_BOOT)]))
    async with again.run_test(size=(120, 34)) as pilot:
        await pilot.pause()
        assert again.split == 35.0 and again.lanes_on is False
        log, code = widths(again)
        assert abs(log - 0.35 * again.query_one("#main").size.width) <= 1.5
        await again.action_quit()
