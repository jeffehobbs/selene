"""Soundsets: the index, installing (checksums, zip safety, atomic swap),
what the model is told, Flow's knob moves, and /sounds in the app."""

import asyncio
import hashlib
import io
import json
import stat
import zipfile

import httpx
import pytest

from selene import blocks, flow, recorder, soundsets
from selene.app import Selene, parse_args
from selene.files import ConfirmScreen, SoundsScreen
from selene.flow import KNOBS, FlowDirector, osc_messages
from selene.recorder import Recorder, osc

MANIFEST = {
    "id": "rhythm", "name": "Selene Rhythm", "version": "1.0.0", "license": "CC0-1.0",
    "about": "modular drums", "drums": True,
    "banks": {"modkick": {"count": 16, "about": "modular kick; :0 tight … :15 boomy"},
              "modhat": {"count": 16, "about": "closed hat"}},
    "synths": {"synkick": {"about": "live modular kick", "knobs": {"decay": "0.1-1.5 s"}}},
    "kit": {"name": "synkit", "about": "all the live drums"},
}


def make_zip(manifest=MANIFEST, extra: dict | None = None, symlink: str | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("manifest.json", json.dumps(manifest))
        z.writestr("samples/modkick/modkick_00.wav", b"RIFF....WAVE")
        z.writestr("synths.scd", "// live synths")
        for name, data in (extra or {}).items():
            z.writestr(name, data)
        if symlink:
            info = zipfile.ZipInfo(symlink)
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            z.writestr(info, "/etc/passwd")
    return buf.getvalue()


def item_for(data: bytes, version="1.0.0", sha=None, sid="rhythm", min_selene="0") -> soundsets.Available:
    return soundsets.Available(id=sid, name="Selene Rhythm", version=version, about="modular drums",
                               url=f"https://example.test/{sid}-{version}.zip",
                               sha256=sha or hashlib.sha256(data).hexdigest(), size=len(data),
                               min_selene=min_selene)


def serving(data: bytes) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, content=data)))


# ── the module ────────────────────────────────────────────────────────────

async def test_install_then_list_then_remove(tmp_path):
    data = make_zip()
    seen = []
    ss = await soundsets.install(item_for(data), tmp_path, serving(data),
                                 progress=lambda done, total: seen.append((done, total)))
    assert ss.path == tmp_path / "rhythm"
    assert (ss.path / "samples/modkick/modkick_00.wav").exists()
    assert seen[-1] == (len(data), len(data))
    [found] = soundsets.installed(tmp_path)
    assert (found.id, found.version, found.banks) == ("rhythm", "1.0.0", ["modhat", "modkick"])
    assert found.synths == ["synkick", "synkit"] and found.drums
    assert found.summary() == "2 banks, 1 live synth + synkit"
    assert [p.name for p in tmp_path.iterdir()] == ["rhythm"]  # no temp leftovers
    soundsets.remove("rhythm", tmp_path)
    assert soundsets.installed(tmp_path) == []


async def test_a_bad_checksum_installs_nothing(tmp_path):
    data = make_zip()
    with pytest.raises(soundsets.SoundsetError, match="checksum"):
        await soundsets.install(item_for(data, sha="0" * 64), tmp_path, serving(data))
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("bad, why", [
    ({"extra": {"../../evil.scd": "x"}}, "outside"),
    ({"extra": {"/abs/evil.scd": "x"}}, "outside"),
    ({"symlink": "samples/link"}, "isn't a file"),
    ({"manifest": {**MANIFEST, "id": "other"}}, "says it's"),
])
async def test_refuses_unsafe_or_mislabeled_packs(tmp_path, bad, why):
    data = make_zip(**bad)
    with pytest.raises(soundsets.SoundsetError, match=why):
        await soundsets.install(item_for(data), tmp_path, serving(data))
    assert list(tmp_path.iterdir()) == []
    assert not (tmp_path.parent / "evil.scd").exists()


async def test_an_update_replaces_the_old_version(tmp_path):
    old = make_zip(extra={"samples/modgone/modgone_00.wav": "x"})
    await soundsets.install(item_for(old), tmp_path, serving(old))
    new = make_zip({**MANIFEST, "version": "1.1.0"})
    ss = await soundsets.install(item_for(new, "1.1.0"), tmp_path, serving(new))
    assert ss.version == "1.1.0"
    assert not (tmp_path / "rhythm/samples/modgone").exists()


