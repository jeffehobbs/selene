"""Flow — the patterns playing themselves.

Depth 1 is the family rule from Thrum, Meter and Counter: nothing is set, only
ramped; every gesture on its own prime clock; nothing announces itself. Each
step up the depth knob trades some of that for obviousness, because a
live-coding set wants the music to *move*:

  1  slow drift: level, tone, space, density (the original Flow)
  2  faster, wider drift
  3  + color (drive, crush, delay send) and rhythm mutations (rotate, stutter,
       half/double time, reverse) that land on phrase boundaries
  4  + arrangement events (drops, fills, rolls, delay throws) and bolder
       rewrites from the model
  5  everything faster, wider, more often

Continuous controls are still always ramped (smootherstep). Stepped controls
(rhythm, events) change exactly on a cycle boundary: Tidal reports its cycle
position through a tap target (see `TidalClock`), and the app sends each step
just before Tidal processes the frame holding that boundary.

The key and the tempo never move. Muted orbits are left alone. The player
always wins: anything they play rebases Flow and holds off structural changes.

The director is pure: `tick(now, cycle)` returns control updates, scheduled
steps and events; the app does the I/O. That's what lets tests run an hour of
Flow in a second.
"""

import math
import random
import socket
import struct
from dataclasses import dataclass, field

from .blocks import sound_names

# ── controls ──────────────────────────────────────────────────────────────

# Continuous controls: name -> (neutral, (lo, hi) at depth 1, (lo, hi) at depth 5).
# gain multiplies the model's level, tone is SuperDirt's djf (0.5 = neutral),
# space is added to room, thin feeds degradeBy, drive/crush/send fill in
# shape/crush/delay where the model didn't set them.
CONTINUOUS = {
    "gain": (1.0, (0.55, 1.1), (0.3, 1.2)),
    "tone": (0.5, (0.28, 0.66), (0.08, 0.88)),
    "space": (0.0, (0.0, 0.3), (0.0, 0.6)),
    "thin": (0.0, (0.0, 0.85), (0.0, 0.85)),
    "drive": (0.0, (0.0, 0.35), (0.0, 0.6)),
    "crush": (16.0, (7.0, 16.0), (4.0, 16.0)),
    "send": (0.0, (0.0, 0.3), (0.0, 0.55)),
}
DRIFTED = ("gain", "tone", "space")
COLOR = ("drive", "crush", "send")
# Stepped controls change only on cycle boundaries: name -> neutral.
STEPPED = {"rate": 1.0, "rot": 0.0, "ply": 1.0, "rev": 0.0, "gate": 1.0, "throw": 0.0}
NEUTRAL = {c: spec[0] for c, spec in CONTINUOUS.items()} | STEPPED

QUANTUM = 0.004  # send a continuous control only when it moved 0.4% of its band
FLOOR_RAMP = 3.0  # no continuous ramp is ever shorter, at any depth
EXIT_SECONDS = 12.0  # Flow off: continuous controls glide home over this long
PHRASE = 4  # cycles; rhythm mutations and events land on multiples of this

PRIMES = (11, 13, 17, 19, 23, 29, 31, 37, 41, 43)
BUDGET_PERIODS = {"transfer": 37, "dropout": 53, "tilt": 61, "spotlight": 71}
EVENT_PERIODS = {"drop": 47, "fill": 29, "roll": 31, "throw": 23}
MIN_DENSITY = 1 - CONTINUOUS["thin"][1][1]

# Drum kits and drum synths. Flow's density budget never thins these: losing
# random hits makes a beat sound broken, not varied.
DRUM_SOUNDS = frozenset("""
    bd sd sn hh hh27 oh ho hc cp cr cy rs rm cb ht mt lt lighter clak tok
    kicklinn linnhats clubkick hardkick popkick reverbkick realclaps stomp hand perc
    drum drumtraks dr dr2 dr55 dr_few gretsch ifdrums sequential jazz house techno
    tech electro1 hardcore gabba gabbaloud gabbalouder feel east tabla tabla2 tablex
    amencutup jungle breaks125 breaks152 breaks157 breaks165
    superkick supersnare superhat superclap super808 soskick sossnare soshats sostoms
""".split())

LOW_WORDS = ("bd", "kick", "bass", "808lt", "sub", "reese", "clubkick", "hardkick")
HIGH_WORDS = ("hh", "hat", "oh", "cy", "cr", "ride", "bell", "arpy", "glitch",
              "click", "tink", "chin", "superchip", "supervibe")


