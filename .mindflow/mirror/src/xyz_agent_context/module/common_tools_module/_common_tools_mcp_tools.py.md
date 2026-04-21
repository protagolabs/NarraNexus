---
code_file: src/xyz_agent_context/module/common_tools_module/_common_tools_mcp_tools.py
last_verified: 2026-04-21
stub: false
---

# _common_tools_mcp_tools.py — CommonToolsModule MCP 工具注册 + 通用超时装饰器 + web_search subprocess 外壳

## 为什么存在

挂所有 "跨模块通用能力" 的 MCP 工具（目前只有 `web_search`），以及提供两个给 MCP 工具用的基础设施：

1. **通用 timeout 装饰器** `with_mcp_timeout(seconds)`——Bug 20 留下的架构遗产，给 **任何** MCP 工具套一层兜底 timeout
2. **`web_search` 的 subprocess 隔离外壳**——Bug 24 引入，用 `_web_search_with_retry` + `_spawn_runner` 把 DDGS 调用隔离到独立子进程里

## 上下游关系

- **被谁用**：`module_runner.py` 启动 CommonToolsModule 的 MCP server（端口 7807）时调 `create_common_tools_mcp_server(port)`
- **依赖谁**：
  - `_common_tools_impl.web_search_runner` 子进程（通过 `python -m <module>` 启动）
  - `_common_tools_impl.web_search.format_results` 把 bundles 渲染成 markdown 给 LLM
  - `mcp.server.fastmcp.FastMCP`

## `with_mcp_timeout` 装饰器（Bug 20 的通用防御）

**动机：** 2026-04-18 事故里，一个 MCP 工具（web_search）因为底层 sync 库卡死把**整个共享 MCP 容器**拖垮 33+ 小时。事后扫过所有现存 MCP 工具，只有 web_search 有"`asyncio.to_thread` + `gather` 全无 timeout"的陷阱 pattern；但**没有任何机制拦截未来新工具重复这个错误**。

**解法：** 一个简单的装饰器，把 handler 包在 `asyncio.wait_for(fn(...), timeout=seconds)` 里。超时返回 `"[tool_error] ..."` 字符串让 LLM 读到"这个工具暂时不可用"。

```python
@mcp.tool()
@with_mcp_timeout(45)
async def some_tool(...) -> str:
    ...
```

**装饰器叠加顺序很重要：** `@mcp.tool()` 在上，`@with_mcp_timeout(...)` 在下——`mcp.tool()` 注册时看到的函数已经是带 timeout 包装的版本。

**装饰器返回类型是 str。** FastMCP 按 wrapped function 的 return annotation 校验输出。现在所有 web_search 类型的工具都 `-> str`，so 这个决定是安全的。如果未来有 `-> dict` 工具要套 timeout，得扩装饰器看 annotation 选 `{"error": ...}` 或 `f"[tool_error] ..."`——这是已知 TODO，没触发就先不做。

**Note:** `with_mcp_timeout` 只 bound 协程，**不能**杀掉底层线程或子进程。需要回收资源的工具（比如 web_search）自己要做 subprocess 隔离——装饰器是它**外面一层**的网，不是替代。

## `web_search` subprocess 隔离（Bug 24）

### 为什么要用 subprocess

Bug 20 的三层 asyncio timeout 只能让**协程**放弃 await，没法 reclaim 底层资源：
- 被卡的 worker thread：Python 没 `thread.kill()`
- 被卡的 socket：DDGS 底层的 primp/libcurl 有 CLOSE_WAIT 清理 bug
- 泄漏累积 → FD 表耗尽 → 整个 MCP 容器 `EMFILE: too many open files`

唯一能**强制回收**资源的边界是进程边界。子进程 `SIGKILL` 之后，Linux 无条件关掉它的所有 FD / socket / 线程。这是 Python 语言层做不到的保证。

### 三个关键常量（module-level）

| 常量 | 默认 | 作用 |
|---|---|---|
| `_SUBPROCESS_TIMEOUT_S` | 25s | 单次 subprocess 尝试的墙钟上限；超时 → SIGKILL → 视作失败 |
| `_MAX_ATTEMPTS` | 4 | 最多尝试次数（K=3 重试 + 1 原始）。全失败才报错 |
| `_RETRY_BACKOFF_S` | 1.0s | 两次尝试之间的固定间隔（非指数）|
| `_WEB_SEARCH_HANDLER_TIMEOUT_S` | 110s | 最外层 `with_mcp_timeout` 的值。覆盖 `4*25 + 3*1 = 103s` 最坏路径 + 余量 |