def test_rejects_ids_that_could_be_paths(tmp_path):
    for bad in ("../x", "a/b", "", "UP", ".hidden"):
        with pytest.raises(soundsets.SoundsetError):
            soundsets.remove(bad, tmp_path)
    assert soundsets.parse_index({"soundsets": [
        {"id": "../x", "name": "x", "version": "1", "url": "https://a/b.zip", "sha256": "0", "size": 1},
        {"id": "ok", "name": "x", "version": "1", "url": "http://plain/b.zip", "sha256": "0", "size": 1},
        {"id": "fine", "name": "Fine", "version": "1", "url": "https://a/b.zip", "sha256": "AB", "size": 1},
    ]}) == [soundsets.Available("fine", "Fine", "1", "", "https://a/b.zip", "ab", 1)]


def test_the_list_says_what_enter_would_do(tmp_path):
    have = [soundsets.Installed("rhythm", tmp_path, {"name": "Selene Rhythm", "version": "1.0.0"}),
            soundsets.Installed("gone", tmp_path, {"name": "Gone", "version": "0.1.0"})]
    available = [item_for(b"x", "1.2.0"), item_for(b"x", "2.0.0", sid="synth"),
                 item_for(b"x", "1.0.0", sid="future", min_selene="9.0.0")]
    rows = {r["id"]: r for r in soundsets.rows(available, have, "1.6.0")}
    assert rows["rhythm"]["kind"] == "update" and rows["rhythm"]["status"] == "update 1.0.0 → 1.2.0"
    assert rows["synth"]["kind"] == "available" and rows["synth"]["can_install"]
    assert rows["future"]["kind"] == "newer selene" and not rows["future"]["can_install"]
    assert rows["gone"]["kind"] == "local" and rows["gone"]["installed"]
    same = soundsets.rows([item_for(b"x", "1.0.0")], have[:1], "1.6.0")[0]
    assert same["kind"] == "installed" and same["size"] == 0
    assert soundsets.newer("1.10.0", "1.9.0") and not soundsets.newer("1.0.0", "1.0.0")


def test_the_model_is_told_what_each_sound_is(tmp_path):
    text = soundsets.prompt_section([soundsets.Installed("rhythm", tmp_path, MANIFEST)])
    assert "modkick (samples, :0-:15): modular kick" in text
    assert "synkick (live synth): live modular kick; knobs: decay 0.1-1.5 s" in text
    assert "synkit (live kit)" in text and "# drive" in text
    assert soundsets.prompt_section([]) == ""


# ── Flow ──────────────────────────────────────────────────────────────────

def test_soundset_drums_count_as_drums_and_live_synths_are_known():
    stmt_kick, stmt_synth = 'd1 $ s "modkick*4"', 'd2 $ n "0 3" # s "synkick" # decay 0.8'
    assert not flow.is_drums(stmt_kick) and not flow.plays_live_synth(stmt_synth)
    flow.use_soundsets({"modkick", "synkick"}, {"synkick", "synkit"})
    assert flow.is_drums(stmt_kick) and flow.plays_live_synth(stmt_synth)
    assert not flow.plays_live_synth(stmt_kick)


def test_flow_moves_knobs_only_on_live_synth_layers_at_depth_3():
    flow.use_soundsets(set(), {"synpluck"})
    layers = {"d1": 'd1 $ n "0 3 7" # s "synpluck"', "d2": 'd2 $ n "0 3 7" # s "superpiano"'}
    moved = {}
    for depth in (2, 3):
        director = FlowDirector(seed=7, depth=depth)
        director.set_depth(depth, 0.0, None)  # no warm-up
        director.rebase(layers, 0.0)
        names = set()
        for t in range(0, 900, 2):
            updates, _, _ = director.tick(float(t))
            names |= {name for name, _ in updates}
        moved[depth] = names
    knob_names = {flow.ctrl_name(k, o) for k in KNOBS for o in ("d1", "d2")}
    assert moved[2] & knob_names == set()
    assert moved[3] & knob_names == {flow.ctrl_name(k, "d1") for k in KNOBS}


