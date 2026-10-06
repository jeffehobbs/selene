"""Small things selene remembers between launches (layout, mostly)."""

import json
import os
from datetime import datetime
from pathlib import Path

DEFAULT_PATH = Path.home() / ".local/share/selene/settings.json"
# record_dir "" means: next to the .tidal files. record_tail: the most
# seconds a take keeps going after you stop it, to let reverb and delays ring
# out (it ends sooner once the output goes silent).
# model "" means: the default (DEFAULT_MODEL in app.py); the picker remembers
# the player's last choice here.
DEFAULTS = {"split": 50.0, "lanes": True, "record_dir": "", "record_tail": 8.0, "model": ""}


def path() -> Path:
    # Tests point this elsewhere so they never touch the player's settings.
    return Path(os.environ.get("SELENE_SETTINGS", DEFAULT_PATH))


def load() -> dict:
    try:
        stored = json.loads(path().read_text())
    except (OSError, ValueError):
        stored = {}
    return {**DEFAULTS, **{k: v for k, v in stored.items() if k in DEFAULTS}}


def save(**changes) -> None:
    current = load() | changes
    target = path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".tmp")
        tmp.write_text(json.dumps(current, indent=2) + "\n")
        tmp.replace(target)
    except OSError:
        pass  # layout memory is a nicety; never fail over it


def preferences_path() -> Path:
    """The player's additions to the model's system prompt, next to settings."""
    return path().with_name("preferences.md")


def load_preferences() -> str:
    try:
        return preferences_path().read_text()
    except OSError:
        return ""


def save_preferences(text: str) -> None:
    target = preferences_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    tmp.write_text(text.rstrip() + "\n" if text.strip() else "")
    tmp.replace(target)


def history_path() -> Path:
    """Every prompt the player has typed, oldest first, one JSON object a line."""
    return path().with_name("prompt_history.jsonl")


def load_history() -> list[str]:
    try:
        lines = history_path().read_text().splitlines()
    except OSError:
        return []
    out: list[str] = []
    for line in lines:
        try:
            text = json.loads(line)["text"]
        except (ValueError, KeyError, TypeError):
            continue
        if text and (not out or out[-1] != text):
            out.append(text)
    return out


def append_history(text: str) -> None:
    try:
        target = history_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a") as f:
            f.write(json.dumps({"at": datetime.now().isoformat(timespec="seconds"),
                                "text": text}) + "\n")
    except OSError:
        pass  # history is a nicety; never fail over it
