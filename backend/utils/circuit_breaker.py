"""
Circuit Breaker Pattern Implementation for External Services

Prevents cascade failures by:
1. Tracking consecutive failures to external services
2. Opening the circuit (failing fast) after threshold failures
3. Allowing periodic retries to check if service recovered
4. Closing circuit when service is healthy again

Usage:
    from utils.circuit_breaker import CircuitBreaker

    openai_breaker = CircuitBreaker(
        name="openai",
        failure_threshold=5,
        recovery_timeout=60
    )

    @openai_breaker.protect
    async def call_openai(...):
        ...

    # Or manual usage:
    if openai_breaker.is_closed():
        try:
            result = await call_openai(...)
            openai_breaker.record_success()
        except Exception as e:
            openai_breaker.record_failure()
            raise
"""

import asyncio
import logging
import time
from enum import Enum
from typing import Callable, Any, Optional, Dict
from functools import wraps
from dataclasses import dataclass

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    CLOSED = "closed"      # Normal operation, requests pass through
    OPEN = "open"          # Circuit tripped, requests fail fast
    HALF_OPEN = "half_open"  # Testing if service recovered


@dataclass
class CircuitStats:
    """Statistics for circuit breaker monitoring"""
    total_calls: int = 0
    successful_calls: int = 0
    failed_calls: int = 0
    rejected_calls: int = 0
    last_failure_time: Optional[float] = None
    last_success_time: Optional[float] = None
    consecutive_failures: int = 0
    consecutive_successes: int = 0


