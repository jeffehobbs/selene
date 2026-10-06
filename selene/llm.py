"""Ask a local Ollama model to write TidalCycles code."""

import json
import re
from typing import AsyncIterator

import httpx

SYSTEM_PROMPT = """\
You are a live-coding musician who writes TidalCycles (Tidal 1.10, Haskell) \
patterns that play through SuperDirt. The user describes music in words; you \
answer with Tidal code that runs as-is in GHCi.

Output rules:
- Reply with ONE ```haskell fenced block and nothing else. No prose.
- Each statement starts at column 0; continuation lines are indented 2 spaces.
- Use orbits d1..d8 (one musical layer each, or a `stack [...]` in one orbit).
- Set tempo with `setcps (BPM/60/4)` as its own statement when tempo matters.
- When revising, return the COMPLETE new code. Write `dN silence` only to \
stop an orbit that is currently playing; never for unused orbits.
- Only use sounds from the lists below. `s "name:3"` picks sample 3 of a bank.

Tidal reminders:
- Mini-notation: "bd*4", "bd ~ sn ~", "[bd bd] sn", "<bd sn cp>", "bd(3,8)", \
"hh*8?", "bd . hh hh", "bd!3 sn", "{bd sn, hh hh hh}", "bd:2".
- Pitch: `n "0 3 7"` (sample index or synth note) or `note "c4 e4 g4"` / \
`note "c'maj e'min7"`. Scales: `n (scale "minor" "0 .. 7")`, also "major" \
"dorian" "pentatonic" "minPent" "majPent" "ritusen" "egyptian" "lydian". \
Shift with `|+ n 12` or `# octave 4`.
- Synths take notes: `n "0 4 7" # s "superpiano"`. Use `legato`, `sustain`.
- Structure: stack, cat, fastcat, slow, fast, every 4 (fast 2), whenmod 8 6 rev, \
sometimesBy 0.3 (# speed 2), degradeBy 0.2, jux rev, off 0.125 (|+ n 12), \
iter 4, chunk 4 (hurry 2), striate 4, chop 8, ply 2, swingBy (1/3) 4, \
arp "up" (note "<c'maj e'min>"), range 200 2000 sine, segment 16.
- Effects (all via #): gain, pan, speed, room, size, orbit, lpf/cutoff, \
hpf, resonance, delay, delaytime, delayfeedback, crush, coarse, shape, \
vowel "a", squiz, accelerate, begin, end, legato, sustain, attack, release.
- Continuous modulation: `# lpf (range 300 3000 $ slow 4 sine)`.
- Keep gain at or below 1.1; keep `room`/`size` at or below 0.9.
- Never use `djf` (reserved). Use lpf/hpf for filtering.

Common mistakes that GHCi rejects:
- Transformations (every, sometimes, jux, fast, slow, off, whenmod, degradeBy, \
chunk, iter, ply) wrap a pattern with `$`; they never follow `#`.
  WRONG: s "hh*8" # gain 0.6 # every 4 (fast 2)
  RIGHT: every 4 (fast 2) $ s "hh*8" # gain 0.6
- Give every transformation ALL its arguments before the `$`:
  sometimesBy 0.3 (# speed 2) $ ...   every 4 (fast 2) $ ...   ply 2 $ ...
  off 0.125 (|+ n 7) $ ...   whenmod 8 6 rev $ ...   jux rev $ ...
  WRONG: sometimesBy 0.3 $ n "0 3"     (missing the function to apply)
- Slicing a break: `slice 8 "0 2 <4 6> 7" $ s "breaks125"` or \
`chop 4 $ s "breaks152"` (the indices are a plain string, not `n`).
- Effects after `#` take a value or pattern: `# speed 2`, `# pan sine`, \
`# lpf "<400 800>"`. Not `# speed`, not `# (fast 2)`.
- In `stack [...]`, separate items with commas and give each item its own `$` \
transformations: `stack [ jux rev $ s "arpy*4", s "bd*2" ]`.
- `n` / `note` values are numbers or note names, not sample names.
- Negative numbers as arguments need parentheses: `range (-0.8) 0.8 sine`,
  `# speed (-1)`. Bare `range -0.8 0.8` is subtraction and fails.

Example:
```haskell
setcps (122/60/4)

d1 $ stack [
  s "808bd:3*4" # gain 1.05,
  every 4 (fast 2) $ s "~ 808hc" # gain 0.8,
  sometimesBy 0.3 (# speed 1.5) $ s "~ cp" # room 0.4 # size 0.6
  ]

d2 $ slow 2 $ off 0.25 (|+ n 12) $ n (scale "minor" "0 [3 5] 7 <10 8>")
  # s "superpiano" # legato 1.5 # lpf 2400 # gain 0.75
```

Installed sample banks: {samples}

SuperDirt synths: {synths}
"""


