---
code_file: src/xyz_agent_context/agent_framework/local_sandbox.py
last_verified: 2026-06-25
stub: false
---

# local_sandbox.py — external IM 本地 turn 的 OS 沙盒(身份分离版 B)

## 为什么存在

external IM subject 的 turn 在**本地**跑(无 cloud executor 容器)时,agent CLI 子进程要
包一层 OS 沙盒,否则一个被注入的 Bash 能读 owner 的文件/密钥、写出 workspace。cloud turn
用 executor 容器隔离,所以这是 **no-broker / local** 那条路。纯构造命令(可测),spawn 在
agent-loop CLI 起进程处接。

## 上下游关系

- **接入点**:`step_3_agent_loop.py` 在 `executor_url is None`(本地)+ claude framework +
  `is_external_subject(ctx.user_id)` 时,`build_sandbox_layout(...)` 算出 `SandboxLayout` 传给
  `get_agent_loop_driver` → `ClaudeAgentSDK(sandbox_layout=...)`。
- **SDK 怎么用**:`ClaudeAgentSDK.__init__` 调 `detect_local_sandbox()` + `prepare_sandbox_wrapper`
  生成一个 wrapper 可执行脚本,设 `ClaudeAgentOptions.cli_path = wrapper`(SDK types.py:735
  支持自定义 CLI 路径)→ SDK 实际 spawn 的是 wrapper,wrapper 再 `exec sandbox-exec/bwrap … 真claude "$@"`。
  同时 `cli_env["HOME"]=sandbox_home` 让 `~/.claude` 落沙盒。turn 结束 finally 调 cleanup 删临时目录。
- **依赖谁**:`workspace_paths`(lazy,算 external/owner ws);其余纯 os/shutil/sys。

## 关键函数

- `build_sandbox_layout(agent_id, subject_user_id, owner_user_id, base)` → `SandboxLayout`
  (external_ws={base}/{subject}/{agent};owner_ws={base}/{owner}/{agent};sandbox_home=external_ws/.home)。
- `prepare_sandbox_wrapper(layout, backend, real_cli)` → `(wrapper_path, cleanup)`,写 profile +
  wrapper 脚本到临时目录,返回 cli_path。**实测:生成的 wrapper 跑真 sandbox-exec 强制隔离通过。**
- `detect_local_sandbox()` → "sandbox-exec"/"bwrap"/None(None → warn-open)。
- **owner ws 在 bwrap 里 bind 到自己的真实路径**(不是 `/owner`),让 owner 路径在
  macOS/Linux/warn-open 三种情况一致,prompt 能引用同一个路径(B-4)。

## 设计决策（2026-06-25 spike 实测验证）

**两后端、两模型**:
- **macOS `sandbox-exec` = BLOCKLIST**(`allow default` + `deny` 敏感集)。原因:deny-default
  allowlist 下 **node 直接 Abort trap: 6**(缺 mach/dyld/ipc 允许),且 sandbox-exec
  deprecated。blocklist 下 node/网络/mach 全正常,只限文件系统。
- **Linux `bwrap` = bind-only allowlist**(只 bind 该给的,更强、对 node 稳)。

**macOS profile 结构(2026-06-25 实测修正)**:`(allow default)` →
`(deny file-write* base)` → `(allow file-write* ext)` → `(deny file* ~/.ssh …)`。
即:**写限制**(只能写自己 ws,owner + 其它 subject 都在 base 下、写被拦)+ **密钥隐藏**。

⚠️ **关键教训(EPERM bug)**:**不能 `(deny file-read* base)`**。claude CLI 启动时会从 cwd
**向上 stat/遍历祖先目录**(项目/配置发现),cwd = `base/{subject}/{agent}` 在 base 下,
read-deny base → 遍历到 base 时 EPERM → CLI exit 1 → 整轮空输出。`--version` 不设 cwd 时不触发,
所以最初漏过(`test_prepare_wrapper_real_claude_runs_with_cwd` 现在专门设 cwd 跑真 claude 兜住)。
→ 改成 **deny WRITE(不 deny read)**:遍历(read/stat)放行,CLI 正常;写仍被限。
**代价:macOS blocklist 不提供跨 subject / owner 的 READ 隔离**(allow default 可读)——
强 read 隔离用 Linux bwrap(bind-only allowlist,只挂该挂的)或 Docker executor。
实测(cwd=ext):`claude --version` rc=0、SIBLING/OWNER 写被拦、SSH 读被拦、EXT 可写。

**路径必须 canonical**:`_canon = os.path.realpath`。macOS `/tmp`→`/private/tmp`,Seatbelt
按 canonical 匹配 `(subpath ...)`,非 canonical 会**静默失效**(deny 变 no-op)。

**网络不隔离**:CLI 要调 LLM,MCP 走 localhost,故不 `--unshare-net` / 不 `deny network`。
= 文件系统隔离;egress + env key 残留(见设计文档)。

**HOME 重定向**:`sandbox_home`(可写)→ CLI 的 `~/.claude` 状态落沙盒内,不碰 owner 真实 home。

## Gotcha / 边界情况

- `detect_local_sandbox()` 返回 None → 调用方 **warn-open**(数据隔离仍在 + 警告),不 fail-closed。
- bwrap 的 `extra_blocked` 不用(allowlist 本就只 bind 该给的)。
- 这里只**构造**命令;真正 spawn + 写 profile 临时文件在 CLI spawn 处(brick 2)。
