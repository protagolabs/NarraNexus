---
code_file: src/narranexus/platform/utils/confined_io.py
last_verified: 2026-10-30
stub: false
---

# confined_io.py — 让"检查路径"和"打开路径"成为同一个操作

## 为什么存在

租户控制自己 workspace 的内容，能在里面建 symlink。宿主侧代码过去的写法是：先校验（`realpath` 落在属主的根内），
再**按字符串**重新 open / replace。两步之间路径上的某一级目录被换成指向别的租户的链接，open 就落到别人的树里
（TOCTOU）。本模块不是更严的校验，而是把校验变成打开本身：从一个**平台自有**的绝对根目录（通常是
`realpath(base_working_path)`）起，其下每一级用 `os.open(name, O_DIRECTORY|O_NOFOLLOW, dir_fd=parent)` 逐级打开，
叶子用 `O_NOFOLLOW` 打开/创建。任何时刻出现的链接——包括检查之后才出现的——被拒绝而不是跟随。

## 约定

- `root` 必须是绝对路径且自身不能是链接（它的祖先正常解析）；`rel` 是 POSIX 相对路径，拒绝空、绝对、`.`/`..`、
  空段、NUL。租户能影响的一切都在 `rel` 里，所以 `root` 要选得足够靠上（base，而不是租户 workspace）。
- 异常分层：`ConfinedPathError(ValueError)` = 路径非法或经过链接（调用方按"越界/404"处理）；
  `SymlinkRefusedError`、`ConfinedIOUnsupported` 是它的子类；缺失 = `FileNotFoundError`；叶子不是常规文件
  （目录/FIFO/设备）= `NotARegularFileError(OSError)`。
- **fail closed**：缺 `O_NOFOLLOW`/`O_DIRECTORY`、`dir_fd`、`scandir(fd)` 支持时抛 `ConfinedIOUnsupported`，绝不回落到
  可竞态的字符串路径。Linux 与 macOS（桌面端）都满足；`os.replace` 与 `os.rename` 共用 dir_fd 支持，所以检查的是
  `os.rename`。
- `open_read` 用 `O_NONBLOCK` 打开叶子再 `fstat` 判类型：租户放一个 FIFO 不会把读卡死。
- `write_atomic`：权限位取原文件 `& 0o777`（不带 setuid/sticky）；`fdopen` 失败时关临时 fd；临时文件经父目录 fd `O_CREAT|O_EXCL|O_NOFOLLOW` 创建 → fsync → 用 src/dst dir_fd `os.replace`；叶子
  是链接时**替换链接本身**而不是写穿；失败清理临时文件；`mode=None` 沿用原文件权限位。
- `dir_regular_bytes`：fd 相对递归，只数常规文件，链接既不跟随也不计入；**fail closed**——深度超过 64 抛 `TreeTooDeepError`（`ConfinedScanError` 子类，消息 "directory tree deeper than N levels"）、
  目录不可读抛 `ConfinedScanError`、扫描中目录被换成链接抛 `SymlinkRefusedError`；绝不少算（总数用来执行大小上限，静默跳过会让租户把字节藏起来）。artifact 侧映射为
  `ArtifactTooLarge("artifact directory cannot be measured …")`，**不再**冒充 `ArtifactPathEscape`。
- `list_dir(root, rel)`：目录 fd 上 scandir + 逐项 lstat（`DirEntryInfo`），链接条目按链接报告其自身元数据。
- `rename_within(root, src_rel, dst_rel)`：两个父目录各自逐级 `O_NOFOLLOW`，`renameat` 作用在两个 dir fd 上；链接叶子按链接改名；非空目标目录 →
  `FileExistsError`（不合并、不嵌套）；已存在的**文件**目标被原子替换（源也须是文件，目录覆盖文件 → `NotADirectoryError`）；已存在的**空目录**目标被替换（源须是目录，文件覆盖目录 → `IsADirectoryError`）。
- `merge_tree(src_root, src_rel, dst_root, dst_rel)`：把**平台暂存区**目录的内容合并进目标目录（缺失则逐级创建）：目标路径逐级 `O_NOFOLLOW`（任何链接 → `SymlinkRefusedError`，之前什么都没写）；
  同名文件走私有 temp + `rename` 替换（链接被替换，不被写穿）；应放目录处有链接 → unlink 该链接；应放文件处是目录 → `IsADirectoryError`（不删树）。用于 bundle 导入的工作区落地。
