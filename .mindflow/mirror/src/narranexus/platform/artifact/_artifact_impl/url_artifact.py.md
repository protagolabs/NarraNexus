---
code_file: src/narranexus/platform/artifact/_artifact_impl/url_artifact.py
last_verified: 2026-10-30
stub: false
---

## 2026-10-30 — 收掉解析差异，并说清这个守卫管不到什么

`_origin_tuple` 先按浏览器规则去掉首尾 C0 和空格（`open_url` 存的也是这个值），再拒绝 urlparse 和浏览器可能读出不同 host 的写法：`?#` 之前含反斜杠（`https://ours\\@evil` 浏览器读成 ours）、authority 里 ≤0x20 的字符、任意位置的 tab/CR/LF（浏览器和 urlparse 都静默删）；路径里的空格和 query 里的反斜杠是合法的、
netloc 含 `%`（浏览器解码 host）、无 host（`https:/ours` urlparse 无 netloc，浏览器有 host）。host 过 IDNA 再比较，全角字母和全角句点折叠成浏览器看到的样子。
`_reject_self_origin` 对判不了的 URL 直接抛 ArtifactError，本地部署也一样；原来是放行给后面的 SSRF 闸门。

**它只判写下来的 URL。** 第三方页面仍可把 iframe 重定向到我们的源，那一步靠公共内容路由响应头里的 `Content-Security-Policy: sandbox`（见 [[public.py]]）。
**`_read_doc` 不是唯一闸门**：浏览器经 raw 路由直接读 `page.url.json`，不走这里；agent 能在创建后改这个文件，所以 [[UrlRenderer.tsx]] 每次渲染前重新检查。
`set_embed_mode` 也复查一次，防止 override 接口重写被改过的 doc，但它只有 `public_base_url` 可比，没有请求推出的 origin。

## 2026-10-30 — 新建 tab 先证明目录无链接再写；读写全部受限

`open_url` 原来 `os.makedirs` + 按串写：workspace 下的 `tabs` 若是指向别人目录的链接，doc 与 content.md 直接落到
受害者目录。现在先 `_make_tab_dir`（[[utils/fs/confined_io]] 的 `make_dirs`，从 base 起逐级 `O_NOFOLLOW`，链接 →
`ArtifactPathEscape`，在此之前什么都不写），再经 `write_confined_atomic` 写 doc 与 content.md。
`set_embed_mode` 的 `_read_doc`/`_write_doc` 同样走受限 fd（读 doc 和原子重写之间目录被换成链接 →
`ArtifactNotFound`，受害者的 doc 不被改写）。注意：`_write_doc` 不再自己建目录，目录必须已存在。

# url_artifact.py — create / update URL-tab artifacts (application/x-url)

## Why it exists

A URL tab is a first-class pointer artifact (NOT a parallel tab system — the
office-live merge taught us "two preview concepts confuse users"). Its entry
file is a small JSON doc (`UrlArtifactDoc`) at `tabs/<slug>/page.url.json` in
the agent workspace. This module owns the doc I/O + orchestration; the actual
registration goes through the shared [[registration.py]], so URL tabs get
heal / delete / bundle / raw-serving for free. No DB column for the URL — the
JSON doc is the source of truth (pointer model preserved).

## open_url flow

1. Reject a URL whose BROWSER-origin equals our own app's
   (`_reject_self_origin`) — a same-origin URL tab would become a same-origin
   scriptable iframe (the renderer's sandbox has `allow-same-origin`) able to
   read the app token. Comparison is browser-accurate (scheme + lowercased
   host + effective port; userinfo dropped) so `AGENT…`, `host:443`, `u@host`
   can't bypass it. Candidate origins: `settings.public_base_url` PLUS the
   `app_origin` the HTTP route derives from the request (so the guard holds
   even if the config is unset); the MCP path passes only public_base_url.
   Then SSRF-gate the URL ([[url_safety.py]]) — a non-public target raises
   ArtifactError(400): tab creation fails loudly.
2. Probe embeddability ([[embed_probe.py]]) — never crashes; a failed probe
   degrades to an optimistic iframe verdict.
3. Write the doc into a DEDICATED `tabs/<slug>/` subdir — each URL tab gets
   its own artifact root so the raw route's isolation means one tab can never
   read another's json.
4. Register through the shared pointer path with `kind=application/x-url`,
   `description=url` (so the URL shows in listings / the agent's state block).

## set_embed_mode

Rewrites the on-disk doc's `embed.user_override` (the user's manual toggle,
which wins over the probe). Bumps updated_at via `repo.update_title` so the
frontend's refetch sees a change. 404s if the artifact is missing / not this
agent's / not a URL tab; a missing doc file on disk (a real pointer-model
state) raises `ArtifactContentGone` (410), not a bare FileNotFoundError → 500.

2026-10-28（PR #421 复审时同类扫描补上）：doc 路径先经 [[confinement]] `confine_entry`，
越界 → `ArtifactNotFound`。原来是 `realpath(resolve_workspace_relative_file(...))` 后直接
读 + 原子重写——tab 目录被换成指向别的租户 tab 目录的链接时，覆盖的是对方的 doc。
测试 `test_set_embed_mode_never_rewrites_a_doc_behind_a_swapped_link`。

## Agent-readable content snapshot (2026-07-22)

The probe (embed verdict) and page-text capture are independent outbound
fetches — `open_url` runs them CONCURRENTLY (`asyncio.gather`) under one
`asyncio.timeout(_OPEN_FETCH_BUDGET_S)` wall-clock budget, so opening a tab
isn't the sum of both worst cases; on timeout it degrades to an optimistic
iframe verdict + no text. This budget is an outbound-HTTP deadline, NOT an
agent_loop ceiling (铁律 #14 untouched). The content filename is the schema
constant `URL_TAB_CONTENT_FILENAME` (not a private local), so the state-block
reader doesn't reach across the package seam.

`open_url` also writes `tabs/<slug>/content.md` next to the doc — a bounded
plain-text snapshot of the page ([[page_text.py]]) so the agent can SEE what
the page says. Written ALWAYS (even when extraction fails, with a "could not
capture" note) so the state-block hint in [[common_tools_module.py]] always
points at a real file. Best-effort: `fetch_page_text` never raises.

## Atomic doc write

`_write_doc` writes a sibling temp file then `os.replace` (atomic on POSIX)
so a crash mid-toggle can't leave a half-written doc that bricks the tab —
a reader always sees the old or the new complete doc.

## Gotcha

The doc is written to `registration.workspace_root(agent_id, user_id)` joined
with the relative entry — NOT to `base_working_path` directly. It MUST match
where `registration._resolve_entry` looks, or register fails "file not found".

## 2026-08-20 — open_url 增必填 `db` 形参(#334 r2 C3)

事件 staging 不再经 `repo.db`(逃生口已删,见 [[base.py]]):调用方
(service)显式传 db。照旧写 `repo.db` 会 AttributeError。
