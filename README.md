# selene

Describe music in words; a local LLM writes [TidalCycles](https://tidalcycles.org)
patterns and plays them through SuperCollider + SuperDirt.

![selene](docs/screenshot.png)

Type *"slow dubby techno with a deep kick and offbeat hats"*, and selene asks a
local [Ollama](https://ollama.com) model for Tidal code, sends it to a GHCi
running Tidal (the same way Pulsar's TidalCycles plugin does), and it starts
playing. Follow-up prompts revise what's playing: *"add a minor piano line on
d2"*, *"half-time the drums"*, *"make it sparser"*.

- **Code you can edit.** The generated pattern lands in an editor; tweak it and
  press `ctrl+e` to play your version.
- **Self-correcting.** If GHCi rejects the code, the error goes back to the
  model (up to twice) with a hint about the likely mistake. The previous
  pattern keeps playing until something valid arrives.
- **Grounded in your install.** The model is told which Dirt-Samples banks and
  SuperDirt synths you actually have, and code naming sounds you don't have is
  sent back for a fix before it plays.
- **Everything local.** No cloud APIs; default model is `gemma4:31b-mlx`.
- **Mute by clicking.** Each playing orbit shows as a `d1 d2 d3` chip in the
  top bar: click to mute/unmute, shift-click to solo. Mutes survive the model
  revising the pattern; `esc` clears them.
- **Flow mode.** `ctrl+f` lets the music play itself: levels, tone, space and
  density ebb and flow across the layers, and every few minutes the model
  evolves one layer, crossfaded in. Off until you turn it on. See [Flow](#flow).
- **Session log.** Every pattern that plays is appended to
  `~/.local/share/selene/<date>.tidal`.

## Requirements

- **Python 3.11+** and [uv](https://docs.astral.sh/uv/)
- **GHC + Tidal**: `ghcup install ghc` then `cabal update && cabal install --lib tidal`
  (tested with GHC 9.14.1, Tidal 1.10.3). `ghci` must be on your `PATH`.
- **SuperCollider + SuperDirt**: install SuperCollider, then in it run
  `Quarks.install("SuperDirt")`. SC3-plugins enable the extra `super*` synths.
- **Ollama** with a model pulled: `ollama pull gemma4:31b-mlx` (any model works;
  see `--model`).

## Run

```sh
git clone https://github.com/jeffehobbs/selene && cd selene
uv run selene                # SuperDirt already running in SuperCollider
uv run selene --superdirt    # or let selene boot sclang + SuperDirt for you
```

Or install it as a command: `uv tool install git+https://github.com/jeffehobbs/selene`.

## Keys

| key | |
|---|---|
| `enter` | generate from the prompt and play |
| `ctrl+e` | play the editor contents |
| `esc` | hush (also cancels a generation) |
| `ctrl+r` | regenerate the last prompt |
| `ctrl+n` | new context: next prompt ignores what's playing |
| `ctrl+f` | Flow on / off |
| `ctrl+b` | boot SuperDirt |
| `ctrl+q` | quit (hushes, stops anything selene started) |
| `↑` / `↓` | prompt history |
| click `dN` | mute / unmute that orbit |
| shift-click `dN` | solo that orbit (again to un-solo) |

In the prompt, `!` sends raw Tidal (`!d3 $ s "cp*4"`), and `/hush`, `/cps 0.6`,
`/bpm 128`, `/mute 2`, `/unmute` (all), `/solo 1`, `/flow`, `/model NAME`,
`/new` are commands.

Edits you make in the editor are the base for your next prompt even if you
haven't played them yet, so you can sketch a change and ask the model to run
with it.

## Flow

A mode, off at launch, in the family of
[Thrum](https://github.com/jeffehobbs/thrum),
[Meter](https://github.com/jeffehobbs/meter) and Counter's Flow: the music
keeps moving so you can work inside it, and nothing announces itself.

- **Nothing is set, only ramped.** Every change is a 20–90 s smootherstep
  slide (never under 9 s) with no edge at either end. Each layer's level, tone
  and space breathe on their own prime-numbered clocks, so nothing lines up.
- **Density is a zero-sum budget.** It moves *between* layers (transfers,
  tilts between low and high parts, spotlights, dropouts); the total holds.
- **Structure evolves slowly.** Every 5–11 minutes the model rewrites one
  layer, subtly, crossfaded in over 16 cycles with Tidal's `xfadeIn`, and it
  may add a layer that swells in from silence or fade one out.
- **The key never moves**, and tempo stays put. A rewrite that changes the
  scale is sent back.
- **You lead.** Anything you prompt, play or edit becomes Flow's new
  starting point and holds off structural changes for two minutes. Muted
  layers are left alone.
- **Turning Flow off** glides every control home over 12 s, leaving whatever
  the music evolved into playing and in the editor. `esc` stops it at once.

Under the hood each `dN` is routed through per-orbit controls
(`gain`, `djf`, `room`, `degradeBy`) that selene drives over Tidal's `/ctrl`
OSC port, so nothing is re-evaluated to make the music move. A custom
`--boot` file needs the `SELENE_CTRL_PORT` lines from the bundled
`BootTidal.hs` for Flow to reach it.

## Options

```
--model NAME        Ollama model or family name (default: gemma4:31b-mlx)
--ollama-url URL    default http://localhost:11434
--ghci PATH         GHCi executable (default: ghci)
--boot FILE         BootTidal.hs to load (default: the bundled one)
--superdirt         boot SuperCollider + SuperDirt if it isn't running
--fix-attempts N    times to feed errors back to the model (default: 2)
```

## How it works

GHCi runs as a child process with a BootTidal.hs. Each top-level statement is
wrapped in `:{ … :}` and followed by a sentinel `putStrLn`, so selene knows
exactly which output (and which errors) belong to each evaluation. SuperDirt
is detected with a real OSC `/dirt/handshake`, since `sclang` holds UDP 57120
even when SuperDirt isn't started.

## Tests

```sh
uv run pytest
```

The tests boot Tidal against UDP 57999 with a fake SuperDirt listener, so they
stay silent even while your real SuperDirt is running. The end-to-end test
needs Ollama and a `gemma4` model and is skipped otherwise.

## License

MIT
