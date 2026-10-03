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
| `ctrl+b` | boot SuperDirt |
| `ctrl+q` | quit (hushes, stops anything selene started) |
| `↑` / `↓` | prompt history |

In the prompt, `!` sends raw Tidal (`!d3 $ s "cp*4"`), and `/hush`, `/cps 0.6`,
`/bpm 128`, `/model NAME`, `/new` are commands.

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
