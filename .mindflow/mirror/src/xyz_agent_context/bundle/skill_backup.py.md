---
code_file: src/xyz_agent_context/bundle/skill_backup.py
last_verified: 2026-08-17
stub: false
---

# skill_backup.py — skill 归档机制 (subproject 2 §8.12.2-5)

## 为什么存在

Bundle export 想以"URL 安装"或"Zip 安装"方式分享 skill 时，必须能 reproduce 接收方的 skill 安装过程。这要求 install 时就把"原始来源"（GitHub URL 或上传的 zip 文件）归档到一个稳定位置，并在数据库登记。skill_backup.py 是这套归档机制的中心。

## 上下游关系

- **被谁用**：
  - `backend/routes/skills.py` — `install_skill` 路由完成时调 `backup_after_api_install`，自动归档
  - `module/skill_module/_skill_mcp_tools.py` — 4 个 MCP backup tools (`skill_backup_from_github`, `_from_md`, `_from_local_zip`, `skill_list_unbackedup`) 给 agent 自己用
  - `bundle/builder.py` — Export 时通过 `SkillArchiveRepository` 间接读 `archive_path`
- **依赖谁**：
  - `repository/skill_archive_repository.py` — DB 层
  - `httpx` — 下 GitHub tarball

## 设计决策

### 双触发路径（PRD §8.12.2）

| 来源 | 触发 |
|---|---|
| API 安装（上传 zip / 填 GitHub URL） | install_skill 路由末尾自动调 |
| Agent 自己装的（不走 API） | Agent 自觉调 backup MCP tool |

### GitHub 用 tarball，不用 git clone（PRD §8.12.4）

`https://github.com/{owner}/{repo}/archive/refs/heads/{branch}.tar.gz` 一次 HTTP 即可，不依赖 git binary（铁律 #9 精神）。

私有仓库 v1 不支持。

### 归档目录

`~/.nexusagent/skill_archives/{user_id}/{skill_name}.zip` 或 `.tar.gz`。

> ⚠️ **SINGLE-WORKER ASSUMPTION**：`archive_path` DB 列存的是绝对本地 fs 路径。多 pod 部署时，pod A 装的 skill，pod B export bundle 时 archive_path 找不到文件。修复方式：换 S3 / 共享 volume。
>
> 详见 `.mindflow/project/references/scaling_assumptions.md` §2。

### `archive_local_zip` 安全校验

只允许 zip 路径在 caller agent 的 workspace 内（防越权）+ 必须含 SKILL.md。

## Gotcha

- 同名 skill 多次上传 → 后写覆盖前写（`upsert`）。前一次的 archive zip 文件被同名 zip 覆盖，sha256 也更新。这是 PRD 6.1.b "保留最新" 的实现方式。
- `pending` sha256：用户通过 `/api/bundle/skills/archives/upload` 提供 GitHub URL 但还没真正下载时，row 的 sha256 会先填 `"pending"`。export 走到这条 archive 时会失败 / 跳过。这是 v1 简化（不立即下载），未来要做 lazy download。

## 2026-07-10 — 排除内置技能

- `list_unbackedup` 用 `_dir_is_builtin`（读 `.skill_meta.json` 的 `builtin`）过滤掉内置技能——它们随 app 出厂，不是用户数据，不该出现在"待备份"列表里。目标机首次运行会自动重新物化。

## 2026-07-14 — `_dir_is_builtin` 去重

- 原本这里自带一份 `_dir_is_builtin`，和 `skill_module.py` 逐字重复、会漂移。现改为 `from .skill_secrets import dir_is_builtin as _dir_is_builtin`——判定逻辑收敛到 [[skill_secrets.py]] 单一真相源，本文件行为不变（仍是 `list_unbackedup` 的内置过滤器）。

## 2026-08-17 — SEC-07：`archive_target()` 成为唯一的归档路径构造点

`skill_archives/{user_id}/{skill_name}.*` 这个拼接原先散在 **7 处 / 3 个
文件**：本文件 4 处、[[importer.py]] 2 处、[[bundle.py]] 1 处。每一处的
`skill_name` 都来自进程外部——Form 字段（route）、bundle manifest（导入方
写的）、LLM 给的 MCP 工具参数——而全部是裸 f-string。route 那处已被 QA
实证可以 `../` 跳出用户目录写进别人的目录。

现在收敛成一个函数：

- **`archive_target(user_id, skill_name, suffix=".zip")`** —— 唯一合法构造
  点，**纯函数**（只校验和计算，不碰文件系统）。`sanitize_filename` 校验
  skill_name → `ensure_within_directory` 落地 → 再用
  `is_within_archives_root` 复查一次。最后这步不是多余的：
  `ensure_within_directory` 锚在**用户目录**上，如果用户目录本身是个指
  向树外的 symlink，结果"合规却在外面"；这里锚回 archives root，而
  symlink 出去的用户目录满足不了它。
- **`ensure_archive_dir(target)` / `prepare_archive_target(...)`** —— 建父
  目录的那一半，只在**真的要写**的时候调。拆开的理由：`archive_target`
  原先内部 `mkdir`，于是 route 里"拒绝时不建目录"的承诺只对非法
  `skill_name` 成立，另外 3 条 400 分支和整个 github 分支都会留下空目
  录——而测试参数表刚好只有非法名，绕开了唯一出问题的分支。纯化之后
  "4xx 零副作用"才是真的。
- ⚠️ **两个 containment 判据是故意不同的，不要"统一"**：写侧
  （`archive_target`）锚 archives **root**，因为它要挡的是 symlink 出逃；
  读侧（`is_within_user_archive_dir`）锚**该用户目录**，因为它要挡的是
  跨用户和 root 散装层。把写侧换成 per-user 判据会恒真（此时用户目录已
  被 resolve），symlink 那个洞会重新打开。
- `_user_archive_dir` 现在也 `sanitize_filename(user_id)`。user_id 来自
  JWT / X-User-Id 而不是表单，但"够可信了"正是 SEC-07 在下一层发生的原
  因。
- **`is_within_user_archive_dir(user_id, path)`** —— 读侧守卫。封住写路径
  **不会**回溯清理已经写坏的行（dev 库还留着 QA 那条），而 [[builder.py]]
  会把 `archive_path` 指向的文件拷进导出包，所以读的时候必须再判一次。
  **必须用 per-user 锚点**：QA 那行存的是
  `{root}/{uid}/../marker.zip`，resolve 之后在 `{root}/marker.zip`，root
  锚点判它"合规"；`{root}/{受害者}/x.zip` 同理。`is_within_archives_root`
  留着只给写侧用，它的 docstring 现在明写自己是更松的那个。

回归测试 `tests/bundle/test_skill_archive_path_safety.py` 里有一条 grep 式
断言：这 3 个文件中不允许再出现 `<dir> / f"...skill_name..."` 形状的行。
这类 bug 的复发方式就是有人又手拼了一次路径。