class CircuitBreaker:
    """
    Circuit Breaker implementation for protecting external service calls.

    States:
    - CLOSED: Normal operation. Failures are tracked.
    - OPEN: Service is down. Requests fail immediately without calling service.
    - HALF_OPEN: Testing recovery. One request allowed through to test service.

    Transitions:
    - CLOSED -> OPEN: When consecutive failures reach threshold
    - OPEN -> HALF_OPEN: After recovery_timeout seconds
    - HALF_OPEN -> CLOSED: On successful test call
    - HALF_OPEN -> OPEN: On failed test call
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout: int = 60,
        success_threshold: int = 2,
        excluded_exceptions: tuple = ()
    ):
        """
        Initialize circuit breaker.

        Args:
            name: Identifier for this circuit (e.g., "openai", "supabase")
            failure_threshold: Number of consecutive failures to open circuit
            recovery_timeout: Seconds to wait before testing recovery
            success_threshold: Consecutive successes needed to close circuit from half-open
            excluded_exceptions: Exception types that don't count as failures
        """
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.success_threshold = success_threshold
        self.excluded_exceptions = excluded_exceptions

        self._state = CircuitState.CLOSED
        self._stats = CircuitStats()
        self._last_state_change = time.time()
        self._lock = asyncio.Lock()

        logger.info(f"🔌 Circuit breaker '{name}' initialized (threshold={failure_threshold}, timeout={recovery_timeout}s)")

    @property
    def state(self) -> CircuitState:
        """Current state of the circuit breaker"""
        return self._state

    @property
    def stats(self) -> CircuitStats:
        """Statistics for monitoring"""
        return self._stats

    def is_closed(self) -> bool:
        """Check if circuit is closed (normal operation)"""
        self._maybe_transition_to_half_open()
        return self._state == CircuitState.CLOSED

    def is_open(self) -> bool:
        """Check if circuit is open (failing fast)"""
        self._maybe_transition_to_half_open()
        return self._state == CircuitState.OPEN

    def is_half_open(self) -> bool:
        """Check if circuit is half-open (testing recovery)"""
        self._maybe_transition_to_half_open()
        return self._state == CircuitState.HALF_OPEN

    def _maybe_transition_to_half_open(self):
        """Check if we should transition from OPEN to HALF_OPEN"""
        if self._state == CircuitState.OPEN:
            time_since_open = time.time() - self._last_state_change
            if time_since_open >= self.recovery_timeout:
                self._state = CircuitState.HALF_OPEN
                self._last_state_change = time.time()
                logger.info(f"🔄 Circuit '{self.name}' transitioning to HALF_OPEN after {time_since_open:.1f}s")

    def record_success(self):
        """Record a successful call"""
        self._stats.total_calls += 1
        self._stats.successful_calls += 1
        self._stats.consecutive_successes += 1
        self._stats.consecutive_failures = 0
        self._stats.last_success_time = time.time()

        if self._state == CircuitState.HALF_OPEN:
            if self._stats.consecutive_successes >= self.success_threshold:
                self._state = CircuitState.CLOSED
                self._last_state_change = time.time()
                logger.info(f"✅ Circuit '{self.name}' CLOSED - service recovered")

    def record_failure(self, exception: Optional[Exception] = None):
        """Record a failed call"""
        # Don't count excluded exceptions as failures
        if exception and isinstance(exception, self.excluded_exceptions):
            logger.debug(f"Circuit '{self.name}': Excluded exception {type(exception).__name__}, not counting as failure")
            return

        self._stats.total_calls += 1
        self._stats.failed_calls += 1
        self._stats.consecutive_failures += 1
        self._stats.consecutive_successes = 0
        self._stats.last_failure_time = time.time()

        if self._state == CircuitState.CLOSED:
            if self._stats.consecutive_failures >= self.failure_threshold:
                self._state = CircuitState.OPEN
                self._last_state_change = time.time()
                logger.warning(
                    f"🔴 Circuit '{self.name}' OPENED after {self._stats.consecutive_failures} "
                    f"consecutive failures. Will retry after {self.recovery_timeout}s"
                )

        elif self._state == CircuitState.HALF_OPEN:
            # Failed during recovery test, go back to open
            self._state = CircuitState.OPEN
            self._last_state_change = time.time()
            logger.warning(f"🔴 Circuit '{self.name}' re-OPENED - recovery test failed")

    def record_rejection(self):
        """Record a rejected call (circuit was open)"""
        self._stats.total_calls += 1
        self._stats.rejected_calls += 1

    def protect(self, func: Callable) -> Callable:
        """
        Decorator to protect a function with this circuit breaker.

        Usage:
            @circuit_breaker.protect
            async def call_external_service():
                ...
        """
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            return await self._execute(func, *args, **kwargs)

        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            # For sync functions, run in executor
            return asyncio.get_event_loop().run_until_complete(
                self._execute(func, *args, **kwargs)
            )

        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper

    async def _execute(self, func: Callable, *args, **kwargs) -> Any:
        """Execute function with circuit breaker protection"""
        self._maybe_transition_to_half_open()

        if self._state == CircuitState.OPEN:
            self.record_rejection()
            raise CircuitBreakerOpenError(
                f"Circuit '{self.name}' is OPEN. Service unavailable. "
                f"Will retry after {self.recovery_timeout}s"
            )

        try:
            if asyncio.iscoroutinefunction(func):
                result = await func(*args, **kwargs)
            else:
                result = func(*args, **kwargs)
            self.record_success()
            return result
        except Exception as e:
            self.record_failure(e)
            raise

    def get_status(self) -> Dict[str, Any]:
        """Get circuit breaker status for monitoring"""
        self._maybe_transition_to_half_open()
        return {
            "name": self.name,
            "state": self._state.value,
            "stats": {
                "total_calls": self._stats.total_calls,
                "successful_calls": self._stats.successful_calls,
                "failed_calls": self._stats.failed_calls,
                "rejected_calls": self._stats.rejected_calls,
                "consecutive_failures": self._stats.consecutive_failures,
                "consecutive_successes": self._stats.consecutive_successes,
            },
            "config": {
                "failure_threshold": self.failure_threshold,
                "recovery_timeout": self.recovery_timeout,
                "success_threshold": self.success_threshold
            },
            "time_in_state": time.time() - self._last_state_change
        }

    def reset(self):
        """Manually reset circuit to closed state"""
        self._state = CircuitState.CLOSED
        self._stats = CircuitStats()
        self._last_state_change = time.time()
        logger.info(f"🔄 Circuit '{self.name}' manually reset to CLOSED")


class CircuitBreakerOpenError(Exception):
    """Raised when circuit is open and call is rejected"""
    pass


# =============================================================================
# PRE-CONFIGURED CIRCUIT BREAKERS FOR COMMON SERVICES
# =============================================================================

# OpenAI API circuit breaker
openai_circuit = CircuitBreaker(
    name="openai",
    failure_threshold=5,
    recovery_timeout=60,
    success_threshold=2
)

# Supabase/Database circuit breaker
supabase_circuit = CircuitBreaker(
    name="supabase",
    failure_threshold=3,
    recovery_timeout=30,
    success_threshold=1
)

# External webhook/API circuit breaker
external_api_circuit = CircuitBreaker(
    name="external_api",
    failure_threshold=5,
    recovery_timeout=120,
    success_threshold=2
)


def get_all_circuit_statuses() -> Dict[str, Any]:
    """Get status of all registered circuit breakers"""
    return {
        "openai": openai_circuit.get_status(),
        "supabase": supabase_circuit.get_status(),
        "external_api": external_api_circuit.get_status()
    }
