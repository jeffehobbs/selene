"""Selene soundsets: modular sound packs installed from inside the app.

A soundset is a folder under ROOT holding manifest.json, samples/<bank>/*.wav
and synths.scd (the live synths, which load patches.scd beside them). The
list of packs lives in the selene repo (soundsets/index.json on main); each
zip is a release asset. Installing downloads the zip, checks its sha256,
unpacks it beside ROOT/<id> and swaps it in with one rename, so a half-done
install never looks installed. Loading it into SuperDirt is the bridge's job
(recorder.py: /selene/sounds/load).
"""

import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import httpx

ROOT = Path(os.environ.get("SELENE_SOUNDSETS", Path.home() / ".local/share/selene/soundsets"))
INDEX_URL = "https://raw.githubusercontent.com/jeffehobbs/selene/main/soundsets/index.json"
# The bridge in sclang checks the same rule (letters, digits, hyphens).
VALID_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
MAX_BYTES = 500_000_000  # no pack is near this; it stops a runaway download


class SoundsetError(Exception):
    pass


@dataclass
class Available:
    """A pack listed in the index."""
    id: str
    name: str
    version: str
    about: str
    url: str
    sha256: str
    size: int
    min_selene: str = "0"


@dataclass
class Installed:
    id: str
    path: Path
    manifest: dict = field(repr=False)

    @property
    def name(self) -> str:
        return self.manifest.get("name", self.id)

    @property
    def version(self) -> str:
        return self.manifest.get("version", "?")

    @property
    def banks(self) -> list[str]:
        return sorted(self.manifest.get("banks", {}))

    @property
    def synths(self) -> list[str]:
        """Live synth names, the kit included."""
        names = list(self.manifest.get("synths", {}))
        if kit := self.manifest.get("kit"):
            names.append(kit["name"])
        return sorted(names)

    @property
    def drums(self) -> bool:
        return bool(self.manifest.get("drums"))

    def summary(self) -> str:
        n_banks, synths = len(self.banks), self.manifest.get("synths", {})
        kit = self.manifest.get("kit")
        parts = [f"{n_banks} bank{'s' * (n_banks != 1)}"] if n_banks else []
        if synths:
            parts.append(f"{len(synths)} live synth{'s' * (len(synths) != 1)}"
                         + (f" + {kit['name']}" if kit else ""))
        return ", ".join(parts)


def selene_version() -> str:
    from importlib.metadata import PackageNotFoundError, version
    try:
        return version("selene")
    except PackageNotFoundError:
        return "0"


def rows(available: list[Available], have: list[Installed], selene: str) -> list[dict]:
    """The /sounds list: every pack in the index plus any installed one the
    index no longer lists. kind: installed (current), update, available,
    newer selene (needs a newer app), local (installed, not in the index)."""
    mine = {i.id: i for i in have}
    out = []
    for a in available:
        i = mine.pop(a.id, None)
        too_new = newer(a.min_selene, selene)
        if i and not newer(a.version, i.version):
            kind, status = "installed", f"installed {i.version}"
        elif too_new:
            kind, status = "newer selene", f"{a.version} needs Selene {a.min_selene}"
        elif i:
            kind, status = "update", f"update {i.version} → {a.version}"
        else:
            kind, status = "available", f"available {a.version}"
        out.append({"id": a.id, "name": a.name, "status": status, "kind": kind,
                    "about": a.about, "size": a.size if kind != "installed" else 0,
                    "installed": i is not None, "can_install": not too_new})
    for i in mine.values():
        out.append({"id": i.id, "name": i.name, "status": f"installed {i.version}", "kind": "local",
                    "about": i.manifest.get("about", ""), "size": 0, "installed": True,
                    "can_install": False})
    return out


def _version_key(v: str) -> tuple:
    return tuple(int(p) if p.isdigit() else 0 for p in re.split(r"[.\-+]", v))


def newer(a: str, b: str) -> bool:
    """Is version a newer than version b?"""
    return _version_key(a) > _version_key(b)


# ── what's installed ──────────────────────────────────────────────────────

def installed(root: Path | None = None) -> list[Installed]:
    root = root or ROOT
    out = []
    if not root.is_dir():
        return out
    for d in sorted(root.iterdir()):
        if not (d.is_dir() and VALID_ID.match(d.name)):
            continue  # leftovers like .tmp-* never count
        try:
            manifest = json.loads((d / "manifest.json").read_text())
        except (OSError, ValueError):
            continue
        if manifest.get("id") == d.name:
            out.append(Installed(d.name, d, manifest))
    return out


def remove(soundset_id: str, root: Path | None = None) -> None:
    root = root or ROOT
    if not VALID_ID.match(soundset_id):
        raise SoundsetError(f"not a soundset id: {soundset_id!r}")
    target = root / soundset_id
    if target.is_dir():
        shutil.rmtree(target)


# ── the index ─────────────────────────────────────────────────────────────

def parse_index(data: dict) -> list[Available]:
    out = []
    for entry in data.get("soundsets", []):
        try:
            item = Available(id=entry["id"], name=entry["name"], version=entry["version"],
                             about=entry.get("about", ""), url=entry["url"],
                             sha256=entry["sha256"].lower(), size=int(entry["size"]),
                             min_selene=entry.get("min_selene", "0"))
        except (KeyError, TypeError, ValueError):
            continue
        if VALID_ID.match(item.id) and item.url.startswith("https://"):
            out.append(item)
    return out


