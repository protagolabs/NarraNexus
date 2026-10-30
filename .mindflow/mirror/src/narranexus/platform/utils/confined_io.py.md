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

### 同类但**未转换**的 by-path 重开（精确清单与原因）

- `backend/routes/agents/files.py:77` `_build_tree`（`os.scandir(path)` 按串递归）：目录在 `is_dir` 与递归之间被换成链接会泄漏
  受害者的文件名/大小。只读元数据，需要把整棵树遍历改成 fd 相对，另行处理。
- `backend/routes/manyfold/files.py:233`（`list_dir` 的 `iterdir`）与 `:276`（`stat_file` 的 `target.stat()`）：同上，只读元数据。
- `backend/routes/office_watch/proxy.py` `/office-watch/open` 与 `utils/office_watch.py`（`Popen` officecli）：把路径交给**另一个进程**，
  无法传 fd；仅由 `resolve_watch_file` 做检查。
- `plugins/builtin.skills/.../skill_module/skill_module.py:1270 / :1521 / :1527` 与 `src/narranexus/platform/bundle/importer.py:1885`：
  对 `workspace/skills/<name>` 做 `shutil.rmtree`，`skills` 是租户可替换的中间目录——属于同一类（删除），涉及插件层与 bundle 导入，
  需要把 base 传进去再用 `remove_tree`，另行处理。
- 整个 workspace 的删除 `backend/routes/auth.py:1808`、`plugins/builtin.channels.lark/.../_lark_workspace.py:109`、
  `plugins/builtin.teams/.../routes.py:236`：对平台自己拼出的路径做 `rmtree`，其最后一级若被换成链接，`shutil.rmtree`（Linux/macOS 上本身
  基于 dir_fd）会拒绝顶层符号链接；路径前缀（`{base}/{user}`）不在租户可写范围，故本 PR 不动。
- 其余 `rmtree` 均作用于平台临时目录 / 安装目录（`temp_dir`、`staging`、插件目录），不在租户树内；`backend/main.py:914/917` 服务前端构建产物。

## 相关约束

2026-10-28 跨租户读取事件的后续（见 [[artifact/_artifact_impl/confinement]]）；`tests/utils/test_confined_io.py` 与
`tests/artifact/test_confined_io_sites.py` 钉住竞态与正常路径。
