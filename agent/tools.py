"""Tools the telemetry-triage agent can call.

For now this is a single local ``@tool`` that returns synthetic "service health"
metrics. In Phase 6 the same capability is exposed through an **AgentCore
Gateway** (a Lambda turned into an MCP tool) so the agent calls it remotely;
this local version keeps the agent runnable end-to-end before Gateway exists.
"""

from __future__ import annotations

import json
import random

from strands import tool

# A small fixed roster so runs are comparable across invocations.
_SERVICES = ("checkout", "search", "payments", "recommendations", "auth")


@tool
def get_service_health(service: str) -> str:
    """Return recent health metrics for a named service.

    Args:
        service: the service name to look up (e.g. "checkout").

    Returns:
        A JSON string with latency percentiles (ms), error rate and requests
        per second over the last 5 minutes.
    """
    known = service if service in _SERVICES else "checkout"
    base = 40 + 20 * _SERVICES.index(known)
    p50 = base + random.randint(-5, 5)
    p95 = p50 + random.randint(30, 120)
    p99 = p95 + random.randint(40, 200)
    payload = {
        "service": known,
        "window": "5m",
        "latency_ms": {"p50": p50, "p95": p95, "p99": p99},
        "error_rate": round(random.uniform(0.0, 0.06), 4),
        "requests_per_second": random.randint(20, 400),
    }
    return json.dumps(payload)
