"""A fake SuperDirt: collect the OSC messages Tidal sends to a UDP port."""

import asyncio
import re
import shutil
import socket
import struct
from pathlib import Path

import pytest

from selene.superdirt import dirt_listening

# Tests boot Tidal against this port so they stay silent even when a real
# SuperDirt is running on 57120.
TEST_PORT = 57999
TEST_BOOT = Path(__file__).with_name("BootTidal_test.hs")


def osc_strings(packet: bytes) -> list[str]:
    """Printable runs in an OSC packet; bundles prefix each message with binary
    timetag/size bytes, so split on non-printables rather than on nulls."""
    return [m.decode() for m in re.findall(rb"[\x20-\x7e]{2,}", packet)]


def osc_decode(packet: bytes) -> list[tuple[str, list]]:
    """(address, args) for every message in an OSC packet or bundle."""
    out = []

    def pad(n):
        return (n + 4) & ~3

    def message(b):
        i = b.index(b"\0")
        addr, j = b[:i].decode(), pad(i)
        k = b.index(b"\0", j)
        tags, j = b[j + 1:k].decode(), pad(k)
        args = []
        for t in tags:
            if t == "s":
                k = b.index(b"\0", j)
                args.append(b[j:k].decode())
                j = pad(k)
            elif t in "fi":
                args.append(struct.unpack(">f" if t == "f" else ">i", b[j:j + 4])[0])
                j += 4
            elif t == "d":
                args.append(struct.unpack(">d", b[j:j + 8])[0])
                j += 8
        out.append((addr, args))

    def walk(b):
        if b.startswith(b"#bundle"):
            j = 16
            while j < len(b):
                n = struct.unpack(">i", b[j:j + 4])[0]
                walk(b[j + 4:j + 4 + n])
                j += 4 + n
        else:
            message(b)

    walk(packet)
    return out


class FakeDirt(asyncio.DatagramProtocol):
    def __init__(self):
        self.messages: list[list[str]] = []
        self.packets: list[bytes] = []

    def datagram_received(self, data, addr):
        self.messages.append(osc_strings(data))
        self.packets.append(data)

    def events(self) -> list[dict]:
        """/dirt/play messages as {param: value} dicts."""
        return [dict(zip(args[0::2], args[1::2]))
                for p in self.packets for addr, args in osc_decode(p) if addr == "/dirt/play"]

    def clear(self) -> None:
        self.messages.clear()
        self.packets.clear()

    def plays(self) -> list[list[str]]:
        return [m for m in self.messages if any("/dirt/play" in s for s in m)]


@pytest.fixture
async def fake_dirt():
    if dirt_listening(TEST_PORT):
        pytest.skip(f"UDP {TEST_PORT} is in use")
    loop = asyncio.get_running_loop()
    transport, proto = await loop.create_datagram_endpoint(
        FakeDirt, local_addr=("127.0.0.1", TEST_PORT), family=socket.AF_INET)
    yield proto
    transport.close()


@pytest.fixture(autouse=True)
def private_settings(tmp_path, monkeypatch):
    """Every test gets its own settings file, never the player's real one."""
    monkeypatch.setenv("SELENE_SETTINGS", str(tmp_path / "settings.json"))
    return tmp_path / "settings.json"


needs_ghci = pytest.mark.skipif(shutil.which("ghci") is None, reason="ghci not installed")
