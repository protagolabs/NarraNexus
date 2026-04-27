---
code_file: src/xyz_agent_context/module/lark_module/run_lark_trigger.py
stub: false
last_verified: 2026-04-14
---

## Why it exists

Standalone entry point for running `LarkTrigger` as an independent
process.  Used when the trigger needs to run outside the main backend
(e.g., `uv run python -m xyz_agent_context.module.lark_module.run_lark_trigger`).

## Design decisions

- **Calls `auto_migrate`** — ensures all tables (including
  `lark_credentials`) exist before starting the trigger.
- **Runs until KeyboardInterrupt** — simple `while True: sleep(1)`
  loop with graceful `trigger.stop()` on exit.

## Upstream / downstream

- **Upstream**: shell / process manager.
- **Downstream**: `LarkTrigger`, `auto_migrate`, `get_db_client`.
