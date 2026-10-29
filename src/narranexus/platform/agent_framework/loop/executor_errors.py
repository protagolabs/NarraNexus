"""
@file_name: executor_errors.py
@author:
@date: 2026-07-22
@description: Typed exceptions for the per-user Executor transport seam.

Raised at the two executor boundaries — ``broker_client.ensure_executor`` /
``wait_until_ready`` (can't reach the broker or the container never boots)
and ``loop.remote_driver.agent_loop`` (the ``:8020`` connection drops
mid-run) — so the orchestration layer can classify an executor-infrastructure
failure by exception TYPE rather than fragile substring matching on the
underlying aiohttp/httpx error text.

Lives in ``agent_framework`` (not ``agent_runtime``) so both drivers here and
``step_3_agent_loop`` one layer up import it along the correct dependency
direction (orchestration → framework), never the reverse.

Why a distinct type matters: an ``ExecutorUnreachableError`` is a PLATFORM-side
failure the user cannot fix by changing their config — surfaced to them as an
``infra_transient`` ErrorMessage ("retry / split the task"), NOT masked by a
fabricated helper-LLM reply. Because it subclasses ``RuntimeError`` and its
class name is NOT in ``loop.circuit_breaker._TRANSIENT_ERROR_TYPES``, it is
never mistaken for a retry-forever transient blip. (User LLM-provider
connection errors are a different class entirely — they arrive as NDJSON
``response.error`` frames inside the stream and are handled by
``response_processor``; they never become this exception.)
"""
from __future__ import annotations


class ExecutorUnreachableError(RuntimeError):
    """The per-user executor / broker could not be reached.

    Wraps the underlying transport error (aiohttp / httpx) at the executor
    boundary. Carries the target so the audit detail and owner-facing message
    can name what was unreachable without re-parsing the cause.
    """

    def __init__(self, message: str, *, target: str | None = None) -> None:
        super().__init__(message)
        self.target = target


class ExecutorTokenRejectedError(ExecutorUnreachableError):
    """The executor rejected this call's API token (HTTP 401/403).

    The executor requires the per-container token the broker planted
    (sandbox isolation, 2026-10-29). A rejection is never the user's fault: it
    means the orchestrator and that container disagree on the token (a broker
    read-back failure, a cross-repo contract drift).

    How it is routed: step 3 classifies a fatal by CLASS NAME
    (``type(e).__name__``) against ``failure._EXECUTOR_UNREACHABLE_TYPES`` —
    that set membership is what makes it surface as ``infra_transient``, get
    audited, and skip the fabricating helper-LLM fallback. Subclassing
    ``ExecutorUnreachableError`` only matters to callers that ``except`` that
    type; step 3 is not one, so a new error class here must ALSO be added to
    that set or it falls through to the fallback.

    Named and worded to stay clear of auth-substring classifiers: the class
    name has no "auth" and the message carries no status code (it is on
    ``.status``), because code that pattern-matches "auth" / "401" in error
    text (job pause logic, the circuit breaker's credential check) would
    otherwise misfile a platform failure as the owner's expired credentials.
    A resend usually recovers: the broker rolls a container whose token it
    cannot read back.
    """

    def __init__(self, message: str, *, target: str | None = None, status: int | None = None) -> None:
        super().__init__(message, target=target)
        self.status = status
