"""
Request Deduplication Utility

Prevents duplicate API requests by tracking in-flight requests and
returning cached results for identical requests within a time window.

Features:
- In-flight request coalescing (same request waits for first to complete)
- Result caching with configurable TTL
- Request fingerprinting based on method, path, and body
- Automatic cleanup of stale entries
- Thread-safe with asyncio locks

Usage:
    from utils.request_deduplication import request_deduplicator

    @request_deduplicator.deduplicate()
    async def my_api_handler(request_data):
        # Expensive operation
        return result

    # Or use as context manager
    async with request_deduplicator.deduplicate_context(fingerprint):
        result = await expensive_operation()
"""

import asyncio
import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional, TypeVar, Generic
from functools import wraps
from enum import Enum
import os

logger = logging.getLogger(__name__)

T = TypeVar('T')


class RequestState(Enum):
    """State of a tracked request"""
    IN_FLIGHT = "in_flight"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class CachedResult(Generic[T]):
    """Cached result with metadata"""
    value: T
    timestamp: float
    state: RequestState
    error: Optional[Exception] = None
    hit_count: int = 0


@dataclass
class InFlightRequest:
    """Tracking data for in-flight request"""
    fingerprint: str
    start_time: float
    event: asyncio.Event = field(default_factory=asyncio.Event)
    result: Optional[Any] = None
    error: Optional[Exception] = None


