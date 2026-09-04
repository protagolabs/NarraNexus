---
code_file: frontend/src/components/awareness/registerBuiltinChannels.ts
last_verified: 2026-09-04
stub: false
---

# awareness/registerBuiltinChannels.ts — the six builtin rows

## Intent

Registers Lark / Slack / Telegram / WeChat / NarraMessenger / Discord into `ui.channels` (owner = `builtin.channels.<x>`, orders 10…60) with the same status probes the hard-coded list had; imported once by `IMChannelsSection`.
