---
code_file: src/narranexus/platform/browser/_browser_impl/actions.py
last_verified: 2026-09-23
stub: false
---

# Fixed browser actions

Ordinary approved-site workflows need usable form and navigation gestures without
granting arbitrary JavaScript execution. This helper validates the five actions
and builds a fixed DOM expression whose arguments are JSON data. BrowserSession
applies current-origin access and exclusive control before evaluating it.

Fill uses native input setters and input/change events, including contenteditable;
select accepts an exact option value. File inputs, disabled/hidden/covered targets
and malformed arguments fail explicitly. Click and scroll resolve visible viewport
coordinates and use CDP input. Press accepts named keys or printable characters
with Control/Alt/Meta/Shift chords, emits release, and suppresses shortcut text.

This is a constrained tool interface, not a network sandbox: existing page event
handlers still execute and can submit forms or navigate. Scripts remain guarded by
full_cdp_access, and the process launcher disables filesystem downloads.
