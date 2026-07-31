"""
@file_name: codex_oauth.py
@date: 2026-05-29
@description: Driver for OpenAI Codex CLI OAuth (host-CLI managed) provider.

The Codex CLI on the host machine performs the OAuth flow (``codex
login`` → Sign in with ChatGPT) and stores the resulting tokens in
``~/.codex/auth.json`` (or ``$CODEX_HOME/auth.json``). NarraNexus
does NOT store the tokens itself; the ``user_providers`` row
carries:

* ``api_key`` = empty string (intentionally — see CodexConfig.to_cli_env)
* ``auth_type`` = ``"oauth"``
* ``auth_ref`` = ``"codex-cli:~/.codex/auth.json"``
* ``supports_anthropic_server_tools`` = False (Codex is OpenAI; no
  Anthropic server tools)

The ``codex exec`` subprocess reads the token from the credentials
file on its own when ``CODEX_API_KEY`` is empty. The
``CodexConfig.to_cli_env`` builder already produces that empty-key
shape for ``auth_type="oauth"``.

OAuth rows can't serve the helper_llm or embedding slot — those need
chat-completions / embedding endpoints, neither of which Codex
provides via the OAuth credential. The agent slot is served through
:meth:`build_codex_config` (the codex_cli framework path): this driver
overrides it to force the shared CLI credential ref
(``CODEX_CLI_CREDENTIALS_REF``) so the ``codex exec`` subprocess reads
the token from ``~/.codex/auth.json`` rather than an env var.
``build_claude_config`` / ``build_openai_config`` stay
NotImplementedError — Codex is not an anthropic provider and the OAuth
credential can't serve chat-completions.
"""
from __future__ import annotations

from xyz_agent_context.agent_framework.api_config import (
    CliHelperConfig,
    CodexConfig,
)
from xyz_agent_context.agent_framework.providers.driver.base import (
    DriverHealth,
    _DriverBase,
)
from xyz_agent_context.agent_framework.providers.driver.derive import (
    CODEX_CLI_CREDENTIALS_REF,
    resolve_codex_credentials_path,
)
from xyz_agent_context.agent_framework.providers.driver.registry import register