@dataclass(frozen=True)
class Depth:
    ramp: tuple[float, float]  # continuous ramp lengths, seconds
    gap: float  # multiplier on every gesture clock
    evolve_minutes: tuple[int, ...]
    xfade: int  # cycles to crossfade an evolved layer in
    yield_seconds: float  # structural changes held off after the player acts
    width: float  # 0 = depth-1 bands, 1 = widest bands
    lean: float  # chance a drift target is near neutral
    color: bool
    rhythm: bool
    events: bool
    bold: bool  # ask the model for clearly audible changes


DEPTHS = {
    1: Depth((20, 90), 1.0, (5, 7, 11), 16, 120, 0.0, 0.5, False, False, False, False),
    2: Depth((12, 50), 0.6, (3, 5, 7), 8, 90, 0.25, 0.4, False, False, False, False),
    3: Depth((8, 30), 0.4, (2, 3, 5), 8, 60, 0.5, 0.3, True, True, False, False),
    4: Depth((5, 18), 0.25, (1, 2, 3), 4, 45, 0.75, 0.2, True, True, True, True),
    5: Depth((3, 12), 0.15, (1, 2), 2, 30, 1.0, 0.1, True, True, True, True),
}
DEFAULT_DEPTH = 3


def smootherstep(x: float) -> float:
    x = min(1.0, max(0.0, x))
    return x * x * x * (x * (x * 6 - 15) + 10)


def ctrl_name(control: str, orbit: str) -> str:
    return f"fl_{control}{orbit[1:]}"


def band(control: str, width: float) -> tuple[float, float]:
    _, (lo1, hi1), (lo5, hi5) = CONTINUOUS[control]
    return lo1 + (lo5 - lo1) * width, hi1 + (hi5 - hi1) * width


@dataclass
class Ramp:
    start: float
    end: float
    t0: float
    duration: float

    def value(self, t: float) -> float:
        return self.start + (self.end - self.start) * smootherstep((t - self.t0) / self.duration)

    def done(self, t: float) -> bool:
        return t >= self.t0 + self.duration


@dataclass
class Step:
    name: str
    value: float
    cycle: int  # takes effect exactly at this cycle


@dataclass
class Event:
    kind: str  # "evolve" (detail vary/add), "silence" (a retired orbit), "log", "drift"
    orbit: str = ""
    detail: str = ""  # for "log"/"drift": what's happening, in words, for the player
    gesture: str = ""  # which gesture, for code: transfer, drop, mutate, ...


def hits(density: float) -> str:
    return f"~{round(density * 100)}% of its hits"


# Drift directions in words: control -> (going up, going down). Tone is djf:
# above 0.5 high-passes (thinner, brighter), below low-passes (darker).
DRIFT_WORDS = {
    "gain": ("louder", "quieter"), "tone": ("brighter", "darker"),
    "space": ("wetter", "drier"), "drive": ("grittier", "cleaner"),
    "crush": ("smoother", "crunchier"), "send": ("more echo", "less echo"),
}


@dataclass
class Orbit:
    values: dict = field(default_factory=lambda: {c: n for c, (n, _, _) in CONTINUOUS.items()})
    ramps: dict = field(default_factory=dict)
    sent: dict = field(default_factory=dict)
    due: dict = field(default_factory=dict)  # gesture -> next time
    stepped: dict = field(default_factory=lambda: dict(STEPPED))  # value once all steps land
    busy_until_cycle: float = -1  # a stepped mutation is in flight until then
    rest_until_cycle: float = -1  # and the layer is heard as written until then
    register: int = 0  # -1 low, 0 mid, +1 high
    drums: bool = False  # exempt from the density budget
    retiring: bool = False


