"""Turn free-form Tidal code into statements GHCi will accept one at a time.

Pulsar evaluates the paragraph under the cursor wrapped in :{ ... :}. A model
reply usually holds several statements (setcps, d1, d2 ...), so we split on
top-level lines and wrap each statement separately.
"""

import re

# A column-0 line starting with one of these continues the previous statement
# (closing a stack, chaining an operator) rather than starting a new one.
CONTINUATION_START = tuple("])}#|+,$.*-<>")

FENCE = re.compile(r"```[a-zA-Z]*[ \t]*\n(.*?)```", re.S)


def extract_code(reply: str) -> str:
    """Pull Tidal code out of a model reply: fenced blocks if any, else the
    text from its first line that looks like Tidal (prose before it dropped,
    the same rule the editor uses while the reply streams in)."""
    fenced = FENCE.findall(reply)
    if not fenced:
        stream = CodeStream()
        return "\n".join(stream.feed(reply.replace("```", "")) + stream.finish()).strip()
    lines = [ln.rstrip() for ln in "\n\n".join(fenced).strip().splitlines()]
    lines = [re.sub(r"^tidal>\s?", "", ln) for ln in lines]
    return "\n".join(lines).strip()


def split_statements(code: str) -> list[str]:
    """Split code into top-level statements, dropping comments and blank lines.

    Continuation lines that sit at column 0 get indented so GHCi's layout rule
    doesn't read them as a new statement inside :{ :}.
    """
    statements: list[list[str]] = []
    for raw in code.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        if stripped in (":{", ":}"):
            continue
        top_level = not line[0].isspace()
        if top_level and statements and stripped.startswith(CONTINUATION_START):
            statements[-1].append("  " + stripped)
        elif top_level or not statements:
            statements.append([stripped])
        else:
            statements[-1].append(line)
    return ["\n".join(s) for s in statements]


def wrap(statement: str) -> str:
    if statement.startswith(":"):  # GHCi command such as :t, can't be wrapped
        return statement + "\n"
    return ":{\n" + statement + "\n:}\n"


def orbits_used(code: str) -> list[str]:
    return sorted(set(re.findall(r"^\s*(d\d{1,2})\b", code, re.M)), key=lambda d: int(d[1:]))


SILENCE = re.compile(r"^d\d{1,2}\s*(?:\$\s*)?silence$")


def sounding_orbits(code: str) -> list[str]:
    """Orbits with a pattern, ignoring `dN silence` statements."""
    live = "\n".join(s for s in split_statements(code) if not SILENCE.match(s))
    return orbits_used(live)


SOUND_STRING = re.compile(r'\b(?:s|sound)\s+"([^"]*)"')


def sound_names(code: str) -> set[str]:
    """Sample/synth names referenced in s "..." / sound "..." strings."""
    names: set[str] = set()
    for body in SOUND_STRING.findall(code):
        # Whole word runs, so "808oh" stays "808oh"; pure numbers (Euclid
        # args, repeat counts) aren't names.
        for tok in re.findall(r"\w+", body):
            if not tok.isdigit():
                names.add(tok)
    return names


# ── Flow rewrites ─────────────────────────────────────────────────────────

ORBIT_STMT = re.compile(r"^d(\d{1,2})\s*\$\s*(.*)$", re.S)


def flowify(code: str) -> str:
    """Route every `dN $ ...` through Flow's per-orbit controls (see flow.py).

    At their defaults the controls are inaudible: rate 1, rot 0, ply 1, rev
    off, degradeBy 0, gain x1, djf 0.5, room +0, and color that only fills in
    shape/crush/delay the model didn't set (`|<` keeps the model's values).
    The closing paren goes on its own line so an inline comment in the
    model's code can't swallow it.
    """
    out = []
    for stmt in split_statements(code):
        m = ORBIT_STMT.match(stmt)
        if not m or SILENCE.match(stmt):
            out.append(stmt)
            continue
        n, body = m.groups()
        c = lambda name: f'"fl_{name}{n}"'  # noqa: E731
        out.append(
            f"d{n} $ fast (toRational <$> cF 1 {c('rate')}) $ rot (cI 0 {c('rot')})"
            f" $ ply (toRational <$> cF 1 {c('ply')}) $ sometimesBy (cF 0 {c('rev')}) rev"
            f" $ degradeBy (cF 0 {c('thin')}) $ ({body}\n"
            f"  ) |* gain (cF 1 {c('gain')}) |* gain (cF 1 {c('gate')})"
            f" # djf (cF 0.5 {c('tone')}) |+ room (cF 0 {c('space')})"
            f" |< shape (cF 0 {c('drive')}) |< crush (cF 16 {c('crush')})"
            f" |< delay (cF 0 {c('send')}) |+ delay (cF 0 {c('throw')})"
            f" |< delaytime (cF 0.375 \"fl_dtime\") |< delayfeedback 0.45")
    return "\n".join(out)


