"""Flow director harness: an hour of Flow on a virtual clock, in well under a second."""

import pytest

from selene.flow import (CONTINUOUS, DEPTHS, EXIT_SECONDS, FLOOR_RAMP, MIN_DENSITY, NEUTRAL,
                         PHRASE, STEPPED, FlowDirector, band, ctrl_name, smootherstep)

TICK = 0.1
CPS = 0.5  # simulated Tidal: cycle = t * CPS
# Melodic layers (low, high, mid), so the density budget has something to move.
CODE = {
    "d1": 'd1 $ n "0 ~ 0 3" # s "jvbass"',
    "d2": 'd2 $ s "arpy*8"',
    "d3": 'd3 $ n "0 3 7" # s "superpiano"',
}
DRUMS = {"d4": 'd4 $ s "808bd*4"', "d5": 'd5 $ s "~ hh*2"'}


def run(director, seconds, start=0.0, on_event=None, steps=None):
    """Tick the director; return per-tick snapshots and every event."""
    snaps, events = [], []
    t = start
    while t < start + seconds:
        _, st, evs = director.tick(t, t * CPS)
        if steps is not None:
            steps += [(t, s) for s in st]
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


@pytest.mark.parametrize("seed,depth", [(s, d) for s in (1, 2, 3) for d in DEPTHS])
def test_an_hour_has_no_edges(seed, depth):
    d = FlowDirector(seed=seed, depth=depth)
    d.rebase(CODE, now=0, player=False)
    snaps, _ = run(d, 3600, on_event=ack_evolves(d))
    # Smootherstep's steepest slope is 1.875/duration: no continuous control
    # may move more than that per tick, given the depth's shortest ramp.
    shortest = max(FLOOR_RAMP, DEPTHS[depth].ramp[0]) if depth > 1 else 20
    for (prev, cur) in zip(snaps, snaps[1:]):
        for key, v in cur.items():
            if key in prev:
                lo, hi = band(key[1], 1.0)
                assert abs(v - prev[key]) <= 1.875 * (hi - lo) * TICK / FLOOR_RAMP + 1e-9, key
    assert min(dur for _, c, dur in d.ramp_log if c != "gain" or dur != EXIT_SECONDS) >= FLOOR_RAMP
    if depth == 1:  # the original promise
        assert min(dur for *_, dur in d.ramp_log) >= shortest
    # Something actually moved, on every orbit.
    for orbit in CODE:
        assert len({round(s[(orbit, "gain")], 3) for s in snaps}) > 20


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
def test_density_budget_is_zero_sum(seed):
    d = FlowDirector(seed=seed, depth=1)
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
    d = FlowDirector(seed=7, depth=5)
    d.rebase(CODE, now=0, player=False)
    run(d, 300, on_event=ack_evolves(d))
    d.set_muted({"d2"}, now=300)
    frozen = dict(d.orbits["d2"].values)
    steps = []
    snaps, events = run(d, 3600, start=300, on_event=ack_evolves(d), steps=steps)
    for s in snaps:
        for c, v in frozen.items():
            assert s[("d2", c)] == v
    assert not [e for _, e in events if e.orbit == "d2" and e.kind in ("evolve", "silence")]
    # Steps scheduled before the mute may still land; none are started after it.
    late = [s for t, s in steps if s.name.endswith("2") and t > 300 + 60]
    assert not late, late


def test_evolve_spacing_and_yield_to_player():
    d = FlowDirector(seed=11, depth=1)
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
    d = FlowDirector(seed=3, depth=5)
    d.rebase(CODE, now=0, player=False)
    _, events = run(d, 3 * 3600)  # never acknowledged
    assert len([e for _, e in events if e.kind == "evolve"]) == 1


def test_retire_fades_to_silence_then_reports():
    d = FlowDirector(seed=0, depth=1)
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
    d = FlowDirector(seed=5, depth=1)
    d.rebase(CODE, now=0, player=False)
    d.rebase({**CODE, "d4": 'd4 $ s "arpy*2"'}, now=10, player=False)
    d.evolved("d4", "add", ok=True, now=10)
    assert d.orbits["d4"].values["gain"] == 0.0
    snaps, _ = run(d, 60, start=10)
    gains = [s[("d4", "gain")] for s in snaps][:int(45 / TICK)]  # the swell itself
    assert gains[0] < 0.01 and gains[-1] > 0.99
    assert all(b >= a - 1e-9 for a, b in zip(gains, gains[1:]))


def test_exit_glides_home_and_sends_exact_neutral():
    d = FlowDirector(seed=9, depth=5)
    d.rebase(CODE, now=0, player=False)
    run(d, 900, on_event=ack_evolves(d))
    home = d.start_exit(now=900, cycle=900 * CPS)
    # Every stepped control goes home on the very next boundary.
    assert {s.name for s in home} == {ctrl_name(c, o) for o in CODE for c in STEPPED}
    assert all(s.value == STEPPED[s.name[3:-1]] and s.cycle == 451 for s in home)
    sent = {}
    t = 900.0
    while not d.exited:
        updates, steps, events = d.tick(t, t * CPS)
        assert not steps and not [e for e in events if e.kind == "evolve"]
        sent.update(updates)
        t = round(t + TICK, 6)
    assert t - 900 <= EXIT_SECONDS + TICK * 2
    for orbit in CODE:
        for c, (neutral, _, _) in CONTINUOUS.items():
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


