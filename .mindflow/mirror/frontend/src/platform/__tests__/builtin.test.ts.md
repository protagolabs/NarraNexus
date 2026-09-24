---
code_file: frontend/src/platform/__tests__/builtin.test.ts
last_verified: 2026-09-23
stub: false
---

# Builtin Registration Contracts

Pins the shell's route table, navigation order, panel component identities and
settings visibility against accidental registration drift. Shell-authored panel
IDs include Browser, but lifecycle ownership belongs to `builtin.browser`; the
literal ID contract therefore includes both shell-owned panels and that feature's
panel. Plugin disable behavior is covered separately by `disableBuiltinUi.test.ts`.
