"""A fake SuperDirt: collect the OSC messages Tidal sends to a UDP port."""

import asyncio
import re
import shutil
import socket
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


class FakeDirt(asyncio.DatagramProtocol):
    def __init__(self):
        self.messages: list[list[str]] = []

    def datagram_received(self, data, addr):
        self.messages.append(osc_strings(data))

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


needs_ghci = pytest.mark.skipif(shutil.which("ghci") is None, reason="ghci not installed")