class FlowDirector:
    def __init__(self, seed: int | None = None, depth: int = DEFAULT_DEPTH):
        self.rng = random.Random(seed)
        self.depth_level = depth
        self.orbits: dict[str, Orbit] = {}
        self.muted: set[str] = set()
        self.ramp_log: list[tuple[str, str, float]] = []  # (orbit, control, duration)
        self.now = 0.0
        self.exiting_until: float | None = None
        self.busy_until = 0.0  # budget gestures are atomic groups
        self.events_busy_cycle = -1.0  # one arrangement event at a time
        self.budget_due = {g: self._jitter(p) for g, p in BUDGET_PERIODS.items()}
        self.event_due = {g: self._jitter(p) for g, p in EVENT_PERIODS.items()}
        self.settled_for: frozenset = frozenset()
        self.evolve_due = self._evolve_gap()
        self.evolve_pending = False
        self.last_evolved: dict[str, float] = {}
        self.drift_events: list[Event] = []
        self.mutation_events: list[Event] = []

    @property
    def depth(self) -> Depth:
        return DEPTHS[self.depth_level]

    # ── lifecycle ─────────────────────────────────────────────────────────

    def rebase(self, code_orbits: dict[str, str], now: float, player: bool = True) -> list[tuple[str, float]]:
        """Adopt the playing orbits {dN: statement}. Returns controls to reset:
        an orbit that left must be neutral if it comes back (Tidal keeps cF state)."""
        resets = []
        for orbit in list(self.orbits):
            if orbit not in code_orbits:
                resets += [(ctrl_name(c, orbit), n) for c, n in NEUTRAL.items()]
                del self.orbits[orbit]
        for orbit, stmt in code_orbits.items():
            if orbit not in self.orbits:
                o = Orbit(register=register_of(stmt), drums=is_drums(stmt))
                for c in DRIFTED + COLOR + ("mutate",):
                    o.due[c] = now + self._jitter(self.rng.choice(PRIMES[:5]))
                self.orbits[orbit] = o
            else:
                o = self.orbits[orbit]
                o.register = register_of(stmt)
                if is_drums(stmt) and not o.drums:
                    # Became a drum layer while thinned: glide back to every hit.
                    o.drums = True
                    if o.values["thin"] or "thin" in o.ramps:
                        self._ramp(orbit, "thin", 0.0, now, self.depth.ramp[0])
                o.drums = is_drums(stmt)
        if player:
            self.evolve_due = max(self.evolve_due, now + self.depth.yield_seconds)
        return resets

    def set_muted(self, muted: set[str], now: float) -> None:
        for orbit in muted - self.muted:  # freeze where it is
            o = self.orbits.get(orbit)
            if o:
                for c in list(o.ramps):
                    o.values[c] = o.ramps.pop(c).value(now)
        self.muted = set(muted)

    def set_depth(self, level: int, now: float, cycle: float | None) -> list[Step]:
        """Change depth live. Anything the new depth doesn't allow goes home:
        color glides back, stepped controls return at the next boundary."""
        self.depth_level = level
        self.settled_for = frozenset()  # the budget's average depends on depth
        self.evolve_due = min(self.evolve_due, now + self._evolve_gap())
        for g in self.event_due:
            self.event_due[g] = min(self.event_due[g], now + self._jitter(EVENT_PERIODS[g] * self.depth.gap))
        for o in self.orbits.values():
            for c in DRIFTED + COLOR + ("mutate",):
                o.due[c] = min(o.due.get(c, now), now + self._jitter(5))
        steps = []
        if not self.depth.color:
            for orbit in self.orbits:
                for c in COLOR:
                    self._ramp(orbit, c, CONTINUOUS[c][0], now, self.depth.ramp[0])
        if not (self.depth.rhythm and self.depth.events):
            steps = self._steps_home(cycle)
        return steps

    def start_exit(self, now: float, cycle: float | None) -> list[Step]:
        """Flow off: glide continuous controls home; stepped ones go home on
        the next cycle boundary."""
        self.exiting_until = now + EXIT_SECONDS
        for orbit, o in self.orbits.items():
            for c, (n, _, _) in CONTINUOUS.items():
                cur = o.ramps[c].value(now) if c in o.ramps else o.values[c]
                o.ramps[c] = Ramp(cur, n, now, EXIT_SECONDS)
                self.ramp_log.append((orbit, c, EXIT_SECONDS))
        return self._steps_home(cycle)

    def resume(self, now: float) -> None:
        """Flow back on mid-exit: keep the glide home and drift on from there,
        rather than a fresh director whose idea of the controls is wrong."""
        self.exiting_until = None
        self.evolve_due = max(self.evolve_due, now + self.depth.yield_seconds)

    @property
    def exited(self) -> bool:
        return self.exiting_until is not None and self.now >= self.exiting_until

    def neutral_updates(self) -> list[tuple[str, float]]:
        return [(ctrl_name(c, orbit), n) for orbit in self.orbits for c, n in NEUTRAL.items()]

    # ── the clock ─────────────────────────────────────────────────────────

    def tick(self, now: float, cycle: float | None = None) -> tuple[list, list[Step], list[Event]]:
        """`cycle` is Tidal's current cycle position, or None if unknown (then
        nothing stepped is scheduled)."""
        self.now = now
        events: list[Event] = []
        steps: list[Step] = []
        if self.exiting_until is None:
            self.drift_events = []
            self._drift(now)
            events += self.drift_events
            events += self._budget(now)
            if cycle is not None:
                if self.depth.rhythm:
                    self.mutation_events = []
                    steps += self._mutate(now, cycle)
                    events += self.mutation_events
                if self.depth.events:
                    s, e = self._event(now, cycle)
                    steps += s
                    events += e
            events += self._evolve(now)
        updates = self._advance(now)
        for orbit, o in self.orbits.items():
            if o.retiring and "gain" not in o.ramps and o.values["gain"] == 0.0:
                o.retiring = False  # report once; the app silences and rebases
                events.append(Event("silence", orbit))
        return updates, steps, events

    def _advance(self, now: float) -> list[tuple[str, float]]:
        updates = []
        for orbit, o in self.orbits.items():
            for c, ramp in list(o.ramps.items()):
                o.values[c] = ramp.value(now)
                if ramp.done(now):
                    o.values[c] = ramp.end
                    del o.ramps[c]
            for c, v in o.values.items():
                neutral, (lo, hi), _ = CONTINUOUS[c]
                moved = abs(v - o.sent.get(c, neutral))
                # Quantized while moving; the exact end value once a ramp lands.
                if moved >= QUANTUM * (hi - lo) or (moved > 0 and c not in o.ramps):
                    o.sent[c] = v
                    updates.append((ctrl_name(c, orbit), round(v, 5)))
        return updates

    def _ramp(self, orbit: str, control: str, target: float, now: float, duration: float) -> None:
        o = self.orbits[orbit]
        cur = o.ramps[control].value(now) if control in o.ramps else o.values[control]
        if not (control == "gain" and o.retiring):  # a retiring orbit fades to 0
            lo, hi = band(control, self.depth.width)
            target = min(hi, max(lo, target))
        duration = max(FLOOR_RAMP, duration)
        o.ramps[control] = Ramp(cur, target, now, duration)
        self.ramp_log.append((orbit, control, duration))

    def _free(self, orbit: str) -> bool:
        return orbit not in self.muted and not self.orbits[orbit].retiring

    # ── continuous gestures ───────────────────────────────────────────────

    drift_events: list  # filled by _drift, handed out by tick

    def _drift(self, now: float) -> None:
        """Each orbit's level/tone/space (and color, at depth 3+) breathe on
        their own prime clocks."""
        controls = DRIFTED + (COLOR if self.depth.color else ())
        for orbit, o in self.orbits.items():
            if not self._free(orbit):
                continue
            for c in controls:
                if now < o.due.get(c, 0) or c in o.ramps:
                    continue
                neutral = CONTINUOUS[c][0]
                lo, hi = band(c, self.depth.width)
                if self.rng.random() < self.depth.lean:  # lean home
                    target = neutral + (self.rng.random() - 0.5) * 0.3 * (hi - lo)
                else:
                    target = self.rng.uniform(lo, hi)
                duration = self.rng.uniform(*self.depth.ramp)
                start = o.values[c]
                self._ramp(orbit, c, target, now, duration)
                o.due[c] = now + duration + self._jitter(self.rng.choice(PRIMES) * self.depth.gap)
                moved = o.ramps[c].end - start
                if abs(moved) >= 0.25 * (hi - lo):  # only moves you'd notice
                    up, down = DRIFT_WORDS[c]
                    self.drift_events.append(Event("drift", orbit, up if moved > 0 else down,
                                                   gesture=c))

    def _density(self, orbit: str) -> float:
        o = self.orbits[orbit]
        thin = o.ramps["thin"].end if "thin" in o.ramps else o.values["thin"]
        return 1 - thin

    def _set_densities(self, targets: dict[str, float], now: float) -> None:
        """One grouped ramp: same start and length, so the sum holds throughout."""
        lo, hi = self.depth.ramp
        duration = self.rng.uniform(max(lo, 0.33 * hi), hi)
        for orbit, d in targets.items():
            self._ramp(orbit, "thin", 1 - d, now, duration)
        self.busy_until = now + duration

    def _budget(self, now: float) -> list[Event]:
        free = [o for o in self.orbits if self._free(o) and not self.orbits[o].drums]
        if len(free) < 2 or now < self.busy_until:
            return []
        if self.settled_for != frozenset(free):
            # At full density nothing can move zero-sum. Once per set of
            # orbits (and depth), settle them to an average below full.
            self.settled_for = frozenset(free)
            mean = 0.8 - 0.2 * self.depth.width
            spread = {o: self.rng.uniform(-0.1, 0.1) for o in free}
            offset = sum(spread.values()) / len(free)
            self._set_densities({o: mean + v - offset for o, v in spread.items()}, now)
            return []
        for gesture, due in sorted(self.budget_due.items(), key=lambda kv: kv[1]):
            if now < due:
                continue
            self.budget_due[gesture] = now + self._jitter(BUDGET_PERIODS[gesture] * self.depth.gap)
            dens = {o: self._density(o) for o in free}
            targets = getattr(self, f"_g_{gesture}")(dens)
            if targets:
                say = targets.pop("_say")
                self._set_densities(targets, now)
                return [Event("log", detail=say, gesture=gesture)]
        return []

    def _g_transfer(self, dens: dict[str, float]) -> dict:
        give = [o for o, d in dens.items() if d > MIN_DENSITY + 0.1]
        if not give:
            return {}
        donor = self.rng.choice(give)
        takers = [o for o, d in dens.items() if o != donor and d < 0.98]
        if not takers:
            return {}
        taker = self.rng.choice(takers)
        amount = min(dens[donor] - MIN_DENSITY, 1 - dens[taker], self.rng.uniform(0.1, 0.35))
        return {donor: dens[donor] - amount, taker: dens[taker] + amount,
                "_say": f"{donor} thins to {hits(dens[donor] - amount)}, "
                        f"{taker} fills in to {hits(dens[taker] + amount)}"}

    def _g_dropout(self, dens: dict[str, float]) -> dict:
        orbit = self.rng.choice(list(dens))
        surplus = dens[orbit] - MIN_DENSITY
        targets = self._spread(dens, {orbit: MIN_DENSITY}, surplus)
        return ({**targets, "_say": f"{orbit} thins right down to {hits(MIN_DENSITY)}; "
                                    "the others fill in"} if targets else {})

    def _g_spotlight(self, dens: dict[str, float]) -> dict:
        orbit = self.rng.choice(list(dens))
        need = 1 - dens[orbit]
        if need < 0.05:
            return {}
        targets = self._spread(dens, {orbit: 1.0}, -need)
        return ({**targets, "_say": f"spotlight on {orbit}: every hit, the others thinner"}
                if targets else {})

    def _g_tilt(self, dens: dict[str, float]) -> dict:
        lows = [o for o in dens if self.orbits[o].register < 0]
        highs = [o for o in dens if self.orbits[o].register > 0]
        if not lows or not highs:
            return {}
        up, down, word = (highs, lows, "up") if self.rng.random() < 0.5 else (lows, highs, "down")
        amount = min(sum(dens[o] - MIN_DENSITY for o in down) / len(down),
                     sum(1 - dens[o] for o in up) / len(up), 0.2)
        if amount < 0.03:
            return {}
        targets = {o: dens[o] - amount for o in down}
        give = amount * len(down)
        targets |= {o: dens[o] + give / len(up) for o in up}
        if any(not MIN_DENSITY - 1e-9 <= d <= 1 + 1e-9 for d in targets.values()):
            return {}
        busier, sparser = (up, down)
        return {**targets, "_say": f"tilt: {' '.join(sorted(busier))} busier, "
                                   f"{' '.join(sorted(sparser))} sparser"}

    def _spread(self, dens: dict, fixed: dict, amount: float) -> dict:
        """Give `amount` (negative = take) to the orbits not in `fixed`, keeping
        each inside [MIN_DENSITY, 1]. Returns {} if it can't conserve the sum."""
        others = [o for o in dens if o not in fixed]
        if not others:
            return {}
        targets = dict(fixed)
        room = {o: (1 - dens[o]) if amount > 0 else (dens[o] - MIN_DENSITY) for o in others}
        total_room = sum(room.values())
        if total_room < abs(amount) - 1e-9:
            return {}
        for o in others:
            share = abs(amount) * room[o] / total_room if total_room else 0
            targets[o] = dens[o] + (share if amount > 0 else -share)
        return targets

    # ── stepped gestures (depth 3+) ───────────────────────────────────────

    def _step(self, orbit: str, control: str, value: float, at: int, back: int | None) -> list[Step]:
        """Set a stepped control at cycle `at`, and back to neutral at `back`."""
        o = self.orbits[orbit]
        name = ctrl_name(control, orbit)
        steps = [Step(name, value, at)]
        o.stepped[control] = value
        end = at
        if back is not None:
            steps.append(Step(name, STEPPED[control], back))
            o.stepped[control] = STEPPED[control]
            end = back
        o.busy_until_cycle = max(o.busy_until_cycle, end)
        return steps

    @staticmethod
    def _next_phrase(cycle: float) -> int:
        """The next phrase boundary far enough ahead to schedule into."""
        return int(math.ceil((cycle + 0.5) / PHRASE) * PHRASE)

    def _mutate(self, now: float, cycle: float) -> list[Step]:
        """Per orbit, on its own clock: a rhythmic mutation for a phrase or
        two, landing on a phrase boundary and returning on one."""
        steps = []
        for orbit, o in self.orbits.items():
            if not self._free(orbit) or now < o.due.get("mutate", 0) \
                    or cycle < max(o.busy_until_cycle, o.rest_until_cycle):
                continue
            o.due["mutate"] = now + self._jitter(self.rng.choice(PRIMES[2:]) * self.depth.gap * 2)
            at = self._next_phrase(cycle)
            back = at + PHRASE * self.rng.choice((1, 1, 2))
            # Then let the layer be heard as written for a while.
            rest = PHRASE * self.rng.choice((1, 2, 3)) * (2 if self.depth_level == 3 else 1)
            o.rest_until_cycle = back + rest
            kind = self.rng.choice(("rot", "ply", "rate", "rev"))
            if kind == "rot":
                k = self.rng.choice((1, 2, 3))
                steps += self._step(orbit, "rot", k, at, back)
                what = f"shifts its pattern {k} step{'s' * (k > 1)} along"
            elif kind == "ply":
                steps += self._step(orbit, "ply", 2.0, at, back)
                what = "stutters (every hit ×2)"
            elif kind == "rate":
                rate = self.rng.choice((0.5, 2.0))
                steps += self._step(orbit, "rate", rate, at, back)
                what = "goes half-time" if rate < 1 else "goes double-time"
            else:
                steps += self._step(orbit, "rev", 1.0, at, back)
                what = "plays backwards"
            self.mutation_events.append(
                Event("log", orbit, f"{orbit} {what}, cycles {at}–{back}", gesture=kind))
        return steps

    def _event(self, now: float, cycle: float) -> tuple[list[Step], list[Event]]:
        """Arrangement events (depth 4+), one at a time, on phrase boundaries."""
        free = [o for o in self.orbits if self._free(o)]
        if not free or cycle < self.events_busy_cycle:
            return [], []
        for kind, due in sorted(self.event_due.items(), key=lambda kv: kv[1]):
            if now < due:
                continue
            at = self._next_phrase(cycle)
            steps, say = getattr(self, f"_e_{kind}")(free, at)
            if steps:
                self.event_due[kind] = now + self._jitter(EVENT_PERIODS[kind] * self.depth.gap * 2)
                # Room to breathe before the next one: more of it at depth 4.
                rest = self.rng.choice((2, 3, 4) if self.depth_level == 4 else (1, 1, 2))
                self.events_busy_cycle = max(s.cycle for s in steps) + PHRASE * rest
                return steps, [Event("log", detail=say, gesture=kind)]
            self.event_due[kind] = now + self._jitter(5)  # nothing free: try again soon
        return [], []

    def _e_drop(self, free: list[str], at: int) -> tuple[list[Step], str]:
        """Everything but one layer falls out for a phrase, then slams back."""
        if len(free) < 2:
            return [], ""
        keep = self.rng.choice(free)
        back = at + PHRASE * self.rng.choice((1, 1, 2))
        steps = []
        for orbit in free:
            if orbit != keep:
                steps += self._step(orbit, "gate", 0.0, at, back)
        return steps, f"drop: only {keep} for cycles {at}–{back}, then everything back in"

    def _e_fill(self, free: list[str], at: int) -> tuple[list[Step], str]:
        """One layer at double time for the last cycle of the phrase."""
        idle = [o for o in free if self.orbits[o].busy_until_cycle < at - 1]
        if not idle:
            return [], ""
        orbit = self.rng.choice(idle)
        return (self._step(orbit, "rate", 2.0, at - 1, at),
                f"fill: {orbit} double-time in cycle {at - 1}, into the next phrase")

    def _e_roll(self, free: list[str], at: int) -> tuple[list[Step], str]:
        """One layer stutters (ply 3) into the boundary."""
        idle = [o for o in free if self.orbits[o].busy_until_cycle < at - 1]
        if not idle:
            return [], ""
        orbit = self.rng.choice(idle)
        return (self._step(orbit, "ply", 3.0, at - 1, at),
                f"roll: {orbit} stutters ×3 into cycle {at}")

    def _e_throw(self, free: list[str], at: int) -> tuple[list[Step], str]:
        """A dub delay throw on one layer for a cycle; the tail rings on."""
        orbit = self.rng.choice(free)
        return (self._step(orbit, "throw", 0.6, at, at + 1),
                f"throw: a dub echo on {orbit} at cycle {at}")

    def _steps_home(self, cycle: float | None) -> list[Step]:
        """Every stepped control back to neutral on the next boundary."""
        at = int(math.ceil((cycle or 0) + 0.5))
        steps = []
        for orbit, o in self.orbits.items():
            for c, n in STEPPED.items():
                steps.append(Step(ctrl_name(c, orbit), n, at))
            o.stepped = dict(STEPPED)
            o.busy_until_cycle = -1
        self.events_busy_cycle = -1
        return steps

    # ── structural ────────────────────────────────────────────────────────

    def _evolve(self, now: float) -> list[Event]:
        if now < self.evolve_due or self.evolve_pending:
            return []
        self.evolve_due = now + self._evolve_gap()
        free = [o for o in self.orbits if self._free(o)]
        if not free:
            return []
        n = len(self.orbits)
        roll = self.rng.random()
        if n >= 4 and roll < 0.2:
            orbit = self.rng.choice(free)
            self.orbits[orbit].retiring = True
            self._ramp(orbit, "gain", 0.0, now, max(self.depth.ramp[1], 12))
            return [Event("log", orbit, f"{orbit} fades out over "
                                        f"{round(max(self.depth.ramp[1], 12))}s, then stops",
                          gesture="retire")]
        if n <= 3 and roll < 0.25:
            used = {int(o[1:]) for o in self.orbits}
            k = next(i for i in range(1, 13) if i not in used)
            self.evolve_pending = True
            return [Event("evolve", f"d{k}", "add")]
        # Prefer the orbit that's gone longest without changing.
        orbit = min(free, key=lambda o: (self.last_evolved.get(o, -1e9), self.rng.random()))
        self.evolve_pending = True
        return [Event("evolve", orbit, "vary")]

    def evolved(self, orbit: str, kind: str, ok: bool, now: float) -> None:
        """The app reports back after rebasing onto the new code. A new layer
        was evaluated with its gain control already at 0; swell it in."""
        self.evolve_pending = False
        if not ok:
            return
        self.last_evolved[orbit] = now
        if kind == "add" and orbit in self.orbits:
            o = self.orbits[orbit]
            o.values["gain"] = o.sent["gain"] = 0.0
            self._ramp(orbit, "gain", 1.0, now, max(self.depth.ramp[1] / 2, 6))

    # ── helpers ───────────────────────────────────────────────────────────

    def _jitter(self, seconds: float) -> float:
        return seconds * self.rng.uniform(0.8, 1.25)

    def _evolve_gap(self) -> float:
        return self._jitter(self.rng.choice(self.depth.evolve_minutes) * 60)


