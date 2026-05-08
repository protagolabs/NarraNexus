---
code_file: backend/routes/bundle.py
last_verified: 2026-05-08
stub: false
---

# bundle.py — REST routes for `.nxbundle` export / import

## 为什么存在

把 `bundle/builder.py` + `bundle/importer.py` + `bundle/skill_backup.py` 的能力暴露成 HTTP 端点供前端使用。Bundle 是用户级别的资源动作（不是 agent 级别），所以走 `/api/bundle/*` 命名空间，独立于 `/api/agents/`。

## 上下游关系

- **被谁用**：前端 `BundleExportPage.tsx` / `BundleImportPage.tsx`
- **依赖谁**：
  - `bundle.builder.build_bundle` / `bundle.importer.preflight` / `bundle.importer.confirm`
  - `repository.SkillArchiveRepository` — list / 手动上传归档
  - `backend.auth._user_id_for_request` — 拿 user_id（local 走 `get_local_user_id`，cloud 走 `request.state.user_id`）

## 设计决策

### 端点

| Method | Path | 用途 |
|---|---|---|
| POST | /export | 流式返回 `.nxbundle` zip |
| POST | /import/preflight | 上传 zip → 解析 + 检测冲突 → return token |
| POST | /import/confirm | 用 token 真正导入 |
| GET  | /skills/archives | 列当前 user 的归档清单 |
| POST | /skills/archives/upload | 手动补归档（github URL or zip 文件） |

### Streaming response

Export 通过 `StreamingResponse` 把 zip 文件分片写回，避免 backend 把整个文件读到内存。`iterfile()` 在生成器关闭时自动清理 tempdir。

### Preflight 跨进程稳定

参见 `bundle/importer.py` 的 mirror md（B5 修复 + scaling_assumptions §1）。

> ⚠️ **SINGLE-WORKER ASSUMPTION 链路**：preflight 落到当前 process 的本机 fs；confirm 必须命中同一台机器（看到同一份 work_dir）。多 pod 时要么共享 volume 要么改对象存储。

## Gotcha

- `import_preflight` 的 `tmpdir` cleanup 在 `finally` 里——这只清上传的 zip 文件，不清 importer 创建的 work_dir（work_dir 在 `~/.nexusagent/bundle_preflight/`）。
- `import_confirm` 失败时 work_dir 不会立即清，靠 6h TTL cleanup 兜底。
- `upload_archive` 的 sha256 在 source_type=github 模式下填 `"pending"` —— 没真正下载 tarball。设计上 export 时再 lazy download，但 v1 没实现 lazy download，所以这种行不会被 bundle export 用到。修法：让 `upload_archive` 走 github 模式时立即调 `archive_github_tarball`。
