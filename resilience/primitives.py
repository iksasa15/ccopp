"""
Resilience Primitives — Circuit Breaker, Retry, Timeout.

Why this exists:
  Local LLMs crash. Ollama can be unresponsive. WMI queries hang.
  Without these primitives, ONE failure cascades into the WHOLE system freezing.

Patterns implemented:
  - Circuit Breaker: stop calling a failing service, give it time to recover
  - Exponential Backoff Retry: retry transient failures intelligently
  - Timeout: never wait forever
  - Bulkhead: isolate failures to one component
"""

import asyncio
import time
from collections.abc import Callable, Awaitable
from dataclasses import dataclass, field
from enum import Enum
from functools import wraps
from typing import Any, TypeVar

from loguru import logger

T = TypeVar("T")


# ============================================================
# Circuit Breaker
# ============================================================

class CircuitState(str, Enum):
    CLOSED = "closed"      # Normal — requests flow through
    OPEN = "open"          # Failing — requests fail fast
    HALF_OPEN = "half_open"  # Testing recovery


@dataclass
class CircuitBreakerConfig:
    failure_threshold: int = 5          # failures before opening
    success_threshold: int = 2           # successes needed in half-open to close
    timeout_seconds: float = 30.0        # how long to stay open
    expected_exceptions: tuple = (Exception,)


class CircuitBreaker:
    """
    State machine that protects callers from a failing dependency.
    
    Lifecycle:
        CLOSED -- N failures --> OPEN
        OPEN   -- T seconds  --> HALF_OPEN
        HALF_OPEN -- success K times --> CLOSED
        HALF_OPEN -- any failure --> OPEN
    """

    def __init__(self, name: str, config: CircuitBreakerConfig | None = None):
        self.name = name
        self.config = config or CircuitBreakerConfig()
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._opened_at: float | None = None
        self._lock = asyncio.Lock()

    @property
    def state(self) -> CircuitState:
        return self._state

    @property
    def is_available(self) -> bool:
        return self._state != CircuitState.OPEN

    async def call(self, func: Callable[..., Awaitable[T]], *args, **kwargs) -> T:
        """Execute func through the breaker. Raises CircuitOpenError if blocked."""
        async with self._lock:
            self._maybe_transition_to_half_open()

            if self._state == CircuitState.OPEN:
                raise CircuitOpenError(
                    f"Circuit '{self.name}' is OPEN. "
                    f"Will retry after {self.config.timeout_seconds}s."
                )

        try:
            result = await func(*args, **kwargs)
        except self.config.expected_exceptions as e:
            await self._on_failure(e)
            raise
        else:
            await self._on_success()
            return result

    def _maybe_transition_to_half_open(self) -> None:
        """If we've been OPEN long enough, try recovery."""
        if (
            self._state == CircuitState.OPEN
            and self._opened_at is not None
            and time.monotonic() - self._opened_at >= self.config.timeout_seconds
        ):
            logger.info(f"Circuit '{self.name}': OPEN -> HALF_OPEN (testing recovery)")
            self._state = CircuitState.HALF_OPEN
            self._success_count = 0

    async def _on_success(self) -> None:
        async with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._success_count += 1
                if self._success_count >= self.config.success_threshold:
                    logger.info(f"Circuit '{self.name}': HALF_OPEN -> CLOSED (recovered)")
                    self._state = CircuitState.CLOSED
                    self._failure_count = 0
            elif self._state == CircuitState.CLOSED:
                self._failure_count = 0  # reset on success

    async def _on_failure(self, exc: Exception) -> None:
        async with self._lock:
            self._failure_count += 1
            logger.warning(
                f"Circuit '{self.name}': failure {self._failure_count}/"
                f"{self.config.failure_threshold} ({type(exc).__name__})"
            )

            if self._state == CircuitState.HALF_OPEN:
                logger.warning(f"Circuit '{self.name}': HALF_OPEN -> OPEN (still failing)")
                self._state = CircuitState.OPEN
                self._opened_at = time.monotonic()
            elif (
                self._state == CircuitState.CLOSED
                and self._failure_count >= self.config.failure_threshold
            ):
                logger.error(f"Circuit '{self.name}': CLOSED -> OPEN (threshold breached)")
                self._state = CircuitState.OPEN
                self._opened_at = time.monotonic()