- `link_or_copy`：两个父目录 fd 上 `linkat`（源必须是常规文件，链接/特殊文件 → `NotARegularFileError`；链接后复核类型，必要时撤销），失败（跨设备等）才字节复制；目标 `O_EXCL`。
- `walk_tree(root, rel, skip=, recurse=, strict=, max_depth=64)`：**线性时间**的遍历——起点经一次受限遍历到达，之下**每层只持有一个目录 fd**，子目录与文件都相对其父 fd
  `openat`（`TreeNode.open_file()` 一次 `openat`，无需从根重走；只在迭代停在该条目期间有效）。链接 / FIFO / socket 不产出；`skip` 剪枝整棵子树；
  顺序是"本目录条目按名排序，再按名排序依次进入子目录"的深度优先（技能内容哈希不依赖它：
  install_pipeline 自己再按 `PurePosixPath.parts` 排序）。**深度硬上限 `MAX_TREE_DEPTH=64`**，超过抛 `TreeTooDeepError`（永不静默跳过——导出 / 哈希 / 大小限制都依赖完整性）；
  进不去的子目录（被换成链接 / 不可读）**记 WARNING 后跳过**，`strict=True` 则抛。`iter_tree` 是只要列表的薄封装。
  （旧实现每个目录都从根重走，深度 D 是 O(D²)：2000 层 5 秒、1 万层约 2 分钟，且后端周期性对账会被拖住；现已修。）
- `copy_tree(src_root, src_rel, dst_root, dst_rel)`：把目录复制成**新**目录。**源必须是平台自有 staging 或同一租户的目录，绝不能是别的租户可控的
  路径**；源与目的都经 dir fd 遍历（`split_source`：base 之下从 base 起，之外取 realpath(parent)+叶子），里面的链接按链接复制（`readlink` +
  `symlink`，从不解引用），常规文件保留权限位 `& 0o777`，FIFO/socket/设备跳过；目的端 `mkdir` / `O_CREAT|O_EXCL|O_NOFOLLOW`；深度上限 64
  （`ConfinedScanError`）；任何失败都清掉半成品目的目录。
- `make_dirs` / `copy_tree(make_parents=True)` 在 `create` 时会先创建缺失的**平台自有**根（首次运行没有 workspace base）。
- `remove_tree`：fd 相对删除，`unlink`/`rmdir` 走 dir_fd，**从不跟随链接**；树内/叶子的链接只删链接本身；**迭代**（每层一个 fd），深度上限 = 进程 fd 软上限的 1/4
  （macOS 的 256 → 64，Linux 常见的 1024 → 256，封顶 4096，`_remove_depth_cap()`），**在为该层打开 fd 之前**就抛 `TreeTooDeepError`，远早于 EMFILE（EMFILE 会饿死本进程的其他请求）；已删部分不回滚；删除过程中被换成链接 / 常规文件的目录（顶层与嵌套一视同仁）**直接 unlink 那个链接/文件**，租户不能靠竞态把自己的树留下。
- `split_under_base(path, base)`：词法还原 (realpath(base), 相对尾段)，接受 base 的真实路径与配置拼写（相对/符号链接），租户尾段
  永不 resolve——对尾段做 realpath 会在"较晚的时刻"穿过已放好的链接，再 NOFOLLOW 打开就成了对受害者树的合法访问。
- 所有 fd 在每条路径上都关闭（有 fd 泄漏测试）。

## 这个文件不做什么

不决定"谁拥有哪些根"——那是 [[artifact/_artifact_impl/confinement]] 的 `owner_roots`/`confine_entry`；本模块只保证
**打开时**不穿过链接。它也不能保护 `root` 之上的祖先目录，也不管已经打开的 fd 之后被谁改内容。

## 上下游

