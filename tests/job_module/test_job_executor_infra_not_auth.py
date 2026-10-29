"""
@file_name: test_job_executor_infra_not_auth.py
@date: 2026-10-29
@description: A platform-side executor failure must never pause a job as the
owner's credential or quota problem.

The infra error message carries raw detail; an executor that rejects the
platform's own API token answers HTTP 401, and the job pause logic used to
substring-match "401" / "unauthorized" in the message — pausing every cloud
job as PAUSED_NO_QUOTA with a "reconfigure your provider" notice the owner can
do nothing about. The structured error_type wins: infra_transient backs off
and retries instead. Genuine auth failures must still pause.
"""
from __future__ import annotations

from narranexus.platform.agent_framework.llm.failure import EXECUTOR_INFRA_USER_MESSAGE
from narranexus.platform.schema.runtime_message import (
    AUTH_EXPIRED_ERROR_TYPE,
    EXECUTOR_INFRA_ERROR_TYPE,
)
from narranexus_plugins.job_module.job_trigger import _is_auth_failure, _is_no_quota_failure

# The shape step 3 surfaces: user guidance + raw detail, here deliberately
# containing every marker the substring checks look for.
_INFRA_RESULT = {
    "success": False,
    "error_type": EXECUTOR_INFRA_ERROR_TYPE,
    "error": (
        next(iter(EXECUTOR_INFRA_USER_MESSAGE.values()))
        + " Detail: executor rejected the platform's API token: HTTP 401 Unauthorized"
        + " (insufficient balance? invalid api key?)"
    ),
}


def test_executor_infra_failure_is_not_an_auth_failure():
    assert _is_auth_failure(_INFRA_RESULT) is False


def test_executor_infra_failure_is_not_a_quota_failure():
    assert _is_no_quota_failure(_INFRA_RESULT) is False


def test_genuine_auth_failures_still_pause():
    assert _is_auth_failure({"success": False, "error_type": AUTH_EXPIRED_ERROR_TYPE, "error": "x"}) is True
    assert _is_auth_failure({"success": False, "error_type": "RuntimeError", "error": "HTTP 401 Unauthorized"}) is True
