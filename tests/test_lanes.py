"""Lanes: events land at their place in the phrase, when they sound."""

import asyncio

from conftest import TEST_BOOT, needs_ghci

from selene.app import OrbitChip, Selene, parse_args
from selene.lanes import LaneModel, LaneStrip


def ev(pid, cycle, s="bd", **kw):
    return {"_id_": str(pid), "cycle": cycle, "s": s, "cps": 1.0, **kw}


def cells(text):
    """The lane's cells without the dividers or the sound names after them."""
    grid = text.plain[:text.plain.rindex("│") + 1]
    return grid.replace("│", "")


def test_events_appear_when_they_sound_at_their_place():
    m = LaneModel()
    for k in range(4):
        m.add(100 + k * 0.25, ev(1, 8 + k * 0.25))  # bd*4 in cycle 8 (phrase 2)
    m.advance(100.3)  # only the first two have sounded
    row = cells(m.row("d1", 4 * 16 + 5, cycle=8.3))
    assert row[0] == "█" and row[4] == "█"  # 16 steps a cycle: hits at 0 and 4
    assert row[8] != "█" and row[12] != "█"
    m.advance(101)
    row = cells(m.row("d1", 4 * 16 + 5, cycle=8.95))
    assert [row[i] for i in (0, 4, 8, 12)] == ["█"] * 4


def test_wipe_shows_last_pass_ahead_of_the_playhead():
    m = LaneModel()
    m.add(0, ev(2, 3.5, s="hh"))  # phrase 0, cycle 3.5
    m.add(0, ev(2, 4.0, s="hh"))  # phrase 1, cycle 4.0
    m.advance(1)
    text = m.row("d2", 4 * 8 + 5, cycle=4.5)  # phrase 1, half a cycle in
    plain = cells(text)
    assert plain[0] == "█"  # this pass
    assert plain[3 * 8 + 4] == "█"  # last pass's cycle-3.5 hit, still ahead of the playhead
    styles = {span.style for span in text.spans}
    assert any("dim" in str(st) for st in styles)


def test_melody_letters_level_shades_and_silent_events():
    m = LaneModel(synths={"superpiano"})
    m.add(0, ev(3, 0.0, s="superpiano", n=4.0))  # E
    m.add(0, ev(3, 0.5, s="superpiano", note=1.0))  # C#
    m.add(0, ev(1, 0.0, s="bd", gain=0.4))
    m.add(0, ev(1, 0.5, s="bd", gain=0.0))  # e.g. a Flow drop
    m.add(0, ev(4, 0.0, s="bd", n=3.0))  # sample index, not a pitch
    m.advance(1)
    piano = cells(m.row("d3", 4 * 8 + 5, cycle=0.9))
    assert piano[0] == "E" and piano[4] == "c"
    drums = cells(m.row("d1", 4 * 8 + 5, cycle=0.9))
    assert drums[0] == "▒" and drums[4] == "·"
    assert cells(m.row("d4", 4 * 8 + 5, cycle=0.9))[0] == "█"
    assert m.lit("d1", 0.05) and not m.lit("d1", 0.5)


def test_resolution_follows_width():
    m = LaneModel()
    assert len(cells(m.row("d1", 200, cycle=0))) == 128
    assert len(cells(m.row("d1", 4 * 16 + 5, cycle=0))) == 64
    assert len(cells(m.row("d1", 40, cycle=0))) == 32


async def wait_for(cond, timeout, step=0.05):
    for _ in range(int(timeout / step)):
        if cond():
            return True
        await asyncio.sleep(step)
    return False


