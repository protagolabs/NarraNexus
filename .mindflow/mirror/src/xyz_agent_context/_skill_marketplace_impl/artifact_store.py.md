---
code_file: src/xyz_agent_context/_skill_marketplace_impl/artifact_store.py
last_verified: 2026-07-21
stub: false
---

# artifact_store.py

Object-storage abstraction for marketplace skill artifacts. **boto3 exists
ONLY in this file** (spec §4, iron rule #9) — swapping S3 for R2/OSS later
touches nothing else. `SKILL_S3_BUCKET` env selects S3 (cloud); otherwise a
filesystem `LocalArtifactStore` under `<base>/../marketplace_store` serves
dev/tests/single-host. The S3 client is created lazily so importing never
needs AWS credentials. LocalArtifactStore path-joins are guarded against
key escapes (`..`).
