# NarraNexus Plugin API Stability and Deprecation Policy

## 1. Scope

This policy covers everything exported from `narranexus.contracts` (Python) and, once
published, `@narranexus/contracts` (TypeScript); the plugin manifest schema
(`narranexus-plugin.json`); the slot tree (`docs/plugins/slots.md`); and the hook
specifications. Nothing else is a public API. Imported names, private (`_`-prefixed)
modules, log text, iteration order and timing are explicitly NOT part of the contract
(Hyrum's Law: we do not promise what we did not declare).

## 2. Stability levels

Since plugin platform batch 6 (2026-09) every kind in `narranexus.contracts.API_VERSIONS` is **stable**: `STABILITY` says so, `scripts/dev/api_check.sh` (griffe) diffs the public surface of `narranexus.contracts` and `narranexus.sdk` against the base branch in CI, and a breaking change must go through section 4.

Every exported symbol and every slot carries exactly one level, declared in code
(`narranexus.contracts.STABILITY`, `Slot.stability`) and in the generated docs.

- **alpha** — May change or be removed in any release without notice. A NEW kind starts
  here until it is promoted in `narranexus.contracts.STABILITY`.
- **beta** — Feature-complete and a candidate for stable. Backwards-incompatible changes
  are allowed only after a deprecation period of at least **2 minor releases or 90 days,
  whichever is longer**, declared at the time the symbol is marked beta.
- **stable** — No backwards-incompatible change within the same major version of the
  contracts package. Removal requires a new major version.

## 3. What counts as a backwards-incompatible change

Any of the following on a beta or stable symbol is incompatible and requires the
deprecation process (beta) or a major version bump (stable):

- Removing or renaming a symbol, method, field, hook, slot, or enum value.
- Changing the type of an existing field or parameter.
- Making an optional parameter or field required, or changing a default value.
- Changing documented semantics, error types, or ordering guarantees.
- Moving a symbol to a different module without leaving a re-export.

Additive changes (new optional fields, new methods with defaults, new hooks, new slots,
new enum values on inputs) are compatible. Extension points are only ever added; a
breaking change to a slot's contract ships as a new slot, never as an edit.

## 4. Deprecation process

1. Mark the symbol with `warnings.deprecated` (PEP 702) / a JSDoc `@deprecated` tag naming
   `since`, `removal` and the replacement, and list it under "Deprecated" in the
   release notes (`docs/RELEASE_NOTES.md`, one section per version).
2. The host emits a `DeprecationWarning` once per process per symbol and records a
   `warnings` entry in the plugin load report so the plugin factory can show it.
3. The replacement must be available in the same release the deprecation is announced.
4. A deprecated API may not be replaced by a less stable one.
5. Old and new forms must round-trip: data written through the new form must be readable
   through the old form during the deprecation window.
6. After the window, removal happens only in a release whose notes list it under "Removed".

## 5. Versioning

- `narranexus.contracts.API_VERSIONS[kind]` is the integer contract version per kind; a
  breaking change bumps it. The host declares what it supports; a plugin manifest declares
  what it needs in `api`; a mismatch fails closed at load time.
- New optional capabilities are negotiated (`capabilities()` on drivers, manifest fields
  with defaults) rather than by bumping the major version.
- Feature flags are a rollout tool, not a compatibility promise; they never substitute for
  this policy.

## 6. Exemptions

Security fixes may shorten or skip the deprecation window; the release notes must say so
explicitly.

## 7. Route prefixes: builtin vs third-party

A builtin plugin's routers keep the absolute prefixes the product always had
(`/api/agents/...`, `/api/jobs`, `/api/skills`, `/api/teams`, ...): the frontend and
every client address them there, and a builtin is the product. A third-party
plugin's routers are confined to `/api/x/<plugin id>` (the host refuses anything
else). This is the one deliberate difference between the two; both are declared
the same way (`backend.routes` in the manifest, a `RouterSpec` in the plugin's
own package). Nothing under `backend/routes/` belongs to a plugin any more.

## 8. Naming conventions for contribution symbols

A manifest's `provides` names module-level symbols; the vocabulary is fixed so a
reader can tell from the name what a symbol holds and a template composes
without renaming:

- A **many-arity** slot is filled by an UPPER_SNAKE plural noun holding a tuple
  of `Contribution`: `ROUTES`, `TABLES`, `WORKERS`, `SETTINGS`, `TOOLS`,
  `MCP_SERVERS`, `BUNDLES`, `SKILLS`, `RECALL_STRATEGIES` (per stage:
  `<STAGE>_STRATEGIES`), `PROFILES`, `CONTEXT_PROVIDERS`, `TRIGGERS`, `MODULES`,
  `CHANNEL` (one descriptor per channel plugin). Builtin plugins use
  `CONTRIBUTIONS` when the plugin is the slot's whole builtin set (e.g. the
  prompt sections).
- A **one-arity** slot is filled by a single `Contribution` named `CONTRIBUTION`
  (a framework driver, the prompt assembler, the pipeline).
- `backend.hooks` names `HOOKS`: a tuple of `@hookimpl(name)` functions.
- A framework plugin's static facts live in `META` (a `FrameworkMeta`) and are
  carried as `Contribution.meta["framework"]`.
- The registered **name** of a contribution is what other plugins refer to (a
  profile names a strategy, a binding names `plugin:name`): lower_snake, unique
  within the slot, prefixed with the plugin package name for third-party
  contributions (`acme_weather_recall`) so two plugins never collide.

Renaming a symbol a manifest names is a breaking change for that plugin only
(the manifest is edited with it); renaming a contribution **name** is a breaking
change for everyone who binds or references it and follows §4.
