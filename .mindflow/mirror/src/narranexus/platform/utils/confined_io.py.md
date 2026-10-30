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
- `dir_regular_bytes`：fd 相对递归，只数常规文件，链接既不跟随也不计入；**fail closed**——深度超过 64、目录不可读、扫描中目录被换成链接都抛
  `ConfinedScanError`/`SymlinkRefusedError`（注册据此拒绝），绝不少算（总数用来执行大小上限，静默跳过会让租户把字节藏起来）。
- `list_dir(root, rel)`：目录 fd 上 scandir + 逐项 lstat（`DirEntryInfo`），链接条目按链接报告其自身元数据。
- `rename_within(root, src_rel, dst_rel)`：两个父目录各自逐级 `O_NOFOLLOW`，`renameat` 作用在两个 dir fd 上；链接叶子按链接改名；非空目标目录 →
  `FileExistsError`（不合并、不嵌套）。
- `copy_tree(src_root, src_rel, dst_root, dst_rel)`：把目录复制成**新**目录。**源必须是平台自有 staging 或同一租户的目录，绝不能是别的租户可控的
  路径**；源与目的都经 dir fd 遍历（`split_source`：base 之下从 base 起，之外取 realpath(parent)+叶子），里面的链接按链接复制（`readlink` +
  `symlink`，从不解引用），常规文件保留权限位 `& 0o777`，FIFO/socket/设备跳过；目的端 `mkdir` / `O_CREAT|O_EXCL|O_NOFOLLOW`；深度上限 64
  （`ConfinedScanError`）；任何失败都清掉半成品目的目录。
- `make_dirs` / `copy_tree(make_parents=True)` 在 `create` 时会先创建缺失的**平台自有**根（首次运行没有 workspace base）。
- `remove_tree`：fd 相对删除，`unlink`/`rmdir` 走 dir_fd，**从不跟随链接**；树内/叶子的链接只删链接本身；深度上限 256，超过抛
  `ConfinedScanError`（已删部分不回滚）。
- `split_under_base(path, base)`：词法还原 (realpath(base), 相对尾段)，接受 base 的真实路径与配置拼写（相对/符号链接），租户尾段
  永不 resolve——对尾段做 realpath 会在"较晚的时刻"穿过已放好的链接，再 NOFOLLOW 打开就成了对受害者树的合法访问。
- 所有 fd 在每条路径上都关闭（有 fd 泄漏测试）。

## 这个文件不做什么

不决定"谁拥有哪些根"——那是 [[artifact/_artifact_impl/confinement]] 的 `owner_roots`/`confine_entry`；本模块只保证
**打开时**不穿过链接。它也不能保护 `root` 之上的祖先目录，也不管已经打开的 fd 之后被谁改内容。

## 上下游

被 [[artifact/_artifact_impl/confinement]] 的 `*_confined` 一族封装后供 artifact 的 raw_access / user_edit / freshness / heal /
registration / commit / url_artifact 使用；被 backend 的 `agents/artifacts.py`（office-asset 上传）、`agents/files.py`
（下载/上传/**删除**）、`manyfold/files.py`（read/write）直接使用；路由侧经 [[backend/routes/_confined_response]] 的
`open_tenant_file` / `ConfinedFileResponse` 使用（agents/attachments、inbox、transcription/public、artifacts/public、files）。

### 平台支持

**Windows 不受支持**（没有 `dir_fd` / `O_NOFOLLOW`）：缺原语时所有操作抛 `ConfinedIOUnsupported`，调用方按"拒绝"处理，
**绝不**回落到按路径串的实现。Linux 与 macOS（桌面端）满足。

### 本 PR 已转换的删除 / 列表 / 元数据位置

- 技能安装 / 启停 / 物化 / 元数据 / 扫描：见 [[skill_module]]（`copy_tree` + `rename_within` + `write_atomic` + `list_dir`）；importer 的 `copy_skill_into_agent` 同。
- 删除：`skill_module.py` 的 `remove_skill`（`skills/<name>` 与 `.disabled/<name>`）与 `install_from_dir` 的替换、`bundle/importer.py`
  `copy_skill_into_agent` 的替换、`teams/routes.py` 的共享目录清空、`backend/routes/auth.py` 的 agent workspace 清理、
  `agents/files.py` 的删除——全部 `remove_tree`。**删除目标必须是词法路径**：`ensure_within_directory` 的返回值已 `resolve()`，
  会把被换成链接的目录解析成受害者的真实路径，之后的受限遍历反而放行。
- 列表 / 元数据：`agents/files.py` 的 `_build_tree`、`manyfold/files.py` 的 `list` 与 `stat`——`list_dir` / `lstat`；链接条目按链接报告。
- 验证过的"不需要转换"：整 workspace 的删除里，`{base}/{user}` 是执行器的绑定挂载点（`executor_protocol.py:200`、
  `_bus_attachment_impl.py` 的说明），租户无法替换它本身；`plugins/builtin.channels.lark/.../_lark_workspace.py:109` 作用于
  `~/.narranexus/lark_workspaces/<agent>`（`LARK_WORKSPACE_BASE`），不在任何租户挂载内。

### 仍**未转换**的 by-path 位置（精确清单与原因）

- `backend/routes/office_watch/proxy.py` `/office-watch/open` 与 `utils/office_watch.py`（`Popen` officecli）：把路径交给**另一个进程**，
  无法传 fd；仅由 `resolve_watch_file` 做检查。已知并接受。
- 技能的**暂存区**一侧：`skill_module.py` 的 zip / GitHub 解包（写入 `tempfile.mkdtemp()` 下的平台自有目录）与 `_find_skill_root` /
  `parse_skill_package` 对 staging 的遍历——staging 不在任何租户挂载内，故不需要。
- 其余 `rmtree` 均作用于平台临时目录 / 安装目录（`temp_dir`、`staging`、插件目录），不在租户树内；`backend/main.py:914/917` 服务前端构建产物。
- 除此之外，本文档此前列出的全部租户树内 by-path 读/写/删/列表/stat 站点均已转换。

## 相关约束

2026-10-28 跨租户读取事件的后续（见 [[artifact/_artifact_impl/confinement]]）；`tests/utils/test_confined_io.py` 与
`tests/artifact/test_confined_io_sites.py` 钉住竞态与正常路径。
