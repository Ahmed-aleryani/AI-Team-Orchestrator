"""
Comprehensive Health Check and Readiness Endpoints

Implements Kubernetes-style health probes:
- /api/health - Overall health (quick check)
- /api/health/live - Liveness probe (is the service running?)
- /api/health/ready - Readiness probe (is the service ready to accept traffic?)
- /api/health/detailed - Detailed health with all component statuses
"""

import os
import logging
import asyncio
from datetime import datetime, timedelta
from typing import Dict, Any, Optional
from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/health", tags=["health"])

# Health check cache to prevent excessive database queries
_health_cache: Dict[str, Any] = {}
_cache_ttl_seconds = int(os.getenv("HEALTH_CHECK_CACHE_TTL", "10"))


async def _check_database_health() -> Dict[str, Any]:
    """Check database connectivity and basic operations"""
    start_time = datetime.now()
    try:
        from database import get_supabase_client
        supabase = get_supabase_client()

        if not supabase:
            return {
                "status": "unhealthy",
                "error": "Supabase client not initialized",
                "latency_ms": 0
            }

        # Execute a simple query to verify connectivity
        result = supabase.table("workspaces").select("id").limit(1).execute()

        latency = (datetime.now() - start_time).total_seconds() * 1000

        return {
            "status": "healthy",
            "latency_ms": round(latency, 2),
            "connection": "active"
        }

    except Exception as e:
        latency = (datetime.now() - start_time).total_seconds() * 1000
        logger.error(f"Database health check failed: {e}")
        return {
            "status": "unhealthy",
            "error": str(e),
            "latency_ms": round(latency, 2)
        }


async def _check_openai_health() -> Dict[str, Any]:
    """Check OpenAI API connectivity"""
    start_time = datetime.now()
    try:
        # Check if API key is configured
        api_key = os.getenv("OPENAI_API_KEY", "")
        if not api_key or api_key.startswith("sk-your"):
            return {
                "status": "degraded",
                "message": "API key not configured or using placeholder",
                "latency_ms": 0
            }

        # Import quota tracker to check status
        from services.openai_quota_tracker import openai_quota_tracker

        quota_status = await openai_quota_tracker.get_usage_status()
        latency = (datetime.now() - start_time).total_seconds() * 1000

        # Determine health based on quota remaining
        remaining_pct = quota_status.get("percentage_remaining", 100)

        if remaining_pct < 5:
            status = "critical"
        elif remaining_pct < 20:
            status = "degraded"
        else:
            status = "healthy"

        return {
            "status": status,
            "latency_ms": round(latency, 2),
            "quota_remaining_pct": remaining_pct,
            "budget_used_usd": quota_status.get("total_cost_usd", 0)
        }

    except Exception as e:
        latency = (datetime.now() - start_time).total_seconds() * 1000
        logger.debug(f"OpenAI health check: {e}")
        return {
            "status": "unknown",
            "message": "Unable to verify OpenAI status",
            "latency_ms": round(latency, 2)
        }


async def _check_task_executor_health() -> Dict[str, Any]:
    """Check task executor status"""
    try:
        from executor import get_executor_status

        status = get_executor_status()

        is_running = status.get("is_running", False)
        pending_tasks = status.get("pending_tasks", 0)

        if not is_running:
            return {
                "status": "unhealthy",
                "message": "Task executor is not running",
                "pending_tasks": pending_tasks
            }

        # Warn if task backlog is high
        if pending_tasks > 100:
            return {
                "status": "degraded",
                "message": f"High task backlog: {pending_tasks} pending",
                "pending_tasks": pending_tasks
            }

        return {
            "status": "healthy",
            "pending_tasks": pending_tasks,
            "is_running": True
        }

    except ImportError:
        return {
            "status": "unknown",
            "message": "Task executor module not available"
        }
    except Exception as e:
        logger.debug(f"Task executor health check: {e}")
        return {
            "status": "unknown",
            "message": str(e)
        }


async def _check_circuit_breakers() -> Dict[str, Any]:
    """Check circuit breaker status"""
    try:
        from utils.circuit_breaker import openai_circuit, supabase_circuit, get_all_circuit_statuses

        all_status = get_all_circuit_statuses()

        # Check if any breakers are open
        open_breakers = [name for name, status in all_status.items()
                        if status.get("state") == "open"]

        if open_breakers:
            return {
                "status": "degraded",
                "message": f"Open circuit breakers: {', '.join(open_breakers)}",
                "breakers": all_status
            }

        return {
            "status": "healthy",
            "breakers": all_status
        }

    except ImportError:
        return {
            "status": "unknown",
            "message": "Circuit breaker module not available"
        }
    except Exception as e:
        return {
            "status": "unknown",
            "message": str(e)
        }