def final_values(steps):
    """Last value scheduled for each stepped control, by cycle order."""
    last = {}
    for _, s in sorted(steps, key=lambda ts: (ts[1].cycle, ts[0])):
        last[s.name] = s.value
    return last


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_deep_flow_steps_land_on_boundaries_and_come_home(seed):
    d = FlowDirector(seed=seed, depth=5)
    d.rebase(CODE, now=0, player=False)
    steps, events = [], []
    _, events = run(d, 3600, on_event=ack_evolves(d), steps=steps)
    assert len(steps) > 100, "depth 5 should be busy"
    for t, s in steps:
        assert s.cycle > t * CPS, "scheduled in the past"
        assert s.cycle == int(s.cycle)
    # Mutations and drops start on phrase boundaries; fills/rolls start the
    # cycle before one.
    starts = [s for _, s in steps if s.value != STEPPED[s.name[3:-1]]]
    assert all(s.cycle % PHRASE in (0, PHRASE - 1) for s in starts)
    # Every deviation is scheduled to come home.
    assert all(v == STEPPED[n[3:-1]] for n, v in final_values(steps).items())
    kinds = {e.gesture for _, e in events if e.kind == "log"}
    assert {"drop", "fill", "roll", "throw"} <= kinds, kinds


def test_gentle_depths_have_no_color_rhythm_or_events():
    for depth in (1, 2):
        d = FlowDirector(seed=4, depth=depth)
        d.rebase(CODE, now=0, player=False)
        steps = []
        snaps, events = run(d, 1800, on_event=ack_evolves(d), steps=steps)
        assert not steps
        for s in snaps:
            for o in CODE:
                for c in ("drive", "crush", "send"):
                    assert s[(o, c)] == CONTINUOUS[c][0]
        assert not [e for _, e in events if e.kind == "log" and e.gesture in
                    ("drop", "fill", "roll", "throw")]


def test_depth_moves_faster_and_wider():
    def gain_travel(depth):
        d = FlowDirector(seed=6, depth=depth)
        d.rebase(CODE, now=0, player=False)
        snaps, _ = run(d, 1800, on_event=ack_evolves(d))
        g = [s[("d1", "gain")] for s in snaps]
        return sum(abs(b - a) for a, b in zip(g, g[1:])), max(g) - min(g)
    travel1, span1 = gain_travel(1)
    travel5, span5 = gain_travel(5)
    assert travel5 > 3 * travel1 and span5 > span1


def test_lowering_depth_sends_everything_home():
    d = FlowDirector(seed=2, depth=5)
    d.rebase(CODE, now=0, player=False)
    run(d, 600, on_event=ack_evolves(d))
    home = d.set_depth(2, now=600, cycle=300.2)
    assert {s.name for s in home} == {ctrl_name(c, o) for o in CODE for c in STEPPED}
    assert all(s.cycle == 301 for s in home)
    steps = []
    snaps, _ = run(d, 600, start=600, on_event=ack_evolves(d), steps=steps)
    assert not steps
    for o in CODE:
        for c in ("drive", "crush", "send"):
            assert snaps[-1][(o, c)] == CONTINUOUS[c][0]


@pytest.mark.parametrize("depth", [1, 3, 5])
def test_drum_layers_are_never_thinned(depth):
    from selene.flow import is_drums
    assert is_drums(DRUMS["d4"]) and is_drums(DRUMS["d5"]) and not is_drums(CODE["d2"])
    d = FlowDirector(seed=8, depth=depth)
    d.rebase({**CODE, **DRUMS}, now=0, player=False)
    snaps, events = run(d, 3600, on_event=ack_evolves(d))
    for s in snaps:
        assert s[("d4", "thin")] == 0 and s[("d5", "thin")] == 0
    # The melodic layers still trade density (conservation itself is covered
    # by test_density_budget_is_zero_sum; here a retirement re-settles it).
    assert any(s[("d1", "thin")] > 0 for s in snaps)
    budget = [e.detail for _, e in events if e.kind == "log" and
              e.gesture in ("transfer", "dropout", "spotlight", "tilt")]
    assert budget and not [g for g in budget if "d4" in g or "d5" in g]


def test_a_layer_that_becomes_drums_glides_back_to_every_hit():
    d = FlowDirector(seed=3, depth=5)
    d.rebase(CODE, now=0, player=False)
    run(d, 400, on_event=ack_evolves(d))
    thinned = max(CODE, key=lambda o: d.orbits[o].values["thin"])
    assert d.orbits[thinned].values["thin"] > 0
    d.rebase({**CODE, thinned: f'{thinned} $ s "bd*4"'}, now=400, player=True)
    snaps, _ = run(d, 30, start=400, on_event=ack_evolves(d))
    thins = [s[(thinned, "thin")] for s in snaps]
    assert thins[-1] == 0 and all(b <= a + 1e-9 for a, b in zip(thins, thins[1:]))


def test_flow_explains_itself():
    """Every gesture comes with words the player can read, with timing for
    anything stepped, and noticeable drift is reported."""
    d = FlowDirector(seed=12, depth=5)
    d.rebase({**CODE, **DRUMS}, now=0, player=False)
    _, events = run(d, 1800, on_event=ack_evolves(d))
    logs = [e for _, e in events if e.kind == "log"]
    by = {}
    for e in logs:
        by.setdefault(e.gesture, []).append(e.detail)
    assert {"transfer", "drop", "fill", "roll", "throw"} <= set(by)
    assert {"rot", "ply", "rate", "rev"} & set(by), "rhythm mutations aren't logged"
    assert all("% of its hits" in t for t in by["transfer"])
    for g in ("drop", "rot", "ply", "rate", "rev"):
        for text in by.get(g, []):
            assert "cycles " in text and "–" in text, text  # when it happens
    for text in by["fill"] + by["roll"] + by["throw"]:
        assert "cycle " in text, text
    drift = [e for _, e in events if e.kind == "drift"]
    assert drift and {e.detail for e in drift} <= {
        "louder", "quieter", "brighter", "darker", "wetter", "drier", "grittier",
        "cleaner", "crunchier", "smoother", "more echo", "less echo"}
