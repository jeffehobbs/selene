"""Small things selene remembers between launches (layout, mostly)."""

import json
import os
from pathlib import Path

DEFAULT_PATH = Path.home() / ".local/share/selene/settings.json"
DEFAULTS = {"split": 50.0, "lanes": True}


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
