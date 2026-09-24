---
code_dir: src/narranexus/platform/browser/_browser_impl/
last_verified: 2026-09-23
---

# Browser implementation boundary

The session combines task-scoped policy decisions, exclusive control and frame
subscriptions. CDP owns protocol liveness, while runtime_launch owns the process.
ApprovalStore persists decisions across processes with atomic compare-and-swap;
stream_auth proves the backend is the caller of the internal stream.

Runtime discovery and installation have their own coordinator and download
implementation. Callers should use BrowserService rather than combine private
objects or create an independent session registry.
