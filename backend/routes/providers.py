"""
@file_name: providers.py
@author: NexusAgent
@date: 2026-04-08
@description: REST API routes for LLM provider and slot configuration

Per-user provider isolation: each user has their own providers and slots
stored in user_providers and user_slots tables. Works identically on
both SQLite (local) and MySQL (cloud).
"""

from __future__ import annotations

import os

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request
from loguru import logger
from pydantic import BaseModel

from xyz_agent_context.agent_framework.model_catalog import (
    get_all_known_models,
    get_default_models,
    get_suggested_models,
    get_known_embedding_models,
    OFFICIAL_BASE_URLS,
)
from xyz_agent_context.schema.provider_schema import (
    LLMConfig,
    SlotName,
    SLOT_REQUIRED_PROTOCOLS,
)

router = APIRouter()


# =============================================================================
# Request/Response Models
# =============================================================================

class SlotDefault(BaseModel):
    protocol: str
    model: str


class AddProviderRequest(BaseModel):
    card_type: str
    name: str = ""
    api_key: str = ""
    base_url: str = ""
    auth_type: str = "api_key"
    models: list[str] = []
    default_slots: dict[str, SlotDefault] | None = None


class SetSlotRequest(BaseModel):
    provider_id: str
    model: str


class UpdateModelsRequest(BaseModel):
    models: list[str]


class SetAgentFrameworkRequest(BaseModel):
    """Body for ``POST /api/providers/agent-framework``."""
    framework: str  # "claude_code" | "codex_cli"


# =============================================================================
# Helpers
# =============================================================================

def _get_user_id(request: Request) -> str:
    """Return the identity established by auth_middleware.

    Cloud mode: JWT-decoded user_id; local mode: X-User-Id header.
    Either way, middleware writes ``request.state.user_id`` BEFORE the
    handler runs, and 401s the request if the caller didn't declare an
    identity — so by the time we get here, the value is present and is
    the only legitimate source.

    The previous version also accepted a ``user_id`` query param as a
    backup. That was a cross-user write/read bug waiting to happen: the
    frontend could trivially construct ``?user_id=alice`` while logged
    in as bob, and have the backend silently scope provider config to
    alice. Identity must come from auth_middleware, never the URL.
    """
    uid = getattr(request.state, "user_id", None)
    if not uid:
        raise HTTPException(
            status_code=401,
            detail="Authentication required (no user_id on request.state)",
        )
    return uid


async def _get_service():
    """Get UserProviderService with DB client."""
    from xyz_agent_context.utils.db_factory import get_db_client
    from xyz_agent_context.agent_framework.user_provider_service import UserProviderService
    db = await get_db_client()
    return UserProviderService(db)


def _config_to_response(config: LLMConfig) -> dict:
    """Convert LLMConfig to API response dict with masked api_key."""
    providers = {}
    for pid, prov in config.providers.items():
        d = prov.model_dump(mode="json")
        if d["api_key"] and len(d["api_key"]) > 4:
            d["api_key_masked"] = "***" + d["api_key"][-4:]
        else:
            d["api_key_masked"] = "***"
        del d["api_key"]
        providers[pid] = d

    slots = {}
    for slot_name in SlotName:
        slot_str = slot_name.value
        required = [p.value for p in SLOT_REQUIRED_PROTOCOLS.get(slot_str, [])]
        slot_cfg = config.slots.get(slot_str)
        slots[slot_str] = {
            "required_protocols": required,
            "config": slot_cfg.model_dump() if slot_cfg else None,
        }

    return {"version": config.version, "providers": providers, "slots": slots}


async def _run_json_subprocess(args: list[str], timeout: float) -> dict | None:
    """Run a short CLI probe without blocking the FastAPI event loop."""
    import asyncio
    import json as _json
    from contextlib import suppress

    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        return None

    try:
        stdout, _stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        with suppress(ProcessLookupError, OSError):
            proc.kill()
        with suppress(Exception):
            await proc.wait()
        return None

    if proc.returncode != 0 or not stdout.strip():
        return None

    try:
        data = _json.loads(stdout.decode("utf-8", errors="replace"))
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


# =============================================================================
# Endpoints
# =============================================================================