def _determine_overall_status(checks: Dict[str, Dict[str, Any]]) -> str:
    """Determine overall health status from individual checks"""
    statuses = [check.get("status", "unknown") for check in checks.values()]

    if "unhealthy" in statuses:
        return "unhealthy"
    elif "critical" in statuses:
        return "critical"
    elif "degraded" in statuses:
        return "degraded"
    elif all(s == "healthy" for s in statuses):
        return "healthy"
    else:
        return "unknown"


@router.get("")
@router.get("/")
async def health_check_quick():
    """
    Quick health check endpoint (liveness + basic readiness)
    Returns 200 if service is operational
    """
    return {
        "status": "healthy",
        "message": "AI Team Orchestrator is operational",
        "timestamp": datetime.utcnow().isoformat(),
        "version": os.getenv("APP_VERSION", "0.1.0")
    }


@router.get("/live")
async def liveness_probe():
    """
    Kubernetes liveness probe
    Returns 200 if the service is alive (not stuck/deadlocked)
    """
    return Response(
        content='{"status": "alive"}',
        media_type="application/json",
        status_code=200
    )


@router.get("/ready")
async def readiness_probe():
    """
    Kubernetes readiness probe
    Returns 200 if the service is ready to accept traffic
    Checks critical dependencies (database)
    """
    # Check database connectivity
    db_health = await _check_database_health()

    if db_health.get("status") == "unhealthy":
        return JSONResponse(
            content={
                "status": "not_ready",
                "reason": "database_unavailable",
                "details": db_health
            },
            status_code=503
        )

    return {
        "status": "ready",
        "timestamp": datetime.utcnow().isoformat()
    }


@router.get("/detailed")
async def detailed_health_check():
    """
    Comprehensive health check with all component statuses
    Useful for debugging and monitoring dashboards
    """
    global _health_cache

    # Check cache
    cache_key = "detailed_health"
    if cache_key in _health_cache:
        cached = _health_cache[cache_key]
        if datetime.utcnow() - cached["timestamp"] < timedelta(seconds=_cache_ttl_seconds):
            return cached["data"]

    # Run all health checks in parallel
    checks = await asyncio.gather(
        _check_database_health(),
        _check_openai_health(),
        _check_task_executor_health(),
        _check_circuit_breakers(),
        return_exceptions=True
    )

    # Process results
    health_data = {
        "database": checks[0] if not isinstance(checks[0], Exception) else {"status": "error", "error": str(checks[0])},
        "openai": checks[1] if not isinstance(checks[1], Exception) else {"status": "error", "error": str(checks[1])},
        "task_executor": checks[2] if not isinstance(checks[2], Exception) else {"status": "error", "error": str(checks[2])},
        "circuit_breakers": checks[3] if not isinstance(checks[3], Exception) else {"status": "error", "error": str(checks[3])}
    }

    overall_status = _determine_overall_status(health_data)

    result = {
        "status": overall_status,
        "timestamp": datetime.utcnow().isoformat(),
        "version": os.getenv("APP_VERSION", "0.1.0"),
        "environment": os.getenv("ENVIRONMENT", "development"),
        "components": health_data,
        "uptime_info": {
            "health_cache_ttl_seconds": _cache_ttl_seconds
        }
    }

    # Update cache
    _health_cache[cache_key] = {
        "timestamp": datetime.utcnow(),
        "data": result
    }

    # Return appropriate status code
    if overall_status == "unhealthy":
        return JSONResponse(content=result, status_code=503)

    return result


@router.get("/startup")
async def startup_probe():
    """
    Kubernetes startup probe
    Returns 200 once the application has finished starting up
    """
    # Check if critical services are initialized
    try:
        from database import get_supabase_client
        supabase = get_supabase_client()

        if supabase is None:
            return JSONResponse(
                content={"status": "starting", "message": "Database not yet initialized"},
                status_code=503
            )

        return {
            "status": "started",
            "timestamp": datetime.utcnow().isoformat()
        }

    except Exception as e:
        return JSONResponse(
            content={"status": "starting", "error": str(e)},
            status_code=503
        )