def as_xfade(stmt: str, cycles: int = 16) -> str:
    """`dN $ X` -> a crossfade into X over `cycles` cycles. xfadeIn skips dN's
    own orbit routing, so add it back (d1..d12 map to orbits 0..11)."""
    m = ORBIT_STMT.match(stmt)
    if not m:
        return stmt
    n, body = m.groups()
    route = f" |< orbit {int(n) - 1}" if int(n) <= 12 else ""
    return f"xfadeIn {n} {cycles} $ ({body}\n  ){route}"


SCALE = re.compile(r'\bscale\s+"([^"]+)"')


def scales(code: str) -> set[str]:
    return set(SCALE.findall(code))


def statement_for(code: str, orbit: str) -> str | None:
    """The single `dN` statement from a model reply, if there is one."""
    found = [s for s in split_statements(code)
             if ORBIT_STMT.match(s) and s.split("$", 1)[0].strip() == orbit]
    return found[-1] if found else None


# ── streaming ─────────────────────────────────────────────────────────────

CODE_START = re.compile(r"^(?:tidal>\s*)?(d\d{1,2}\b|setcps\b|hush\b|xfadeIn\b|once\b|solo\b|"
                        r"unsolo\b|let\b|--)")


class CodeStream:
    """A model reply as it streams in -> whole lines of code, as they complete.

    Shows only what `extract_code` will keep: the opening fence and any prose
    before it are skipped, and the stream ends at the closing fence. A reply
    with no fence counts as code from its first line that looks like Tidal.
    Holding back the partial line means nothing is drawn half-typed.
    """

    def __init__(self):
        self.buffer = ""
        self.state = "before"  # before -> code -> done
        self.started = False  # past any blank lines at the top of the code

    def feed(self, chunk: str) -> list[str]:
        self.buffer += chunk
        lines = []
        while "\n" in self.buffer and self.state != "done":
            line, self.buffer = self.buffer.split("\n", 1)
            lines += self._line(line)
        return lines

    def finish(self) -> list[str]:
        rest, self.buffer = self.buffer, ""
        return self._line(rest) if rest.strip() and self.state != "done" else []

    def _line(self, line: str) -> list[str]:
        stripped = line.strip()
        if self.state == "before":
            if stripped.startswith("```"):
                self.state = "code"
                return []
            if not CODE_START.match(stripped):
                return []  # prose before the code
            self.state = "code"
        elif stripped.startswith("```"):
            self.state = "done"
            return []
        line = re.sub(r"^tidal>\s?", "", line.rstrip())
        if not self.started and not line.strip():
            return []
        self.started = True
        return [line]


def describe_change(old: str, new: str, limit: int = 3) -> str:
    """What changed between two versions of a statement, compactly:
    `"0 2 <4 3> 7" → "0 2 <4 5> 7"; + # room 0.3`. Mini-notation strings are
    compared whole, so a changed pattern reads as before → after."""
    import difflib

    def tokens(stmt: str) -> list[str]:
        return re.findall(r'"[^"]*"|[^\s"]+', re.sub(r"^d\d{1,2}\s*\$\s*", "", stmt.strip()))

    def clip(words: list[str], width: int = 40) -> str:
        text = " ".join(words)
        return text if len(text) <= width else text[:width - 1] + "…"

    a, b = tokens(old), tokens(new)
    hunks = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        if op == "replace":
            hunks.append(f"{clip(a[i1:i2])} → {clip(b[j1:j2])}")
        elif op == "delete":
            hunks.append(f"− {clip(a[i1:i2])}")
        elif op == "insert":
            hunks.append(f"+ {clip(b[j1:j2])}")
    if not hunks:
        return "no change to the code"
    return "; ".join(hunks[:limit]) + ("; …" if len(hunks) > limit else "")