@router.get("")
async def get_providers(request: Request):
    uid = _get_user_id(request)
    service = await _get_service()
    config = await service.get_user_config(uid)
    return {"success": True, "data": _config_to_response(config)}


@router.post("")
async def add_provider(req: AddProviderRequest, request: Request):
    uid = _get_user_id(request)
    try:
        service = await _get_service()
        config, new_ids = await service.add_provider(
            user_id=uid,
            card_type=req.card_type,
            name=req.name,
            api_key=req.api_key,
            base_url=req.base_url,
            auth_type=req.auth_type,
            models=req.models if req.models else None,
        )

        if req.default_slots:
            for slot_name, slot_def in req.default_slots.items():
                match_pid = None
                for pid in new_ids:
                    prov = config.providers.get(pid)
                    if prov and prov.protocol.value == slot_def.protocol:
                        match_pid = pid
                        break
                if match_pid:
                    config = await service.set_slot(uid, slot_name, match_pid, slot_def.model)

        # Hot-reload for current process (local mode)
        try:
            from xyz_agent_context.agent_framework.api_config import (
                get_user_runtime_llm_configs,
                set_user_config,
            )
            cfg = await get_user_runtime_llm_configs(uid)
            set_user_config(cfg.claude, cfg.openai, cfg.embedding, cfg.codex)
        except Exception:
            pass

        # Edge-triggered recovery: a newly-added provider (with default slots)
        # can make the user runnable — revive their PAUSED_NO_QUOTA jobs.
        from xyz_agent_context.module.job_module.job_recovery import (
            schedule_user_no_quota_rearm,
        )
        schedule_user_no_quota_rearm(uid)

        return {"success": True, "provider_ids": new_ids, "data": _config_to_response(config)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception(f"[add_provider] Error: {e}", exc_info=True)
        return {"success": False, "detail": str(e)}


@router.delete("/{provider_id}")
async def remove_provider(provider_id: str, request: Request):
    uid = _get_user_id(request)
    service = await _get_service()
    try:
        config = await service.remove_provider(uid, provider_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"success": True, "data": _config_to_response(config)}


@router.post("/{provider_id}/test")
async def test_provider(provider_id: str, request: Request):
    uid = _get_user_id(request)
    service = await _get_service()
    success, message = await service.test_provider(uid, provider_id)
    return {"success": success, "message": message}


@router.post("/sync-defaults")
async def sync_default_models(request: Request):
    """Backfill the latest default model list from `model_catalog` into every
    one of this user's providers whose (source, protocol) pair has defaults.

    Idempotent — providers already in sync return zero added entries.
    Existing user-curated entries are preserved; only missing defaults are
    appended at the end.
    """
    uid = _get_user_id(request)
    service = await _get_service()
    config = await service.get_user_config(uid)

    updates: list[dict] = []
    for prov_id, prov in config.providers.items():
        # Only sync preset providers (netmind, yunwu, openrouter, claude_oauth, ...).
        # `source="user"` means a custom provider where the user picked the model
        # list themselves — auto-injecting "official" suggestion lists there
        # would dump OpenAI/Anthropic-only models into proxies that may not
        # support them.
        if prov.source.value == "user":
            continue
        defaults = list(get_default_models(prov.source.value, prov.protocol.value))
        if not defaults:
            continue  # no canonical default list registered for this combo
        existing = list(prov.models or [])
        missing = [m for m in defaults if m not in existing]
        if not missing:
            continue
        new_models = existing + missing
        await service.update_models(uid, prov_id, new_models)
        updates.append({
            "provider_id": prov_id,
            "name": prov.name,
            "source": prov.source.value,
            "protocol": prov.protocol.value,
            "added": missing,
        })

    return {
        "success": True,
        "updates": updates,
        "providers_updated": len(updates),
        "total_models_added": sum(len(u["added"]) for u in updates),
    }


@router.put("/{provider_id}/models")
async def update_provider_models(provider_id: str, req: UpdateModelsRequest, request: Request):
    uid = _get_user_id(request)
    service = await _get_service()
    try:
        config = await service.update_models(uid, provider_id, req.models)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"success": True, "data": _config_to_response(config)}


