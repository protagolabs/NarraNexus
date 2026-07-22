---
code_file: src/xyz_agent_context/artifact/_artifact_impl/url_artifact.py
last_verified: 2026-07-22
stub: false
---

# url_artifact.py — create / update URL-tab artifacts (application/x-url)

## Why it exists

A URL tab is a first-class pointer artifact (NOT a parallel tab system — the
office-live merge taught us "two preview concepts confuse users"). Its entry
file is a small JSON doc (`UrlArtifactDoc`) at `tabs/<slug>/page.url.json` in
the agent workspace. This module owns the doc I/O + orchestration; the actual
registration goes through the shared [[registration.py]], so URL tabs get
heal / delete / bundle / raw-serving for free. No DB column for the URL — the
JSON doc is the source of truth (pointer model preserved).

## open_url flow

1. SSRF-gate the initial URL ([[url_safety.py]]) — a non-public target raises
   ArtifactError(400): tab creation fails loudly.
2. Probe embeddability ([[embed_probe.py]]) — never crashes; a failed probe
   degrades to an optimistic iframe verdict.
3. Write the doc into a DEDICATED `tabs/<slug>/` subdir — each URL tab gets
   its own artifact root so the raw route's isolation means one tab can never
   read another's json.
4. Register through the shared pointer path with `kind=application/x-url`,
   `description=url` (so the URL shows in listings / the agent's state block).

## set_embed_mode

Rewrites the on-disk doc's `embed.user_override` (the user's manual toggle,
which wins over the probe). Bumps updated_at via `repo.update_title` so the
frontend's refetch sees a change. 404s if the artifact is missing / not this
agent's / not a URL tab.

## Gotcha

The doc is written to `registration.workspace_root(agent_id, user_id)` joined
with the relative entry — NOT to `base_working_path` directly. It MUST match
where `registration._resolve_entry` looks, or register fails "file not found".
