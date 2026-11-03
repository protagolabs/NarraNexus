---
code_file: frontend/src/lib/buildIdContract.ts
last_verified: 2026-11-02
stub: false
---

# buildIdContract.ts — the /build.json contract, written by the build, read by every tab

The file name and the `buildId` field were first written separately in
`vite.config.ts` and [[buildWatch.ts]]; renaming either would have silently
disabled the "new version" banner with every test still green (it fails
safe — never a false banner — which is exactly why nothing would notice).
Both sides now import the name, the emitting Vite plugin and the reader from
here, and `buildIdContract.test.ts` round-trips the emitted asset through the
reader.

Must stay pure (no DOM, no Node APIs): `vite.config.ts` compiles under
`tsconfig.node`, `buildWatch.ts` under `tsconfig.app`. The `vite` import is
type-only.
