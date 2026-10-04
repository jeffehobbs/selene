"""Line numbers are zero-padded so the code never shifts at line 10."""

from textual.app import App

from selene.app import PatternEditor


class Host(App):
    def compose(self):
        yield PatternEditor("", id="code", show_line_numbers=True, soft_wrap=False)


def gutter(editor, y):
    return editor.render_line(y).text[:editor.gutter_width]


async def test_numbers_are_zero_padded_and_the_gutter_holds_still():
    app = Host()
    async with app.run_test(size=(60, 20)) as pilot:
        editor = app.query_one(PatternEditor)
        editor.load_text("d1 $ s \"bd\"\nd2 $ s \"hh\"\nd3 $ s \"cp\"")
        await pilot.pause()
        short = editor.gutter_width
        assert [gutter(editor, y).strip() for y in range(3)] == ["01", "02", "03"]
        code_x = editor.render_line(0).text.index("d1")

        editor.load_text("\n".join(f'd{i} $ s "bd"' for i in range(1, 13)))
        await pilot.pause()
        assert editor.gutter_width == short  # no shift going past line 9
        assert gutter(editor, 8).strip() == "09" and gutter(editor, 9).strip() == "10"
        assert editor.render_line(0).text.index("d1") == code_x
        assert editor.render_line(9).text.index("d10") == code_x
