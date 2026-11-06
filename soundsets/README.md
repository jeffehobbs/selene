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

The Synth pack is mostly live synths, which play every note in tune where a
sample would only be sped up. Its voices come in two kinds. **Gated** ones
(`gated: true`: bass, acid, lead, pad, drone) last the event's sustain, so
Tidal's step, `legato` and `sustain` shape the note as they do on
`supersaw`. **Struck** ones (pluck, bell, lpg, and every Rhythm drum) ring
for their decay whatever the step, like a sample. Melodic synths add
`slide`, `lfo`, `detune` and `accelerate` to the knobs. Its four banks
(`modtex moddrone modstab modsweep`) are stereo and 16-bit, which keeps the
pack near 33 MB.

The sounds are CC0. The code is MIT, like the rest of Selene.

## Layout

    <pack>/patches.scd   the DSP: one function per voice, plus how its bank varies
    <pack>/pack.json     name, version, and what each sound is (this goes to the model)
    <pack>/e2e.scd       the pack's check events (build.py's EXPECT says what they must show)
    synth/demo.scd       a listening demo, played through SuperDirt
    render.scd           offline renderer (NRT), run with an isolated HOME
    live.scd             SuperDirt wrapper, shipped in each pack as synths.scd
    e2e.scd              plays packs and an events file in a silent SuperDirt, recording it
    build.py, wav.py     the build (standard library only)
    build/               output, git-ignored

## Building

```
uv run python soundsets/build.py rhythm --check
uv run python soundsets/build.py synth --check
```

Each renders every voice (seconds), levels the banks, and writes
`build/<pack>/pack/` and `selene-<pack>-<version>.zip`, printing its sha256.
It also writes listening reels to `build/<pack>/reel/`: one per bank with
every variant in order, `live.wav` with the live synths at their default
knobs, and for Rhythm `groove.wav`. Rebuilds are byte-identical.

`--check` then boots a private SuperDirt on BlackHole (it refuses to run
without it, so nothing plays through the speakers). It loads the pack the
way Selene will and verifies the knobs, Flow's inputs, note lengths and
pitch (Rhythm: the kit and drum lengths; Synth: tuning, gated and struck
lengths, slide, accelerate, the drone banks' pitch classes). For Synth it
also records `reel/demo.wav`, a short piece using every sound, with
Rhythm's drums when that pack is built too.