@router.put("/slots/{slot_name}")
async def set_slot(slot_name: str, req: SetSlotRequest, request: Request):
    uid = _get_user_id(request)
    try:
        service = await _get_service()
        config = await service.set_slot(uid, slot_name, req.provider_id, req.model)

        errors = []
        for s in SlotName:
            if s.value not in config.slots:
                errors.append(f"Slot '{s.value}' not configured")

        # Hot-reload for current process
        try:
            from xyz_agent_context.agent_framework.api_config import (
                get_user_runtime_llm_configs,
                set_user_config,
            )
            cfg = await get_user_runtime_llm_configs(uid)
            set_user_config(cfg.claude, cfg.openai, cfg.embedding, cfg.codex)
        except Exception:
            pass

        # Edge-triggered recovery: completing/changing the agent slot can make
        # the user runnable — revive their PAUSED_NO_QUOTA jobs (non-blocking).
        from xyz_agent_context.module.job_module.job_recovery import (
            schedule_user_no_quota_rearm,
        )
        schedule_user_no_quota_rearm(uid)

        return {"success": True, "data": _config_to_response(config), "validation_errors": errors}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/slots/validate")
async def validate_slots(request: Request):
    uid = _get_user_id(request)
    service = await _get_service()
    errors = await service.validate_slots(uid)
    return {"success": True, "errors": errors, "all_configured": len(errors) == 0}


# =============================================================================
# Agent Framework (coding-agent CLI choice — Claude Code vs Codex CLI)
# =============================================================================
#
# Persisted as ``user_slots[user_id, slot_name='agent'].agent_framework``.
# Read by ``step_3_agent_loop._resolve_agent_framework_sdk`` per turn
# to pick which SDK class drives the agent_loop. Defaults to
# "claude_code" so existing users are unaffected.


from xyz_agent_context.agent_framework.user_provider_service import (
    UserProviderService as _UserProviderServiceForFrameworks,
)
# Single source of truth — keep the route's whitelist in sync with the
# service layer. Adding a v3 framework name in user_provider_service
# automatically opens the route here, no double-edit required.
_SUPPORTED_AGENT_FRAMEWORKS = _UserProviderServiceForFrameworks._SUPPORTED_AGENT_FRAMEWORKS

# npm install timeout for auto-install of @openai/codex. Codex CLI is a
# Rust binary distributed via npm; the first install pulls a postinstall
# script that downloads the platform binary, which can take 30-60s on
# slow links. Matches the timeout used for @anthropic-ai/claude-code in
# run.sh:_try_install_claude_cli.
_CODEX_NPM_INSTALL_TIMEOUT = 120.0