def is_drums(stmt: str) -> bool:
    """Does this layer play a drum kit (or drum synth)?"""
    return any(name in DRUM_SOUNDS or name.startswith(("808", "909"))
               for name in sound_names(stmt))


def register_of(stmt: str) -> int:
    low = sum(stmt.count(w) for w in LOW_WORDS)
    high = sum(stmt.count(w) for w in HIGH_WORDS)
    return (high > low) - (low > high)


# ── I/O helpers ───────────────────────────────────────────────────────────

NTP_EPOCH = 2208988800
FRAME_SECONDS = 1 / 20  # Tidal's processing frame (clockFrameTimespan)
SEND_MARGIN = 0.01


class TidalClock:
    """Where Tidal's cycle is, learned from the events it sends to our tap.

    Each event bundle carries its play time (OSC timetag) plus `cycle` and
    `cps`. Tidal sends a frame's events as it processes the frame, `ahead`
    seconds before they play; to change a control for events from cycle B on,
    it must arrive before Tidal processes the frame holding B.
    """

    def __init__(self):
        self.anchor: tuple[float, float, float] | None = None  # (time, cycle, cps)
        self.aheads: list[float] = []

    def observe(self, received: float, timetag: float, cycle: float, cps: float) -> None:
        self.anchor = (timetag, cycle, cps)
        self.aheads = (self.aheads + [timetag - received])[-64:]

    @property
    def known(self) -> bool:
        return self.anchor is not None

    @property
    def ahead(self) -> float:
        return min(self.aheads) if self.aheads else 0.3

    @property
    def cps(self) -> float | None:
        return self.anchor[2] if self.anchor else None

    def cycle_at(self, t: float) -> float | None:
        if not self.anchor:
            return None
        t0, c0, cps = self.anchor
        return c0 + (t - t0) * cps

    def play_time(self, cycle: float) -> float:
        t0, c0, cps = self.anchor
        return t0 + (cycle - c0) / cps

    def send_time(self, cycle: float) -> float:
        return self.play_time(cycle) - self.ahead - FRAME_SECONDS - SEND_MARGIN

    def feed(self, packet: bytes, received: float) -> list[tuple[float, dict]]:
        """Parse a tapped OSC bundle (Tidal sends one per event). Returns the
        events as (play time, params) for anything else that wants them."""
        if not packet.startswith(b"#bundle") or len(packet) < 16:
            return []
        sec, frac = struct.unpack(">II", packet[8:16])
        timetag = sec - NTP_EPOCH + frac / 2 ** 32
        events = []
        for addr, args in osc_messages(packet):
            if addr == "/dirt/play":
                kv = dict(zip(args[0::2], args[1::2]))
                events.append((timetag, kv))
                if kv.get("cycle") is not None and kv.get("cps"):
                    self.observe(received, timetag, float(kv["cycle"]), float(kv["cps"]))
        return events


