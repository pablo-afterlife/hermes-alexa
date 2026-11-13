# Architecture

> Status: Phase 0 (feasibility). Nothing here is final.

## Goal

Make Alexa a first-class Hermes channel, without replacing Alexa:

- **Inbound:** "Alexa, ask Hermes …" reaches Hermes like a Telegram message. Each Echo is
  its own conversation. Fast answers are spoken inline; slow ones get a short
  acknowledgement and the answer is announced later **on the same Echo**.
- **Outbound:** Hermes can make any Echo speak, announce, or run a text command
  ("turn off the living room light") from any channel, cron job or hook.

## Pieces

```
Alexa skill ──HTTPS (tunnel)──▶ hermes-alexa platform adapter ──▶ Hermes gateway / agent
                                         │
Hermes tools / cron / hooks ─────────────┤
                                         ▼
                              hermes-alexa bridge (sidecar) ──▶ Amazon (aioamazondevices)
```

1. **Hermes plugin** (`kind: platform`, `ctx.register_platform`): the adapter receives the
   skill's requests, verifies Amazon's signature, and hands the text to the gateway. It also
   ships `provides_tools` (speak, announce, command, devices, volume, routine) and a
   `standalone_sender_fn` so `deliver=alexa` cron jobs work.
2. **Bridge (sidecar process):** owns the Amazon session and calls Alexa.

## Decision: why a sidecar

`aioamazondevices` (the library behind Home Assistant's official *Alexa Devices*
integration) requires **Python >= 3.13** since 17.2.4 (2026-11-02). Hermes supports
`>=3.11`, and real installs run 3.11. The library also pins `aiohttp>=3.14.5` and ships a
new release almost weekly (230 releases so far) because Amazon changes its private API.

Running it in its own process (own venv, managed with `uv`) means:

- works with any Hermes Python version;
- library updates never touch Hermes's dependencies (the plugin catalog checks them);
- if Amazon breaks something, the bridge crashes, not the gateway.

The cost is one more process and a local HTTP hop (loopback + token), which is
negligible next to the Amazon round trip.

## Findings from Phase 0 (2026-11-13)

- Login works with a **Brazilian account** (amazon.com.br) using email + password + an
  authenticator-app code; the stored session is reused without the password afterwards.
- Echo Dot 5th gen: `call_alexa_speak` and `call_alexa_announcement` both play, in
  Portuguese.
- **Gotcha:** the library does not send immediately. It batches commands for 0.3 s and
  POSTs from a background task, swallowing errors into the log. A process that exits, or
  blocks the event loop (e.g. a sync `input()`), right after the call drops the command
  silently. The bridge must be a long-running async service, never block its loop, and
  surface the library's error log.
