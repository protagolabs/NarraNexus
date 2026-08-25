---
code_file: frontend/src/components/ui/popover.tsx
last_verified: 2026-08-20
stub: false
---

## 2026-08-20 — `style` prop was silently wiping the panel background

Bug: `PopoverContent` set its own `style={{background: ...}}` and then
spread `{...props}` (which can include a caller `style`) AFTER it in JSX —
object-spread semantics mean the later one wins wholesale, not merges. The
first caller to ever pass its own `style` prop
([[../../pages/CreateAgentPage.tsx]]'s `IconSelect`, matching its dropdown
width to `--radix-popover-trigger-width`) got a fully transparent, borderless
popover — background/border/radius/shadow/color all silently gone, list
items rendering see-through over whatever was underneath. Fixed by
destructuring `style` out of `props` and spreading it INTO the base style
object (`{ background: ..., ...style }`) instead of letting a full
replacement happen. Any future caller passing `style` to `PopoverContent`
now only overrides the specific keys it sets, same as `className` already
did via `cn()`.

# popover.tsx — Radix Popover with NM design system styling

Thin wrapper over `@radix-ui/react-popover`. NM-styled in M2: RaisedPanel
treatment — paper-raised bg + hairline border + radius-md + soft lift
shadow (the second allowed shadow exception after RaisedPanel + Toast +
Dialog).

Slide-in direction matches the trigger side via Radix `data-side` attribute.

Used by `CostPopover` in the context panel header and other dropdown
patterns. Not re-exported from `index.ts` — import as
`@/components/ui/popover`.

## Why RaisedPanel-style

Per NM Axiom #4: a popover IS by definition "above the page". RaisedPanel
treatment makes it visually consistent with every other "above" surface
(Dropdown, Toast, Dialog).

## Related

- `nm/surface.tsx` RaisedPanel — same visual language for non-popover surfaces
- Spec: §5.9
