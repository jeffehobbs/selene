"""Recording to WAV through sclang, prompt history, and the SuperDirt offer."""

import asyncio
import time
from pathlib import Path

import pytest
from conftest import TEST_BOOT, needs_ghci

import selene.app as app_module
from selene import recorder, settings
from selene.app import REC_LEAD, PromptInput, Selene, parse_args
from selene.files import ConfirmScreen
from selene.flow import PHRASE, osc_messages
from selene.recorder import Recorder, osc


async def wait_for(cond, timeout, step=0.05):
    for _ in range(int(timeout / step)):
        if cond():
            return True
        await asyncio.sleep(step)
    return False


def dialog(app):
    return isinstance(app.screen, ConfirmScreen) and bool(app.screen.query("Button"))


class FakeSC(asyncio.DatagramProtocol):
    """The recorder OSCdefs as sclang runs them, minus the audio."""

    def __init__(self):
        self.answering = True
        self.calls: list[tuple[str, float]] = []
        self.tails: list[float] = []
        self.path: Path | None = None
        self.recording = False

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        for address, args in osc_messages(data):
            self.calls.append((address, time.time()))
            if not self.answering:
                continue
            name = address.rsplit("/", 1)[-1]
            reply = {"ping": ("/selene/rec/pong", recorder.VERSION, 1, int(self.recording))}
            if name == "prepare":
                self.path = Path(args[0])
                self.path.write_bytes(b"RIFF")  # sclang writes the header at once
                reply["prepare"] = ("/selene/rec/ready", args[0])
            elif name == "go":
                self.recording = True
                reply["go"] = ("/selene/rec/started", str(self.path))
            elif name == "stop":
                self.tails.append(args[0])
                self.recording = False
                reply["stop"] = ("/selene/rec/stopped", str(self.path))
            if name in reply:
                self.transport.sendto(osc(*reply[name]), addr)

    def times(self, name):
        return [t for a, t in self.calls if a.endswith("/" + name)]


@pytest.fixture
async def fake_sc():
    loop = asyncio.get_running_loop()
    transport, proto = await loop.create_datagram_endpoint(FakeSC, local_addr=("127.0.0.1", 0))
    proto.port = transport.get_extra_info("sockname")[1]
    yield proto
    transport.close()


# ── pieces ────────────────────────────────────────────────────────────────

def test_osc_roundtrip():
    assert osc_messages(osc("/selene/rec/prepare", "/tmp/a b.wav", 3)) == \
        [("/selene/rec/prepare", ["/tmp/a b.wav", 3])]


def test_install_appends_once_and_updates(tmp_path):
    startup = tmp_path / "startup.scd"
    assert recorder.installed_version(startup) is None
    assert recorder.install(startup) is None  # no file yet: nothing to back up
    assert startup.read_text() == recorder.SC_CODE

    mine = "(\ns.reboot { ~dirt = SuperDirt(2, s) };\n);\n"
    startup.write_text(mine)
    backup = recorder.install(startup)
    assert backup.read_text() == mine
    text = startup.read_text()
    assert text.startswith(mine.rstrip()) and text.endswith(recorder.SC_CODE)
    assert recorder.installed_version(startup) == recorder.VERSION

    # An older block is replaced in place, never duplicated; the rest survives.
    startup.write_text(text.replace(f"recorder v{recorder.VERSION}", "recorder v0") + "// mine\n")
    assert recorder.installed_version(startup) == 0
    recorder.install(startup)
    text = startup.read_text()
    assert text.count(recorder.BEGIN) == 1 and text.count(recorder.END) == 1
    assert "// mine" in text and recorder.installed_version(startup) == recorder.VERSION


async def test_recorder_client(fake_sc, tmp_path):
    rec = Recorder(fake_sc.port)
    assert (await rec.ping()).version == recorder.VERSION
    await rec.prepare(tmp_path / "x.wav")
    await rec.go()
    assert fake_sc.recording
    await rec.stop()
    fake_sc.answering = False
    assert await rec.ping(0.2) is None
    with pytest.raises(RuntimeError):
        await rec.go()
    rec.close()


