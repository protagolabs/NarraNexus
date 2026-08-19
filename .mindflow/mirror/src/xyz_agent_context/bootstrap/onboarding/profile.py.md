---
code_file: src/xyz_agent_context/bootstrap/onboarding/profile.py
last_verified: 2026-08-19
stub: false
---

# profile.py — the "onboarding" BootstrapProfile

## Why it exists

The guide agent's first-run flow (bilingual greeting, first-chat playbook,
welcome artifact) rides the standard bootstrap-profile machinery instead of a
bespoke path, exactly like Arena: provisioning passes the provision-time
random picks through `BootstrapContext.extra` (`persona_key`, `topic_index`,
`is_local`) and `apply_bootstrap` renders-then-stores.

## Upstream / Downstream

**Registered by:** importing this module (the subpackage `__init__` does it;
`provisioning.ensure_guide_agent` imports the subpackage before applying the
profile). Same import-side-effect pattern as the arena profile.
**Reads:** `personas.py` renderers; `welcome_templates.default_welcome_html`
for the standard welcome card (the guide agent's job IS explaining NarraNexus,
so the generic capability card is the right artifact — no bespoke HTML).

## Design decisions

- **ctx.extra plumbing with safe fallbacks:** a bare ctx (or stale keys from
  an old snapshot) still renders persona[0] / topic[0] / cloud mode rather
  than raising — greeting rendering must never break agent listing.
- **auto_delete_after_events=3** matches default/arena: the Bootstrap.md
  playbook self-clears after the first few turns.

## Gotchas

- `provision_new_agent` is called with `bootstrap_profile="none"` by
  provisioning.py, which then applies THIS profile itself with the extra-laden
  ctx — because the provision seam's own apply_bootstrap call can't carry
  extra. Mirrors `arena_provisioning_service.py`. Don't "simplify" by passing
  `bootstrap_profile="onboarding"` there: you'd get the fallback rendering
  (persona[0]/topic[0]) stored, then a second apply overwriting it.
