"""
@file_name: executor_protocol.py
@author:
@date: 2026-06-17
@description: Wire format for the agent-loop executor boundary.

The agent-loop (step 3 of the 7-step pipeline) is the ONLY place that
spawns the claude/codex CLI. Extracting it into a separate "executor"
service means that boundary must be crossed over the network instead of
an in-process call. This module is the shared (de)serialization for that
boundary, used by BOTH ends:

  * orchestrator side: ``RemoteAgentLoopDriver`` builds the request
    (incl. a snapshot of the resolved provider configs, which normally
    travel via ContextVar and therefore would NOT survive a network hop).
  * executor side: ``executor_service`` rebuilds the configs, re-applies
    them via ``api_config.set_user_config``, runs the LOCAL driver, and
    streams raw event dicts back.

Keeping this in the core package (not backend/) so both the executor
service entrypoint and the driver can import it without a backend dep.

Resume authentication (2026-07-28): ``resume_session_id`` is the one field
on this body that names a resource living OUTSIDE the request, so it is
HMAC-signed here and verified on the executor side. See
``sign_resume_token`` / ``authorize_resume_session_id`` below.
"""
from __future__ import annotations

import dataclasses
import hashlib
import hmac
import time
from typing import Any, Optional

from loguru import logger

from xyz_agent_context.agent_framework.api_config import (
    AnthropicHelperConfig,
    ClaudeConfig,
    CliHelperConfig,
    CodexConfig,
    OpenAIConfig,
    set_user_config,
    snapshot_user_config,
)
from xyz_agent_context.settings import settings

# Maps the snapshot keys to their dataclass types for reconstruction.
_CONFIG_TYPES = {
    "claude": ClaudeConfig,
    "openai": OpenAIConfig,
    "codex": CodexConfig,
    "anthropic_helper": AnthropicHelperConfig,
    "cli_helper": CliHelperConfig,
}

# ---------------------------------------------------------------------------
# Resume-capability authentication
# ---------------------------------------------------------------------------
# ``POST /agent-loop`` is unauthenticated by design (internal-trust: the
# executor holds no platform secret and needs no DB — see executor_service's
# module docstring). That is tolerable while every field describes only THIS
# request. ``resume_session_id`` breaks that property: it names a CLI session
# transcript in a CLAUDE_CONFIG_DIR that is SHARED across all tenants (one dir
# per auth kind, ``settings.claude_cli_config_path`` /
# ``claude_oauth_config_path``). Combined with a guessable ``working_path``
# (``{base}/{user_id}/{agent_id}``), an attacker who reaches the endpoint
# directly could make the CLI reload — and stream back — someone else's
# conversation. High-entropy session ids were the only mitigation.
#
# Fix: the ORCHESTRATOR (which did the real per-user validation) signs a token
# binding the handle to this exact call; the executor verifies it in constant
# time and, on any doubt, drops resume and cold-starts. This closes the
# escalation WITHOUT turning the endpoint into a credentialed surface — the
# secret authenticates one capability, not the request as a whole.
_RESUME_TOKEN_VERSION = "v1"

# Freshness window for the signed token, in seconds, applied symmetrically
# (``|now - issued_at| <= TTL``) so a modest orchestrator/executor clock skew
# does not silently disable resume.
#
# Why an explicit ``issued_at`` inside the MAC rather than a "unix minute
# bucket": (1) a bucket forces the verifier to try several candidate buckets to
# avoid the boundary race, i.e. more HMAC comparisons and a fuzzy real TTL;
# (2) ``issued_at`` is itself covered by the MAC, so it cannot be shifted by a
# replayer — it only lets the verifier bound the age; (3) the window becomes a
# tunable independent of the bucket granularity. A turn's request is built and
# POSTed immediately, so 300s is already generous.
_RESUME_TOKEN_TTL_SECONDS = 300

# One-time "cloud is missing the secret" warning. Not a per-request log: the
# condition is a deploy state, and an agent turn happens continuously.
_resume_secret_warning_emitted = False


def _resume_canonical_string(
    *,
    resume_session_id: str,
    working_path: str,
    framework: str,
    issued_at: int,
) -> str:
    """The exact byte string both sides MAC over.

    Every component is load-bearing:
      * ``resume_session_id`` — the handle being authorized;
      * ``working_path`` — pins the handle to ONE workspace, so a captured
        token cannot be retargeted at another tenant's directory (this is the
        field that made the escalation possible);
      * ``framework`` — a claude_code handle must not be replayed as codex;
      * ``issued_at`` — bounds replay to the freshness window.

    ``|`` is a safe separator here because none of the components can contain
    it (ids are ``prefix_hex``, framework is a registry key, working_path is a
    POSIX path, issued_at is an int) — so no ambiguity is possible.
    """
    return "|".join(
        [
            _RESUME_TOKEN_VERSION,
            resume_session_id,
            working_path,
            framework,
            str(issued_at),
        ]
    )


def _resume_secret() -> str:
    """The configured HMAC secret, or "" when resume auth is not provisioned."""
    return (settings.executor_resume_hmac_secret or "").strip()


