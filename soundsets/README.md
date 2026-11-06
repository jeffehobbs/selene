# Selene soundsets

Modular-style sound packs that Selene can install into SuperDirt. Each sound is
one SuperCollider patch that ships two ways:

- **`mod*` sample banks.** The patch is rendered offline with 16 seeded knob
  settings, ordered along an axis (for example `modkick:0` is short and tight,
  `:15` long and boomy). Each file is trimmed and leveled to sit next to
  Dirt-Samples' 808 kit.
- **`syn*` live synths.** The same patch is wrapped for SuperDirt, so its knobs
  can change on every event: `voice` (0–1 timbre), `decay` (seconds), `drive`
  (0–1) and `pitch1`, with `n`/`note` for tuning. The knobs are Tidal params
  that don't start any SuperDirt effect. Flow has its own inputs (`fvoice`,
  `fdecay`, `fdrive`), so it never overwrites a value the code set. Drums ring
  for their full decay regardless of the step length; use `# sustain` to
  choke one.

The Rhythm pack also has `synkit`, all the live drums under one name.
`synkit:0` is the kick, `:1` the snare, `:2` the closed hat, `:3` the open
hat, `:4` the clap, `:5` the tom, `:6` metal and `:7` the zap. `n` only picks
the drum; `note` tunes it.

The sounds are CC0. The code is MIT, like the rest of Selene.

## Layout

    rhythm/patches.scd   the DSP: one function per voice, plus how its bank varies
    rhythm/pack.json     name, version, and what each sound is (this goes to the model)
    render.scd           offline renderer (NRT), run with an isolated HOME
    live.scd             SuperDirt wrapper, shipped in each pack as synths.scd
    e2e.scd              silent check of the live synths in a real SuperDirt
    build.py, wav.py     the build (standard library only)
    build/               output, git-ignored

## Building

```
uv run python soundsets/build.py rhythm --check
```

This renders every voice (about 20 s), levels the banks, and writes
`build/rhythm/pack/` and `selene-rhythm-<version>.zip`, printing its sha256.
It also writes listening reels to `build/rhythm/reel/`: one per bank with
every variant in order, `live.wav` with the live synths at their default
knobs, and `groove.wav`. Rebuilds are byte-identical.

`--check` then boots a private SuperDirt on BlackHole (it refuses to run
without it, so nothing plays through the speakers). It loads the pack the
way Selene will and verifies the knobs, the kit, Flow's inputs and the drum
lengths.
