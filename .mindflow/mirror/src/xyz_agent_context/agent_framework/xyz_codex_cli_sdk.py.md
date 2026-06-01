---
code_file: src/xyz_agent_context/agent_framework/xyz_codex_cli_sdk.py
stub: false
last_verified: 2026-05-31
---

## Why it exists

OpenAI Codex CLI wrapper — the second coding-agent SDK NarraNexus
supports, alongside Claude Code. Same async-generator contract as
``ClaudeAgentSDK`` so ``step_3_agent_loop`` can swap one for the
other based on the per-user ``user_slots.agent_framework`` choice
without any caller change.

Conceptual position: where ``ClaudeAgentSDK`` is a wrapper around
the official ``claude_agent_sdk`` Python SDK (which itself wraps
``claude`` CLI subprocess), this file wraps ``codex exec --json``
directly because **no official Codex Python SDK exists**. All the
heavy lifting (subprocess lifecycle, JSON Lines parsing,
cancellation race, SIGTERM/SIGKILL fallback) lives here.

## Design decisions

- **Spawn ``codex exec --json -`` directly.** No Python SDK to wrap.
  The ``-`` argument makes Codex read the prompt from stdin; we
  write the per-turn user message and close stdin to signal EOF
  (without the close, Codex blocks forever).
- **Per-run ``$CODEX_HOME`` temp directory.** Every call writes a
  fresh ``config.toml`` + ``instructions.md`` into a fresh
  ``tempfile.TemporaryDirectory``. With ``--ignore-user-config``
  the user's ``~/.codex/config.toml`` is bypassed entirely —
  behaviour is deterministic across hosts regardless of what the
  user has globally configured. OAuth is the exception: when
  `CodexConfig.auth_ref` points at a host `codex login` auth file,
  the wrapper copies that file into the temp `CODEX_HOME` as
  `auth.json` before spawning the subprocess.
- **MCP via file, not dict.** Codex requires ``[mcp_servers.<name>]``
  TOML tables; we generate them in
  ``_codex_config_toml_builder.build_codex_config_toml``. Adding /
  removing MCP servers between turns is fine — each turn writes a
  fresh config.toml.
- **System prompt via file, not argv.** Codex reads
  ``model_instructions_file`` from config.toml, so there is no
  argv-byte ceiling like CC's 128 KiB limit. The source-aware
  history-eviction logic from CC is preserved (token budget still
  matters) but the byte-cap belt-and-braces is dropped.
- **Race-with-cancel JSON Lines receive loop.** Exact same pattern
  as ``xyz_claude_agent_sdk.py`` lines 393-451: wait on the next
  stdout line and the cancellation token simultaneously,
  ``return_when=FIRST_COMPLETED``. Sub-100ms cancellation latency
  even during a long-running tool call.
- **SIGTERM → 5s grace → SIGKILL fallback** on cleanup. Mirror CC
  wrapper. Codex CLI doesn't document its SIGTERM behaviour
  precisely; in practice it exits cleanly within ~1s, but the
  SIGKILL is the safety net.
- **Event translation via ``output_transfer(transfer_type="codex_cli")``**.
  The translation table is in ``output_transfer.py``; this file
  just yields the post-translation events directly to step_3.
  Tool-call dedupe (``seen_tool_call_ids``) mirrors CC's pattern
  for the started/completed event pair.
- **Tool-policy via config.toml ``[permissions]``, not per-call
  hook.** Codex has no PreToolUse hook API. The
  ``_codex_permission_translator`` module renders the CC tool-policy
  rules into Codex's declarative TOML form. Some dynamic checks (e.g.
  ``Path.resolve(strict=False)`` symlink-escape detection from CC's
  ``_tool_policy_guard.py``) cannot be transcribed; the
  ``--sandbox workspace-write`` flag fills the gap at the syscall
  layer.

## Upstream / downstream

- **Upstream**: ``agent_runtime._agent_runtime_steps.step_3_agent_loop``
  (via the ``_resolve_agent_framework_sdk`` dispatcher).
- **Downstream**:
  - ``_codex_config_toml_builder.build_codex_config_toml`` — writes
    config.toml content.
  - ``_codex_permission_translator.translate_tool_policy_to_codex_permissions``
    — translates CC tool-policy to Codex permissions.
  - ``output_transfer.output_transfer(transfer_type="codex_cli")``
    — translates each Codex JSON event to the
    OpenAI-Agents-SDK-style event shape ``response_processor``
    expects.
  - ``api_config.codex_config`` — reads ``model``, ``base_url``,
    ``api_key`` from the current task's ContextVar (set by the
    per-user resolver before step_3 fires).

## Gotchas

- **``codex`` binary must be on PATH.** ``shutil.which("codex")``
  gate at the top of ``agent_loop`` raises an actionable error
  message pointing at ``npm install -g @openai/codex``. We could
  bundle a default-PATH probe but the error message is the
  preferred surface — installing Codex is the user's responsibility.
- **Auth fallback uses staged `auth.json` inside temp `CODEX_HOME`**.
  Codex itself reads `$CODEX_HOME/auth.json` when `CODEX_API_KEY` is
  empty. Because NarraNexus uses a temp `CODEX_HOME` per run, the
  wrapper must copy the host `codex login` file there first; otherwise
  a logged-in host would still look unauthenticated to the subprocess.
- **JSON Lines might NOT be one-per-line if Codex flushes
  mid-line.** We parse each ``readline()`` result independently and
  drop non-JSON lines with a DEBUG log. Codex's ``--json`` does
  appear to flush per event in practice, but the defensive parse
  costs nothing.
- **Cancellation behaviour during ``subprocess.communicate``** —
  we do NOT use ``communicate()`` because it blocks until EOF.
  Instead each ``readline()`` is wrapped in a task that we can
  race against the cancellation token. Without this pattern, a
  user Stop while Codex is executing a long Bash command would
  not be acted on until Codex finished.
- **``--sandbox workspace-write`` is the syscall-level guard**
  for filesystem operations outside the workspace. Even if the
  ``[permissions]`` glob misses a path (e.g. a symlink resolving
  outside the workspace), this kernel-level gate catches it. Do
  NOT change this default without re-validating the sandbox
  envelope.
- **No Python SDK = no monkey-patching seams.** The CC wrapper
  monkey-patches ``claude_agent_sdk._internal.message_parser`` to
  tolerate unknown message types. Codex's CLI is the wire format,
  so any "unknown type" survives as raw JSON we either translate
  or drop with a DEBUG log — no patch needed.
- **Tool policy guard semantic loss.** The CC ``_tool_policy_guard.py``
  uses Python ``re.compile`` patterns with shell-style anchoring
  (``(?:^|[\\s;&|`$(])``); Codex globs cannot replicate that. The
  translation in ``_codex_permission_translator`` covers the common
  cases (``brew install *`` / ``sudo *`` / etc.) but misses
  obscure shell pipelines. If you observe agents bypassing the
  guard via ``echo brew install x | bash`` or similar, escalate by
  tightening ``--sandbox`` mode or adding more deny patterns.