被 [[artifact/_artifact_impl/confinement]] 的 `*_confined` 一族封装后供 artifact 的 raw_access / user_edit / freshness / heal /
registration / commit / url_artifact 使用；被 backend 的 `agents/artifacts.py`（office-asset 上传）、`agents/files.py`
（下载 / 上传 / 删除 / **目录树列表**）、`manyfold/files.py`（read / write / **list / stat**）、`auth.py`（删 agent）、
`arena_onboarding.py` / `arena_provisioning_service.py` 直接使用；路由侧经 [[backend/routes/_confined_response]] 的
`open_tenant_file` / `ConfinedFileResponse` 使用（agents/attachments、inbox、transcription/public、artifacts/public、files、manyfold）。
其余使用者：`attachment_storage`、`message_bus/_bus_attachment_impl`、`bundle/{builder,importer,skill_backup,skill_secrets}`、
`marketplace/.../install_pipeline`（内容哈希）、`services/skill_sync_service`、`migration/applier`、`bootstrap/profiles`、
`module_system/{data_access/workspace_cwd,managed_channel_ingress}`、`agent_runtime/.../step_3_agent_loop`、teams `routes`、skill 插件、
narramessenger `_matrix_send`。

### 平台支持

**Windows 不受支持**（没有 `dir_fd` / `O_NOFOLLOW`）：缺原语时所有操作抛 `ConfinedIOUnsupported`，调用方按"拒绝"处理，
**绝不**回落到按路径串的实现。Linux 与 macOS（桌面端）满足。

### 本 PR 已转换的位置（按类）

- **读 / 服务**：artifact raw / edit / freshness / heal 元数据 / office 版本轮询；`agents/files.py` 下载；attachments、inbox、transcription 路由；
  manyfold read；`attachment_storage` 解析与索引读；bus 附件解析 / 哈希 / 边车读；`_matrix_send` 出站媒体；`managed_channel_ingress` 附件；
  arena 凭据读回；`dir_is_builtin`；skill 内容哈希；skill 扫描 / 解析（`skill_module`）。
- **写 / 建目录**：artifact 保存 / url tab / office-asset；`agents/files.py` 上传；manyfold write；`attachment_storage` 上传与索引；bus 暂存 / 上传 / 边车；
  arena `install_skill`；bootstrap `Bootstrap.md` 与欢迎 artifact；CLI CWD 与 agent 工作目录创建；skill 安装 / 物化 / 元数据；`copy_skill_into_agent`；`migration/applier` 技能复制。
- **改名 / 复制**：skill 启停（`rename_within`）；skill 安装 / 物化 / 复制（`copy_tree`）；bus 暂存（`link_or_copy`）。
- **删除**：`remove_skill`、skill 安装替换、`copy_skill_into_agent` / `applier` 替换、team 共享目录清空、agent 删除、`agents/files.py` 删除——全部 `remove_tree`。
  **删除目标必须是词法路径**：`ensure_within_directory` 的返回值已 `resolve()`，会把被换成链接的目录解析成受害者的真实路径，之后的受限遍历反而放行。
- **列表 / stat / 打包**：`_build_tree`、manyfold `list` / `stat`、bundle 导出的 workspace tar 与 skill zip、`list_unbackedup`、对账（`skill_sync_service`）、
  内置技能扫描——`list_dir` / `iter_tree` / `lstat`；链接条目按链接报告（打包时跳过）。
- 验证过的"不需要转换"：整 workspace 的删除里，`{base}/{user}` 是执行器的绑定挂载点（`executor_protocol.py:200`、`_bus_attachment_impl.py` 的说明），
  租户无法替换它本身；lark 的 HOME / 配置目录 `~/.narranexus/lark_workspaces/<agent>`（`_lark_workspace.py`，`LARK_WORKSPACE_BASE`）不在任何租户挂载内。
  （`lark_cli_client` 另有一处把**租户 workspace** 当子进程 cwd，见下方例外清单。）

### 完整性声明（口径）与仍**未转换**的位置

扫描口径：`git grep -lE "agent_workspace_path|agent_workspace_relpath|resolve_existing_workspace|base_working_path|get_workspace_path|team_shared_dir|bus_files_dir|user_shared_root|resolve_agent_workspace_cwd|workspace_candidate_relpaths|resolve_workspace_relative_file|resolve_inside_workspace|BASE_WORKING_PATH|bootstrap_path" -- src backend plugins scripts`，
外加对 `src backend plugins` 中每一处 `rmtree` / `os.remove` / `unlink` 的逐个检查（路径来自计算对象、不含上述词的删除点——
如 `context_runtime` 的 `status.bootstrap_path`——就是这样漏掉过的）。
命中的**每个**文件都已逐个检查（含 `resolve_agent_workspace_cwd` 的全部调用方）。**除下表外**，它们对租户可写树的 by-path 读 / 写 / 删 / 列表 / stat 站点均已转换；下表是**全部**例外及原因：