async def test_prompt_history_survives_relaunch(private_settings):
    from textual.app import App

    class Harness(App):
        def compose(self):
            yield PromptInput(id="prompt")

    async with Harness().run_test() as pilot:
        field = pilot.app.query_one(PromptInput)
        for text in ("dub techno", "dub techno", "/flow 3", "add hats"):
            field.remember(text)
    assert settings.load_history() == ["dub techno", "/flow 3", "add hats"]

    async with Harness().run_test() as pilot:  # a later launch
        field = pilot.app.query_one(PromptInput)
        field.focus()
        await pilot.press("up")
        assert field.value == "add hats"
        await pilot.press("up", "up")
        assert field.value == "dub techno"
        await pilot.press("down", "down", "down")
        assert field.value == ""


# ── in the app ────────────────────────────────────────────────────────────

@needs_ghci
async def test_records_on_phrase_boundaries(tmp_path, fake_dirt, fake_sc):
    app = Selene(parse_args(["--boot", str(TEST_BOOT), "--dir", str(tmp_path)]))
    app.recorder = Recorder(fake_sc.port)
    async with app.run_test(size=(120, 34)) as pilot:
        assert await wait_for(lambda: app.state["ghci"] == "ready", 60)
        app.query_one("#code").text = 'setcps 1\nd1 $ s "bd*4"'
        await pilot.press("ctrl+e")
        assert await wait_for(lambda: app.clock.known, 5)

        await pilot.press("ctrl+g")
        assert await wait_for(lambda: app.rec and app.rec["phase"] == "armed", 3)
        assert await wait_for(lambda: app.rec and app.rec["phase"] == "recording", PHRASE + 2)
        went = fake_sc.times("go")[0]
        cycle = app.clock.cycle_at(went + REC_LEAD)
        assert abs(cycle - round(cycle / PHRASE) * PHRASE) < 0.02, cycle  # on the downbeat
        assert fake_sc.path.parent == tmp_path and fake_sc.path.name.startswith("selene-")
        assert fake_sc.path.suffix == ".wav"

        await pilot.press("ctrl+g")
        assert await wait_for(lambda: app.rec is None, PHRASE + 2)
        stopped = fake_sc.times("stop")[-1]
        cycle = app.clock.cycle_at(stopped + REC_LEAD)
        assert abs(cycle - round(cycle / PHRASE) * PHRASE) < 0.02, cycle
        take = fake_sc.path
        assert take.exists()  # kept
        assert fake_sc.tails[-1] == 8.0  # rings out (up to the default 8 s)

        # Armed and called off: the empty file goes.
        await pilot.press("ctrl+g")
        assert await wait_for(lambda: app.rec and fake_sc.path != take, 3)
        armed = fake_sc.path
        assert await wait_for(lambda: armed.exists(), 2)
        await pilot.press("ctrl+g")
        assert await wait_for(lambda: app.rec is None, 3)
        assert not armed.exists()

        # /record dir points takes elsewhere (remembered in settings.json).
        app.command("record tail 0")
        assert settings.load()["record_tail"] == 0
        app.command(f"record dir {tmp_path / 'takes'}")
        assert settings.load()["record_dir"] == str(tmp_path / "takes")
        app.command("hush")
        assert await wait_for(lambda: not app.playing_code, 3)
        app.command("record")  # nothing playing: starts at once
        assert await wait_for(lambda: app.rec and app.rec["phase"] == "recording", 2)
        assert fake_sc.path.parent == tmp_path / "takes"
        app.command("record")
        assert await wait_for(lambda: app.rec is None, 2)  # nothing playing: stops at once
        assert fake_sc.tails[-1] == 0
        app.command("record")
        assert await wait_for(lambda: app.rec and app.rec["phase"] == "recording", 2)
        app.command("record tail 5")
        await app.action_quit()  # quitting closes the take, without waiting for a tail
    assert not fake_sc.recording and fake_sc.tails[-1] == 0