def test_knobs_go_home_when_a_layer_stops_playing_a_live_synth():
    flow.use_soundsets(set(), {"synpluck"})
    director = FlowDirector(seed=3, depth=3)
    director.set_depth(3, 0.0, None)
    director.rebase({"d1": 'd1 $ s "synpluck"'}, 0.0)
    for t in range(0, 600, 2):
        director.tick(float(t))
    assert any(director.orbits["d1"].values[k] != flow.CONTINUOUS[k][0] for k in KNOBS)
    director.rebase({"d1": 'd1 $ s "superpiano"'}, 600.0)
    for t in range(600, 800, 2):
        director.tick(float(t))
    assert all(director.orbits["d1"].values[k] == flow.CONTINUOUS[k][0] for k in KNOBS)


def test_flowify_routes_the_knobs_through_flow_inputs():
    out = blocks.flowify('d3 $ s "synkick" # decay 0.8')
    assert '|< pF "fvoice" (cF 0 "fl_fvoice3")' in out
    assert '|< pF "fdecay" (cF 1 "fl_fdecay3")' in out
    assert "# decay 0.8" in out  # the code's own knob is untouched


# ── the app ───────────────────────────────────────────────────────────────

class FakeBridge(asyncio.DatagramProtocol):
    """Selene's OSCdefs in sclang (recorder block v2), minus SuperDirt."""

    def __init__(self):
        self.version = recorder.VERSION
        self.loads: list[str] = []
        self.unloads: list[list[str]] = []

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        for address, args in osc_messages(data):
            if address == "/selene/rec/ping":
                self.transport.sendto(osc("/selene/rec/pong", self.version, 1, 0), addr)
            elif address == "/selene/sounds/load":
                self.loads.append(args[0])
                self.transport.sendto(osc("/selene/sounds/loaded", args[0]), addr)
            elif address == "/selene/sounds/unload":
                self.unloads.append(list(args))
                self.transport.sendto(osc("/selene/sounds/unloaded", len(args)), addr)


@pytest.fixture
async def bridge():
    loop = asyncio.get_running_loop()
    transport, proto = await loop.create_datagram_endpoint(FakeBridge, local_addr=("127.0.0.1", 0))
    proto.port = transport.get_extra_info("sockname")[1]
    yield proto
    transport.close()


async def wait_for(cond, timeout, step=0.05):
    for _ in range(int(timeout / step)):
        if cond():
            return True
        await asyncio.sleep(step)
    return False


def no_ghci_app(tmp_path, bridge) -> Selene:
    app = Selene(parse_args(["--ghci", "/nonexistent/ghci", "--dir", str(tmp_path)]))
    app.recorder = Recorder(bridge.port)
    return app


async def test_installed_packs_load_when_superdirt_is_up(tmp_path, bridge, private_soundsets):
    data = make_zip()
    await soundsets.install(item_for(data), private_soundsets, serving(data))
    app = no_ghci_app(tmp_path, bridge)
    async with app.run_test(size=(120, 34)):
        assert await wait_for(lambda: app.sounds_loaded, 5)
        assert bridge.loads == ["rhythm"]
        assert "synkick (live synth)" in app.system_prompt
        assert {"modkick", "synkick", "synkit"} <= app.known_sounds
        assert "synkick" in app.lane_model.synths
        assert flow.is_drums('d1 $ s "modkick"') and flow.plays_live_synth('d1 $ s "synkit:2"')
        # SuperDirt goes away: its packs leave the prompt until it's back.
        app._set(dirt="off")
        assert not app.sounds_loaded and "synkick" not in app.system_prompt
        app._set(dirt="listening")
        assert await wait_for(lambda: app.sounds_loaded, 5)
        assert bridge.loads == ["rhythm", "rhythm"]
        await app.action_quit()


async def test_an_old_bridge_means_a_hint_not_a_load(tmp_path, bridge, private_soundsets):
    bridge.version = 1  # someone's sclang with the v1 recorder block
    data = make_zip()
    await soundsets.install(item_for(data), private_soundsets, serving(data))
    app = no_ghci_app(tmp_path, bridge)
    async with app.run_test(size=(120, 34)):
        assert await wait_for(lambda: app._sounds_unloadable_said, 5)
        assert not bridge.loads and not app.sounds_loaded
        assert "synkick" not in app.system_prompt
        await app.action_quit()