async def fetch_index(url: str = INDEX_URL, client: httpx.AsyncClient | None = None) -> list[Available]:
    async with _client(client) as c:
        try:
            r = await c.get(url, timeout=15, follow_redirects=True)
            r.raise_for_status()
            return parse_index(r.json())
        except (httpx.HTTPError, ValueError) as e:
            raise SoundsetError(f"couldn't read the soundset list: {e}") from e


class _client:
    """Use the caller's client (tests pass a mock transport) or a fresh one."""

    def __init__(self, client: httpx.AsyncClient | None):
        self.given, self.own = client, None

    async def __aenter__(self) -> httpx.AsyncClient:
        if self.given:
            return self.given
        self.own = httpx.AsyncClient()
        return self.own

    async def __aexit__(self, *exc) -> None:
        if self.own:
            await self.own.aclose()


# ── installing ────────────────────────────────────────────────────────────

async def install(item: Available, root: Path | None = None,
                  client: httpx.AsyncClient | None = None,
                  progress: Callable[[int, int], None] = lambda done, total: None) -> Installed:
    """Download, verify and unpack a pack, replacing any installed version."""
    root = root or ROOT
    if not VALID_ID.match(item.id):
        raise SoundsetError(f"not a soundset id: {item.id!r}")
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f".tmp-{item.id}-", dir=root) as tmp:
        tmp = Path(tmp)
        archive = tmp / "pack.zip"
        await _download(item, archive, client, progress)
        unpacked = tmp / "unpacked"
        _extract(archive, unpacked)
        try:
            manifest = json.loads((unpacked / "manifest.json").read_text())
        except (OSError, ValueError) as e:
            raise SoundsetError("the pack has no readable manifest.json") from e
        if manifest.get("id") != item.id:
            raise SoundsetError(f"the pack says it's {manifest.get('id')!r}, not {item.id!r}")
        target = root / item.id
        old = tmp / "previous"
        if target.exists():
            target.rename(old)
        try:
            unpacked.rename(target)  # same filesystem: one atomic step
        except OSError as e:
            if old.exists():
                old.rename(target)  # put the old version back
            raise SoundsetError(f"couldn't install into {target}: {e}") from e
    return Installed(item.id, target, manifest)


async def _download(item: Available, dest: Path, client, progress) -> None:
    digest, done = hashlib.sha256(), 0
    async with _client(client) as c:
        try:
            async with c.stream("GET", item.url, timeout=60, follow_redirects=True) as r:
                r.raise_for_status()
                total = int(r.headers.get("content-length") or item.size or 0)
                with dest.open("wb") as f:
                    async for chunk in r.aiter_bytes():
                        done += len(chunk)
                        if done > MAX_BYTES:
                            raise SoundsetError("the download is far bigger than any pack")
                        digest.update(chunk)
                        f.write(chunk)
                        progress(done, total)
        except httpx.HTTPError as e:
            raise SoundsetError(f"download failed: {e}") from e
    if digest.hexdigest() != item.sha256:
        raise SoundsetError("the download doesn't match its checksum; nothing was installed")


def _extract(archive: Path, dest: Path) -> None:
    """Unzip, refusing anything that would land outside dest or isn't a
    plain file or folder (zip slip, symlinks, devices)."""
    dest.mkdir()
    base = dest.resolve()
    try:
        with zipfile.ZipFile(archive) as z:
            for info in z.infolist():
                kind = stat.S_IFMT(info.external_attr >> 16)
                if kind not in (0, stat.S_IFREG, stat.S_IFDIR):
                    raise SoundsetError(f"the pack holds something that isn't a file: {info.filename}")
                target = (dest / info.filename).resolve()
                if target != base and base not in target.parents:
                    raise SoundsetError(f"the pack tries to write outside its folder: {info.filename}")
            z.extractall(dest)
    except zipfile.BadZipFile as e:
        raise SoundsetError("the download isn't a zip file") from e


# ── telling the model ─────────────────────────────────────────────────────

def prompt_section(sets: list[Installed]) -> str:
    """What the model needs about loaded packs: each sound's role and knobs.
    The bare names say nothing (what is synlpg?), so this goes in the prompt."""
    if not sets:
        return ""
    lines = ["Selene soundsets (installed, alongside the lists above):"]
    for ss in sets:
        m = ss.manifest
        lines.append(f"{ss.name}:")
        for bank, info in sorted(m.get("banks", {}).items()):
            lines.append(f"- {bank} (samples, :0-:{info.get('count', 16) - 1}): {info.get('about', '')}")
        for synth, info in sorted(m.get("synths", {}).items()):
            knobs = ", ".join(f"{k} {v}" for k, v in info.get("knobs", {}).items())
            lines.append(f"- {synth} (live synth): {info.get('about', '')}"
                         + (f"; knobs: {knobs}" if knobs else ""))
        if kit := m.get("kit"):
            lines.append(f"- {kit['name']} (live kit): {kit.get('about', '')}")
    lines.append("Live synth knobs are params: `s \"synkick\" # decay 0.8 # drive 0.4`. "
                 "`drive` is defined; `voice`, `decay`, `pitch1` are Tidal's. `:N` picks a "
                 "variant only in the sample banks and the kit; on a live synth n is the pitch, "
                 "so write `s \"synhat*8\"`, not `s \"synhat:2*8\"`.")
    return "\n".join(lines)
