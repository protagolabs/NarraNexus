"""
@file_name: xyz_codex_official_sdk.py
@author: NarraNexus
@date: 2026-06-04
@description: CodexSDKv2 — Codex CLI wrapper via OpenAI's OFFICIAL
``openai-codex`` Python SDK (JSON-RPC ``app-server`` mode, not
``exec`` mode like v1).

Differences from v1 (xyz_codex_cli_sdk.py)
------------------------------------------
* Subprocess lifecycle owned by SDK — we drop ~150 lines of stdout
  reader / race-with-cancel / SIGTERM-SIGKILL fallback.
* Wire format is structured pydantic Notifications, not JSON Lines.
* Reasoning summary STREAMS via ``ReasoningSummaryTextDeltaNotification`` —
  fixes the v1 "Thinking panel appears as one big block" UX. Matches
  DeepSeek/CC reasoning streaming.
* Cancellation is ``handle.interrupt()`` (server-side RPC), not
  subprocess termination. < 1s release vs v1's 1–5s.
* MCP configuration goes through ``CodexConfig.config_overrides``
  (a tuple of ``--config k=v`` strings), not a filesystem
  ``$CODEX_HOME/config.toml`` write. Filesystem is still used for
  ``instructions.md`` (system prompt) + OAuth ``auth.json`` staging.

What v1 functionality survives in v2
-------------------------------------
Imported directly from v1's module to avoid duplication while both
implementations coexist:

* ``_build_system_prompt_and_user_msg`` — message-list → system prompt
  + per-turn user message split (with source-aware history eviction).
* ``_stage_codex_oauth_credentials`` — copy host ``~/.codex/auth.json``
  into per-run CODEX_HOME tempdir.
* ``_sse_url_to_streamable_http`` — rewrite MCP URLs from the SSE
  form Claude Code uses to the streamable-HTTP form codex CLI wants.

Also reused unchanged:

* ``_codex_permission_translator.translate_tool_policy_to_codex_permissions``
  — NarraNexus CC-style tool policy → codex permissions dict.
* ``api_config.codex_config`` — per-call ContextVar carrying model
  / base_url / api_key / auth_type / auth_ref.

Registration
------------
This class registers itself in the ``agent_loop_driver`` registry as
both ``codex_cli_v2`` (the user-facing framework name written to
``user_slots.agent_framework``) and ``codex_official`` (short alias
for env override ``AGENT_LOOP_FRAMEWORK=codex_official``). v1's
``CodexSDK`` remains registered as ``codex_cli`` (default) and ``codex``
during the A/B coexistence period.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any, AsyncGenerator

from loguru import logger

from xyz_agent_context.utils.deployment_mode import (
    DeploymentMode,
    get_deployment_mode,
)
from xyz_agent_context.utils.logging import timed

# Per-call config + helpers reused from v1. The cross-file import is
# intentional and avoids duplicating ~150 lines of system-prompt /
# OAuth-staging / URL-rewrite logic that's identical for v1 and v2.
# When v1 is deleted post-cutover (Phase 3 of the design doc), these
# helpers move to a shared ``_codex_common.py`` — but for the
# coexistence period the direct import keeps the diff small.
try:
    from .api_config import codex_config
    from ._codex_permission_translator import (
        translate_tool_policy_to_codex_permissions,
    )
    from .xyz_codex_cli_sdk import (
        _build_system_prompt_and_user_msg,
        _stage_codex_oauth_credentials,
        _sse_url_to_streamable_http,
    )
    from .output_transfer import output_transfer
except ImportError:  # absolute-import fallback for script-style runs
    from api_config import codex_config  # type: ignore[no-redef]
    from _codex_permission_translator import (  # type: ignore[no-redef]
        translate_tool_policy_to_codex_permissions,
    )
    from xyz_codex_cli_sdk import (  # type: ignore[no-redef]
        _build_system_prompt_and_user_msg,
        _stage_codex_oauth_credentials,
        _sse_url_to_streamable_http,
    )
    from output_transfer import output_transfer  # type: ignore[no-redef]


# ---------------- Sandbox / approval string constants ----------------
#
# The SDK exposes ``Sandbox`` and ``ApprovalMode`` as enums, but we
# carry the raw string values here so the file imports cleanly even
# when the SDK isn't installed (the actual usage is guarded by the
# SDK import inside agent_loop). Strings MUST stay in sync with the
# enum values exported by ``openai_codex.Sandbox`` /
# ``openai_codex.ApprovalMode``. See the spike output captured in
# ``.mindflow/project/specs/2026-06-04-codex-sdk-v2-design.md``.
_SANDBOX_DANGER_FULL_ACCESS = "danger-full-access"  # OS sandbox OFF
_SANDBOX_WORKSPACE_WRITE = "workspace-write"          # OS sandbox: workspace only
_SANDBOX_READ_ONLY = "read-only"
_REASONING_SUMMARY_DETAILED = "detailed"  # surfaces reasoning to UI

# config_overrides uses codex's internal string names (matching the
# ``--sandbox`` CLI flag); ``thread_start(sandbox=)`` takes the SDK's
# ``Sandbox`` enum, which drops the "danger" prefix and uses snake_case.
# This maps one to the other.
_SANDBOX_ENUM_ATTR: dict[str, str] = {
    _SANDBOX_DANGER_FULL_ACCESS: "full_access",
    _SANDBOX_WORKSPACE_WRITE: "workspace_write",
    _SANDBOX_READ_ONLY: "read_only",
}


def _resolve_sandbox_mode() -> str:
    """Sandbox mode for codex runs.

    Default is deployment-mode aware (an explicit ``CODEX_SANDBOX_MODE`` env
    var overrides both):

    - **cloud → ``workspace-write``**: confine codex to its per-agent
      workspace at the OS level (Seatbelt / bubblewrap). Reads/writes
      outside the workspace and network are blocked by the kernel — the
      multi-tenant isolation PR #25 review §1/§2 requires. Verified
      2026-06-12: v2 app-server mode auto-approves MCP tool calls (an
      ``item/autoApprovalReview`` step), so codex issue #16685 (MCP calls
      auto-cancelled under a non-full sandbox, an old ``exec``-mode bug)
      does NOT bite here — MCP tools complete normally under workspace-write.
    - **local → ``danger-full-access``**: the user's own machine, no
      multi-tenant boundary to enforce. Mirrors ``_tool_policy_guard``,
      which only enforces workspace containment in cloud.

    Override with ``CODEX_SANDBOX_MODE=workspace-write|danger-full-access|read-only``.
    """
    raw = (os.environ.get("CODEX_SANDBOX_MODE") or "").strip().lower()
    if raw in _SANDBOX_ENUM_ATTR:
        return raw

    # No (valid) override → pick by deployment mode.
    try:
        from xyz_agent_context.utils.deployment_mode import get_deployment_mode
        is_cloud = get_deployment_mode() == "cloud"
    except Exception:  # noqa: BLE001 — a mode-lookup failure must not break the run
        is_cloud = False
    default = _SANDBOX_WORKSPACE_WRITE if is_cloud else _SANDBOX_DANGER_FULL_ACCESS

    if raw:
        logger.warning(
            f"[CodexSDKv2] unknown CODEX_SANDBOX_MODE={raw!r}; "
            f"valid: {sorted(_SANDBOX_ENUM_ATTR)}; using {default}"
        )
    return default

# Custom model-provider name used in config_overrides when the agent slot
# authenticates with an API key (not OAuth). Mirrors v1's
# ``_codex_config_toml_builder._CUSTOM_PROVIDER_NAME``.
_CODEX_CUSTOM_PROVIDER_NAME = "narranexus"
# codex's built-in OpenAI provider endpoint — the default we point the
# custom provider at when the slot didn't specify a base_url.
_DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"

# Required for ItemCompletedNotification.item discriminator matching
# in the notification translator. The actual enum lives in
# ``openai_codex.generated.v2_all.ThreadItem`` as a RootModel union;
# we don't need to import it here because translation matches on the
# ``type`` field of the model-dumped dict.


# =========================================================================
# Helpers
# =========================================================================


def _build_codex_config_overrides(
    *,
    instructions_path: Path,
    mcp_server_urls: dict[str, str],
    permissions: dict | None,
    writable_roots: list[Path] | None = None,
    sandbox_mode: str = _SANDBOX_DANGER_FULL_ACCESS,
    reasoning_summary: str = _REASONING_SUMMARY_DETAILED,
    model: str | None = None,
    api_key: str = "",
    base_url: str = "",
    auth_type: str = "oauth",
) -> tuple[str, ...]:
    """Assemble the ``CodexConfig.config_overrides`` tuple.

    The SDK forwards each entry as a ``--config k=v`` flag to the
    codex binary at launch. Values must be valid TOML literals — strings
    need surrounding double quotes; arrays need brackets; nested table
    keys use dotted paths.

    Replaces v1's ``_codex_config_toml_builder.build_codex_config_toml``
    file-on-disk approach with an in-memory tuple. Same logical content,
    different surface.

    Args:
        instructions_path: Path the codex binary should read system
            prompt + history from. Becomes ``model_instructions_file``.
        mcp_server_urls: ``{name: url}`` map. Each entry becomes one
            ``mcp_servers.<name>.url=...`` override. URLs are SSE-shaped
            (``http://host:port/sse``); we rewrite to the streamable-HTTP
            shape codex CLI's MCP client requires.
        permissions: Output of
            ``translate_tool_policy_to_codex_permissions``. Flattened
            into dotted-path ``permissions.<category>."<rule>"=...``
            entries. Glob characters in rule keys MUST be quoted so
            codex's TOML parser treats them as string keys, not
            wildcards.
        writable_roots: Extra writable directories beyond the cwd.
            Emitted as ``sandbox_workspace_write.writable_roots=[...]``.
            Largely cosmetic under ``danger-full-access`` (which doesn't
            enforce a writable set), kept for documentation parity with
            v1's config.toml and easy downgrade if codex issue #16685
            ever ships a real fix.
        sandbox_mode: ``"read-only" | "workspace-write" |
            "danger-full-access"``. Defaults to ``danger-full-access``
            because MCP tool calls auto-cancel under the other two
            in exec-style mode (issue #16685). The app-server mode v2
            uses may not have this bug, but we don't have evidence yet
            — staying on danger-full-access keeps the v1 workaround.
        reasoning_summary: ``"none" | "concise" | "detailed" | "auto"``.
            Default ``detailed`` because that's what surfaces a useful
            narrative paragraph in the Thinking panel — verified
            against codex CLI 0.137 on 2026-06-04.
        model: Optional model name override. ``None`` leaves the codex
            binary's default in place (which is what the user's
            ``codex login`` selected).
        api_key: The agent slot's API key (empty for OAuth). When set with
            ``auth_type="api_key"`` we declare a custom model provider so
            codex knows to authenticate with it — see below.
        base_url: Custom OpenAI-compatible endpoint. Empty → official
            OpenAI (``https://api.openai.com/v1``).
        auth_type: ``"oauth"`` | ``"api_key"``. For OAuth, codex reads the
            staged ``$CODEX_HOME/auth.json`` and no provider block is
            needed. For api_key we MUST declare a ``model_providers``
            block — see below.

    API-key auth (incident 2026-06-11):
        codex's built-in ``openai`` provider authenticates via OAuth
        (auth.json) by default; it does NOT read ``CODEX_API_KEY``. So an
        API-key slot that only sets the env var hits the Responses API
        with no auth header → ``401 Missing bearer``. We must declare a
        custom provider whose ``env_key`` points codex at ``CODEX_API_KEY``
        and set ``model_provider`` to it — exactly what v1's config.toml
        builder did and the v2 cutover dropped. ``wire_api="responses"``
        keeps the reasoning-capable Responses surface.

    Returns:
        Tuple of TOML-literal ``key=value`` strings.
    """
    overrides: list[str] = [
        f'model_instructions_file="{instructions_path}"',
        f'sandbox_mode="{sandbox_mode}"',
        f'model_reasoning_summary="{reasoning_summary}"',
    ]
    if model:
        overrides.append(f'model="{model}"')

    # API-key auth: declare the custom provider + env_key so codex reads
    # the key from CODEX_API_KEY (which CodexConfig.to_cli_env sets). OAuth
    # needs none of this — it uses the staged auth.json.
    if auth_type == "api_key" and api_key:
        provider_base_url = base_url or _DEFAULT_OPENAI_BASE_URL
        p = _CODEX_CUSTOM_PROVIDER_NAME
        overrides.extend([
            f'model_provider="{p}"',
            f'model_providers.{p}.name="NarraNexus-configured provider"',
            f'model_providers.{p}.base_url="{provider_base_url}"',
            f'model_providers.{p}.env_key="CODEX_API_KEY"',
            f'model_providers.{p}.wire_api="responses"',
        ])

    # MCP servers — one mcp_servers.<name>.url entry per server.
    for name in sorted(mcp_server_urls.keys()):
        url = mcp_server_urls[name]
        stream_url = _sse_url_to_streamable_http(url)
        overrides.append(f'mcp_servers.{name}.url="{stream_url}"')

    # Permissions — flatten translator output. The translator returns
    # a dict like {"filesystem": {"/etc/**": "deny", ...},
    #              "commands": {"brew install *": "deny", ...},
    #              "tools": {"WebSearch": "deny"}}.
    # Keys with shell-meta characters MUST be quoted on the LHS so
    # codex's --config TOML parser doesn't try to expand them.
    if permissions:
        for category, rules in permissions.items():
            if not isinstance(rules, dict):
                continue
            for rule_key, rule_value in rules.items():
                # Quote the rule key as a TOML string literal — handles
                # glob chars (* ? [] etc.) and spaces (which the
                # translator emits for things like "brew install *").
                overrides.append(
                    f'permissions.{category}."{rule_key}"="{rule_value}"'
                )

    if writable_roots:
        roots_repr = ", ".join(f'"{p}"' for p in writable_roots)
        overrides.append(
            f"sandbox_workspace_write.writable_roots=[{roots_repr}]"
        )

    return tuple(overrides)


# NOTE: An earlier draft of this file shipped an ``_aiter_stream``
# wrapper that ran ``next(stream, SENTINEL)`` through
# ``asyncio.to_thread`` — built on the (wrong) assumption that
# ``AsyncTurnHandle.stream()`` returned a sync iterator. It does not.
# ``stream()`` is an **async generator** (``isasyncgenfn=True``) so we
# can iterate it with ``async for`` directly with no thread bridge.
# Removed to avoid the double-await/thread overhead and to make the
# control flow obvious. If a future SDK version reverts to a sync
# stream, restore the bridge — but check ``inspect.isasyncgenfunction``
# first, don't assume.


# =========================================================================
# CodexSDKv2 — the AgentLoopDriver implementation
# =========================================================================


class CodexSDKv2:
    """Codex CLI wrapper via the OFFICIAL ``openai-codex`` Python SDK.

    Same async-generator contract as v1 ``CodexSDK`` and
    ``ClaudeAgentSDK`` — conforms to ``agent_loop_driver.AgentLoopDriver``
    Protocol via structural typing.
    """

    def __init__(self, working_path: str | Path = "./"):
        self.working_path = str(working_path)

    @timed("llm.codex_v2.agent_loop", slow_threshold_ms=15000)
    async def agent_loop(
        self,
        messages: list[dict[str, Any]],
        mcp_server_urls: dict[str, str],
        streaming: bool = True,
        extra_env: dict[str, str] | None = None,
        cancellation: Any | None = None,
        **kwargs: Any,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Drive one full Codex agent turn via the official SDK.

        Signature mirrors :meth:`xyz_codex_cli_sdk.CodexSDK.agent_loop`
        and :meth:`xyz_claude_agent_sdk.ClaudeAgentSDK.agent_loop`
        (the Protocol contract). ``**kwargs`` swallows wrapper-specific
        args (hooks etc.) that don't apply here.
        """
        del kwargs  # signature compatibility — discarded

        # Import the SDK inside the method so a missing install doesn't
        # break the module-level import (which would also break v1 by
        # association — they share __init__.py registration).
        try:
            from openai_codex import (  # noqa: PLC0415 — see comment above
                AsyncCodex,
                CodexConfig,
                Sandbox,
                TextInput,
            )
        except ImportError as e:
            raise RuntimeError(
                "Official openai-codex SDK is not installed. "
                "Install via ``uv pip install openai-codex`` and ensure "
                "``from openai_codex import Codex`` works before "
                "triggering an agent that uses the codex_cli_v2 "
                "framework."
            ) from e

        # ---- Step 1: split system prompt + per-turn user message ----
        system_prompt, user_message = _build_system_prompt_and_user_msg(
            messages
        )

        mode: DeploymentMode = get_deployment_mode()
        is_cloud = mode == "cloud"

        # ---- Step 2: per-run CODEX_HOME tempdir ----
        # Tempfile context manager guarantees cleanup even on
        # CancelledError / unexpected exception.
        with tempfile.TemporaryDirectory(prefix="codex_v2_agent_") as home_str:
            codex_home_path = Path(home_str)

            # 2a. instructions.md (system prompt + history). The SDK
            # has a ``base_instructions`` kwarg on ``thread_start`` but
            # it's practically constrained to small strings (argv).
            # We use ``model_instructions_file`` in config_overrides
            # so very large prompts (70k+ chars common in NarraNexus)
            # don't blow argv limits.
            instructions_path = codex_home_path / "instructions.md"
            instructions_path.write_text(system_prompt, encoding="utf-8")

            # INFO proof-of-wiring: fingerprint the prompt so the
            # backend log shows exactly what codex will read.
            _sp_head = system_prompt[:160].replace("\n", " ⏎ ")
            _sp_tail = system_prompt[-160:].replace("\n", " ⏎ ")
            logger.info(
                f"[CodexSDKv2] system prompt → {instructions_path} "
                f"({len(system_prompt):,} chars)"
            )
            logger.info(f"[CodexSDKv2]   head: {_sp_head!r}")
            logger.info(f"[CodexSDKv2]   tail: {_sp_tail!r}")

            _stage_codex_oauth_credentials(codex_home_path)

            # ---- Step 3: build permissions + config_overrides ----
            permissions = translate_tool_policy_to_codex_permissions(
                workspace=self.working_path,
                supports_server_tools=False,
                cloud_mode=is_cloud,
            )

            sandbox_mode = _resolve_sandbox_mode()
            config_overrides = _build_codex_config_overrides(
                instructions_path=instructions_path,
                mcp_server_urls=mcp_server_urls,
                permissions=permissions,
                writable_roots=[Path(self.working_path)],
                sandbox_mode=sandbox_mode,
                reasoning_summary=_REASONING_SUMMARY_DETAILED,
                model=codex_config.model or None,
                api_key=codex_config.api_key,
                base_url=codex_config.base_url,
                auth_type=codex_config.auth_type,
            )
            logger.info(
                f"[CodexSDKv2] sandbox_mode={sandbox_mode} "
                f"(workspace={self.working_path}) — set CODEX_SANDBOX_MODE to override"
            )

            _mcp_lines = [
                f"  - {name}: {url}"
                for name, url in mcp_server_urls.items()
            ] or ["  (no MCP servers configured)"]
            logger.info(
                f"[CodexSDKv2] config_overrides → {len(config_overrides)} "
                f"entries, MCP servers:\n" + "\n".join(_mcp_lines)
            )

            # ---- Step 4: env vars (CODEX_HOME + NO_PROXY + api_key) ----
            env: dict[str, str] = {**os.environ}
            env["CODEX_HOME"] = str(codex_home_path)
            # MCP servers run locally — must NOT route through HTTPS_PROXY.
            no_proxy_hosts = "localhost,127.0.0.1"
            env["NO_PROXY"] = no_proxy_hosts
            env["no_proxy"] = no_proxy_hosts
            # CODEX_API_KEY + any provider-specific env vars from
            # CodexConfig.to_cli_env (carries the resolver's choice).
            for k, v in codex_config.to_cli_env().items():
                env[k] = v
            if extra_env:
                env.update(extra_env)

            # ---- Step 5: construct SDK client + start thread ----
            sdk_config = CodexConfig(
                env=env,
                cwd=str(self.working_path),
                config_overrides=config_overrides,
            )

            codex = AsyncCodex(sdk_config)

            # Note the two-layer naming mismatch we deliberately preserve:
            #   * ``config_overrides`` sets ``sandbox_mode="danger-full-access"``
            #     — that's codex's *internal* config value name (matches the
            #     CLI flag ``--sandbox danger-full-access``).
            #   * ``thread_start(sandbox=...)`` takes the SDK's ``Sandbox``
            #     enum, where the same mode is named ``full_access``
            #     (the SDK dropped the "danger" prefix from the enum but
            #     the underlying mode is identical).
            # Both must be set: config_overrides locks it in the persisted
            # config; the kwarg is the per-thread guarantee.
            #
            # NOTE: ``skip_git_repo_check`` is NOT a thread_start kwarg
            # (only a v1 ``codex exec`` CLI flag). The SDK's app-server
            # mode handles non-git cwds via ``sandbox_mode`` instead —
            # ``danger-full-access`` bypasses the git-repo guard. If a
            # future SDK reintroduces the check, route the equivalent
            # CLI flag through ``CodexConfig.launch_args_override``
            # rather than re-adding a thread_start kwarg.
            #
            # Both ``thread_start`` and ``thread.turn`` are coroutines
            # in 0.1.0b3 — directly ``await``. The contract test
            # ``test_thread_start_accepts_kwargs_we_actually_pass``
            # locks in the kwarg surface; the parallel
            # ``test_turn_is_coroutine_function`` locks in the
            # async-ness, so an SDK that flips ``thread.turn`` back
            # to sync would fail at CI not at user-turn time.
            _sandbox_attr = _SANDBOX_ENUM_ATTR.get(sandbox_mode, "full_access")
            _sandbox_enum = getattr(Sandbox, _sandbox_attr, None)
            if _sandbox_enum is None:
                # SDK renamed the enum member — don't crash opaquely; log the
                # real member names (so a workspace-write test tells us the
                # right attr) and fall back to full_access (current behavior).
                _members = [m for m in dir(Sandbox) if not m.startswith("_")]
                logger.warning(
                    f"[CodexSDKv2] Sandbox has no {_sandbox_attr!r} "
                    f"(available: {_members}); falling back to full_access"
                )
                _sandbox_enum = Sandbox.full_access
            thread = await codex.thread_start(sandbox=_sandbox_enum)
            logger.info(
                f"[CodexSDKv2] thread started "
                f"(cwd={self.working_path}, CODEX_HOME={codex_home_path})"
            )

            # ---- Step 6: start the turn, stream notifications ----
            handle = await thread.turn(TextInput(text=user_message))

            # ``handle.stream()`` is an async generator — iterate it
            # directly. ``handle.interrupt()`` is a coroutine — await
            # it. Both were wrapped in ``asyncio.to_thread`` in an
            # earlier draft; that was wrong and would silently
            # deadlock or raise depending on Python version.
            stream = handle.stream()

            # Pump notifications through the translator and yield.
            # Cancellation check BEFORE each yield so a Stop interrupts
            # before the next event reaches the response_processor.
            event_count = 0
            try:
                async for notification in stream:
                    event_count += 1
                    if cancellation is not None and getattr(
                        cancellation, "is_set", lambda: False
                    )():
                        logger.info(
                            f"[CodexSDKv2] cancellation set after "
                            f"{event_count} events — invoking interrupt"
                        )
                        await handle.interrupt()
                        break

                    # The notification is a pydantic model; serialize
                    # to dict so the translator's existing dict-shaped
                    # contract holds. ``by_alias=False`` keeps
                    # snake_case field names (we don't want camelCase
                    # leaking into our internal events).
                    dump = (
                        notification.model_dump(mode="json", by_alias=False)
                        if hasattr(notification, "model_dump")
                        else dict(notification)
                        if isinstance(notification, dict)
                        else {"raw": repr(notification)}
                    )
                    # Stash the notification ``method`` (RPC routing key
                    # like ``turn/itemCompleted``) at top level for the
                    # translator's dispatch — pydantic's model_dump on
                    # ``Notification`` dataclass already includes
                    # ``method`` and ``payload``, but be defensive.
                    if "method" not in dump and hasattr(notification, "method"):
                        dump["method"] = notification.method
                    if "payload" not in dump and hasattr(notification, "payload"):
                        # If payload is a pydantic model, expand it.
                        pl = notification.payload
                        dump["payload"] = (
                            pl.model_dump(mode="json", by_alias=False)
                            if hasattr(pl, "model_dump")
                            else pl
                        )

                    # DIAGNOSTIC (2026-06-11): turns end with
                    # reasoning_chars=0 + no_reply and only ~5 notifications
                    # — none of which translate to text/reasoning/tool.
                    # Dump every raw notification (method + truncated
                    # payload) so we can see exactly what codex emits and
                    # why nothing maps. INFO-level, self-contained; remove
                    # once the turn-empty root cause is found. (Mirror doc
                    # notes v1 had the same ``[CodexSDK][raw]`` diagnostic.)
                    _raw_pl = repr(dump.get("payload"))
                    if len(_raw_pl) > 1000:
                        _raw_pl = _raw_pl[:1000] + "…<truncated>"
                    logger.info(
                        f"[CodexSDKv2][raw] #{event_count} "
                        f"method={dump.get('method')!r} payload={_raw_pl}"
                    )

                    for translated in output_transfer(
                        dump,
                        transfer_type="codex_official",
                        streaming=streaming,
                    ):
                        yield translated
            finally:
                logger.info(
                    f"[CodexSDKv2] stream ended after {event_count} "
                    f"notifications"
                )
