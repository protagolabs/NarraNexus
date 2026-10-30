---
code_file: frontend/src/components/awareness/IMChannelsSection.tsx
last_verified: 2026-10-30
stub: false
---

# IMChannelsSection.tsx — three-level disclosure shell for IM channel bindings

## Why it exists

The single Awareness-panel entry point for all IM channel bindings
(Lark/Slack/Telegram/WeChat/NarraMessenger/Discord, plus any channel plugin
adds its own). Renders the collapsed → expanded → one-card-open disclosure
and the connected-count badge; the actual per-channel bind/unbind forms live
in each channel's own config component.

## This file doesn't do

It is not a registry of channels — that's `ui.channels` (`@/platform/registries`),
populated by `registerBuiltinChannels.ts` for the six builtins and by
individual channel plugins for third-party ones. This file only reads
the registry snapshot (`useRegistryEntries(CHANNELS)`), sorts it with
`sortedChannels` (stable per snapshot), and renders whatever rows are currently
registered.

It also does not define `ChannelConfigProps` — that type lives in
`@/platform/registries`. See the 2026-09-07 entry below.

## Upstream / Downstream

- **Used by**: the Awareness settings panel.
- **Depends on**: `ui.channels` registry (`@/platform/registries`); imports
  `./registerBuiltinChannels` for its side effect (registering the six
  builtin rows) — the import itself is otherwise unused.
- **Each rendered channel card**: renders that row's `component`, one of
  `LarkConfig` / `SlackConfig` / `TelegramConfig` / `WeChatConfig` /
  `NarramessengerConfig` / `DiscordConfig`, or a channel plugin's own
  component — all of which import their `ChannelConfigProps` type from
  `@/platform/registries` directly.

## 2026-09-07 — stopped re-exporting `ChannelConfigProps` (I-8)

Previously this file `export type { ChannelConfigProps } from '@/platform/registries'`
and the five builtin config components imported the type from here instead
of from the registry module that actually declares it. That made this file
an unintended type hub: a channel plugin authored outside this app package
had no importable path to the shared props type (it can't import a private
app component), and a future refactor of this file could silently change
the type five other files depend on. The re-export is gone; every consumer
now imports `ChannelConfigProps` straight from `@/platform/registries`.

## 2026-10-30 — stable row array + latest-round-wins (credential-probe loop)

`sortedChannels` used to copy-and-sort on every call, so calling it in render
produced a new array every render. That array is a dependency of
`refreshConnected`, which the mount effect depends on — so every
`setStatusMap` re-render rebuilt the callback, refired the effect and probed
all channels again: an unbounded loop of `GET /api/channels/<ch>/credential`
for as long as the section was mounted (about 30 requests/s per open panel at
a 200 ms round-trip). On prod that tripped the edge flood auto-ban (IP ban +
linked account suspension) for any user who opened the Channels section.

Now `sortedChannels` caches its result per input array (see
`platform/registries/channels.ts`), and the input is the registry snapshot,
referentially stable until the registry changes — so `channels`,
`refreshConnected` and the mount effect only change when the agent or the
registry does.

Probe budget: one round per mount, per agent switch, per section re-open, per
explicit refresh / child bind-state change, plus one round when the backend
catalog lands after mount. Rounds may overlap; a round counter
(`roundRef`, bumped before the `agentId` check so clearing the agent also
retires in-flight rounds) lets only the latest round write `statusMap`, so a
slow round for the previous agent cannot paint its badges over the current one. The re-open
refresh runs outside the `setSectionOpen` updater (updaters must be pure;
StrictMode runs them twice).

Locked by `__tests__/imChannelsProbeBudget.test.tsx` (mount, re-render, agent
switch, Refresh, collapse → re-open, and out-of-order rounds — asserting both
that the latest round's badges appear and that the stale round cannot replace
them) and
`__tests__/imChannelsLateCatalog.test.tsx`; removing the helper cache or the
round guard turns them red. `refreshConnected` is also each card's
`onBindStateChange`, so its identity is stable for children too.
