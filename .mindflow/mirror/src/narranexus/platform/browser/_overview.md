---
code_dir: src/narranexus/platform/browser/
last_verified: 2026-09-23
---

# Browser platform

The platform owns runtime readiness, one session per agent, browser permissions
and the authenticated frame transport. The browser plugin contributes MCP tools
and trusted invocation scope; the backend route authenticates human viewers.
Installer implementations remain independent of the session and transport logic.

BrowserService is the common facade. stream_bridge serves sessions in their
owning process. Private implementations handle control, CDP, policy, approvals
and process cleanup. See the individual mirrors for the deliberately limited
permission guarantees around arbitrary page JavaScript.

The package `__main__` is the manual installer entrypoint. It delegates to
`_browser_impl/install.py` and is included in the engine wheel so recovery works
with the desktop's bundled Python as well as a source checkout.