@register
class CodexOAuthDriver(_DriverBase):
    """OpenAI Codex CLI OAuth provider — token lives in the host CLI."""

    @classmethod
    def driver_type(cls) -> str:
        return "codex_oauth"

    # build_claude_config / build_openai_config keep the _DriverBase
    # NotImplementedError defaults — Codex is not anthropic, and the OAuth
    # credential can't serve chat-completions DIRECTLY. The agent slot uses
    # build_codex_config; the helper slot uses build_cli_helper_config
    # (framework="codex_cli"), which runs one-shot through the same codex CLI
    # so a single subscription covers both slots.

    def build_cli_helper_config(self, model: str) -> CliHelperConfig:
        return CliHelperConfig(
            framework="codex_cli",
            model=model,
            base_url=self.card.base_url or "",
            auth_type=self.card.auth_type or "oauth",
            api_key=self.card.api_key or "",
        )

    def build_codex_config(
        self,
        model: str,
        *,
        thinking: str = "",
        reasoning_effort: str = "",
    ) -> CodexConfig:
        # OAuth: the token lives in the host CLI's auth.json, not in the
        # card. Force the canonical credential ref so the run-time stager
        # copies ~/.codex/auth.json into the per-run CODEX_HOME; leave
        # api_key empty (to_cli_env blanks CODEX_API_KEY for oauth).
        auth_type = (self.card.auth_type or "oauth")
        auth_ref = (
            CODEX_CLI_CREDENTIALS_REF
            if auth_type.lower() == "oauth"
            else (self.card.auth_ref or "")
        )
        return CodexConfig(
            api_key=self.card.api_key,
            base_url=self.card.base_url,
            model=model,
            auth_type=auth_type,
            auth_ref=auth_ref,
            thinking=thinking,
            reasoning_effort=reasoning_effort,
        )

    async def verify_live(self) -> tuple[bool, str]:
        """Real end-to-end check: one one-shot ``codex exec`` call.

        The P0 this fixes (2026-07-31): the Test button answered every
        ``auth_type == "oauth"`` row with an unconditional pass, so expired
        codex CLI credentials showed "usable" — and ProviderReadiness
        re-armed paused jobs onto them. Existence of ``auth.json`` is not
        health: the file survives while its refresh token dies. The only
        honest verdict is the same transport the agent uses, so this runs
        one tool-free turn through the registered codex agent-loop driver.

        The codex driver reads its config from the ambient ``_codex_ctx``
        ContextVar (the AGENT slot's config, not this card's), so the
        card's own CodexConfig is installed for the duration and reset
        after — the same pattern as ``CliHelperSDK._run_codex_oneshot``.
        Dead credentials surface as a terminal error EVENT
        (error_type="unauthorized"), not an exception.

        Expensive (spawns the CLI), so it runs only on explicit user
        action or a readiness edge, never from ``probe()``. A single
        tool-free turn is NOT the agent_loop — helper-scale timeouts do
        not violate 铁律 #14.
        """
        import asyncio
        import os
        import shutil
        import tempfile

        # Fail fast without spawning: no credentials file → nothing to verify.
        health = await self.probe()
        if not health.ok:
            return False, health.detail
        if shutil.which("codex") is None:
            return False, "codex CLI not found on PATH — cannot verify"

        from xyz_agent_context.agent_framework.api_config import _codex_ctx
        from xyz_agent_context.agent_framework.providers.model_catalog import (
            get_default_models,
        )
        from xyz_agent_context.settings import settings as _settings

        # Curated default first: stored `models` may carry pinned ids that
        # upstream has retired (the 2026-07-30 dead-pinned-id lesson), and a
        # verification must not fail on a dead model name while the
        # credential is fine.
        defaults = get_default_models("codex_oauth", "openai")
        model = defaults[0] if defaults else (self.card.models[0] if self.card.models else "")
        if not model:
            return False, "no codex model available to verify with"

        async def _one_shot() -> tuple[bool, str]:
            from xyz_agent_context.agent_framework import get_agent_loop_driver
            from xyz_agent_context.agent_framework.loop.events import (
                DATA_TYPE_ERROR,
                DATA_TYPE_TEXT_DELTA,
                TYPE_RAW_RESPONSE_EVENT,
            )

            # Disposable, per-uid cwd: same containment rationale as
            # CliHelperSDK's _HELPER_CWD (writable_roots must never be the
            # backend process cwd).
            cwd = os.path.join(
                tempfile.gettempdir(), f"narranexus-verify-{os.getuid()}"
            )
            os.makedirs(cwd, mode=0o700, exist_ok=True)
            driver = get_agent_loop_driver(framework="codex_cli", working_path=cwd)
            got_text = False
            err_msg = ""
            async for ev in driver.agent_loop(
                messages=[
                    {"role": "system", "content": "Reply with exactly: OK"},
                    {"role": "user", "content": "ping"},
                ],
                mcp_servers={},
            ):
                if not isinstance(ev, dict) or ev.get("type") != TYPE_RAW_RESPONSE_EVENT:
                    continue
                data = ev.get("data") or {}
                dtype = data.get("type")
                if dtype == DATA_TYPE_TEXT_DELTA and (data.get("delta") or ""):
                    got_text = True
                elif dtype == DATA_TYPE_ERROR:
                    # Keep type AND message: codex phrases auth failures as
                    # error_type="unauthorized" with a message that carries no
                    # credential marker on its own (same lesson as
                    # cli_helper's codex one-shot).
                    _etype = str(data.get("error_type") or "").strip()
                    _emsg = str(data.get("error_message") or "").strip()
                    err_msg = ": ".join(p for p in (_etype, _emsg) if p) or "codex error"
            if err_msg:
                return False, f"live verification failed: {err_msg[:200]}"
            if got_text:
                return True, "credentials verified — live codex CLI call succeeded"
            return False, "CLI run produced no reply — credentials may be invalid"

        token = _codex_ctx.set(self.build_codex_config(model))
        try:
            return await asyncio.wait_for(
                _one_shot(),
                timeout=_settings.helper_cli_total_timeout_seconds,
            )
        except asyncio.TimeoutError:
            return False, (
                "verification timed out after "
                f"{_settings.helper_cli_total_timeout_seconds}s"
            )
        except Exception as exc:  # noqa: BLE001 — verdict, not control flow
            summary = str(exc).splitlines()[0][:200] if str(exc) else type(exc).__name__
            return False, f"live verification failed: {summary}"
        finally:
            _codex_ctx.reset(token)

    async def probe(self) -> DriverHealth:
        """Check whether the host CLI credentials file actually exists.

        Existence is the CHEAP signal for the Settings page ("✓ Codex CLI
        linked" vs "✗ run `codex login`") — it is NOT health; the honest
        verdict is ``verify_live``. Like ClaudeOAuthDriver.
        """
        path = resolve_codex_credentials_path(self.card.auth_ref)
        if path is None:
            return DriverHealth(
                ok=False,
                detail="auth_ref is missing or not a codex-cli: reference",
            )
        if not path.exists():
            return DriverHealth(
                ok=False,
                detail=(
                    f"credentials file not found at {path}. "
                    f"Run `codex login` on the host to create it."
                ),
            )
        if not path.is_file():
            return DriverHealth(
                ok=False,
                detail=f"credentials path exists but is not a file: {path}",
            )
        return DriverHealth(ok=True, detail=f"credentials present at {path}")
