---
code_file: frontend/src/components/artifacts/renderers/OfficeRenderer.tsx
last_verified: 2026-07-13
stub: false
---

# OfficeRenderer.tsx — renderer for the three Office ArtifactKinds

## Why it exists

Renders `.docx` / `.xlsx` / `.pptx` artifacts in the panel. The wrinkle: an
Office artifact's **entry pointer IS the original office file** (so
"download original" grabs the real doc — see [[registration]] /
[[_office_mcp_tools]]), which a browser can't display directly. The actual
visual preview is a **sibling** HTML snapshot the backend generated with
`officecli view <file> html -o <stem>.preview.html` (see [[officecli_client]]).

So this renderer derives the sibling `<stem>.preview.html` name from
`artifact.file_path`, fetches it through the token-protected raw route, and
renders it as a **blob URL inside a sandboxed iframe**.

## Upstream / Downstream

- **Dispatched by:** [[ArtifactRenderer]] `RENDERER_BY_KIND` (the three OOXML
  kinds map here, lazily).
- **Depends on:** `useArtifactRawUrl` (mint view token / raw dir URL),
  `artifactsApi.fetchArtifactBlobUrl` + `lib/tauri.fetchArtifactViaTauri`
  (fetch the preview bytes as a blob).

## Design decisions

**Preview-name derivation MUST match the backend.** `previewSiblingName`
(`slides.pptx` → `slides.preview.html`) mirrors `preview_name_for` /
`PREVIEW_SUFFIX` in [[officecli_client]]. If either side changes the naming
convention, the fetch 404s. Keep the two in lockstep.

**Blob URL, not a raw-URL iframe** (unlike the multi-file [[HtmlRenderer]]
path). OfficeCLI's `view html` output is a static, self-contained snapshot that
needs no sibling-asset resolution, and a blob URL is same-origin to the parent —
which uniformly sidesteps Tauri's WKWebView mixed-content block across desktop
and web.

**Same sandbox contract as [[HtmlRenderer]]:** `allow-scripts`, **no**
`allow-same-origin`, no top-navigation, `referrerPolicy="no-referrer"`. Scripts
run (the OfficeCLI snapshot may need them) but the frame can't reach back into
the app origin.

## Gotchas

- Keyed on `artifact.updated_at`: re-running `office_render` with
  `target_artifact_id` bumps `updated_at`, which re-mints the token and refetches
  the regenerated preview — so an edited document's tab refreshes in place.
- The old blob URL is `URL.revokeObjectURL`-d on cleanup / re-fetch to avoid
  leaking object URLs across preview refreshes.
- On error it tells the user to ask the agent to run `office_render` again — the
  common failure is a stale/missing preview sibling (e.g. the agent edited the
  doc but never re-rendered).
