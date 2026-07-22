---
code_file: frontend/src/components/artifacts/renderers/UrlRenderer.tsx
last_verified: 2026-07-22
stub: false
---

# UrlRenderer.tsx — renderer for URL-tab artifacts (application/x-url)

## Why it exists

Renders an `application/x-url` artifact. The entry file is a JSON doc
(`UrlArtifactDoc`) holding the URL + the server-side embed verdict; this
renderer fetches it through the token-authed raw route, then:

- `effective_mode === 'iframe'` → iframes the EXTERNAL url directly.
- `effective_mode === 'stream'` → fallback card (open-in-new-window). This is
  the seam where the future streaming renderer (方案三) plugs in.
- always shows a mode toggle so the user can override a wrong verdict; the
  override persists via `artifactsApi.setEmbedMode` and a `refreshKey` bump
  re-mints the token + refetches the doc.

## Design decisions

- The iframe `src` is the external URL, not our backend, so HtmlRenderer's
  Tauri mixed-content blob dodge is unnecessary for https targets — one code
  path serves both run modes (铁律 #7).
- iframe sandbox is `allow-scripts allow-same-origin allow-forms allow-popups
  allow-popups-to-escape-sandbox` — a real third-party site needs its own
  origin/storage/forms to function. `allow-same-origin` is safe ONLY because a
  URL tab is cross-origin third-party content; a tab pointing at our OWN origin
  would become a same-origin scriptable iframe reaching the app token, so the
  backend (`url_artifact._reject_self_origin`) refuses to open one. The sandbox
  safety DEPENDS on that guard — don't copy it to a same-origin renderer.
- Sandbox is `allow-scripts allow-same-origin allow-forms` — NO
  `allow-popups` / `allow-popups-to-escape-sandbox` (2026-07-22): with them,
  a `target="_blank"` link inside the page escaped into a new OS-browser
  window ("links jump out of the app"). Without them, same-frame links stay in
  the tab and popups are neutralised. We CANNOT turn a cross-origin page's link
  into a new in-app tab (same-origin policy) — full in-app navigation is a
  streaming-browser (方案三) capability. The mode toggle labels are "Inline"
  (iframe) / "External" (the open-in-browser fallback card, formerly the
  misleading "Full").
- The doc fetch uses `fetchArtifactText` like the other text renderers
  (Csv/Markdown/Chart) — same pattern, same Tauri behavior.

## Gotcha

`effectiveEmbedMode` (in types/artifact.ts) collapses recommend + override;
don't read `recommended` directly in the renderer or a user override is
ignored.
