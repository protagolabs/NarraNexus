---
code_file: frontend/src/platform/__tests__/disableBuiltinUi.test.ts
last_verified: 2026-09-23
stub: false
---

# Builtin feature UI lifecycle

Feature-level UI contributions must belong to their plugin ID so the loader can
remove them together, while protected shell entries remain. Tests cover the
existing teams route contributions and browser panel/settings ownership.

A disabled browser in the factory response must remove both contributions, retain
unrelated Jobs and provider settings, reject later settings registration by the
same disabled owner, and tolerate repeated disable calls. Browser state is restored
between tests. This exercises loader removal and registration blacklisting, not
just consumers checking whether a manually removed panel exists.
