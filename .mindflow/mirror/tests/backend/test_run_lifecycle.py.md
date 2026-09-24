---
code_file: tests/backend/test_run_lifecycle.py
last_verified: 2026-09-23
stub: false
---

# Detached Run Shutdown Regressions

Tests reproduce the ownership gap before a run ID exists and verify resource
cleanup waits for finalization. Cancellation of shutdown callers, failed peers,
duplicate close calls and refused launches must not lose live work. A real
isolated SQLite recorder proves heartbeat updates and terminal stream flushes
still work while shutdown waits. No live process or user database is involved.

Job runs survive both HTTP and SSE disconnects, managed audit work participates
in drain, and repeated diagnostic intervals cannot cancel work. Resource cleanup
joins housekeeping finalizers and attempts remaining releases after worker errors.
