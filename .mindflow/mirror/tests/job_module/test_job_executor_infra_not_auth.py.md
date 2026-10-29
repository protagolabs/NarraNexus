---
code_file: tests/job_module/test_job_executor_infra_not_auth.py
stub: false
last_verified: 2026-10-29
---

# tests/job_module/test_job_executor_infra_not_auth.py

钉住「平台侧执行器故障永不把 Job 暂停成 owner 的凭据 / 额度问题」：构造 step 3 实际产出的 infra 报错形状（用户指引 + 原始细节，细节里故意塞满 `401` / `Unauthorized` / `invalid api key` / `insufficient balance` 等子串标记），断言 `_is_auth_failure` 与 `_is_no_quota_failure` 都返回 False；反例断言真正的 `auth_expired`、以及非 infra 的 401 文本仍然暂停。来源：执行器 API token（2026-10-29）上线后，执行器拒绝平台 token 回 401，若被子串命中会把每个云端定时 Job 误暂停。见 [[job_trigger.py]]。
