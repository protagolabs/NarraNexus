---
code_file: tests/backend/test_bundle_archive_path_safety.py
last_verified: 2026-08-17
stub: false
---

# test_bundle_archive_path_safety.py — SEC-07 的 HTTP 边界

## 为什么存在

[[test_skill_archive_path_safety.py]] 钉的是路径构造点本身；这个文件钉的是
**route 契约**——同一个洞在 HTTP 层的两面：

1. `POST /skills/archives/upload` 的 `skill_name`（写）
2. `POST /export` 的 `skills[].archive_path`（读）

`/skills/archives/upload` 之前**零覆盖**，这也是它能漏这么久的原因。

## 断言了什么

- 10 个穿越 payload 全部 **400**（不是 500 —— 让 `ValueError` 冒成 500 就
  是 #113 的 `BadZipFile` 误报模式重演），且 detail 里点名 `skill name`，
  用户看得懂改哪。
- **拒绝时不留痕**：比对 `tmp_path` 下的文件集合前后一致，且 fake repo 的
  `upsert` 调用列表为空。这条比"返回 400"更重要——校验必须发生在建目录 /
  写字节 / 写库之前。
- github 分支（不写盘）同样校验 `skill_name`。
- happy path 落在 `{root}/{user_id}/{name}.zip`，且 DB 记的是**解析后**的
  路径而不是原始客户端串。
- 超过 `max_upload_bytes` → 400，且不落盘。
- `/export`：请求体里带 `archive_path` / `manual_zip_path` 时，进 builder 的
  `skill_methods` 条目里**不含**这两个 key，且整条 repr 里不出现那个路径。

## Gotcha

- 用户身份固定成 `victim_neighbour`，穿越 payload 里另有
  `../victim_user/stolen`——命名上刻意体现"跨用户写"，别改成中性名字。
- `archives_root` fixture 同样 monkeypatch
  `skill_backup.SKILL_ARCHIVES_ROOT`；route 是 `from ... import
  archive_target` 按值导入的，但 `archive_target` 每次调用才读那个全局，
  所以 patch 生效。
- `/export` 用例里 `build_bundle` 的 stub 必须返回
  `{"warnings": [...], "manifest": {"integrity_sha256": ..., "info": [...]}}`
  —— route 拿这些拼 `X-Bundle-*` 响应头，少一个 key 就是 500，很容易误读
  成"路由挂了"。
