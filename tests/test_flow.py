"""Flow director harness: an hour of Flow on a virtual clock, in well under a second."""

import pytest

from selene.flow import (CONTROLS, EXIT_SECONDS, MIN_DENSITY, SHORTEST_RAMP, FlowDirector,
                         ctrl_name, smootherstep)

TICK = 0.1
CODE = {
    "d1": 'd1 $ s "808bd*4"',
    "d2": 'd2 $ s "~ hh*2"',
    "d3": 'd3 $ n "0 3 7" # s "superpiano"',
}


def run(director, seconds, start=0.0, on_event=None):
    """Tick the director; return per-tick snapshots and every event."""
    snaps, events = [], []
    t = start
    while t < start + seconds:
        _, evs = director.tick(t)
        for e in evs:
            events.append((t, e))
            if on_event:
                on_event(t, e)
        snaps.append({(o, c): v for o, orb in director.orbits.items()
                      for c, v in orb.values.items()})
        t = round(t + TICK, 6)
    return snaps, events


def ack_evolves(director):
    def on_event(t, e):
        if e.kind == "evolve":
            director.evolved(e.orbit, e.detail, ok=False, now=t)
    return on_event


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
def test_an_hour_has_no_edges(seed):
    d = FlowDirector(seed=seed)
    d.rebase(CODE, now=0, player=False)
    snaps, _ = run(d, 3600, on_event=ack_evolves(d))
    # Smootherstep's steepest slope is 1.875/duration; with the 9 s floor no
    # control may move more than this per tick.
    for (prev, cur) in zip(snaps, snaps[1:]):
        for key, v in cur.items():
            if key in prev:
                lo, hi, _ = CONTROLS[key[1]]
                assert abs(v - prev[key]) <= 1.875 * (hi - lo) * TICK / SHORTEST_RAMP + 1e-9, key
    assert min(dur for _, _, dur in d.ramp_log) >= SHORTEST_RAMP
    # Something actually moved, on every orbit.
    for orbit in CODE:
        assert len({round(s[(orbit, "gain")], 3) for s in snaps}) > 20


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
def test_density_budget_is_zero_sum(seed):
    d = FlowDirector(seed=seed)
    d.rebase(CODE, now=0, player=False)
    snaps, events = run(d, 3600, on_event=ack_evolves(d))
    settled = int(200 / TICK)  # the one-time settle ramp is over by then
    assert abs(sum(1 - snaps[settled][(o, "thin")] for o in CODE) - 0.8 * len(CODE)) < 1e-6
    sums = [sum(1 - s[(o, "thin")] for o in CODE) for s in snaps[settled:]]
    assert max(sums) - min(sums) < 1e-6, "density leaked"
    for s in snaps:
        for o in CODE:
            assert MIN_DENSITY - 1e-9 <= 1 - s[(o, "thin")] <= 1 + 1e-9
    gestures = [e.detail for _, e in events if e.kind == "log"]
    assert len(gestures) >= 20, gestures  # the budget is busy, not idle


def test_muted_orbits_are_never_touched():
    d = FlowDirector(seed=7)
    d.rebase(CODE, now=0, player=False)
    run(d, 300, on_event=ack_evolves(d))
    d.set_muted({"d2"}, now=300)
    frozen = dict(d.orbits["d2"].values)
    snaps, events = run(d, 3600, start=300, on_event=ack_evolves(d))
    for s in snaps:
        for c, v in frozen.items():
            assert s[("d2", c)] == v
    assert not [e for _, e in events if e.orbit == "d2" and e.kind in ("evolve", "silence")]


def test_evolve_spacing_and_yield_to_player():
    d = FlowDirector(seed=11)
    d.rebase(CODE, now=0, player=False)
    _, events = run(d, 3 * 3600, on_event=ack_evolves(d))
    times = [t for t, e in events if e.kind == "evolve"]
    assert len(times) >= 10
    gaps = [b - a for a, b in zip(times, times[1:])]
    assert min(gaps) >= 5 * 60 * 0.8 - TICK
    # The player acting pushes the next structural change back.
    d.rebase(CODE, now=3 * 3600, player=True)
    _, events = run(d, 119, start=3 * 3600, on_event=ack_evolves(d))
    assert not [e for _, e in events if e.kind == "evolve"]


def test_no_evolve_while_one_is_pending():
    d = FlowDirector(seed=3)
    d.rebase(CODE, now=0, player=False)
    _, events = run(d, 3 * 3600)  # never acknowledged
    assert len([e for _, e in events if e.kind == "evolve"]) == 1


def test_retire_fades_to_silence_then_reports():
    d = FlowDirector(seed=0)
    four = {**CODE, "d4": 'd4 $ s "cp(3,8)"'}
    d.rebase(four, now=0, player=False)
    retired = []
    def on_event(t, e):
        if e.kind == "evolve":
            d.evolved(e.orbit, e.detail, ok=False, now=t)
        if e.kind == "silence":
            retired.append((t, e.orbit, d.orbits[e.orbit].values["gain"]))
    run(d, 6 * 3600, on_event=on_event)
    assert retired, "no retirement in six hours with four orbits"
    assert all(g == 0.0 for _, _, g in retired)


def test_add_swells_in_from_silence():
    d = FlowDirector(seed=5)
    d.rebase(CODE, now=0, player=False)
    d.rebase({**CODE, "d4": 'd4 $ s "arpy*2"'}, now=10, player=False)
    d.evolved("d4", "add", ok=True, now=10)
    assert d.orbits["d4"].values["gain"] == 0.0
    snaps, _ = run(d, 60, start=10)
    gains = [s[("d4", "gain")] for s in snaps][:int(45 / TICK)]  # the swell itself
    assert gains[0] < 0.01 and gains[-1] > 0.99
    assert all(b >= a - 1e-9 for a, b in zip(gains, gains[1:]))


def test_exit_glides_home_and_sends_exact_neutral():
    d = FlowDirector(seed=9)
    d.rebase(CODE, now=0, player=False)
    run(d, 900, on_event=ack_evolves(d))
    d.start_exit(now=900)
    sent = {}
    t = 900.0
    while not d.exited:
        updates, events = d.tick(t)
        assert not [e for e in events if e.kind == "evolve"]
        sent.update(updates)
        t = round(t + TICK, 6)
    assert t - 900 <= EXIT_SECONDS + TICK * 2
    for orbit in CODE:
        for c, (_, _, neutral) in CONTROLS.items():
            assert d.orbits[orbit].values[c] == neutral
            name = ctrl_name(c, orbit)
            if name in sent:
                assert sent[name] == neutral


def test_rebase_resets_departed_orbits():
    d = FlowDirector(seed=1)
    d.rebase(CODE, now=0, player=False)
    resets = d.rebase({"d1": CODE["d1"]}, now=5)
    names = {n for n, _ in resets}
    assert ctrl_name("gain", "d2") in names and ctrl_name("thin", "d3") in names
    assert set(d.orbits) == {"d1"}


def test_smootherstep_ends_are_flat():
    eps = 1e-4
    assert smootherstep(0) == 0 and smootherstep(1) == 1
    assert (smootherstep(eps) - smootherstep(0)) / eps < 1e-6
    assert (smootherstep(1) - smootherstep(1 - eps)) / eps < 1e-6
