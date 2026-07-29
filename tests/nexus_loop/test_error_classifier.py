"""
@file_name: test_error_classifier.py
@author: Bin Liang
@date: 2026-07-29
@description: Rule-table classification: overflow markers win, class
names beat messages, unknown is conservative, chains are traversed.
"""

import pytest

from xyz_agent_context.agent_framework.nexus_loop.contracts.errors import ErrorType
from xyz_agent_context.agent_framework.nexus_loop._nexus_loop_impl.session.error_classifier import (
    DefaultErrorClassifier,
    NoRetry,
    StepRetry,
)


class ContextWindowExceededError(Exception):
    pass


class RateLimitError(Exception):
    pass


@pytest.fixture()
def classifier():
    return DefaultErrorClassifier()


@pytest.mark.parametrize(
    "exc, expected, retryable",
    [
        (ContextWindowExceededError("too big"), ErrorType.CONTEXT_OVERFLOW, True),
        (RateLimitError("slow down"), ErrorType.RATE_LIMIT, True),
        (Exception("This model's maximum context length is 32768 tokens"),
         ErrorType.CONTEXT_OVERFLOW, True),
        (Exception("Incorrect API key provided"), ErrorType.AUTHENTICATION_FAILED, False),
        (Exception("Your credit balance is too low"), ErrorType.BILLING_ERROR, False),
        (Exception("mystery failure"), ErrorType.UNKNOWN, False),
    ],
)
def test_classification_table(classifier, exc, expected, retryable):
    err = classifier.classify(exc)
    assert err.error_type is expected
    assert err.retryable is retryable
    assert err.provider_raw is exc


def test_exception_chain_is_traversed(classifier):
    try:
        try:
            raise RateLimitError("upstream 429")
        except RateLimitError as inner:
            raise RuntimeError("wrapped") from inner
    except RuntimeError as outer:
        err = classifier.classify(outer)
    assert err.error_type is ErrorType.RATE_LIMIT


def test_legacy_mapping_never_leaks_new_vocabulary(classifier):
    err = classifier.classify(ContextWindowExceededError("x"))
    assert err.legacy_error_type() == "invalid_request"
    err2 = classifier.classify(Exception("rate limit hit"))
    assert err2.legacy_error_type() == "rate_limit"


@pytest.mark.asyncio
async def test_retry_policies(classifier):
    overflow = classifier.classify(ContextWindowExceededError("x"))
    assert await NoRetry().should_retry(overflow, attempt=0) is False
    retry = StepRetry(max_attempts_per_step=2)
    assert await retry.should_retry(overflow, attempt=0) is True
    assert await retry.should_retry(overflow, attempt=2) is False
