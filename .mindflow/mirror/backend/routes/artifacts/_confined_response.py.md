---
code_file: backend/routes/artifacts/_confined_response.py
last_verified: 2026-10-30
stub: false
---

# _confined_response.py — 服务"已打开的文件对象"的 FileResponse

## 为什么存在

raw 路由原先把路径串交给 `FileResponse`，Starlette 在发送时再 open 一次——这是 resolve 与发送之间的 TOCTOU 窗口
（目录被换成指向别的租户的链接）。路由现在通过 `ResolvedRawFile.open()`（[[artifact/_artifact_impl/raw_access]]，逐级
`O_NOFOLLOW`）拿到文件对象，本类从这个对象服务。

## 设计

继承 `FileResponse`：头（Content-Length/Last-Modified/ETag/Accept-Ranges）、HEAD、单/多 Range、If-Range、416 的
框架逻辑全部沿用；只替换三个会碰文件的 `_handle_simple/_handle_single_range/_handle_multiple_ranges`，改为从持有的
文件对象读（线程里读，不阻塞事件循环）。被否决：自己写一个只支持单 Range 的响应（行为分叉）、把 `/proc/self/fd/N`
当 path 交给 FileResponse（Linux 专属，macOS 桌面端不同）。

## Gotcha

- **触发**：Starlette 升级后重命名/改签名这三个内部方法 → **症状**：Range 请求 TypeError/500 → **根因**：我们覆写了
  私有方法；`tests/artifact/test_confined_io_sites.py` 的 Range/HEAD/multi-range 用例会立刻红。
- 响应拥有文件对象，`__call__` 的 `finally` 一定关闭（含 HEAD、客户端断开）；构造之后若没人调用它就会泄漏——路由
  总是立即返回它。
- `http.response.pathsend` 扩展被剥掉：服务器绝不能拿标签路径自己去打开。
