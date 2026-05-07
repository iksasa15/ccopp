"""
Unit tests for resilience primitives.
"""

import asyncio
import pytest

from resilience.primitives import (
    Bulkhead, CircuitBreaker, CircuitBreakerConfig, CircuitOpenError,
    CircuitState, RetryConfig, retry_with_backoff, with_timeout
)


# ============================================================
# Circuit Breaker
# ============================================================

class TestCircuitBreaker:
    @pytest.mark.asyncio
    async def test_starts_closed(self):
        cb = CircuitBreaker("test")
        assert cb.state == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_successful_calls_keep_closed(self):
        cb = CircuitBreaker("test")
        
        async def ok():
            return "success"
        
        for _ in range(10):
            result = await cb.call(ok)
            assert result == "success"
        
        assert cb.state == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_opens_after_threshold_failures(self):
        config = CircuitBreakerConfig(failure_threshold=3)
        cb = CircuitBreaker("test", config)
        
        async def fail():
            raise RuntimeError("boom")
        
        # First 3 failures should propagate
        for _ in range(3):
            with pytest.raises(RuntimeError):
                await cb.call(fail)
        
        assert cb.state == CircuitState.OPEN
        
        # 4th call should fail fast
        with pytest.raises(CircuitOpenError):
            await cb.call(fail)

    @pytest.mark.asyncio
    async def test_recovery_via_half_open(self):
        config = CircuitBreakerConfig(
            failure_threshold=2,
            success_threshold=2,
            timeout_seconds=0.1,
        )
        cb = CircuitBreaker("test", config)
        
        async def fail():
            raise RuntimeError("boom")
        
        async def ok():
            return "ok"
        
        # Open the circuit
        for _ in range(2):
            with pytest.raises(RuntimeError):
                await cb.call(fail)
        assert cb.state == CircuitState.OPEN
        
        # Wait for half-open transition
        await asyncio.sleep(0.15)
        
        # Two successes should close it
        await cb.call(ok)
        await cb.call(ok)
        assert cb.state == CircuitState.CLOSED


# ============================================================
# Retry
# ============================================================

class TestRetry:
    @pytest.mark.asyncio
    async def test_succeeds_on_first_attempt(self):
        attempts = 0
        
        async def func():
            nonlocal attempts
            attempts += 1
            return "success"
        
        result = await retry_with_backoff(
            func, RetryConfig(max_attempts=3, base_delay_seconds=0.01)
        )
        assert result == "success"
        assert attempts == 1

    @pytest.mark.asyncio
    async def test_succeeds_after_retries(self):
        attempts = 0
        
        async def flaky():
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise RuntimeError("transient")
            return "success"
        
        result = await retry_with_backoff(
            flaky, RetryConfig(max_attempts=5, base_delay_seconds=0.01)
        )
        assert result == "success"
        assert attempts == 3

    @pytest.mark.asyncio
    async def test_exhausts_retries(self):
        async def always_fail():
            raise RuntimeError("permanent")
        
        with pytest.raises(RuntimeError, match="permanent"):
            await retry_with_backoff(
                always_fail, RetryConfig(max_attempts=2, base_delay_seconds=0.01)
            )

    @pytest.mark.asyncio
    async def test_does_not_retry_excluded_exceptions(self):
        attempts = 0
        
        async def func():
            nonlocal attempts
            attempts += 1
            raise ValueError("don't retry")
        
        config = RetryConfig(
            max_attempts=5,
            base_delay_seconds=0.01,
            do_not_retry_on=(ValueError,),
        )
        
        with pytest.raises(ValueError):
            await retry_with_backoff(func, config)
        
        assert attempts == 1  # no retries


# ============================================================
# Timeout
# ============================================================

class TestTimeout:
    @pytest.mark.asyncio
    async def test_completes_in_time(self):
        async def quick():
            await asyncio.sleep(0.01)
            return "done"
        
        result = await with_timeout(quick(), timeout_seconds=1.0)
        assert result == "done"

    @pytest.mark.asyncio
    async def test_times_out(self):
        async def slow():
            await asyncio.sleep(2.0)
            return "done"
        
        with pytest.raises(TimeoutError):
            await with_timeout(slow(), timeout_seconds=0.1)


# ============================================================
# Bulkhead
# ============================================================

class TestBulkhead:
    @pytest.mark.asyncio
    async def test_limits_concurrency(self):
        bulkhead = Bulkhead("test", max_concurrent=2)
        in_flight_max = 0
        in_flight = 0
        
        async def task():
            nonlocal in_flight, in_flight_max
            async with bulkhead:
                in_flight += 1
                in_flight_max = max(in_flight_max, in_flight)
                await asyncio.sleep(0.05)
                in_flight -= 1
        
        await asyncio.gather(*[task() for _ in range(10)])
        
        assert in_flight_max <= 2
