"""
@file_name: run_narramessenger_trigger.py
@date: 2026-07-02
@description: Standalone entry point for the NarraMessenger trigger process.

Usage:
    uv run python -m xyz_agent_context.module.narramessenger_module.run_narramessenger_trigger

As of Commit 7 (2026-07-02) this launches ONLY the Direct-Matrix path:
:class:`MatrixTrigger` opens a ``matrix.netmind.chat`` ``/sync`` connection
per active credential and drives the full receive → authorize-event →
classify → route pipeline. The legacy Gateway (polling) trigger was
deleted in the same commit — NarraMessenger's guide labels Gateway as a
fallback path and we no longer maintain a poller on our side.

Owners of pre-Matrix binds (``connection_mode='gateway'`` rows) will need
to re-run the bind flow once — the driver in ``_narramessenger_service``
completes the Direct Matrix bind and upserts a fresh row.
"""

import asyncio

from loguru import logger

from xyz_agent_context.utils.logging import setup_logging


async def main():
    from xyz_agent_context.utils.db_factory import get_db_client
    from xyz_agent_context.utils.schema_registry import auto_migrate
    from xyz_agent_context.module.narramessenger_module.matrix_trigger import (
        MatrixTrigger,
    )

    db = await get_db_client()

    # Ensure tables exist (channel_narramessenger_credentials,
    # channel_seen_messages, channel_trigger_audit, ...). Idempotent —
    # safe on every restart.
    await auto_migrate(db._backend)

    trigger = MatrixTrigger(max_workers=3)
    await trigger.start(db)

    try:
        while True:
            await asyncio.sleep(1)
    except (KeyboardInterrupt, asyncio.CancelledError):
        await trigger.stop()
    finally:
        flush = logger.complete()
        if hasattr(flush, "__await__"):
            await flush


if __name__ == "__main__":
    setup_logging("narramessenger_trigger")
    logger.info("Starting NarraMessenger Matrix Trigger...")
    asyncio.run(main())
