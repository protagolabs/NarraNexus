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
- `write_atomic`：临时文件经父目录 fd `O_CREAT|O_EXCL|O_NOFOLLOW` 创建 → fsync → 用 src/dst dir_fd `os.replace`；叶子
  是链接时**替换链接本身**而不是写穿；失败清理临时文件；`mode=None` 沿用原文件权限位。
- `dir_regular_bytes`：fd 相对递归，只数常规文件，链接（文件或目录）既不跟随也不计入；深度封顶 64。
- 所有 fd 在每条路径上都关闭（有 fd 泄漏测试）。

## 这个文件不做什么

不决定"谁拥有哪些根"——那是 [[artifact/_artifact_impl/confinement]] 的 `owner_roots`/`confine_entry`；本模块只保证
**打开时**不穿过链接。它也不能保护 `root` 之上的祖先目录，也不管已经打开的 fd 之后被谁改内容。

## 上下游

被 [[artifact/_artifact_impl/confinement]] 的 `*_confined` 一族封装后供 artifact 的 raw_access / user_edit / freshness /
registration / commit / url_artifact 使用，并被 backend `agents/artifacts.py` 的 office-asset 上传直接使用。
同类但**未转换**的 by-string 重开：backend `agents/files.py`（工作区文件下载/上传）、office_watch 代理（把路径交给
另一个进程，无法传 fd）、heal 的目录扫描（只列名，不读内容）。

## 相关约束

2026-10-28 跨租户读取事件的后续（见 [[artifact/_artifact_impl/confinement]]）；`tests/utils/test_confined_io.py` 与
`tests/artifact/test_confined_io_sites.py` 钉住竞态与正常路径。