class RequestDeduplicator:
    """
    Request deduplication manager.

    Tracks in-flight requests and caches results to prevent duplicate
    processing of identical requests.
    """

    def __init__(
        self,
        cache_ttl_seconds: float = 5.0,
        max_cache_size: int = 1000,
        cleanup_interval_seconds: float = 60.0
    ):
        """
        Initialize deduplicator.

        Args:
            cache_ttl_seconds: How long to cache results (default 5s)
            max_cache_size: Maximum number of cached results
            cleanup_interval_seconds: How often to clean up stale entries
        """
        self.cache_ttl_seconds = float(os.getenv("DEDUP_CACHE_TTL_SECONDS", cache_ttl_seconds))
        self.max_cache_size = int(os.getenv("DEDUP_MAX_CACHE_SIZE", max_cache_size))
        self.cleanup_interval_seconds = cleanup_interval_seconds

        self._in_flight: Dict[str, InFlightRequest] = {}
        self._cache: Dict[str, CachedResult] = {}
        self._lock = asyncio.Lock()
        self._cleanup_task: Optional[asyncio.Task] = None
        self._stats = {
            "total_requests": 0,
            "cache_hits": 0,
            "coalesced_requests": 0,
            "cache_misses": 0
        }

    async def start(self):
        """Start background cleanup task"""
        if self._cleanup_task is None:
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())
            logger.info("Request deduplicator started")

    async def stop(self):
        """Stop background cleanup task"""
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            self._cleanup_task = None
            logger.info("Request deduplicator stopped")

    def _generate_fingerprint(
        self,
        method: str,
        path: str,
        body: Optional[Any] = None,
        extra_keys: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Generate unique fingerprint for a request.

        Args:
            method: HTTP method
            path: Request path
            body: Request body (will be JSON serialized)
            extra_keys: Additional keys to include in fingerprint

        Returns:
            SHA256 hash fingerprint
        """
        data = {
            "method": method.upper(),
            "path": path,
        }

        if body is not None:
            try:
                data["body"] = json.dumps(body, sort_keys=True, default=str)
            except (TypeError, ValueError):
                data["body"] = str(body)

        if extra_keys:
            data["extra"] = json.dumps(extra_keys, sort_keys=True, default=str)

        fingerprint_str = json.dumps(data, sort_keys=True)
        return hashlib.sha256(fingerprint_str.encode()).hexdigest()[:32]

    async def get_or_execute(
        self,
        fingerprint: str,
        executor: Callable[[], Any],
        cache_result: bool = True
    ) -> Any:
        """
        Get cached/in-flight result or execute the operation.

        Args:
            fingerprint: Request fingerprint
            executor: Async function to execute if needed
            cache_result: Whether to cache the result

        Returns:
            Result from cache, in-flight request, or new execution
        """
        self._stats["total_requests"] += 1

        async with self._lock:
            # Check cache first
            if fingerprint in self._cache:
                cached = self._cache[fingerprint]
                if time.time() - cached.timestamp < self.cache_ttl_seconds:
                    if cached.state == RequestState.COMPLETED:
                        cached.hit_count += 1
                        self._stats["cache_hits"] += 1
                        logger.debug(f"Cache hit for {fingerprint[:8]}...")
                        return cached.value
                    elif cached.state == RequestState.FAILED and cached.error:
                        raise cached.error

            # Check for in-flight request
            if fingerprint in self._in_flight:
                in_flight = self._in_flight[fingerprint]
                self._stats["coalesced_requests"] += 1
                logger.debug(f"Coalescing request {fingerprint[:8]}...")

        # Wait for in-flight request if exists
        if fingerprint in self._in_flight:
            in_flight = self._in_flight[fingerprint]
            await in_flight.event.wait()
            if in_flight.error:
                raise in_flight.error
            return in_flight.result

        # Execute new request
        self._stats["cache_misses"] += 1

        async with self._lock:
            # Create in-flight tracking
            in_flight = InFlightRequest(
                fingerprint=fingerprint,
                start_time=time.time()
            )
            self._in_flight[fingerprint] = in_flight

        try:
            # Execute the operation
            if asyncio.iscoroutinefunction(executor):
                result = await executor()
            else:
                result = executor()

            # Store result
            in_flight.result = result

            if cache_result:
                async with self._lock:
                    self._cache[fingerprint] = CachedResult(
                        value=result,
                        timestamp=time.time(),
                        state=RequestState.COMPLETED
                    )
                    self._enforce_cache_limit()

            return result

        except Exception as e:
            in_flight.error = e

            # Cache failures briefly to prevent rapid retries
            async with self._lock:
                self._cache[fingerprint] = CachedResult(
                    value=None,
                    timestamp=time.time(),
                    state=RequestState.FAILED,
                    error=e
                )

            raise

        finally:
            # Signal waiting requests and cleanup
            in_flight.event.set()
            async with self._lock:
                self._in_flight.pop(fingerprint, None)

    def deduplicate(
        self,
        method: str = "POST",
        path_extractor: Optional[Callable[[Any], str]] = None,
        body_extractor: Optional[Callable[[Any], Any]] = None,
        cache_result: bool = True
    ):
        """
        Decorator for deduplicating function calls.

        Args:
            method: HTTP method for fingerprinting
            path_extractor: Function to extract path from args
            body_extractor: Function to extract body from args
            cache_result: Whether to cache results

        Returns:
            Decorated function
        """
        def decorator(func: Callable) -> Callable:
            @wraps(func)
            async def wrapper(*args, **kwargs):
                # Generate fingerprint
                path = func.__name__
                if path_extractor:
                    try:
                        path = path_extractor(*args, **kwargs)
                    except Exception:
                        pass

                body = None
                if body_extractor:
                    try:
                        body = body_extractor(*args, **kwargs)
                    except Exception:
                        pass
                else:
                    # Use all args/kwargs as body by default
                    body = {"args": str(args), "kwargs": str(kwargs)}

                fingerprint = self._generate_fingerprint(method, path, body)

                async def executor():
                    return await func(*args, **kwargs)

                return await self.get_or_execute(fingerprint, executor, cache_result)

            return wrapper
        return decorator

    def _enforce_cache_limit(self):
        """Remove oldest entries if cache exceeds max size"""
        if len(self._cache) > self.max_cache_size:
            # Sort by timestamp and remove oldest
            sorted_entries = sorted(
                self._cache.items(),
                key=lambda x: x[1].timestamp
            )
            entries_to_remove = len(self._cache) - self.max_cache_size
            for fingerprint, _ in sorted_entries[:entries_to_remove]:
                del self._cache[fingerprint]

    async def _cleanup_loop(self):
        """Background task to clean up stale cache entries"""
        while True:
            try:
                await asyncio.sleep(self.cleanup_interval_seconds)
                await self._cleanup_stale_entries()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in cleanup loop: {e}")

    async def _cleanup_stale_entries(self):
        """Remove expired cache entries"""
        async with self._lock:
            current_time = time.time()
            expired = [
                fp for fp, cached in self._cache.items()
                if current_time - cached.timestamp > self.cache_ttl_seconds * 10
            ]
            for fingerprint in expired:
                del self._cache[fingerprint]

            if expired:
                logger.debug(f"Cleaned up {len(expired)} stale cache entries")

    def get_stats(self) -> Dict[str, Any]:
        """Get deduplication statistics"""
        total = self._stats["total_requests"]
        return {
            **self._stats,
            "cache_size": len(self._cache),
            "in_flight_count": len(self._in_flight),
            "hit_rate": self._stats["cache_hits"] / total if total > 0 else 0,
            "coalesce_rate": self._stats["coalesced_requests"] / total if total > 0 else 0
        }

    def clear_cache(self):
        """Clear all cached results"""
        self._cache.clear()
        logger.info("Deduplication cache cleared")


# Global instance
request_deduplicator = RequestDeduplicator()


# =============================================================================
# CONVENIENCE FUNCTIONS
# =============================================================================

def generate_request_fingerprint(
    method: str,
    path: str,
    body: Optional[Any] = None
) -> str:
    """Generate fingerprint for a request"""
    return request_deduplicator._generate_fingerprint(method, path, body)


async def deduplicated_call(
    fingerprint: str,
    executor: Callable[[], Any],
    cache_result: bool = True
) -> Any:
    """Execute a deduplicated call"""
    return await request_deduplicator.get_or_execute(fingerprint, executor, cache_result)