async def _ensure_codex_installed() -> dict:
    """Auto-install ``@openai/codex`` via ``npm install -g`` when the
    user opts into Codex CLI from the Settings page.

    Mirrors the behaviour of ``run.sh:_try_install_claude_cli`` for
    Claude Code — that one is run unconditionally at boot because
    Claude is the default framework. Codex is opt-in, so the install
    fires lazily at framework-selection time instead.

    Returns:
        Dict shape ``{"installed": bool, "action": str, "reason": str}``.
        ``action`` values:
          - ``"already_installed"`` — ``codex`` was already on PATH.
          - ``"auto_installed"`` — we just installed it; ``codex`` now
            on PATH.
          - ``"blocked"`` — refused because we're in cloud mode.
          - ``"install_failed"`` — npm exited non-zero, timed out, or
            the binary isn't on PATH after a successful exit.

    Cloud-mode refusal is deliberate: a multi-tenant deployment must
    not let one user mutate the shared host's globally-installed npm
    packages. Cloud operators install Codex out-of-band if they want
    to support that path; users only see ``blocked`` until then.

    Auto-login is NOT triggered here. ``codex login`` requires a
    browser OAuth round-trip that we can't drive from a HTTP handler.
    After ``auto_installed``, the probe will report
    ``auth missing — run codex login`` until the user completes login
    on the host.
    """
    import asyncio
    import shutil

    if shutil.which("codex"):
        return {
            "installed": True,
            "action": "already_installed",
            "reason": "",
        }

    from xyz_agent_context.utils.deployment_mode import get_deployment_mode
    if get_deployment_mode() == "cloud":
        return {
            "installed": False,
            "action": "blocked",
            "reason": (
                "Cloud mode: per-user global npm install is not "
                "permitted on a shared host. Ask the administrator "
                "to install @openai/codex on the cloud deployment "
                "if you need Codex CLI support."
            ),
        }

    if not shutil.which("npm"):
        return {
            "installed": False,
            "action": "install_failed",
            "reason": (
                "npm is not installed or not on PATH. Install Node.js "
                "from https://nodejs.org/ (or `brew install node` on "
                "macOS) and try again."
            ),
        }

    logger.info(
        "[providers] auto-installing @openai/codex "
        f"(timeout {_CODEX_NPM_INSTALL_TIMEOUT}s)"
    )
    try:
        proc = await asyncio.create_subprocess_exec(
            "npm", "install", "-g", "@openai/codex",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        # Race: npm vanished between which() and exec(). Rare but
        # possible on a system being modified in parallel.
        return {
            "installed": False,
            "action": "install_failed",
            "reason": "npm binary not found at exec time",
        }

    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=_CODEX_NPM_INSTALL_TIMEOUT
        )
    except asyncio.TimeoutError:
        try:
            proc.kill()
            await proc.wait()
        except (ProcessLookupError, OSError):
            pass
        return {
            "installed": False,
            "action": "install_failed",
            "reason": (
                f"npm install -g @openai/codex timed out after "
                f"{_CODEX_NPM_INSTALL_TIMEOUT:.0f}s. Check your network "
                f"or run it manually in a terminal."
            ),
        }

    if proc.returncode != 0:
        # Trim stderr — npm error output is verbose; first ~500 chars
        # usually carries the actionable detail (EACCES, ENOTFOUND, ...).
        err_msg = stderr.decode("utf-8", errors="replace").strip()
        if len(err_msg) > 500:
            err_msg = err_msg[:500] + "...(truncated)"
        logger.warning(
            f"[providers] @openai/codex install failed "
            f"(rc={proc.returncode}): {err_msg}"
        )
        return {
            "installed": False,
            "action": "install_failed",
            "reason": (
                f"npm install -g @openai/codex exited rc={proc.returncode}. "
                f"Output: {err_msg}"
            ),
        }

    # Sanity: confirm `codex` is actually on PATH now. npm sometimes
    # reports success but the global prefix isn't in $PATH — annoying
    # corner case that's easier to surface here than at agent_loop time.
    if not shutil.which("codex"):
        return {
            "installed": False,
            "action": "install_failed",
            "reason": (
                "npm install reported success but `codex` is not on "
                "PATH. Check `npm config get prefix` is in your $PATH "
                "(typically /usr/local or ~/.local — re-source your "
                "shell or restart the backend)."
            ),
        }

    logger.info("[providers] @openai/codex auto-installed successfully")
    return {
        "installed": True,
        "action": "auto_installed",
        "reason": "",
    }


async def _probe_agent_framework_auth(framework: str) -> dict:
    """Run the per-framework OAuth credential probe.

    Returns ``{"ok": bool, "detail": str}``. Synthesizes a stub
    ProviderCard with the right ``auth_ref`` so we can reuse the
    existing driver's probe() — no need to look up an actual
    ``user_providers`` row (the framework choice is independent of
    which provider drives the helper_llm / embedding slots).
    """
    from xyz_agent_context.agent_framework.provider_driver.base import ProviderCard

    # Codex auth probe — reads ``~/.codex/auth.json`` regardless of
    # which codex driver class is registered (v1 or v2 share the
    # auth file path).
    if framework == "codex_cli":
        from xyz_agent_context.agent_framework.provider_driver.drivers.codex_oauth import (
            CodexOAuthDriver,
        )
        from xyz_agent_context.agent_framework.provider_driver.derive import (
            CODEX_CLI_CREDENTIALS_REF,
        )
        stub = ProviderCard(
            provider_id="_probe_codex",
            user_id="_probe",
            name="probe",
            source="codex_oauth",
            protocol="openai",
            auth_type="oauth",
            api_key="",
            base_url="",
            auth_ref=CODEX_CLI_CREDENTIALS_REF,
            driver_type="codex_oauth",
        )
        health = await CodexOAuthDriver(stub).probe()
        return {"ok": health.ok, "detail": health.detail}

    if framework == "claude_code":
        from xyz_agent_context.agent_framework.provider_driver.drivers.claude_oauth import (
            ClaudeOAuthDriver,
        )
        from xyz_agent_context.agent_framework.provider_driver.derive import (
            CLAUDE_CLI_CREDENTIALS_REF,
        )
        stub = ProviderCard(
            provider_id="_probe_claude",
            user_id="_probe",
            name="probe",
            source="claude_oauth",
            protocol="anthropic",
            auth_type="oauth",
            api_key="",
            base_url="",
            auth_ref=CLAUDE_CLI_CREDENTIALS_REF,
            driver_type="claude_oauth",
        )
        health = await ClaudeOAuthDriver(stub).probe()
        return {"ok": health.ok, "detail": health.detail}

    return {"ok": False, "detail": f"unknown framework: {framework}"}


