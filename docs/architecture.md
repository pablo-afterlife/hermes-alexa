# Architecture

> Status: Phase 2 implemented (platform adapter + skill); Phase 1 done (bridge + tools).

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

## Inbound: from "Alexa, ask Hermes" to a spoken answer

```
Echo ─▶ Alexa cloud ─▶ HTTPS tunnel /alexa ─▶ adapter (127.0.0.1:8796)
                                                │ 1. verify (alexa_verify.py)
                                                │ 2. handle_message(event)  ──▶ agent
                                                │ 3. wait ≤ ALEXA_REPLY_SECONDS for send()
                                                ▼
                          reply in time: Alexa says it in the same exchange
                          too slow:      "I'll let you know"; the reply is announced
                                         on the same Echo through the bridge
```

- **Verification** (Amazon's rules for skills hosted as a web service): the certificate URL
  must be `https://s3.amazonaws.com[:443]/echo.api/…`; the chain must be valid now, issued
  for `echo-api.amazon.com` and chain to a trusted root (certifi); `Signature-256` must be
  the RSA/SHA-256 signature of the raw body; the timestamp within 150 s; the
  `applicationId` must be `ALEXA_SKILL_ID`. Nothing is parsed or handed on before that.
- **Timing:** Alexa gives a skill about 8 s. The adapter waits `ALEXA_REPLY_SECONDS`
  (5.5 s) for the agent, then a short settle for a trailing chunk. Gateway status messages
  (busy acks, `_interim_send`) are never announced; one is spoken only when nothing else
  came back in time.
- **Identity:** `chat_id` = `echo-<hash of the skill's deviceId>` (one conversation per
  Echo), `user_id` = `amzn-<hash of the Alexa userId>`. Unknown users go through the
  gateway's pairing (the code is spoken) or `ALEXA_ALLOWED_USERS`.
- **Which Echo is `echo-…`?** The skill's `deviceId` is opaque and no Amazon API maps it to a
  serial. A few seconds after the first request from an Echo, the bridge looks at Alexa's
  voice history (`sync_history_state`, latest record per device) and remembers the Echo that
  just spoke (`skill-devices.json`). With a single Echo there is nothing to look up.
- **Voice is not a password:** anyone near the Echo can speak, and the TV can too. A voice
  request gets only `web`, `session_search`, `todo` and the `alexa` tools: no terminal,
  code, files, browser, computer control or memory writes, and no `/update`. The owner can
  pick other toolsets with `platform_toolsets.alexa` in `config.yaml` (`hermes tools`).
- **Amazon session health:** every `ALEXA_CHECK_MINUTES` (30) and after a failed
  announcement, the adapter lists the Echos through the bridge. While that fails, a slow
  answer gets "I can't let you know here, run `hermes alexa login`" instead of a promise, and
  with `ALEXA_ALERT_TO` the owner hears it once in that platform's home chat (and once when
  it is back), through `inject_message` (needs `allow_gateway_injection`).
- **Display tier `minimal`:** no tool progress or interim messages are read out loud.
- **Conversation mode:** "Alexa, open Hermes" keeps the session open (reprompt "anything
  else?") until the user stops; a one-shot "Alexa, ask Hermes …" ends after the answer.

## Decision: why a sidecar

`aioamazondevices` (the library behind Home Assistant's official *Alexa Devices*
integration) requires **Python >= 3.13** since 17.2.4 (2026-11-02). Hermes supports
`>=3.11`: the Windows desktop's gateway runs a bundled 3.14, but source installs commonly
run 3.11. The library also pins `aiohttp>=3.14.5` and ships a
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
