"""AirPods as input and output: scsynth quits over the mic's sample rate, and
selene boots it again with the built-in mic."""

import asyncio

import selene.superdirt as sd


class FakeStdin:
    def __init__(self):
        self.sent: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.sent.append(data)

    async def drain(self) -> None:
        pass


class FakeSclang:
    returncode = None

    def __init__(self, lines: list[str]):
        self.stdout = asyncio.StreamReader()
        for line in lines:
            self.stdout.feed_data(line.encode() + b"\n")
        self.stdout.feed_eof()
        self.stdin = FakeStdin()


async def run(lines: list[str], monkeypatch) -> tuple[FakeSclang, list[str]]:
    real_sleep = asyncio.sleep
    monkeypatch.setattr(sd.asyncio, "sleep", lambda s: real_sleep(0))
    out: list[str] = []
    dirt = sd.SuperDirt(on_output=out.append)
    dirt.proc = FakeSclang(lines)
    dirt.startup = "/x/startup.scd"
    ready = asyncio.get_running_loop().create_future()
    await dirt._read(ready)
    for _ in range(5):
        await real_sleep(0)  # let the retry task run
    return dirt.proc, out


async def test_rate_mismatch_retries_once_with_builtin_mic(monkeypatch):
    mismatch = "WARNING: Input sample rate is 24000, but output is 48000. Attempting to ..."
    proc, out = await run([mismatch, "ERROR: Setting sample rate failed.", mismatch], monkeypatch)
    assert len(proc.stdin.sent) == 1
    sent = proc.stdin.sent[0].decode()
    assert sent.startswith("s.options.inDevice = ServerOptions.inDevices.detect")
    assert '"/x/startup.scd".load;' in sent
    assert any("built-in mic" in line for line in out)


async def test_normal_boot_sends_nothing(monkeypatch):
    proc, _ = await run(["SC_AudioDriver: sample rate = 48000.000000",
                         "SuperDirt: listening on port 57120"], monkeypatch)
    assert proc.stdin.sent == []
