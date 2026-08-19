---
code_file: frontend/src/components/onboarding/GuideAgentCoachmark.tsx
last_verified: 2026-08-19
stub: false
---

# GuideAgentCoachmark.tsx — one-shot "create your own agent" bubble

## Why it exists

The retired OnboardingChecklist's replacement on the nudge front: new users
whose first agent (the guide) was auto-created server-side get one bubble
pointing at the sidebar "+" saying they can create more themselves. Shown
while `lib/guideCoachmark` reports 'pending' (armed by the login path via
`is_new_user`); dismissing writes 'done', permanently.

## Upstream / Downstream

Mounted unconditionally in `MainLayout` (renders nothing unless pending AND
the anchor is measurable). Reads/writes via `lib/guideCoachmark`. i18n keys:
`onboarding.guideCoachmark.{text,gotIt}` (en + zh; other locales fall back).

## Design decisions

Anchoring/portal mechanics are a deliberate copy of `MigrationCoachmark`
(measure `[data-help-id="sidebar.create-agent"]`, portal to body, 500ms
mount-race interval capped at ~10s, nothing on collapsed rail) — same UX,
same failure modes, reviewed once already. Not extracted into a shared base:
two instances is below the abstraction threshold and their gating differs.