@router.get("/agent-framework")
async def get_agent_framework(request: Request):
    """Return the user's current coding-agent framework + auth probe."""
    uid = _get_user_id(request)
    service = await _get_service()
    framework = await service.get_user_agent_framework(uid)
    probe = await _probe_agent_framework_auth(framework)
    return {
        "success": True,
        "data": {
            "framework": framework,
            "supported": list(_SUPPORTED_AGENT_FRAMEWORKS),
            "probe": probe,
        },
    }


@router.post("/agent-framework")
async def set_agent_framework(request: Request, body: SetAgentFrameworkRequest):
    """Persist the user's coding-agent framework choice.

    Side effect: when ``framework == "codex_cli"`` and the ``codex``
    binary is not on PATH (local mode only), this endpoint
    auto-installs ``@openai/codex`` via ``npm install -g`` before
    persisting the choice. The install result is returned in the
    response so the frontend can surface it ("auto-installed, now
    run ``codex login``" / "install failed, see reason" / "blocked
    in cloud mode").
    """
    uid = _get_user_id(request)
    if body.framework not in _SUPPORTED_AGENT_FRAMEWORKS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unknown framework {body.framework!r}. "
                f"Supported: {list(_SUPPORTED_AGENT_FRAMEWORKS)}"
            ),
        )

    # Auto-install codex CLI on opt-in (local mode only; cloud mode
    # returns action="blocked"). claude_code path skips this — the
    # `claude` binary is already installed at run.sh boot time. The
    # ``openai-codex`` Python SDK internally spawns the ``codex``
    # binary in app-server mode, so the install side-effect is still
    # required.
    install_result: dict | None = None
    if body.framework == "codex_cli":
        install_result = await _ensure_codex_installed()

    service = await _get_service()
    try:
        await service.set_user_agent_framework(uid, body.framework)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    probe = await _probe_agent_framework_auth(body.framework)
    return {
        "success": True,
        "data": {
            "framework": body.framework,
            "probe": probe,
            "install": install_result,  # null for claude_code, dict for codex_cli
        },
    }


@router.get("/catalog")
async def get_catalog():
    return {
        "success": True,
        "known_models": get_all_known_models(),
        "suggestions": {
            "anthropic": get_suggested_models("anthropic"),
            "openai": get_suggested_models("openai"),
        },
        "embedding_models": get_known_embedding_models(),
        "official_base_urls": {protocol: list(urls) for protocol, urls in OFFICIAL_BASE_URLS.items()},
        "slot_protocols": {slot.value: [p.value for p in protos] for slot, protos in SLOT_REQUIRED_PROTOCOLS.items()},
    }


# =============================================================================
# Claude Code Auth Status
# =============================================================================