async def test_sounds_installs_then_removes(tmp_path, bridge, private_soundsets, monkeypatch):
    data = make_zip()
    item = item_for(data)
    real_install = soundsets.install

    async def fetch_index(*a, **k):
        return [item]

    async def install(it, root=None, client=None, progress=lambda d, t: None):
        return await real_install(it, root, serving(data), progress)

    monkeypatch.setattr(soundsets, "fetch_index", fetch_index)
    monkeypatch.setattr(soundsets, "install", install)
    app = no_ghci_app(tmp_path, bridge)
    async with app.run_test(size=(120, 40)) as pilot:
        app.command("sounds")
        assert await wait_for(lambda: isinstance(app.screen, SoundsScreen), 5)
        await pilot.pause()
        assert str(app.screen.query_one("#install").label) == "Install"
        assert app.screen.query_one("#remove").disabled
        await pilot.press("enter")  # Enter installs the highlighted pack
        assert await wait_for(lambda: app.sounds_loaded, 5)
        assert (private_soundsets / "rhythm/manifest.json").exists()
        assert bridge.loads == ["rhythm"] and "synkick" in app.system_prompt

        app.command("sounds")
        assert await wait_for(lambda: isinstance(app.screen, SoundsScreen), 5)
        await pilot.pause()
        assert str(app.screen.query_one("#install").label) == "Reinstall"
        await pilot.click("#remove")
        assert await wait_for(lambda: isinstance(app.screen, ConfirmScreen), 5)
        await pilot.press("enter")  # Cancel is the default: nothing removed
        assert await wait_for(lambda: not app._modal(), 2)
        assert (private_soundsets / "rhythm").exists()

        app.command("sounds")
        assert await wait_for(lambda: isinstance(app.screen, SoundsScreen), 5)
        await pilot.pause()
        await pilot.click("#remove")
        assert await wait_for(lambda: isinstance(app.screen, ConfirmScreen), 5)
        await pilot.pause()
        await pilot.click("#yes")
        assert await wait_for(lambda: not (private_soundsets / "rhythm").exists(), 5)
        assert bridge.unloads == [["modhat", "modkick", "synkick", "synkit"]]
        assert not app.sounds_loaded and "synkick" not in app.system_prompt
        await app.action_quit()


async def test_installing_with_superdirt_off_offers_to_start_it(tmp_path, bridge, private_soundsets,
                                                                monkeypatch):
    """Usable as soon as it's installed: with SuperDirt off, the install offers
    to start it, and the pack is loaded (once) as soon as it's up."""
    import selene.app as app_module
    data = make_zip()
    item = item_for(data)
    real_install = soundsets.install

    async def fetch_index(*a, **k):
        return [item]

    async def install(it, root=None, client=None, progress=lambda d, t: None):
        return await real_install(it, root, serving(data), progress)

    status = {"now": "off"}
    boots = []

    async def boot_dirt(self):
        boots.append(1)
        status["now"] = "listening"
        self._set(dirt="listening")
        return True

    monkeypatch.setattr(soundsets, "fetch_index", fetch_index)
    monkeypatch.setattr(soundsets, "install", install)
    monkeypatch.setattr(app_module, "dirt_status", lambda *a, **k: status["now"])
    monkeypatch.setattr(Selene, "_boot_dirt", boot_dirt)
    app = no_ghci_app(tmp_path, bridge)
    async with app.run_test(size=(120, 40)) as pilot:
        # The launch offer to start SuperDirt: not now.
        assert await wait_for(lambda: isinstance(app.screen, ConfirmScreen), 5)
        await pilot.press("escape")
        assert await wait_for(lambda: not app._modal(), 2)
        assert app.state["dirt"] == "off"

        app.command("sounds")
        assert await wait_for(lambda: isinstance(app.screen, SoundsScreen), 5)
        await pilot.pause()
        await pilot.press("enter")
        assert await wait_for(lambda: isinstance(app.screen, ConfirmScreen), 5)
        assert "Start it with SuperDirt, then load the sounds?" in str(
            app.screen.query_one("Static").render())
        await pilot.press("enter")  # Start is the default here
        assert await wait_for(lambda: app.sounds_loaded, 5)
        await pilot.pause(0.5)
        assert boots == [1] and bridge.loads == ["rhythm"]
        assert "synkit (live kit)" in app.system_prompt
        await app.action_quit()
