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
    """Pull Tidal code out of a model reply: fenced blocks if any, else the text."""
    fenced = FENCE.findall(reply)
    text = "\n\n".join(fenced) if fenced else reply.replace("```", "")
    lines = [ln.rstrip() for ln in text.strip().splitlines()]
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