@needs_ghci
async def test_lanes_in_the_app(fake_dirt):
    app = Selene(parse_args(["--boot", str(TEST_BOOT)]))
    async with app.run_test(size=(120, 34)) as pilot:
        assert await wait_for(lambda: app.state["ghci"] == "ready", 60)
        app.evaluate('setcps 1\nd1 $ s "bd*4"\nd2 $ s "hh*8"', source="editor")
        lanes = app.query_one("#lanes")
        assert await wait_for(lambda: len(list(lanes.query(LaneStrip))) == 2, 5)
        assert lanes.display and not app.query_one("#orbits").display
        await asyncio.sleep(2.2)  # a couple of cycles
        strips = {s.orbit: s for s in lanes.query(LaneStrip)}
        cycle = app.clock.cycle_at(__import__("time").time())
        row1 = cells(app.lane_model.row("d1", strips["d1"].size.width, cycle))
        row2 = cells(app.lane_model.row("d2", strips["d2"].size.width, cycle))
        # The last cycle that has fully sounded: bd on every quarter, hh on
        # every eighth (Tidal's cycle count runs from boot, so find it).
        steps = len(row1) // 4
        seg = (int(cycle) - 1) % 4
        cyc1 = row1[seg * steps:(seg + 1) * steps]
        cyc2 = row2[seg * steps:(seg + 1) * steps]
        assert [cyc1[k * steps // 4] for k in range(4)] == ["█"] * 4, row1
        assert cyc2.count("█") == 8, row2

        # The lane chips mute like the bar's did.
        await pilot.click(lanes.chips[1])
        assert await wait_for(lambda: app.muted == {"d2"}, 3)
        assert lanes.chips[1].has_class("muted")

        # ctrl+l hides the lanes and the chips return to the bar.
        await pilot.press("ctrl+l")
        await pilot.pause()
        assert not lanes.display and app.query_one("#orbits").display
        assert [c.orbit for c in app.query_one("#orbits").query(OrbitChip)] == ["d1", "d2"]
        await app.action_quit()


def test_sound_names_fill_the_rest_of_the_row():
    m = LaneModel(synths={"superpiano"})
    m.add(0, ev(1, 0.0, s="808bd", n=3.0))
    m.add(0, ev(1, 0.5, s="cp"))
    m.add(0, ev(2, 0.0, s="superpiano", n=7.0))
    m.add(0, ev(1, 0.25, s="hh", gain=0.0))  # silent: not listed
    m.advance(1)
    row = m.row("d1", 4 * 16 + 5 + 20, cycle=0.9).plain
    assert row.endswith("cp 808bd:3 ")
    assert "superpiano" in m.row("d2", 4 * 16 + 5 + 20, cycle=0.9).plain
    assert m.row("d1", 4 * 16 + 5, cycle=0.9).plain.endswith("│")  # no room, no names


@needs_ghci
async def test_rows_line_up_past_d9(fake_dirt):
    app = Selene(parse_args(["--boot", str(TEST_BOOT)]))
    async with app.run_test(size=(120, 34)) as pilot:
        assert await wait_for(lambda: app.state["ghci"] == "ready", 60)
        app.evaluate('d1 $ s "bd"\nd9 $ s "hh"\nd10 $ s "cp"\nd12 $ s "arpy"', source="editor")
        lanes = app.query_one("#lanes")
        assert await wait_for(lambda: len(list(lanes.query(LaneStrip))) == 4, 5)
        await pilot.pause()
        assert len({s.region.x for s in lanes.query(LaneStrip)}) == 1, "lanes start in different columns"
        assert len({c.outer_size.width for c in lanes.chips}) == 1
        assert [str(c.render()).rstrip() for c in lanes.chips] == [" d1", " d9", " d10", " d12"]
        await app.action_quit()


def test_events_without_an_id_fall_back_to_their_orbit():
    """xfadeIn (Flow evolutions, /fade) plays patterns Tidal never tagged with _id_."""
    m = LaneModel()
    m.add(0, {"cycle": 0.0, "s": "bd", "orbit": 2})
    m.add(0, {"cycle": 0.5, "s": "bd"})  # no id, no orbit: nowhere to put it
    m.advance(1)
    assert set(m.hits) == {"d3"} and len(m.hits["d3"]) == 1
