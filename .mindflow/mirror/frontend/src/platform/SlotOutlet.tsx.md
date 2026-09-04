---
code_file: frontend/src/platform/SlotOutlet.tsx
last_verified: 2026-09-04
stub: false
---

# platform/SlotOutlet.tsx — mount a component slot point

## Intent

Renders every visible entry of a component slot (when-filtered, ordered), each inside its own error boundary so one plugin's render error is reported to the error sink (attributed to that owner) and removed, never taking the surface (top bar, composer, sidebar, agent row) down. `as` wraps the entries when the surface wants a container.
