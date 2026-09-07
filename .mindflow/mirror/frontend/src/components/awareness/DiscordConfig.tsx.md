---
code_file: frontend/src/components/awareness/DiscordConfig.tsx
stub: false
last_verified: 2026-09-07
---

## 2026-07-13 — activation toggle + parent-list sync

Renders the shared `ChannelActiveToggle` in the bound state (flip `enabled` via `POST /api/discord/set-active`) — primary use is activating a bundle-imported (inactive) credential. The toggle handler AND the header refresh button now call `onBindStateChange` so the parent `IMChannelsSection` status badge updates immediately (was stale until remount).

## Why it exists

The Discord card in the Awareness panel's IM Channels section — bind /
test / unbind a Discord bot for the active agent. Cloned from
``TelegramConfig.tsx`` (single token, two states: unbound form / bound
status).

## Design decisions

- **Single Bot Token + optional numeric owner user id.** No OAuth dance;
  the disclosure walks the Developer Portal flow.
- **Message Content Intent is called out prominently** (yellow REQUIRED
  marker) — it's the one manual step that silently breaks the bot (empty
  message bodies) if missed. The disclosure also explains the
  blank-message and no-reply-in-server symptoms.
- **No owner "pending" state** (unlike Telegram). Discord resolves the
  owner's display name at bind time from the numeric id, so the bound
  state is binary: owner registered or not.
- **Icon: lucide ``Bot``** (no official Discord glyph in lucide).

## Upstream / downstream

- **Upstream**: ``ChannelConfigProps`` from `@/platform/registries`;
  ``api.getDiscordCredential`` / ``bindDiscordBot`` /
  ``testDiscordConnection`` / ``unbindDiscordBot`` from ``lib/api.ts``;
  ``DiscordCredentialData`` from ``types/api.ts``.
- **Registered in**: the `ui.channels` registry, via `registerBuiltinChannels.ts` (owner `builtin.channels.discord`).

## Gotchas

- Client-side owner-id validation is numeric-only (defensive — backend
  re-validates). The token field is ``type="password"`` and never echoed.

## 2026-09-04 · generic channel API (batch 4d.3)

Calls `api.channel*('discord', …)` with the channel's own typed envelopes; the bind body is the descriptor's `bind_fields` as a `fields` object. UI and flow unchanged.

## 2026-09-07 · import fixed to the canonical source; disable now actually works

`ChannelConfigProps` is imported from `@/platform/registries` directly (not
re-exported through `IMChannelsSection.tsx` — that shim created a
`registerBuiltinChannels → *Config → IMChannelsSection` circular import
`.dependency-cruiser.cjs` flags). Disabling this channel's builtin plugin
(`disableBuiltinUi('builtin.channels.<x>')`) now genuinely removes the row
from `ui.channels` even when it runs before `registerBuiltinChannels.ts`'s
lazy registration: the loader blacklists the owner, and `Registry.register`
silently drops any future write from a blacklisted owner (see
`platform/registries/registry.ts` and `platform/loader.ts`). Previously this
was a known-false claim in this file.