- **路径交给另一个进程**（无法传 fd，只能靠 `resolve_*` 的检查，已知并接受）：`backend/routes/office_watch/proxy.py` `/office-watch/open` 与 `utils/office_watch.py`（`Popen` officecli）；
  `plugins/builtin.channels.lark/.../lark_cli_client.py`（~:293 `cwd = resolve_agent_workspace_cwd`，lark-cli 子进程）；
  `plugins/builtin.channels.narramessenger/.../narra_cli_client.py`（~:345，narra-cli 子进程）。cwd 的**创建**已经受限（`workspace_cwd.py`）。
- `plugins/builtin.nexus_plugins_module/.../_nexus_plugins_impl/service.py` 的 `scaffold` / `edit` / `list`（agent 自己的插件草稿目录 `dev_root(workspace)`）：
  经 CLI 脚手架库用 pathlib 写很多文件，仅靠 `check_inside` 的词法守卫；同一类，需要把脚手架库整体改为接受受限根，**未转换**。
- `src/narranexus/platform/artifact/_artifact_impl/heal.py` `_scan_workspace_for_kind` 的 `os.walk` 发现阶段：只产生**候选名字**；每个候选的元数据与内容读取都已走受限路径，
  被换成链接的候选被丢弃。名字泄漏的窗口只剩 walk 与 `_confined` 之间。
- `src/narranexus/platform/agent_runtime/executor_service.py`：执行器侧，以租户 uid 在容器内运行，本来就只能访问自己的挂载；不是后端读写租户树。
- **一次性 / 运维的顶层目录改名或迁移**（路径前缀与叶子都不在租户可写范围，用户目录本身是挂载点）：`backend/integrations/netmind/identity_migration.py:294-296`、
  `backend/migrations/m0002_workspace_nested_layout.py`、`backend/routes/admin/migration.py`、`scripts/data_migrations/migrate_workspace_layout.py`、
  `scripts/data_migrations/migrate_users_to_netmind.py`、`scripts/release/deploy-cloud.sh`。
- **平台自有路径，不在租户挂载内**：`backend/routes/manyfold/diagnostics.py`（对 `base_working_path` 本身做 `os.access`、日志目录 `iterdir`）；
  `marketplace/_skill_marketplace_impl/{artifact_store,secret_box}.py`（`base_working_path` 的**兄弟**目录 / 平台私有目录）；`platform/settings.py` / `utils/db/schema_registry.py`（仅配置 / 文档字符串）。
- **调试脚本，不是运行路径**：`scripts/debug/verify_arena_provision.py`（~:105 对租户技能文件按路径 `.exists()`，
  且仍用旧的平铺布局；只在开发机手动运行）。
- **只做路径字符串拼接、不碰磁盘**：`module_system/data_access/__init__.py`（仅 re-export）、`plugins/builtin.common_tools/.../artifact_lines.py`、`plugins/builtin.social_network/.../routes.py`（仅注释）、`message_bus/team_files.py`、`message_bus/message_bus_trigger.py`
  （`team_shared_dir` 只用于生成提示词里的路径）、`utils/workspace_paths.py`（路径构造本身）、`artifact_repository.py` / `artifact_schema.py`（字段文档）。
- 技能的**暂存区**一侧：`skill_module.py` 的 zip / GitHub 解包（写入 `tempfile.mkdtemp()` 下的平台自有目录）与 `_find_skill_root` / `parse_skill_package` 对 staging 的遍历——staging 不在任何租户挂载内；
  bundle 导入的工作区 tar 在 `mkdtemp` 暂存区解开后再 `merge_tree`。
- 其余 `rmtree` 均作用于平台临时目录 / 安装目录（`temp_dir`、`staging`、插件目录），不在租户树内；`backend/main.py:914/917` 服务前端构建产物。

## 相关约束

2026-10-28 跨租户读取事件的后续（见 [[artifact/_artifact_impl/confinement]]）；`tests/utils/test_confined_io.py` 与
`tests/artifact/test_confined_io_sites.py` 钉住竞态与正常路径。