def osc_messages(packet: bytes) -> list[tuple[str, list]]:
    """(address, args) for every message in an OSC packet or bundle."""
    out: list[tuple[str, list]] = []

    def pad(n: int) -> int:
        return (n + 4) & ~3

    def message(b: bytes) -> None:
        i = b.index(b"\0")
        addr, j = b[:i].decode(), pad(i)
        k = b.index(b"\0", j)
        tags, j = b[j + 1:k].decode(), pad(k)
        args: list = []
        for t in tags:
            if t == "s":
                k = b.index(b"\0", j)
                args.append(b[j:k].decode())
                j = pad(k)
            elif t in "fi":
                args.append(struct.unpack(">f" if t == "f" else ">i", b[j:j + 4])[0])
                j += 4
            elif t == "d":
                args.append(struct.unpack(">d", b[j:j + 8])[0])
                j += 8
        out.append((addr, args))

    def walk(b: bytes) -> None:
        if b.startswith(b"#bundle"):
            j = 16
            while j < len(b):
                n = struct.unpack(">i", b[j:j + 4])[0]
                walk(b[j + 4:j + 4 + n])
                j += 4 + n
        else:
            message(b)

    try:
        walk(packet)
    except (ValueError, struct.error, UnicodeDecodeError):
        pass
    return out


class CtrlSender:
    """Sends Tidal `/ctrl name value` OSC messages to its control port."""

    def __init__(self, port: int):
        self.port = port
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    @staticmethod
    def _s(text: str) -> bytes:
        b = text.encode() + b"\0"
        return b + b"\0" * (-len(b) % 4)

    def send(self, name: str, value: float) -> None:
        packet = self._s("/ctrl") + self._s(",sf") + self._s(name) + struct.pack(">f", value)
        try:
            self.sock.sendto(packet, ("127.0.0.1", self.port))
        except OSError:
            pass

    def close(self) -> None:
        self.sock.close()


def free_udp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
