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
`sortedChannels` inside a `useMemo`, and renders whatever rows are currently
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

## 2026-10-30 — memoized the sorted row array (credential-probe loop)

`sortedChannels` copies before sorting, so calling it directly in render
produced a new array every render. That array is a dependency of
`refreshConnected`, which the mount effect depends on — so every
`setStatusMap` re-render rebuilt the callback, refired the effect and probed
all channels again: an unbounded loop of `GET /api/channels/<ch>/credential`
for as long as the section was mounted (about 30 requests/s per open panel at
a 200 ms round-trip). On prod that tripped the edge flood auto-ban (IP ban +
linked account suspension) for any user who opened the Channels section.

The rows are now `useMemo(() => sortedChannels(entries), [entries])` over the
registry snapshot, which is referentially stable until the registry changes.
Probe budget: one round per mount, per agent switch, per section re-open and
per explicit refresh / child bind-state change, plus one round when the
backend catalog lands after mount. Locked by
`__tests__/imChannelsProbeBudget.test.tsx` and
`__tests__/imChannelsLateCatalog.test.tsx` (both fail if the memo is removed).
`refreshConnected` is also passed to each channel card as
`onBindStateChange`, so its identity is now stable for children too.