def sign_resume_token(
    *,
    resume_session_id: str,
    working_path: str,
    framework: str,
    issued_at: int,
) -> Optional[str]:
    """HMAC-SHA256 the canonical string; ``None`` when no secret is configured.

    Never logs the secret or the token.
    """
    secret = _resume_secret()
    if not secret:
        return None
    canonical = _resume_canonical_string(
        resume_session_id=resume_session_id,
        working_path=working_path,
        framework=framework,
        issued_at=issued_at,
    )
    return hmac.new(
        secret.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def verify_resume_token(
    token: Any,
    *,
    resume_session_id: str,
    working_path: str,
    framework: str,
    issued_at: Any,
    now: Optional[int] = None,
) -> bool:
    """Constant-time verification of a resume token against the body's fields.

    Returns False (never raises) for every failure mode: no secret, absent /
    non-string token, non-integer or stale ``issued_at``, or a digest that does
    not match the reconstructed canonical string.
    """
    if not _resume_secret():
        return False
    if not isinstance(token, str) or not token:
        return False
    if isinstance(issued_at, bool) or not isinstance(issued_at, int):
        return False
    current = int(time.time()) if now is None else int(now)
    if abs(current - issued_at) > _RESUME_TOKEN_TTL_SECONDS:
        return False
    expected = sign_resume_token(
        resume_session_id=resume_session_id,
        working_path=working_path,
        framework=framework,
        issued_at=issued_at,
    )
    if expected is None:
        return False
    # compare_digest, not `==`: a byte-wise short-circuit compare leaks the
    # matching prefix length through timing, which is enough to forge a digest.
    return hmac.compare_digest(expected, token)


def authorize_resume_session_id(body: dict[str, Any]) -> Optional[str]:
    """Executor-side gate: the ``resume_session_id`` this run may actually use.

    Returns the handle only when the body carries a valid token for exactly
    these fields; otherwise ``None`` = cold start. Deliberately DEGRADES rather
    than rejecting the request: resume is an optimization, cold start is always
    correct, and a 4xx here would turn a signing/clock/deploy problem into a
    user-visible turn failure.
    """
    global _resume_secret_warning_emitted

    requested = body.get("resume_session_id") or None
    if not requested:
        return None

    if not _resume_secret():
        if not _resume_secret_warning_emitted:
            _resume_secret_warning_emitted = True
            logger.warning(
                "[Executor] resume auth is NOT provisioned "
                "(EXECUTOR_RESUME_HMAC_SECRET empty) — ignoring "
                "resume_session_id on every request and cold-starting. Set the "
                "same secret on the orchestrator and on this container to "
                "re-enable the resume optimization."
            )
        return None

    if verify_resume_token(
        body.get("resume_auth_token"),
        resume_session_id=str(requested),
        working_path=str(body.get("working_path") or ""),
        framework=str(body.get("framework") or ""),
        issued_at=body.get("resume_auth_issued_at"),
    ):
        return str(requested)

    logger.warning(
        "[Executor] resume_session_id rejected: invalid, stale or absent auth "
        "token for this (working_path, framework) — cold-starting this turn."
    )
    return None


def serialize_provider_configs() -> dict[str, Optional[dict]]:
    """Snapshot the current task's resolved provider configs as plain dicts.

    Called on the orchestrator side (which ran the provider resolver).
    ``None`` entries are preserved so the executor reproduces the exact
    same ContextVar state (e.g. anthropic_helper unset).
    """
    snap = snapshot_user_config()
    out: dict[str, Optional[dict]] = {}
    for key, cfg in snap.items():
        out[key] = dataclasses.asdict(cfg) if cfg is not None else None
    return out


def apply_provider_configs(payload: dict[str, Optional[dict]]) -> None:
    """Rebuild provider configs from the wire payload and set ContextVars.

    Called on the executor side before running the driver, so the SDK's
    ``to_cli_env`` resolves the same scoped credentials the orchestrator
    chose — without the executor ever touching the DB or the resolver.
    """
    def _build(key: str):
        raw = payload.get(key)
        if raw is None:
            return None
        return _CONFIG_TYPES[key](**raw)

    set_user_config(
        claude=_build("claude") or ClaudeConfig(),
        openai=_build("openai") or OpenAIConfig(),
        codex=_build("codex"),
        anthropic_helper=_build("anthropic_helper"),
        cli_helper=_build("cli_helper"),
    )


def build_agent_loop_request(
    *,
    framework: str,
    working_path: str,
    messages: list[dict[str, Any]],
    mcp_servers: dict[str, dict],
    extra_env: Optional[dict[str, str]],
    streaming: bool = True,
    disallowed_tools: Optional[list[str]] = None,
    resume_session_id: Optional[str] = None,
) -> dict[str, Any]:
    """Assemble the JSON body for ``POST /agent-loop``.

    ``cancellation`` is intentionally NOT serialized — the orchestrator
    cancels by aborting the HTTP stream; the executor observes client
    disconnect. Provider configs are snapshotted here so the scoped creds
    cross the boundary explicitly (they normally ride a ContextVar).

    When ``resume_session_id`` is set we ALSO mint the HMAC token that
    authorizes it for this exact (working_path, framework) at this time — the
    executor honours the handle only against a valid token. With no secret
    configured the token fields are simply absent and the executor cold-starts.
    """
    body: dict[str, Any] = {
        "framework": framework,
        "working_path": working_path,
        "messages": messages,
        "mcp_servers": mcp_servers,
        "extra_env": extra_env,
        "streaming": streaming,
        # Setup-residency: per-agent tool suppression must cross the network
        # boundary explicitly (it is per-run state, like the messages).
        "disallowed_tools": disallowed_tools or [],
        # Agent-loop resume: the validated CLI session handle for this run
        # (None = cold start). Per-run state like disallowed_tools; an old
        # executor that ignores the field just cold-starts — fail-open.
        "resume_session_id": resume_session_id,
        "provider_configs": serialize_provider_configs(),
    }
    if resume_session_id:
        issued_at = int(time.time())
        token = sign_resume_token(
            resume_session_id=resume_session_id,
            working_path=working_path,
            framework=framework,
            issued_at=issued_at,
        )
        if token is not None:
            # issued_at travels in the clear ON PURPOSE: it is inside the MAC,
            # so it is tamper-evident, and the verifier needs it to bound age.
            body["resume_auth_issued_at"] = issued_at
            body["resume_auth_token"] = token
    return body
