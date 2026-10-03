import asyncio

from conftest import TEST_BOOT, needs_ghci

from selene.ghci import Ghci


@needs_ghci
async def test_ghci_plays_to_superdirt_port_and_reports_errors(fake_dirt):
    ghci = Ghci(boot=TEST_BOOT)
    assert (await ghci.start()).ok

    good = await ghci.eval('setcps (130/60/4)\nd1 $ stack [\n  s "bd*4",\n  s "hh*8"\n]\n  # gain 0.9')
    assert good.ok, good.error_text
    await asyncio.sleep(2)
    plays = fake_dirt.plays()
    assert plays, f"no /dirt/play messages; got {fake_dirt.messages[:3]}"
    sounds = {s for m in plays for s in m}
    assert {"bd", "hh"} <= sounds

    type_err = await ghci.eval('d1 $ s "bd" # nonsense 3')
    assert not type_err.ok and "nonsense" in type_err.error_text

    parse_err = await ghci.eval('d1 $ s "bd(3,8"')
    assert not parse_err.ok and "Syntax error" in parse_err.error_text

    # Stops at the first failing statement: d2 never runs.
    partial = await ghci.eval('d1 $ s "bd" # nope 1\nd2 $ s "cp*16"')
    assert not partial.ok

    assert (await ghci.hush()).ok
    await asyncio.sleep(0.5)
    fake_dirt.messages.clear()
    await asyncio.sleep(1)
    assert not fake_dirt.plays(), "still playing after hush"
    await ghci.stop()
    assert not ghci.running
