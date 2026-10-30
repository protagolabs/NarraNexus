---
code_file: frontend/src/platform/registries/channels.ts
last_verified: 2026-10-30
stub: false
---

# registries/channels.ts — ui.channels

## Intent

The rows of the Channels section as a registry (label, icon, config component, status probe, order). Builtins register in `components/awareness/registerBuiltinChannels.ts` with their plugin id as owner (so `disableBuiltinUi` removes the row); a channel plugin registers its own row (batch 4b brings the schema-driven `GenericChannelConfig`). `ChannelConfigProps` lives here and is re-exported from `IMChannelsSection` so the six config components keep their import path.

## 2026-10-30 — `sortedChannels` is referentially stable

It now caches the sorted array per input array (`WeakMap`) and defaults to
`CHANNELS.snapshot()` instead of `list()`, so the same snapshot always yields
the same array and the result is safe in hook dependency lists. The array is
shared between callers, so the return type is `readonly`. Returning a new
array per call fed the Channels section's credential-probe loop (see
`IMChannelsSection.tsx.md`). The sibling helpers (`sortedSidebarItems`,
`sortedSettingsSections`, `visibleSlotEntries`) still return fresh arrays: they
take extra inputs and every current caller only renders the result.
