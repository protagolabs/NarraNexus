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
provides via the OAuth credential. The agent slot is also handled
specially: Codex doesn't fit the ``build_claude_config`` shape, so
:meth:`build_claude_config` raises NotImplementedError. The Step 3
agent-loop dispatcher reads ``user_slots.agent_framework`` directly
to pick the SDK class instead of relying on the driver's
``build_*_config`` methods for the Codex case.
"""
from __future__ import annotations

from xyz_agent_context.agent_framework.provider_driver.base import (
    DriverHealth,
    _DriverBase,
)
from xyz_agent_context.agent_framework.provider_driver.derive import (
    resolve_codex_credentials_path,
)
from xyz_agent_context.agent_framework.provider_driver.registry import register


@register
class CodexOAuthDriver(_DriverBase):
    """OpenAI Codex CLI OAuth provider — token lives in the host CLI."""

    @classmethod
    def driver_type(cls) -> str:
        return "codex_oauth"

    # No build_claude_config / build_openai_config / build_embedding_config
    # overrides — the _DriverBase defaults all raise NotImplementedError,
    # which is the correct contract: Codex doesn't fit any of these three
    # config shapes. Step 3 reads user_slots.agent_framework to dispatch
    # to CodexSDK instead.

    async def probe(self) -> DriverHealth:
        """Check whether the host CLI credentials file actually exists.

        Like ClaudeOAuthDriver — existence is a sufficient signal for
        the Settings page to show "✓ Codex CLI linked" vs "✗ run
        `codex login`".
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
