---
code_file: backend/routes/_confined_response.py
last_verified: 2026-10-30
stub: false
---

## 2026-10-30 — `open_tenant_file(path, within=...)`

resolver 常返回**真实路径**（`ensure_within_directory` / `.resolve()`）：租户在 workspace 里预先放好的链接会让 resolver 吐出受害者的真实路径，
这条路径里已经没有链接，逐级 `O_NOFOLLOW` 也拦不住。`within` 是调用方自己的**词法**根（workspace / per-user 根，由 id 拼成、从不 resolve），
要求文件的 base 相对路径落在其下，否则返回 None。attachments / transcription / inbox / files 下载 / manyfold read 都传了 `within`。

# _confined_response.py — 受限打开 + 服务"已打开文件对象"的响应（路由共享）

## 为什么存在

路由原先把路径串交给 `FileResponse`，Starlette 在发送时再 open 一次——这是"检查"与"打开"之间的 TOCTOU 窗口（目录被换成指向别的
租户的链接）。`open_tenant_file(path)` 把 resolver 给出的路径当作**标签**：词法还原 base 相对路径，经 [[utils/fs/confined_io]] 逐级
`O_NOFOLLOW` 打开；`ConfinedFileResponse` 从这个已打开的对象服务。放在 `backend/routes/`（与 `_ownership.py` 同级）因为
artifacts / agents / inbox / transcription / manyfold 多个路由组共用，不属于任何一组。

## 设计

继承 `FileResponse`：头（Content-Length/Last-Modified/ETag/Accept-Ranges）、HEAD、单/多 Range、If-Range、416 的框架逻辑全部沿用，
`filename` 触发的 Content-Disposition 也沿用；只替换三个会碰文件的 `_handle_*`，改为从持有的文件对象读（线程里读）。
被否决：自己写只支持单 Range 的响应（行为分叉）；把 `/proc/self/fd/N` 当 path 交给 FileResponse（Linux 专属，macOS 桌面端不同）。
`open_tenant_file` 返回 None 的情形：缺失、非常规文件、不在 base 之下、路径经过链接——调用方保持各自原有的 404 形状。

## Gotcha

- **触发**：Starlette 升级后改名/改签名这三个内部方法 → **症状**：Range 请求 TypeError/500 → **根因**：我们覆写了私有方法；
  `tests/backend/test_tenant_file_routes_confined.py` 与 `tests/artifact/test_confined_io_sites.py` 的 Range/HEAD/multi-range 用例会立刻红。
- 响应拥有文件对象：`__call__` 的 `finally` 一定关闭（含 HEAD、客户端断开）；构造后从未被 await（handler 随后抛错）由 `__del__` 兜底；
  构造本身失败（fstat 抛错）先关文件再抛。
- `http.response.pathsend` 扩展被剥掉：服务器绝不能拿标签路径自己去打开。
- 被服务的文件必须在 `settings.base_working_path` 之下，否则 `open_tenant_file` 返回 None（fail closed）——测试里的假 workspace 要放在 base 下。
- 平台支持：与 confined_io 相同，**Windows 不受支持**（见 [[utils/fs/confined_io]]）——缺原语时 fail closed，不回落按路径服务。
