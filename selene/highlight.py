"""Event highlighting, as in Pulsar: light up each mini-notation token while
it sounds.

Before code goes to GHCi, every pattern string is wrapped in
`(deltaContext col id "...")`. Tidal then reports each event's source
position to the boot file's `/editor/highlights` target (selene's tap port):
the token's columns, and a line number that is really `id + 1`. The id maps
back to the line of code the string was on, so a highlight can find its line
in the editor even after the editor has been rearranged (a block played,
Flow rewriting a layer), as long as that line's text hasn't changed.
"""

import re
import struct
from dataclasses import dataclass

from .flow import NTP_EPOCH, osc_messages

ADDRESS = "/editor/highlights"

# The string literals on a line, and where a `--` comment starts outside them.
TOKEN = re.compile(r'"(?:[^"\\]|\\.)*"|--')
# Strings that are names, not patterns: pS "name", cF 0 "name", setF "name".
NAME_ARG = re.compile(r'\b(?:[pc][A-Z]\w*|set[A-Z]\w*|nTake)\s+(?:-?[\d.]+\s+)?$')
# ("a", pattern) pairs, as in ur, squeeze, inhabit: the name is a String.
PAIR_NAME = re.compile(r'\(\s*$')

KEEP = 4096  # ids remembered; far more lines than could be playing at once


@dataclass(frozen=True)
class Source:
    line: int  # where the line was in the code that was played
    text: str


class Highlighter:
    def __init__(self):
        self.sources: dict[int, Source] = {}
        self.next_id = 0
        # (id, start col, end col) -> (from, until), in time.time() seconds.
        self.events: dict[tuple[int, int, int], tuple[float, float]] = {}

    # ── outgoing code ─────────────────────────────────────────────────────

    def annotate(self, code: str, first_line: int = 0) -> str:
        """`code` with its pattern strings wrapped in deltaContext. Lines
        stay where they are, so statement splitting is unchanged."""
        return "\n".join(self._line(line, first_line + i)
                         for i, line in enumerate(code.split("\n")))

    def _line(self, line: str, number: int) -> str:
        out, last, id_ = [], 0, None
        for m in TOKEN.finditer(line):
            if m.group() == "--":
                break
            before = line[:m.start()]
            if (len(m.group()) == 2 or NAME_ARG.search(before)
                    or (PAIR_NAME.search(before) and line[m.end():].lstrip().startswith(","))):
                continue
            if id_ is None:
                id_ = self._register(Source(number, line))
            out.append(line[last:m.start()])
            out.append(f"(deltaContext {m.start()} {id_} {m.group()})")
            last = m.end()
        return "".join(out) + line[last:] if out else line

    def _register(self, source: Source) -> int:
        id_ = self.next_id
        self.next_id += 1
        self.sources[id_] = source
        if len(self.sources) > KEEP:
            del self.sources[next(iter(self.sources))]
        return id_

    # ── incoming events ───────────────────────────────────────────────────

    def feed(self, packet: bytes) -> None:
        """Take in a tapped bundle. Tidal re-sends an event's position every
        frame (50 ms) while it lasts; the timetag is the event's onset."""
        if not packet.startswith(b"#bundle") or len(packet) < 16:
            return
        sec, frac = struct.unpack(">II", packet[8:16])
        onset = sec - NTP_EPOCH + frac / 2 ** 32
        for addr, args in osc_messages(packet):
            if addr != ADDRESS or len(args) < 6:
                continue
            _ident, delta_us, _cycle, x, y, x2 = args[:6]
            id_ = int(y) - 1
            if id_ in self.sources and x2 > x:
                self.events[(id_, int(x), int(x2))] = (onset, onset + float(delta_us) / 1e6)

    def active(self, now: float) -> dict[Source, list[tuple[int, int]]]:
        """Column ranges sounding now, by the source line they came from."""
        out: dict[Source, list[tuple[int, int]]] = {}
        for key, (start, end) in list(self.events.items()):
            if end <= now:
                del self.events[key]
            elif start <= now and (source := self.sources.get(key[0])):
                out.setdefault(source, []).append(key[1:])
        return out

    def clear(self) -> None:
        self.events.clear()


def place(active: dict[Source, list[tuple[int, int]]],
          lines: list[str]) -> dict[int, list[tuple[int, int]]]:
    """Editor line -> column ranges. A source line is found where it was, or
    else at the nearest editor line with the same text; edited lines get
    nothing."""
    by_text: dict[str, list[int]] = {}
    for i, text in enumerate(lines):
        by_text.setdefault(text, []).append(i)
    out: dict[int, list[tuple[int, int]]] = {}
    for source, ranges in active.items():
        if source.line < len(lines) and lines[source.line] == source.text:
            row = source.line
        elif candidates := by_text.get(source.text):
            row = min(candidates, key=lambda i: abs(i - source.line))
        else:
            continue
        out.setdefault(row, []).extend(ranges)
    return out