`_RUNNER_CMD` 也是 module-level（`[sys.executable, "-m", <runner_module>]`），方便测试把它 monkeypatch 成 `python -c` 模拟各种失败。

### 重试策略

**重试条件**（都会继续下一次 attempt）：
- `asyncio.TimeoutError`：subprocess 被 kill 过
- `_RunnerFailure`：exit code ≠ 0，或 stdout 不是合法 JSON

**不重试**：
- 子进程 exit 0 且 JSON OK，即使 bundles 里面的 query 全是 `"error": "..."`——这是 DDG 真没返回结果，重试也没用。LLM 看 bundle 错误字段自己判断。
- 空 queries 列表——`search_many` 直接返回 `[]`，子进程跑完立即退出，正常 happy path。

全部尝试失败 → `_web_search_with_retry` 抛 `RuntimeError`。handler 捕获后返回 `"web_search failed: {message}"` 字符串给 LLM。

## Timeout budget（本文件 + web_search.py 协作）

| 层 | 位置 | 常量 | 默认 |
|---|---|---|---|
| 1 | `web_search.py` → `DDGS(timeout=...)` | `DDGS_CLIENT_TIMEOUT_S` | 5s |
| 2 | `web_search.py` → `_one` 的 `wait_for(to_thread)` | `PER_QUERY_TIMEOUT_S` | 15s |
| 3 | `web_search.py` → `search_many` 的 `wait_for(gather)` | `OVERALL_TIMEOUT_S` | 30s |
| 4 | **本文件** → subprocess kill 墙钟 | `_SUBPROCESS_TIMEOUT_S` | 25s ⚠️ |
| 5 | **本文件** → `@with_mcp_timeout(...)` on handler | `_WEB_SEARCH_HANDLER_TIMEOUT_S` | 110s |

⚠️ **Layer 4 < Layer 3 是故意的：** 25s < 30s 看起来倒挂，但思路是——真要跑到 25s 说明内层三层 asyncio timeout 都**没能自救**（primp 在 C 层 spin 没释放 GIL 之类），这时候与其再等 5s 给它"最后一次机会"，不如直接 SIGKILL 然后重试。内层 timeout 工作正常的情况下，subprocess 15-20s 就会正常 return 错误 bundles。

Idle timeout 在 `xyz_claude_agent_sdk.py` 是 600s，足够覆盖最深的 110s handler + LLM thinking 时间。

## Gotcha / 边界情况

- **`@with_mcp_timeout(_WEB_SEARCH_HANDLER_TIMEOUT_S)` 读常量的时机**：装饰器在 `create_common_tools_mcp_server` **调用时**读 `_WEB_SEARCH_HANDLER_TIMEOUT_S`——import time 就固定下来的话没法测。所以测试里 `monkeypatch.setattr(tools, "_WEB_SEARCH_HANDLER_TIMEOUT_S", 2)` 必须在 `create_common_tools_mcp_server` 之前。这是**故意**的设计，不是 bug
- **subprocess 启动开销**：Python 冷启动 + `from .web_search import search_many` 大概 300-500ms。每次 web_search 多付这个钱。算 well worth it——比挂容器便宜得多
- **测试通过 monkeypatch `_RUNNER_CMD`**：看 `tests/common_tools_module/test_web_search_subprocess.py`，大多数 case 用 `python -c "..."` 直接模拟 runner 的行为，不启动真实 runner。真实 runner 单独测在 `test_web_search_runner.py`

## 新人易踩的坑

- 新加 MCP 工具**必须**叠 `@with_mcp_timeout(N)`——没有这个，一次 bug 可以挂整个 MCP 容器所有 session
- 如果新工具要调 **同步外部 HTTP / sync 库**（requests、urllib3、某些第三方 SDK），**不要直接 `asyncio.to_thread`**——照 web_search 的范式做 subprocess 隔离，或者改用 async SDK。`asyncio.to_thread` 是个陷阱，它**看起来**能超时，实际只能 bound 协程不能回收线程
- timeout 数值选择：handler timeout > 该工具内部所有 timeout 之和 + buffer。不能搞反
- 如果你的工具是**纯 async**（没有 `to_thread` / 子进程），装饰器也要加——asyncio 层的 bug 一样能阻塞（比如 `await asyncio.Event().wait()` 没 timeout 的情况）
