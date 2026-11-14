# hermes-alexa

Alexa as a native channel for [Hermes Agent](https://github.com/NousResearch/hermes-agent) —
talk to Hermes through your Echo, and let Hermes talk back, **without replacing Alexa**.

> 🚧 Phase 1 of 4: Hermes can make your Echo talk. Talking *to* Hermes through Alexa is next.
> 🇧🇷 [Leia em português](README.pt-BR.md)

## What works today

Five tools, usable from any Hermes channel (Telegram, CLI, desktop, cron…):

| Tool | What it does |
|---|---|
| `alexa_speak` | Say something on an Echo (`announce: true` plays the chime first) |
| `alexa_command` | Give Alexa a command as if spoken: "turn off the living room light", timers, music |
| `alexa_devices` | List the Echos on your Amazon account |
| `alexa_volume` | Set an Echo's volume |
| `alexa_routines` | List or run your Alexa routines |

Plus `hermes alexa setup | login | status | start | stop | say "text"`.

Tested with an Echo Dot (5th gen) on a Brazilian Amazon account (amazon.com.br).

## Install

Requires [uv](https://docs.astral.sh/uv/) (bundled with Hermes on Windows) and an Amazon
account whose 2-step verification uses an **authenticator app** (SMS is not supported).

```bash
git clone https://github.com/pablo-afterlife/hermes-alexa ~/.hermes/plugins/hermes-alexa
hermes plugins enable hermes-alexa
hermes alexa setup        # installs the bridge, asks for your Amazon login once
hermes alexa say "Hello from Hermes"
```

Your password is never stored; only the Amazon session, in
`<hermes home>/plugin-data/hermes-alexa/` (treat it like a password).

## How it works

A small bridge process (Python 3.13, its own venv) keeps your Amazon session and listens
on `127.0.0.1` with a token; the plugin starts it on demand. It uses
[aioamazondevices](https://github.com/chemelli74/aioamazondevices), the library behind
Home Assistant's official *Alexa Devices* integration. Amazon's API is unofficial and can
change. See [docs/architecture.md](docs/architecture.md).

## Roadmap

1. ✅ Phase 0 — feasibility (login + speak)
2. ✅ Phase 1 — Hermes → Echo tools and `hermes alexa` CLI
3. Phase 2 — Alexa as a Hermes platform: "Alexa, ask Hermes…", answer on the same Echo,
   long tasks announced when done, `deliver=alexa` cron jobs
4. Phase 3 — one-command skill setup, docs, demo, Hermes plugin catalog
5. Phase 4 — Alexa+ (MCP), Echo Show screen, custom voice

## Development

```bash
# bridge (Python 3.13)
cd bridge && uv venv --python 3.13 .venv && uv pip install --python .venv -e . pytest pytest-aiohttp
.venv/Scripts/python -m pytest          # Linux/macOS: .venv/bin/python

# plugin (runs on Hermes's Python; needs hermes-agent importable)
PYTHONPATH=/path/to/hermes-agent python -m pytest
```

## License

MIT
