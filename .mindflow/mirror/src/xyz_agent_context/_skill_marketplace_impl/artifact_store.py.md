---
code_file: src/xyz_agent_context/_skill_marketplace_impl/artifact_store.py
last_verified: 2026-07-21
stub: false
---

## 2026-07-21 — get_template_store()(Team Marketplace)

新增 team 专用 store 选择器,与 skill 物理分开:S3 走 TEMPLATE_S3_BUCKET
(回落 SKILL_S3_BUCKET)+ TEMPLATE_S3_PREFIX(默认 narranexus-teams);本地
落 marketplace_store/teams/。boto3 仍只此一文件。


# artifact_store.py

Object-storage abstraction for marketplace skill artifacts. **boto3 exists
ONLY in this file** (spec §4, iron rule #9) — swapping S3 for R2/OSS later
touches nothing else. `SKILL_S3_BUCKET` env selects S3 (cloud); otherwise a
filesystem `LocalArtifactStore` under `<base>/../marketplace_store` serves
dev/tests/single-host. The S3 client is created lazily so importing never
needs AWS credentials. LocalArtifactStore path-joins are guarded against
key escapes (`..`).