def build_system_prompt(samples: list[str], synths: list[str], preferences: str = "") -> str:
    # str.replace, not format: the mini-notation examples contain braces.
    prompt = (SYSTEM_PROMPT.replace("{samples}", " ".join(samples))
              .replace("{synths}", " ".join(synths)))
    if preferences.strip():
        # Last, so they win over the defaults above.
        prompt += ("\nThe player's preferences (follow these unless a request says "
                   f"otherwise):\n{preferences.strip()}\n")
    return prompt


def user_message(prompt: str, current_code: str, unplayed_edits: str = "",
                 held: str = "") -> str:
    message = _user_message(prompt, current_code, unplayed_edits)
    if held.strip():
        message = (f"Also still playing from earlier code (held layers). Leave them out "
                   f"of your reply unless the request is about them; to stop one, write "
                   f"`dN silence`:\n```haskell\n{held.strip()}\n```\n\n{message}")
    return message


def _user_message(prompt: str, current_code: str, unplayed_edits: str = "") -> str:
    if unplayed_edits.strip():
        playing = (f"Currently playing:\n```haskell\n{current_code.strip()}\n```\n\n"
                   if current_code.strip() else "")
        return (f"{playing}The player has edited the code in the editor but not played it "
                f"yet. Treat these edits as their intent and build on them, not on what "
                f"is playing:\n```haskell\n{unplayed_edits.strip()}\n```\n\n"
                f"Request: {prompt}")
    if current_code.strip():
        return f"Currently playing:\n```haskell\n{current_code.strip()}\n```\n\nRequest: {prompt}"
    return f"Request: {prompt}"


def unknown_sounds_message(names: set[str]) -> str:
    return (f"These sounds are not installed and would be silent: {', '.join(sorted(names))}. "
            "Return the complete code using only sounds from the lists.")


# GHC error fragments -> what the model usually got wrong.
FIX_HINTS = {
    "applied to too few arguments": "A transformation is missing an argument or sits "
        "after `#`. Each needs all its arguments, then `$`, then the pattern: "
        '`every 4 (fast 2) $ ...`, `sometimesBy 0.3 (# speed 2) $ ...`, `ply 2 $ ...`. '
        "Move it in front of the pattern with `$`, never after `#`.",
    "In the second argument of ‘slice’": 'slice takes a plain index string: '
        '`slice 8 "0 2 4 6" $ s "breaks125"`.',
    "Variable not in scope": "A function or parameter name doesn't exist in Tidal 1.10. "
        "Use only names from the reminders.",
    "Syntax error in sequence": "Mini-notation inside quotes is malformed: check brackets, "
        "`(3,8)` Euclid syntax, and `<>` alternation.",
    "parse error": "Haskell layout or punctuation is off: statements start at column 0, "
        "continuation lines are indented, stack items are comma-separated.",
}


def evolve_message(current_code: str, orbit: str, kind: str, bold: bool = False) -> str:
    if kind == "add":
        ask = (f"Add ONE new layer as {orbit} that complements what is playing: sparse, "
               "in the same key and scale, a different register or role from the others.")
    elif bold:
        ask = (f"Transform {orbit} so the change is clearly audible: a new rhythm, a "
               "different sound from the lists, or a new transformation (every, off, jux, "
               "chop, striate, euclid...). Keep its musical role and the key/scale.")
    else:
        ask = (f"Evolve only {orbit}: change ONE musical element subtly (rhythm placement, "
               "a note or two, a transformation, an effect amount). Keep its role, its "
               "sounds, its density and the key/scale.")
    return (f"Currently playing:\n```haskell\n{current_code.strip()}\n```\n\n{ask} "
            f"Do not change the key, scale or tempo; no setcps. Reply with only the "
            f"{orbit} statement in one ```haskell block.")


