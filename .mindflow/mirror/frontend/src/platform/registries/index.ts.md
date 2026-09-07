---
code_file: frontend/src/platform/registries/index.ts
last_verified: 2026-09-07
stub: false
---

## 2026-09-03（批 2d）— 导出 `THEMES`/`COMMANDS` 及主题辅助

## 2026-09-03 — 注册表包的公开入口

`.dependency-cruiser.cjs`'s `registries-are-pure` rule forbids this directory from importing
components/pages/stores/lib/hooks: registries only hold types and tables, never a concrete page.
(2026-09-07: the doc-mismatch check found this section also cited a `plugins-only-import-contracts`
dependency-cruiser rule that referenced a `src/plugins/`/`src/contracts/` split which never
actually landed — see the 2026-09-07 entry below and M-7 in `.dependency-cruiser.cjs` itself for
the fix; that sentence has been dropped rather than corrected in place, since there is no
`src/plugins/` for a rule to exempt anything for.)

## 2026-09-07 — `REGISTRIES` table (architecture E3)

`REGISTRIES` is the single table of the 16 named registries, keyed by the name `HostAPI.registries`
exposes it under. `host.ts` (the per-plugin facade) and `loader.ts` (`SHELL_REGISTRIES`) both
consume it instead of each spelling the same 16 names in their own object/array literal — four
copies of that list used to exist across the two files. Also re-exports `disableOwner` /
`enableOwner` / `isOwnerDisabled` from `registry.ts` (I-2's persistent blacklist) and the new
`PanelStripDef` type from `panels.ts` (I-3).

## 2026-09-04 · UI slot points (batch 3d.2)

Exports the `when` grammar and the slot-point / content registries with their defs and the `visibleSlotEntries` / `rendererFor` reads.

## 2026-09-04 · channels as descriptors (batch 4a)

Exports `CHANNELS` / `sortedChannels` and the channel row types.
