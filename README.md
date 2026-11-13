# hermes-alexa

Alexa as a native channel for [Hermes Agent](https://github.com/NousResearch/hermes-agent) —
talk to Hermes through your Echo, and let Hermes talk back, **without replacing Alexa**.

> 🚧 Early work (Phase 0: feasibility). Not usable yet.
> 🇧🇷 [Leia em português](README.pt-BR.md)

## What it will do

- "Alexa, ask Hermes …" → Hermes answers on the same Echo, with its memory and tools.
- Long tasks: Alexa acknowledges right away, and the Echo announces the result when Hermes
  finishes.
- Hermes can make any Echo speak, announce, or run Alexa commands from any channel,
  cron job or hook.

See [docs/architecture.md](docs/architecture.md).

## License

MIT