class CircuitOpenError(Exception):
    """Raised when calls are blocked by an open circuit."""


# ============================================================
# Retry with Exponential Backoff
# ============================================================

@dataclass
class RetryConfig:
    max_attempts: int = 3
    base_delay_seconds: float = 1.0
    max_delay_seconds: float = 30.0
    exponential_base: float = 2.0
    jitter: bool = True
    retry_on: tuple = (Exception,)
    do_not_retry_on: tuple = ()


async def retry_with_backoff(
    func: Callable[..., Awaitable[T]],
    config: RetryConfig | None = None,
    *args,
    **kwargs,
) -> T:
    """Retry func with exponential backoff. Returns last result or raises last exception."""
    import random

    cfg = config or RetryConfig()
    last_exception: Exception | None = None

    for attempt in range(1, cfg.max_attempts + 1):
        try:
            return await func(*args, **kwargs)
        except cfg.do_not_retry_on:
            raise
        except cfg.retry_on as e:
            last_exception = e
            if attempt == cfg.max_attempts:
                break

            delay = min(
                cfg.base_delay_seconds * (cfg.exponential_base ** (attempt - 1)),
                cfg.max_delay_seconds,
            )
            if cfg.jitter:
                delay *= 0.5 + random.random()

            logger.warning(
                f"Attempt {attempt}/{cfg.max_attempts} failed: {type(e).__name__}: {e}. "
                f"Retrying in {delay:.2f}s..."
            )
            await asyncio.sleep(delay)

    assert last_exception is not None
    raise last_exception


# ============================================================
# Timeout Wrapper
# ============================================================

async def with_timeout(
    coro: Awaitable[T],
    timeout_seconds: float,
    timeout_message: str = "Operation timed out",
) -> T:
    """Wrap a coroutine with a hard timeout."""
    try:
        return await asyncio.wait_for(coro, timeout=timeout_seconds)
    except asyncio.TimeoutError as e:
        logger.error(f"Timeout ({timeout_seconds}s): {timeout_message}")
        raise TimeoutError(timeout_message) from e


# ============================================================
# Decorator combining all three (the most common case)
# ============================================================

def resilient(
    circuit_name: str,
    timeout_seconds: float = 30.0,
    max_retries: int = 3,
    circuit_config: CircuitBreakerConfig | None = None,
    retry_config: RetryConfig | None = None,
):
    """
    Decorator that adds circuit-breaker + retry + timeout to an async function.
    
    Usage:
        @resilient("ollama", timeout_seconds=60)
        async def call_llm(prompt: str): ...
    """
    breaker = CircuitBreaker(circuit_name, circuit_config)
    rcfg = retry_config or RetryConfig(max_attempts=max_retries)

    def decorator(func: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        @wraps(func)
        async def wrapper(*args, **kwargs) -> T:
            async def call():
                return await with_timeout(
                    func(*args, **kwargs),
                    timeout_seconds=timeout_seconds,
                    timeout_message=f"{func.__name__} exceeded {timeout_seconds}s",
                )

            return await breaker.call(
                lambda: retry_with_backoff(call, rcfg)
            )

        wrapper.circuit_breaker = breaker  # type: ignore
        return wrapper

    return decorator


# ============================================================
# Bulkhead — limit concurrent calls (resource isolation)
# ============================================================

class Bulkhead:
    """Semaphore-based concurrency limiter. Prevents one slow agent from starving others."""

    def __init__(self, name: str, max_concurrent: int = 5):
        self.name = name
        self._sem = asyncio.Semaphore(max_concurrent)
        self._max = max_concurrent
        self._in_flight = 0

    async def __aenter__(self):
        await self._sem.acquire()
        self._in_flight += 1
        return self

    async def __aexit__(self, *args):
        self._in_flight -= 1
        self._sem.release()

    @property
    def utilization(self) -> float:
        return self._in_flight / self._max
