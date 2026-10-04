"""Flow — the patterns playing themselves, Thrum-style.

The rules carried over from Thrum, Meter and Counter:

- Nothing is set, only ramped: every change is a smootherstep slide (zero
  velocity and acceleration at both ends) of 20–90 s, never under 9 s.
- Every gesture runs on its own prime-numbered clock, so nothing lines up.
- Density is a zero-sum budget across orbits (Meter): it moves between layers;
  the total stays put. Grouped ramps share one start and duration, so the sum
  is constant at every instant, not just at the ends.
- Flow never touches the key, never touches a muted orbit, and yields to the
  player: anything typed or played rebases it.

The director is pure: `tick(now)` returns control updates and events, and the
app does the I/O. That's what lets tests run an hour of Flow in a second.
"""

import random
import socket
import struct
from dataclasses import dataclass, field

# name -> (low, high, neutral). gain multiplies the model's level, tone is
# SuperDirt's djf (0.5 = neutral), space is added to room, thin feeds degradeBy.
CONTROLS = {
    "gain": (0.55, 1.1, 1.0),
    "tone": (0.28, 0.66, 0.5),
    "space": (0.0, 0.3, 0.0),
    "thin": (0.0, 0.85, 0.0),
}
DRIFTED = ("gain", "tone", "space")  # each on its own clock; thin is the budget's
MIN_DENSITY = 1 - CONTROLS["thin"][1]
BUDGET_MEAN = 0.8  # average density the budget settles to; zero-sum after that
QUANTUM = 0.004  # send a control only when it moved 0.4% of its band
SHORTEST_RAMP = 9.0
EXIT_SECONDS = 12.0  # Flow off: everything glides home over this long

PRIMES = (11, 13, 17, 19, 23, 29, 31, 37, 41, 43)
BUDGET_PERIODS = {"transfer": 37, "dropout": 53, "tilt": 61, "spotlight": 71}
EVOLVE_MINUTES = (5, 7, 11)
YIELD_SECONDS = 120  # after the player acts, no structural change for this long

LOW_WORDS = ("bd", "kick", "bass", "808lt", "sub", "reese", "clubkick", "hardkick")
HIGH_WORDS = ("hh", "hat", "oh", "cy", "cr", "ride", "bell", "arpy", "glitch",
              "click", "tink", "chin", "superchip", "supervibe")


def smootherstep(x: float) -> float:
    x = min(1.0, max(0.0, x))
    return x * x * x * (x * (x * 6 - 15) + 10)


def ctrl_name(control: str, orbit: str) -> str:
    return f"fl_{control}{orbit[1:]}"


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
class Event:
    kind: str  # "evolve" (detail vary/add), "silence" (a retired orbit), "log"
    orbit: str = ""
    detail: str = ""


@dataclass
class Orbit:
    values: dict = field(default_factory=lambda: {c: n for c, (_, _, n) in CONTROLS.items()})
    ramps: dict = field(default_factory=dict)
    sent: dict = field(default_factory=dict)
    due: dict = field(default_factory=dict)  # drifted control -> next gesture time
    register: int = 0  # -1 low, 0 mid, +1 high
    retiring: bool = False


