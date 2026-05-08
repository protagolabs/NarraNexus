---
code_file: src/xyz_agent_context/bundle/importer.py
last_verified: 2026-05-08
stub: false
---

# importer.py — bundle import pipeline (preflight + confirm)

## 为什么存在

`.nxbundle` 文件 = 跨实例分享 NarraNexus agent / team 的载体。导入端必须同时做完一连串复杂的安全 + 兼容 + ID rewrite 工作，且**必须事务性**（任何一步失败 = 没导入过）。把这些动作集中在一个文件里，让出错路径可控。

## 上下游关系

- **被谁用**：
  - `backend/routes/bundle.py` — 路由层，把上传的 zip 交给 `preflight()`，再用返回 token 交给 `confirm()`
- **依赖谁**：
  - `bundle/security.py` — `extract_zip_safely`、size limits
  - `bundle/id_field_map.py` / `id_schema.py` — ID rewrite 5 层防御 Layer 2 + Layer 1
  - `utils/db_factory.get_db_client` — 写库
  - `bundle_preflight_sessions` 表 — token 持久化（B5 修复）

## 设计决策

### Preflight + Confirm 两步走（PRD §8.5）

UX 要求用户先看预览再决定。preflight 解压 + 解析 + 检测冲突，return token；confirm 用 token 真正写库。

### Token 用 SQLite 表存，不用内存 dict

最初实现用 `_PREFLIGHT_STORE` Python 字典，发版重启 / 多 worker 都会丢。**已替换**为 `bundle_preflight_sessions` 表，6h TTL，inline cleanup。

详见 `.mindflow/project/references/scaling_assumptions.md` §1。

### work_dir 在持久路径下

`~/.nexusagent/bundle_preflight/<token>/`，docker compose 可以用 named volume 挂着。

> ⚠️ **SINGLE-WORKER ASSUMPTION**：work_dir 是本机 fs 路径。多 pod 部署 (k8s with ephemeral storage) 时，confirm 命中另一个 pod 会找不到 work_dir。修复方式：mount RWX volume 或上 S3。

### CPU 重活 → asyncio.to_thread

`extract_zip_safely`、`_extract_tar_safely` 都用 `asyncio.to_thread` 包装，避免阻塞 event loop（影响所有用户的 chat WS）。

### ID Rewrite 设计

5 层防御实现了 Layer 1 + 2 + 4：
- Layer 1 = `id_schema.ID_KINDS` regex 字典
- Layer 2 = `id_field_map.STRUCTURED_ID_FIELDS` 表-列登记
- Layer 4 = 自由文本 regex 兜底（`free_text_regex.sub`）

未做的 Layer 3（CI 反向检查）+ Layer 5（roundtrip test）记在议题 6 后续 TODO。

## Gotcha

- 重启后 confirm 报 "preflight working dir missing" = 用户中间发版了，让用户重传。
- ID rewrite 在自由文本里**有概率误命中**普通 hex 串（极低，可接受）。
- 整个 confirm 是非事务的（一个 insert 一个 insert），失败时 staging dir 清掉但已经入库的 row 不会回滚。这是 v1 简化，spec 阶段需要包 transaction。
