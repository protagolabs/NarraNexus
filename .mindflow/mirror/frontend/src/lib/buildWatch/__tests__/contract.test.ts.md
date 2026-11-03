---
code_file: frontend/src/lib/buildWatch/__tests__/contract.test.ts
last_verified: 2026-11-02
stub: false
---

# lib/buildWatch/__tests__/contract.test.ts — emit and read agree

Calls the plugin's `generateBundle` with a stubbed `emitFile` and parses the
emitted asset back through `readBuildId`: the file name and the field cannot
drift apart without this going red. Also pins `apply: 'build'` (the dev server
must not serve a build.json) and that non-string / empty ids read as null.