class FlowDirector:
    def __init__(self, seed: int | None = None):
        self.rng = random.Random(seed)
        self.orbits: dict[str, Orbit] = {}
        self.muted: set[str] = set()
        self.ramp_log: list[tuple[str, str, float]] = []  # (orbit, control, duration)
        self.now = 0.0
        self.exiting_until: float | None = None
        self.busy_until = 0.0  # budget gestures are atomic groups
        self.budget_due = {g: self._jitter(p) for g, p in BUDGET_PERIODS.items()}
        self.settled_for: frozenset = frozenset()
        self.evolve_due = self._evolve_gap()
        self.evolve_pending = False
        self.last_evolved: dict[str, float] = {}

    # ── lifecycle ─────────────────────────────────────────────────────────

    def rebase(self, code_orbits: dict[str, str], now: float, player: bool = True) -> list[tuple[str, float]]:
        """Adopt the playing orbits {dN: statement}. Returns controls to reset:
        an orbit that left must be neutral if it comes back (Tidal keeps cF state)."""
        resets = []
        for orbit in list(self.orbits):
            if orbit not in code_orbits:
                for c, (_, _, n) in CONTROLS.items():
                    resets.append((ctrl_name(c, orbit), n))
                del self.orbits[orbit]
        for orbit, stmt in code_orbits.items():
            if orbit not in self.orbits:
                o = Orbit(register=register_of(stmt))
                for c in DRIFTED:
                    o.due[c] = now + self._jitter(self.rng.choice(PRIMES[:5]))
                self.orbits[orbit] = o
            else:
                self.orbits[orbit].register = register_of(stmt)
        if player:
            self.evolve_due = max(self.evolve_due, now + YIELD_SECONDS)
        return resets

    def set_muted(self, muted: set[str], now: float) -> None:
        for orbit in muted - self.muted:  # freeze where it is
            o = self.orbits.get(orbit)
            if o:
                for c in list(o.ramps):
                    o.values[c] = o.ramps.pop(c).value(now)
        self.muted = set(muted)

    def start_exit(self, now: float) -> None:
        """Flow off: glide every control home, then stop."""
        self.exiting_until = now + EXIT_SECONDS
        for orbit, o in self.orbits.items():
            for c, (_, _, n) in CONTROLS.items():
                cur = o.ramps[c].value(now) if c in o.ramps else o.values[c]
                o.ramps[c] = Ramp(cur, n, now, EXIT_SECONDS)
                self.ramp_log.append((orbit, c, EXIT_SECONDS))

    def resume(self, now: float) -> None:
        """Flow back on mid-exit: keep the glide home and drift on from there,
        rather than a fresh director whose idea of the controls is wrong."""
        self.exiting_until = None
        self.evolve_due = max(self.evolve_due, now + YIELD_SECONDS)

    @property
    def exited(self) -> bool:
        return self.exiting_until is not None and self.now >= self.exiting_until

    def neutral_updates(self) -> list[tuple[str, float]]:
        return [(ctrl_name(c, orbit), n) for orbit in self.orbits
                for c, (_, _, n) in CONTROLS.items()]

    # ── the clock ─────────────────────────────────────────────────────────

    def tick(self, now: float) -> tuple[list[tuple[str, float]], list[Event]]:
        self.now = now
        events: list[Event] = []
        if self.exiting_until is None:
            self._drift(now)
            events += self._budget(now)
            events += self._evolve(now)
        updates = self._advance(now)
        for orbit, o in self.orbits.items():
            if o.retiring and "gain" not in o.ramps and o.values["gain"] == 0.0:
                o.retiring = False  # report once; the app silences and rebases
                events.append(Event("silence", orbit))
        return updates, events

    def _advance(self, now: float) -> list[tuple[str, float]]:
        updates = []
        for orbit, o in self.orbits.items():
            for c, ramp in list(o.ramps.items()):
                o.values[c] = ramp.value(now)
                if ramp.done(now):
                    o.values[c] = ramp.end
                    del o.ramps[c]
            for c, v in o.values.items():
                lo, hi, neutral = CONTROLS[c]
                moved = abs(v - o.sent.get(c, neutral))
                # Quantized while moving; the exact end value once a ramp lands.
                if moved >= QUANTUM * (hi - lo) or (moved > 0 and c not in o.ramps):
                    o.sent[c] = v
                    updates.append((ctrl_name(c, orbit), round(v, 5)))
        return updates

    def _ramp(self, orbit: str, control: str, target: float, now: float, duration: float) -> None:
        o = self.orbits[orbit]
        cur = o.ramps[control].value(now) if control in o.ramps else o.values[control]
        lo, hi, _ = CONTROLS[control]
        if not (control == "gain" and o.retiring):  # a retiring orbit fades to 0
            target = min(hi, max(lo, target))
        duration = max(SHORTEST_RAMP, duration)
        o.ramps[control] = Ramp(cur, target, now, duration)
        self.ramp_log.append((orbit, control, duration))

    def _free(self, orbit: str) -> bool:
        return orbit not in self.muted and not self.orbits[orbit].retiring

    # ── gestures ──────────────────────────────────────────────────────────

    def _drift(self, now: float) -> None:
        """Each orbit's gain/tone/space breathes on its own prime clock."""
        for orbit, o in self.orbits.items():
            if not self._free(orbit):
                continue
            for c in DRIFTED:
                if now < o.due.get(c, 0) or c in o.ramps:
                    continue
                lo, hi, neutral = CONTROLS[c]
                # Wander, but lean home: half the time the target is near neutral.
                if self.rng.random() < 0.5:
                    target = neutral + (self.rng.random() - 0.5) * 0.3 * (hi - lo)
                else:
                    target = self.rng.uniform(lo, hi)
                duration = self.rng.uniform(20, 90)
                self._ramp(orbit, c, target, now, duration)
                o.due[c] = now + duration + self._jitter(self.rng.choice(PRIMES))

    def _density(self, orbit: str) -> float:
        o = self.orbits[orbit]
        thin = o.ramps["thin"].end if "thin" in o.ramps else o.values["thin"]
        return 1 - thin

    def _set_densities(self, targets: dict[str, float], now: float) -> None:
        """One grouped ramp: same start and length, so the sum holds throughout."""
        duration = self.rng.uniform(30, 90)
        for orbit, d in targets.items():
            self._ramp(orbit, "thin", 1 - d, now, duration)
        self.busy_until = now + duration

    def _budget(self, now: float) -> list[Event]:
        free = [o for o in self.orbits if self._free(o)]
        if len(free) < 2 or now < self.busy_until:
            return []
        if self.settled_for != frozenset(free):
            # At full density nothing can move zero-sum. Once per set of
            # orbits, settle them (slowly) to an average below full.
            self.settled_for = frozenset(free)
            spread = {o: self.rng.uniform(-0.1, 0.1) for o in free}
            mean = sum(spread.values()) / len(free)
            self._set_densities({o: BUDGET_MEAN + v - mean for o, v in spread.items()}, now)
            return []
        for gesture, due in sorted(self.budget_due.items(), key=lambda kv: kv[1]):
            if now < due:
                continue
            self.budget_due[gesture] = now + self._jitter(BUDGET_PERIODS[gesture])
            dens = {o: self._density(o) for o in free}
            targets = getattr(self, f"_g_{gesture}")(dens)
            if targets:
                say = targets.pop("_say")
                self._set_densities(targets, now)
                return [Event("log", detail=say)]
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
                "_say": f"{donor} → {taker}"}

    def _g_dropout(self, dens: dict[str, float]) -> dict:
        orbit = self.rng.choice(list(dens))
        surplus = dens[orbit] - MIN_DENSITY
        targets = self._spread(dens, {orbit: MIN_DENSITY}, surplus)
        return {**targets, "_say": f"{orbit} thinning out"} if targets else {}

    def _g_spotlight(self, dens: dict[str, float]) -> dict:
        orbit = self.rng.choice(list(dens))
        need = 1 - dens[orbit]
        if need < 0.05:
            return {}
        targets = self._spread(dens, {orbit: 1.0}, -need)
        return {**targets, "_say": f"spotlight on {orbit}"} if targets else {}

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
        return {**targets, "_say": f"tilting {word}"}

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
            self._ramp(orbit, "gain", 0.0, now, 40)
            return [Event("log", orbit, f"{orbit} fading out")]
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
            self._ramp(orbit, "gain", 1.0, now, 45)

    # ── helpers ───────────────────────────────────────────────────────────

    def _jitter(self, seconds: float) -> float:
        return seconds * self.rng.uniform(0.8, 1.25)

    def _evolve_gap(self) -> float:
        return self._jitter(self.rng.choice(EVOLVE_MINUTES) * 60)


def register_of(stmt: str) -> int:
    low = sum(stmt.count(w) for w in LOW_WORDS)
    high = sum(stmt.count(w) for w in HIGH_WORDS)
    return (high > low) - (low > high)


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