NEGATIVE_ARG = re.compile(r"[\w)]\s+-\d")


def fix_message(error: str, code: str = "") -> str:
    hints = [hint for key, hint in FIX_HINTS.items() if key in error]
    if NEGATIVE_ARG.search(re.sub(r'"[^"]*"', '""', code)):  # mini-notation may say "0 -1"
        hints.append("A negative number is used as an argument without parentheses; "
                     "write `(-0.8)`, e.g. `range (-0.8) 0.8 sine`.")
    hint_text = ("\nLikely cause: " + " ".join(hints)) if hints else ""
    return (f"GHCi rejected that code:\n```\n{error[-1500:]}\n```{hint_text}\n"
            "Return the corrected complete code, same musical idea. "
            "Do not repeat the construct that failed.")


class Ollama:
    def __init__(self, url: str = "http://localhost:11434", model: str = "gemma4:31b-mlx"):
        self.url = url.rstrip("/")
        self.model = model
        self.requested = model
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(10, read=300))
        self._think_supported = True

    async def models(self) -> list[str]:
        r = await self.client.get(f"{self.url}/api/tags")
        r.raise_for_status()
        return [m["name"] for m in r.json().get("models", [])]

    async def installed(self) -> list[dict]:
        """Installed models, by name, with Ollama's size and details for each."""
        r = await self.client.get(f"{self.url}/api/tags")
        r.raise_for_status()
        return sorted(r.json().get("models", []), key=lambda m: m["name"])

    async def loaded(self) -> set[str]:
        """Models Ollama has in memory right now."""
        r = await self.client.get(f"{self.url}/api/ps")
        r.raise_for_status()
        return {m["name"] for m in r.json().get("models", [])}

    async def warm(self) -> None:
        """Load the model into memory now, so the first prompt doesn't wait."""
        r = await self.client.post(f"{self.url}/api/generate", json={"model": self.model})
        if r.status_code >= 400:
            raise RuntimeError(r.text)

    async def resolve_model(self) -> str:
        """Accept a bare family name like "gemma4" and match an installed tag."""
        installed = await self.models()
        if self.model in installed:
            return self.model
        for name in installed:
            if name == f"{self.model}:latest" or name.split(":")[0] == self.model:
                self.model = name
                return name
        raise LookupError(f"model {self.model!r} not installed; have: {', '.join(installed) or 'none'}")

    async def chat(self, messages: list[dict], _retry: bool = True) -> AsyncIterator[str]:
        body = {"model": self.model, "messages": messages, "stream": True,
                "options": {"temperature": 0.8}}
        if self._think_supported:
            body["think"] = False  # code only; skip hidden reasoning tokens
        async with self.client.stream("POST", f"{self.url}/api/chat", json=body) as r:
            if r.status_code == 400 and self._think_supported:
                text = (await r.aread()).decode(errors="replace")
                if "think" in text:
                    self._think_supported = False
                    async for chunk in self.chat(messages):
                        yield chunk
                    return
                raise RuntimeError(text)
            if r.status_code >= 400:
                text = (await r.aread()).decode(errors="replace")
                if _retry and "not found" in text:
                    # The resolved tag was removed (e.g. a re-pull); match again.
                    self.model = self.requested
                    await self.resolve_model()
                    async for chunk in self.chat(messages, _retry=False):
                        yield chunk
                    return
                raise RuntimeError(text)
            async for line in r.aiter_lines():
                if not line:
                    continue
                data = json.loads(line)
                if "error" in data:
                    raise RuntimeError(data["error"])
                if chunk := data.get("message", {}).get("content"):
                    yield chunk

    async def close(self) -> None:
        if not self.client.is_closed:
            await self.client.aclose()
