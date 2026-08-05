---
code_file: src/xyz_agent_context/module/narramessenger_module/_narra_command_security.py
stub: false
last_verified: 2026-08-05
---

## 2026-08-05 — 删掉不可达的空命令分支（随 lark 侧同源清理）

`validate_command` 开头已有 `if not command or not command.strip(): return
"Empty command"`，因此 `command.strip()` 非空且已去空白；这种输入
`shlex.split` 不会返回 `[]`（`str.strip()` 的空白集合是 shlex 的
`' \t\r\n'` 的超集）。所以 `shlex.split` 之后那条
`if not tokens: return "Empty command"` 不可达，且与开头返回同一句话。

来源：lark 侧 `_lark_command_security.py` 修 payload-vs-控制面 guard 时删掉了
同一类死分支，review 指出孪生 guard 里一模一样（铁律 #8 顺手扫相邻代码）。

**本文件的匹配写法一直是对的**，是 lark 侧此次修复对齐的参考实现：先 shlex
分词，再 `lowered[:len(pat)] == pat` 前导 token 锚定 + flag 整 token 等值 ——
正文永远够不着控制面规则。

## Why it exists

The ``narra_cli`` MCP tool is a passthrough: it hands an arbitrary
command string to the ``narra-cli`` binary. This module is what makes
that safe. It mirrors ``lark_module/_lark_command_security.py`` but the
surface is far smaller (narra-cli has ~6 domains), so the whitelist is
short and hand-auditable.

## Design decisions

- **Whitelist by domain, not per-command wrapping.** ``ALLOWED_DOMAINS``
  gates the first token (``room`` / ``im`` / ``speech`` / ``explore`` /
  ``status`` / ``help``). New subcommands/flags under an allowed domain
  pass with zero code change — that is the whole durability point. A new
  *top-level domain* is the only CLI-growth event that needs a one-line
  whitelist add.
- **``BLOCKED_PATTERNS`` carve out what must not go through passthrough.**
  ``configure`` (endpoint is platform-global), ``doctor`` (probe surface),
  and — TRANSITIONALLY — ``im send``. ``im send`` is blocked because the
  send/media path stays on the Matrix-direct dedicated tools
  (``narra_reply`` / ``narra_send`` / ``narra_send_media``) until the
  proxy media path's moderation / compound / failure behaviour is
  validated on dev (owner decision 2026-07-20). ``im messages`` /
  ``im attachments`` are NOT matched by the ``im send`` prefix and remain
  allowed. Remove this block (and the dedicated send tools) once proxy
  send/media is verified.
- **``BLOCKED_FLAGS`` = ``--token`` / ``--token-file``.** The platform
  injects the bearer per call (see [[narra_cli_client]]); an agent
  supplying its own is either overriding our injection or probing for a
  readable path — always rejected.
- **``explore`` passes the whitelist; official-only is enforced
  SERVER-SIDE.** The runtime guide states a non-official agent gets an
  ``official-agent-required`` JSON error from the backend. We deliberately
  do NOT gate ``explore`` client-side: there is no reliable client-side
  signal of official status, and a client gate would only block everyone
  (and hide the informative backend error). An earlier ``is_official``
  param was removed 2026-07-20 for exactly this reason.
- **All checks run on the shlex-tokenized command (2026-07-20 review fix), not
  the raw string.** Matching blocked patterns on the raw string let ``im  send``
  (extra whitespace) slip past the ``im send`` block while the token-based domain
  check still saw ``im``. ``validate_command`` now ``shlex.split``s once and
  matches blocked patterns against **leading tokens** — whitespace-robust, and
  quotes are respected so whitespace inside a quoted arg survives.
- **Escape expansion is scoped to text-value flags (2026-07-20 review fix).**
  ``\n``/``\t``/``\r`` are expanded ONLY in the value of ``--text`` / ``--markdown``
  (``_TEXT_VALUE_FLAGS``) — never in paths (``--out``/``--output``/``--input``),
  search terms (``--keyword``), or ids, where a literal backslash sequence must
  survive. Was applied to every token, which would silently rewrite a path.
- **``shlex.split`` + ``shell=False`` argv is the real injection defense,
  NOT a shell-metachar denylist.** Under ``execve`` the metachars are
  literal; a denylist would only break legitimate content ("S&P 500",
  "$76,000", markdown tables) — the exact lesson already burned in on the
  Lark side. ``sanitize_command`` also expands ``\n``/``\t``/``\r`` so an
  LLM-written ``--text "a\nb"`` renders a real newline.

## Upstream / downstream

- **Called by**: ``_narramessenger_mcp_tools.narra_cli`` (validate →
  sanitize → run).
- Independent (binding rule #3): no cross-module imports; a shape-twin of
  the Lark security layer, not a shared one.

## Gotchas

- Ordering: ``BLOCKED_PATTERNS`` is checked before ``BLOCKED_FLAGS``, so a
  command that is both blocked-pattern and carries a token flag reports
  the pattern reason. Both reject — the reason string is the only
  difference.
- When the send tools are eventually removed, drop ``"im send"`` from
  ``BLOCKED_PATTERNS`` in the same change.
