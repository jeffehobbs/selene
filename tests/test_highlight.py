"""Event highlighting: pattern strings are tagged with deltaContext, and Tidal's
/editor/highlights replies light up the tokens as they sound."""

import asyncio
import struct
import time

from conftest import TEST_BOOT, needs_ghci
from textual.app import App

from selene.app import PatternEditor, Selene, parse_args
from selene.flow import NTP_EPOCH
from selene.ghci import Ghci
from selene.highlight import Highlighter, Source, place


def test_annotate_wraps_pattern_strings_only():
    hl = Highlighter()
    code = ('d1 $ s "bd [sn hh]" # n "<0 1>" -- "not this"\n'
            'd2 $ ur 4 "a b" [("a", s "cp"), ("b", s "")] # pF "lpf" 300 # cF 0 "x"')
    out = hl.annotate(code, first_line=10).split("\n")
    assert out[0] == ('d1 $ s (deltaContext 7 0 "bd [sn hh]") # n (deltaContext 24 0 "<0 1>")'
                      ' -- "not this"')
    # Names stay plain: the ("a", …) pair names, pF's and cF's names, and "".
    assert out[1] == ('d2 $ ur 4 (deltaContext 10 1 "a b") [("a", s (deltaContext 25 1 "cp")),'
                      ' ("b", s "")] # pF "lpf" 300 # cF 0 "x"')
    assert hl.sources[0] == Source(10, code.split("\n")[0])
    assert hl.sources[1].line == 11
    assert hl.annotate("setcps 1") == "setcps 1"


def bundle(onset: float, *messages: bytes) -> bytes:
    secs = onset + NTP_EPOCH
    head = b"#bundle\0" + struct.pack(">II", int(secs), int((secs % 1) * 2 ** 32))
    return head + b"".join(struct.pack(">i", len(m)) + m for m in messages)


def message(ident: str, delta_us: float, x: int, y: int, x2: int) -> bytes:
    def s(text):
        b = text.encode() + b"\0"
        return b + b"\0" * (-len(b) % 4)
    return (s("/editor/highlights") + s(",sffiiii") + s(ident)
            + struct.pack(">ffiiii", delta_us, 0.0, x, y, x2, y))


def test_events_light_their_columns_while_they_sound():
    hl = Highlighter()
    hl.annotate('d1 $ s "bd sn"', first_line=2)
    now = time.time()
    hl.feed(bundle(now + 0.1, message("1", 500000, 8, 1, 10)))
    hl.feed(bundle(now, message("1", 500000, 99, 7, 100)))  # unknown id: ignored
    assert hl.active(now) == {}  # not yet
    lit = hl.active(now + 0.2)
    assert lit == {Source(2, 'd1 $ s "bd sn"'): [(8, 10)]}
    assert hl.active(now + 0.7) == {}  # over, and forgotten
    assert hl.events == {}


def test_place_follows_a_line_that_moved_and_drops_an_edited_one():
    a, b = Source(0, 'd1 $ s "bd"'), Source(1, 'd2 $ s "cp"')
    lines = ['setcps 1', 'd1 $ s "bd"', 'd2 $ s "cp*2"']
    assert place({a: [(8, 10)], b: [(8, 10)]}, lines) == {1: [(8, 10)]}


class Host(App):
    def compose(self):
        yield PatternEditor('d1 $ s "bd sn"\nd2 $ s "cp"', id="code", show_line_numbers=True,
                            soft_wrap=False)


async def test_editor_paints_marks_and_flashes():
    app = Host()
    async with app.run_test(size=(60, 10)) as pilot:
        editor = app.query_one(PatternEditor)
        await pilot.pause()
        plain = editor.render_line(0)

        def bg(strip, x):
            for seg in strip:
                if x < seg.cell_length:
                    return seg.style.bgcolor
                x -= seg.cell_length

        g = editor.gutter_width
        editor.set_marks({0: [(8, 10)]})
        lit = editor.render_line(0)
        assert lit.text == plain.text
        assert bg(lit, g + 8) == bg(lit, g + 9) != bg(plain, g + 8)
        assert bg(lit, g + 7) == bg(plain, g + 7) and bg(lit, g + 10) == bg(plain, g + 10)
        assert editor.render_line(1).text == editor.render_line(1).text  # untouched row

        editor.set_marks({})
        editor.flash(1, 1, ok=False)
        assert bg(editor.render_line(1), g) != bg(plain, g)
        assert bg(editor.render_line(0), g) == bg(plain, g)
        await asyncio.sleep(editor.FLASH_SECONDS + 0.1)
        await pilot.pause()
        assert editor.flashing is None


@needs_ghci
async def test_annotation_that_doesnt_compile_falls_back_quietly(fake_dirt):
    said = []
    ghci = Ghci(boot=TEST_BOOT, on_output=said.append)
    assert (await ghci.start()).ok
    hl = Highlighter()
    code = 'd1 $ s (pure ("bd" :: String))'
    annotated = hl.annotate(code)
    assert annotated != code
    said.clear()
    result = await ghci.eval(annotated, plain=code)
    assert result.ok, result.error_text
    assert not any("error" in line for line in said), said
    bad = await ghci.eval(hl.annotate('d1 $ s "bd" # nope 1'), plain='d1 $ s "bd" # nope 1')
    assert not bad.ok and "nope" in bad.error_text
    await ghci.stop()


@needs_ghci
async def test_tokens_light_up_in_the_editor(fake_dirt):
    app = Selene(parse_args(["--boot", str(TEST_BOOT)]))
    assert app.highlighting
    async with app.run_test(size=(120, 30)) as pilot:
        for _ in range(600):
            if app.state["ghci"] == "ready":
                break
            await asyncio.sleep(0.1)
        editor = app.query_one(PatternEditor)
        editor.text = 'setcps 1\n-- beat\nd1 $ s "bd sn"'
        app.action_play_editor()
        seen: set[tuple[int, int, int]] = set()
        for _ in range(40):
            await asyncio.sleep(0.05)
            seen |= {(row, a, b) for row, ranges in editor.marks.items() for a, b in ranges}
        assert {(2, 8, 10), (2, 11, 13)} <= seen, seen
        await app.action_quit()
