"""
@file_name: test_error_classifier.py
@author: Bin Liang
@date: 2026-07-29
@description: Rule-table classification: overflow markers win, class
names beat messages, unknown is conservative, chains are traversed.
"""

import pytest

from xyz_agent_context.agent_framework.nexus_power.contracts.errors import ErrorType
from xyz_agent_context.agent_framework.nexus_power._nexus_power_impl.session.error_classifier import (
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


def test_step_retry_backs_off_between_attempts():
    """Three retries fired within milliseconds are worse than none.

    A RATE_LIMIT answered by an immediate burst is just a bigger burst,
    and a SERVER_ERROR gets no time to pass (2026-07-29 review). The
    schedule is exponential and capped so a long turn riding out a
    provider hiccup never stalls for minutes.
    """
    from xyz_agent_context.agent_framework.nexus_power._nexus_power_impl.session.error_classifier import (
        StepRetry,
    )

    policy = StepRetry(max_attempts_per_step=5, base_delay_s=1.0, max_delay_s=15.0)
    assert [policy.delay_for(n) for n in (1, 2, 3, 4)] == [1.0, 2.0, 4.0, 8.0]
    assert policy.delay_for(10) == 15.0  # capped


@pytest.mark.asyncio
async def test_step_retry_stops_at_the_attempt_bound_without_sleeping():
    from xyz_agent_context.agent_framework.nexus_power._nexus_power_impl.session.error_classifier import (
        StepRetry,
    )
    from xyz_agent_context.agent_framework.nexus_power.contracts.errors import (
        ErrorType,
        LoopError,
    )

    policy = StepRetry(max_attempts_per_step=2, base_delay_s=0.0)
    retryable = LoopError(ErrorType.SERVER_ERROR, "boom", retryable=True)
    assert await policy.should_retry(retryable, 1) is True
    assert await policy.should_retry(retryable, 2) is False  # bound reached, no sleep

    fatal = LoopError(ErrorType.AUTHENTICATION_FAILED, "nope", retryable=False)
    assert await policy.should_retry(fatal, 1) is False
