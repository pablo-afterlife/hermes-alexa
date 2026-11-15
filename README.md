# hermes-alexa

Alexa as a native channel for [Hermes Agent](https://github.com/NousResearch/hermes-agent) —
talk to Hermes through your Echo, and let Hermes talk back, **without replacing Alexa**.

> 🚧 Phase 2 of 4 (in testing): talk to Hermes from your Echo through your own Alexa skill.
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

## Talk to Hermes from the Echo

"Alexa, ask Hermes Agent to summarize my email" goes to your Hermes like a Telegram message.
A quick answer is spoken right away; a slow one gets "I'll let you know" and is announced
on the same Echo when ready. "Alexa, open Hermes Agent" starts a back-and-forth conversation.
Each Echo is its own conversation. `deliver=alexa` cron jobs announce on your Echo.

It needs your own Alexa skill (free, stays in development, only on your account) and an
HTTPS tunnel to your machine. Phase 3 turns this into one command; today:

1. Create the skill from [`skill/`](skill/) with the
   [ASK CLI](https://developer.amazon.com/docs/smapi/quick-start-alexa-skills-kit-command-line-interface.html):
   `ask smapi create-skill-for-vendor` with `skill/skill.json` (put your HTTPS URL in
   `{{ENDPOINT}}`), then `ask smapi set-interaction-model -l pt-BR` with
   `skill/interactionModels/custom/pt-BR.json`, then `ask smapi set-skill-enablement -g development`.
2. Expose the endpoint: e.g. `tailscale funnel --bg --set-path /alexa http://127.0.0.1:8796/alexa`.
3. Add `ALEXA_SKILL_ID=amzn1.ask.skill...` to Hermes's `.env` and restart the gateway.
4. Say "Alexa, open Hermes Agent". The first time, Alexa reads a pairing code: approve it with
   `hermes pairing approve alexa <code>` (or set `ALEXA_ALLOWED_USERS`).

Only requests signed by Amazon for your skill id get in (see
[architecture](docs/architecture.md#inbound-from-alexa-ask-hermes-to-a-spoken-answer)).

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
3. 🚧 Phase 2 — Alexa as a Hermes platform: "Alexa, ask Hermes…", answer on the same Echo,
   long tasks announced when done, `deliver=alexa` cron jobs (in testing)
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