@needs_ghci
async def test_adds_recorder_to_startup_and_restarts(tmp_path, fake_dirt, fake_sc, monkeypatch):
    startup = tmp_path / "startup.scd"
    startup.write_text("// the player's own\n")
    monkeypatch.setattr(app_module, "STARTUP", startup)
    restarts = []

    async def stop_external():
        restarts.append("stop")
        return True

    async def boot_dirt(self):
        restarts.append("boot")
        fake_sc.answering = True  # the restarted sclang loaded startup.scd
        return True

    monkeypatch.setattr(app_module, "stop_external", stop_external)
    monkeypatch.setattr(Selene, "_boot_dirt", boot_dirt)
    fake_sc.answering = False  # someone else's sclang, without the recorder
    app = Selene(parse_args(["--boot", str(TEST_BOOT), "--dir", str(tmp_path)]))
    app.recorder = Recorder(fake_sc.port)
    async with app.run_test(size=(120, 34)) as pilot:
        assert await wait_for(lambda: app.state["ghci"] == "ready", 60)

        # Cancel (the default) leaves startup.scd alone.
        await pilot.press("ctrl+g")
        assert await wait_for(lambda: dialog(app), 5)
        assert "startup.scd" in str(app.screen.query_one("Static").render())
        await pilot.press("enter")
        assert await wait_for(lambda: not app._modal(), 2)
        assert startup.read_text() == "// the player's own\n" and not restarts

        await pilot.press("ctrl+g")
        assert await wait_for(lambda: dialog(app), 5)
        await pilot.pause()  # laid out, so the click lands on the button
        await pilot.click("#yes")
        assert await wait_for(lambda: app.rec and app.rec["phase"] == "recording", 5)
        assert restarts == ["stop", "boot"]
        assert recorder.installed_version(startup) == recorder.VERSION
        assert startup.read_text().startswith("// the player's own")
        assert (tmp_path / "startup.scd.selene-bak").read_text() == "// the player's own\n"
        await app.action_quit()


@needs_ghci
async def test_offers_to_start_superdirt(monkeypatch):
    monkeypatch.setattr(app_module, "dirt_status", lambda *a, **k: "off")
    booted = []
    monkeypatch.setattr(Selene, "action_boot_dirt", lambda self: booted.append(1))
    app = Selene(parse_args(["--boot", str(TEST_BOOT)]))
    async with app.run_test(size=(120, 34)) as pilot:
        assert await wait_for(lambda: dialog(app), 5)
        assert app.screen.focused.id == "yes"  # Start is the default here
        await pilot.press("escape")  # declines, and doesn't hush anything
        assert await wait_for(lambda: not app._modal(), 2)
        assert not booted
        await app.action_quit()

    app = Selene(parse_args(["--boot", str(TEST_BOOT)]))
    async with app.run_test(size=(120, 34)) as pilot:
        assert await wait_for(lambda: dialog(app), 5)
        await pilot.press("enter")
        assert await wait_for(lambda: booted, 2)
        await app.action_quit()


def test_repl_line_is_one_entry():
    from selene.superdirt import repl_line
    line = repl_line(recorder.SC_CODE)
    assert line.endswith(b"\x0c\n") and line.count(b"\n") == 1
    assert b"//" not in line and b"OSCdef(\\seleneRecStop" in line
    assert line.count(b"{") == line.count(b"}")


@needs_ghci
async def test_says_ready_once_superdirt_is_up(monkeypatch):
    monkeypatch.setattr(app_module, "dirt_status", lambda *a, **k: "off")
    app = Selene(parse_args(["--boot", str(TEST_BOOT)]))

    def readies():
        return sum(s.text.strip() == "Ready." for s in app.query_one("#log").lines)

    async with app.run_test(size=(120, 34)) as pilot:
        assert await wait_for(lambda: app.state["ghci"] == "ready", 60)
        assert await wait_for(lambda: dialog(app), 5)
        await pilot.press("escape")  # not now
        assert readies() == 0  # Tidal alone isn't ready: nothing would sound
        app._dirt_line("SuperDirt: listening to Tidal on port 57120")
        await pilot.pause()
        assert readies() == 1
        app._ghci_line("Connected to SuperDirt.")  # already said
        app._set(dirt="booting")  # a restart
        app._set(dirt="listening")
        await pilot.pause()
        assert readies() == 2
        await app.action_quit()