@router.get("/claude-status")
async def get_claude_status(request: Request):
    """Check if Claude Code CLI is logged in. Cloud: only staff can use it.

    Response fields:
      - cli_installed: bool — `claude` binary on PATH
      - logged_in:     bool — auth status reports an active token
      - email:         str | None — account email if discoverable
      - expires_at:    str | None — ISO-8601 token expiry if surfaced
    """
    import json as _json
    from pathlib import Path

    result = {"cli_installed": False, "logged_in": False, "email": None, "expires_at": None}

    is_staff = getattr(request.state, 'role', '') == 'staff'
    is_cloud = not os.environ.get("DATABASE_URL", "").startswith("sqlite")
    if is_cloud and not is_staff:
        return {"success": True, "data": {**result, "allowed": False}}

    import shutil
    claude_path = shutil.which("claude")
    if claude_path:
        result["cli_installed"] = True

    # Preferred: use `claude auth status` (Claude Code v2.x+).
    # Output schema isn't formally documented and shifts between minor
    # versions, so we probe a few common shapes for email / expiry instead
    # of pinning to one. Anything we can't parse stays None — the UI just
    # won't show those subfields.
    if claude_path:
        auth_data = await _run_json_subprocess(
            [claude_path, "auth", "status"],
            timeout=10,
        )
        if auth_data:
            if auth_data.get("loggedIn"):
                result["logged_in"] = True
            # Email — try flat then nested under account/user.
            email = auth_data.get("email")
            if not email:
                for nested_key in ("account", "user", "profile"):
                    nested = auth_data.get(nested_key)
                    if isinstance(nested, dict) and nested.get("email"):
                        email = nested["email"]
                        break
            if isinstance(email, str) and email:
                result["email"] = email
            # Expiry — flat fields first, then under token/oauth.
            for key in ("expiresAt", "expires_at", "tokenExpiresAt"):
                val = auth_data.get(key)
                if val:
                    result["expires_at"] = str(val)
                    break
            if not result["expires_at"]:
                for nested_key in ("token", "oauth", "credentials"):
                    nested = auth_data.get(nested_key)
                    if isinstance(nested, dict):
                        for key in ("expiresAt", "expires_at"):
                            if nested.get(key):
                                result["expires_at"] = str(nested[key])
                                break
                        if result["expires_at"]:
                            break

    # Fallback: check legacy credentials file (Claude Code v1.x).
    # Mostly used to backfill logged_in when `claude auth status` is missing
    # or the user is on an older CLI. Email/expires_at usually aren't in
    # this file, so they may stay None even when we mark logged_in=True.
    if not result["logged_in"]:
        creds_file = Path.home() / ".claude" / ".credentials.json"
        if creds_file.is_file():
            try:
                data = _json.loads(creds_file.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    for key in ("accessToken", "oauthToken", "claudeAiOauth", "oauth"):
                        if data.get(key):
                            result["logged_in"] = True
                            break
                    # Best-effort metadata extraction from the credentials
                    # file. Different CLI versions stash these in different
                    # places; we walk the common ones.
                    if not result["email"]:
                        for nested_key in ("claudeAiOauth", "oauth", "account", "user"):
                            nested = data.get(nested_key)
                            if isinstance(nested, dict) and nested.get("email"):
                                result["email"] = nested["email"]
                                break
                    if not result["expires_at"]:
                        for nested_key in ("claudeAiOauth", "oauth"):
                            nested = data.get(nested_key)
                            if isinstance(nested, dict):
                                for key in ("expiresAt", "expires_at"):
                                    if nested.get(key):
                                        result["expires_at"] = str(nested[key])
                                        break
                                if result["expires_at"]:
                                    break
            except Exception:
                pass

    return {"success": True, "data": result}


# =============================================================================
# Codex CLI Auth Status (mirror of /claude-status)
# =============================================================================


@router.get("/codex-status")
async def get_codex_status(request: Request):
    """Check if Codex CLI is installed + has an active OAuth session.

    Response fields mirror ``/claude-status`` so the frontend can
    reuse the same UI shape:
      - ``cli_installed``: bool — ``codex`` binary on PATH
      - ``logged_in``:     bool — ``~/.codex/auth.json`` present
      - ``email``:         str | None — best-effort if parseable
      - ``expires_at``:    str | None — best-effort if parseable

    Cloud mode hides the card for non-staff (per /claude-status policy).
    Auth.json's schema is undocumented and may shift between versions;
    we only attempt to extract email + expiry on a best-effort basis,
    leaving them None when we can't parse.
    """
    import json as _json
    from pathlib import Path

    result = {
        "cli_installed": False,
        "logged_in": False,
        "email": None,
        "expires_at": None,
    }

    is_staff = getattr(request.state, "role", "") == "staff"
    is_cloud = not os.environ.get("DATABASE_URL", "").startswith("sqlite")
    if is_cloud and not is_staff:
        return {"success": True, "data": {**result, "allowed": False}}

    import shutil
    if shutil.which("codex"):
        result["cli_installed"] = True

    # Existence check on the auth file is the canonical "logged in"
    # signal — Codex CLI itself uses the same check on subprocess
    # start (it errors with "not logged in" if the file is absent).
    codex_home = os.environ.get("CODEX_HOME")
    if codex_home:
        creds_file = Path(codex_home).expanduser() / "auth.json"
    else:
        creds_file = Path.home() / ".codex" / "auth.json"

    if creds_file.is_file():
        result["logged_in"] = True
        # Best-effort metadata extraction — Codex auth.json schema
        # is undocumented; we look for common shapes and leave the
        # fields None when none match.
        try:
            data = _json.loads(creds_file.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                # Email — try flat then nested under common keys
                email = data.get("email")
                if not email:
                    for nested_key in ("account", "user", "profile", "chatgpt"):
                        nested = data.get(nested_key)
                        if isinstance(nested, dict) and nested.get("email"):
                            email = nested["email"]
                            break
                if isinstance(email, str) and email:
                    result["email"] = email
                # Expiry — flat then nested under token/oauth
                for key in ("expiresAt", "expires_at", "tokenExpiresAt"):
                    val = data.get(key)
                    if val:
                        result["expires_at"] = str(val)
                        break
                if not result["expires_at"]:
                    for nested_key in ("token", "oauth", "credentials"):
                        nested = data.get(nested_key)
                        if isinstance(nested, dict):
                            for key in ("expiresAt", "expires_at"):
                                if nested.get(key):
                                    result["expires_at"] = str(nested[key])
                                    break
                            if result["expires_at"]:
                                break
        except Exception:
            # File is present but unparseable — keep logged_in=True
            # (Codex itself would still try to use it), just leave
            # email + expires_at None.
            pass

    return {"success": True, "data": result}


# =============================================================================
# Embedding Migration
# =============================================================================

@router.get("/embeddings/status")
async def get_embedding_status(
    request: Request,
    user_id: str = Query(..., description="User ID to scope the status"),
):
    """
    Per-user embedding migration status.

    Returns counts of entities (narrative / event / job / entity) that
    belong to `user_id` and whether each has an embedding for that user's
    active model. Concurrent status checks by different users do not
    interfere with each other.
    """
    from xyz_agent_context.utils.db_factory import get_db_client
    from xyz_agent_context.services.embedding_migration_service import EmbeddingMigrationService
    if not user_id:
        raise HTTPException(status_code=400, detail="user_id is required")
    db = await get_db_client()
    resolver = getattr(request.app.state, "provider_resolver", None)
    service = EmbeddingMigrationService(db, user_id=user_id, resolver=resolver)
    status = await service.get_status()
    return {"success": True, "data": status}


@router.post("/embeddings/rebuild")
async def rebuild_embeddings(
    request: Request,
    background_tasks: BackgroundTasks,
    user_id: str = Query(..., description="User ID whose entities to rebuild"),
):
    """
    Kick off a background rebuild of this user's missing embeddings.

    Each user has an independent `MigrationProgress`; starting a rebuild
    for user A does not block user B. If the same user already has a
    rebuild running, the request is a no-op.
    """
    from xyz_agent_context.utils.db_factory import get_db_client
    from xyz_agent_context.services.embedding_migration_service import (
        EmbeddingMigrationService,
        get_migration_progress,
    )
    if not user_id:
        raise HTTPException(status_code=400, detail="user_id is required")
    progress = get_migration_progress(user_id)
    if progress.is_running:
        return {
            "success": False,
            "error": "Migration already in progress",
            "data": progress.to_dict(),
        }
    db = await get_db_client()
    resolver = getattr(request.app.state, "provider_resolver", None)
    service = EmbeddingMigrationService(db, user_id=user_id, resolver=resolver)
    background_tasks.add_task(service.rebuild_all)
    return {"success": True, "message": "Embedding rebuild started"}
