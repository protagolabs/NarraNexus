---
code_file: src/narranexus/platform/artifact/_artifact_impl/confinement.py
stub: false
last_verified: 2026-10-30
---

## 2026-10-30 — import 路径跟随 `utils/fs/` 分组

`file_safety` / `fs_safety` / `workspace_paths` 搬入 `narranexus.platform.utils.fs`（见 [[utils/fs/__init__]]），本文件只改了 import 路径，行为与上文意图不变。

## 2026-10-30（复审）— 度量失败有自己的错误

`confined_dir_size`：过深 / 不可读（`ConfinedScanError`）→ `ArtifactTooLarge("artifact directory cannot be measured, so it is refused: …")`（413），**不再**报成 `ArtifactPathEscape`；
只有链接 / 越界才是 `ArtifactPathEscape`。

## 2026-10-30（复审）— `split_under_base` 改为词法，新增 `confined_stat`

`split_under_base(abs, base)` 现在只做词法归一（`abspath`，绝不 `realpath` 租户尾段），并同时接受 base 的真实路径与配置拼写
（相对路径或符号链接的 `BASE_WORKING_PATH`，本地 `run.sh` / DMG 常见）；委托给 [[utils/fs/confined_io]] 的同名函数。
**不要**在这里对 entry 做 realpath 再去打开——realpath 会在"较晚的时刻"穿过租户已放好的链接，再 NOFOLLOW 打开就成了
对受害者树的合法访问。`confined_stat(abs)` 给出受限 fd 上的 fstat（heal 候选、office 版本轮询用），由 artifact 包公共口导出。

## 2026-10-30 — 检查与打开合为一个操作（`*_confined` 一族）

`confine_entry` 只是**检查**：返回 realpath 之后，调用方若再按字符串重新打开，检查与打开之间路径上的
目录可以被换成指向别的租户的链接（租户的沙箱能在自己的 workspace 里建 symlink），打开就落到别人的树里。
文件末尾新增的 `open_confined_read` / `sha256_of_confined` / `confined_file_size` / `write_confined_atomic` /
`confined_dir_size` / `confined_path_exists` 把 I/O 本身交给 [[utils/fs/confined_io]]：从 `realpath(base_working_path)`
起，其下每一级都是 `O_NOFOLLOW` + dir_fd 逐级打开，任何时刻出现的链接都被拒绝而不是跟随。
`split_under_base(abs, base)` 负责把 confine_entry 给出的绝对路径还原成 (base, rel)；不在 base 下 → `ArtifactPathEscape`。
`ConfinedPathError`（含链接、平台不支持 dir_fd）统一映射成 `ArtifactPathEscape`；缺失/非常规文件仍是普通
`OSError`，由调用方映射 410。**不要**在需要读写 entry 的地方再 `open(abs_entry)`——那正是被修掉的竞态。

# artifact/_artifact_impl/confinement.py — 把 artifact 的 file_path 钉在属主的根下

## 为什么存在

`instance_artifacts.file_path` 是用户可影响的值：bundle 导入写进来、LLM 工具参数写进来、
老布局的行也在。2026-10-28 审计发现 raw 读（[[raw_access]]）只检查"在 base_working_path
之下"，而 base 是**全体租户的根**；编辑写（[[user_edit]]）和 office 资产上传
（[[artifacts]]）干脆不限定。一条 `../<victim>/<agent>/x` 的指针三处都当成合法。

## 口径

- `owner_roots(agent_id, user_id, base, team_id)`：该 agent 的 workspace（`workspace_candidate_relpaths`
  ——当前 nested 布局 + 仍有真实数据的两种 legacy flat 目录名），有 `team_id` 时再加该
  team 的共享目录。**不含用户整根**：同一用户的兄弟 agent 目录也不行（raw 路由只凭
  view token，不过 JWT，放进去等于把兄弟 agent 的 `.env`/skill 密钥交出去）。
- 根是**字面路径**（`normpath`），绝不 `realpath`：这些目录都在租户自己的挂载里，租户能把
  workspace 目录换成指向别人目录的链接；对根做 realpath 等于把链接目标当成"自己的根"。
  entry 则做 realpath，链接一旦出现就落到根外、fail closed；根本身是链接也直接拒绝。
- `confine_entry(...)`：用 `resolve_workspace_relative_file` 做布局兼容解析 → realpath →
  必须落在某个根内，否则 `ArtifactPathEscape`；调用方映射成自己的 404/410。
- 公共入口：`ArtifactService.resolve_owned_entry(artifact)`，路由层只走它。

- `owner_roots` 同时就是"容器根"集合：[[raw_access]] 的单文件模式直接复用它（entry 直接
  落在任一 workspace 形态或 team 目录根上时不服务兄弟文件）。只拿"第一个存在的 workspace"
  会漏掉 nested 与 legacy flat 同时存在时落在 legacy 根上的 entry（PR #421 复审 I4）。

## 上下游

上游（"artifact 路径一律走 `confine_entry`"在代码里为真的全部入口）：[[raw_access]]、
[[user_edit]]（两处，确权后的 realpath 交给 [[commit]]）、[[freshness]]（越界 → "missing"）、
[[url_artifact]] 的 `set_embed_mode`（越界 → `ArtifactNotFound`）、经
`ArtifactService.resolve_owned_entry` 的 backend `agents/artifacts.py` office-asset 与
common_tools 状态块的 office 锁探测。[[heal]] 用同一口径的 `resolve_inside_workspace`
（它扫的是 scope 根而不是一条已存指针）。
下游：[[workspace_paths]]。注册时的 `_resolve_entry`（[[registration]]）是写入侧的同类
规则，本文件是读/改侧的。
