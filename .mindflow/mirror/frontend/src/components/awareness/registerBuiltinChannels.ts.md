---
code_file: frontend/src/components/awareness/registerBuiltinChannels.ts
last_verified: 2026-09-07
stub: false
---

## 2026-09-07 — each row guarded on its own id, not one shared guard (I-2 follow-on)

All six `CHANNELS.register(...)` calls used to sit behind a single `if (!CHANNELS.has('lark'))`.
Disabling one builtin channel (e.g. `builtin.channels.discord`) alone did not stop this file's
row from registering — the shared guard only checked whether the FIRST row (`lark`) was present,
so disabling a later one had no visible effect. Each row is now guarded on its own id
(`if (!CHANNELS.has('slack')) CHANNELS.register('slack', ...)`, etc.), so
`disableBuiltinUi('builtin.channels.<x>')` correctly removes exactly that row.

# awareness/registerBuiltinChannels.ts — the six builtin rows

## Intent

Registers Lark / Slack / Telegram / WeChat / NarraMessenger / Discord into `ui.channels` (owner = `builtin.channels.<x>`, orders 10…60) with the same status probes the hard-coded list had; imported once by `IMChannelsSection`.

## 2026-09-04 · one probe (batch 4d.3)

`probe(channel)` reads `api.channelCredential` and maps `enabled` → active/inactive for all six rows (Lark included — no more `is_active` special case).
