"""AgentCore Runtime entrypoint for the AgentCore -> Bronto observability demo.

A Strands "telemetry triage" agent (Amazon Nova Pro) that, each invocation:
  1. pulls synthetic service-health metrics via a tool (local now; an AgentCore
     Gateway MCP tool in Phase 6),
  2. analyses them in the AgentCore Code Interpreter sandbox (Python), and
  3. recalls prior runs from AgentCore Memory to spot trends.

The multi-step reasoning loop + tool calls produce deep, nested GenAI traces,
which ship via OTLP to the collector and on to Bronto.

AgentCore Memory is wired via the first-party Strands session manager when
``AGENTCORE_MEMORY_ID`` is set: conversation turns are persisted/restored per
session (cross-invocation continuity), and the semantic strategy
(``AGENTCORE_SEMANTIC_STRATEGY_ID``) supplies long-term recall across sessions.
"""

from __future__ import annotations

# Telemetry must be configured before Strands / instrumented libraries are used.
from telemetry import get_meter, setup_telemetry

setup_telemetry()

import logging  # noqa: E402
import os  # noqa: E402

from bedrock_agentcore.runtime import BedrockAgentCoreApp  # noqa: E402
from strands import Agent  # noqa: E402
from strands.models import BedrockModel  # noqa: E402
from strands_tools.code_interpreter import AgentCoreCodeInterpreter  # noqa: E402

from tools import get_service_health  # noqa: E402

log = logging.getLogger(__name__)

REGION = os.getenv("AWS_REGION", "eu-west-1")
MODEL_ID = os.getenv("BEDROCK_MODEL_ID", "eu.amazon.nova-pro-v1:0")

# AgentCore Memory (optional — agent runs statelessly if unset).
MEMORY_ID = os.getenv("AGENTCORE_MEMORY_ID")
SEMANTIC_STRATEGY_ID = os.getenv("AGENTCORE_SEMANTIC_STRATEGY_ID")
ACTOR_ID = os.getenv("AGENT_ACTOR_ID", "telemetry-triage")
# A stable default session gives cross-invocation continuity for the demo;
# callers may override per request via the payload.
DEFAULT_SESSION_ID = os.getenv("AGENT_SESSION_ID", "triage-main")

# Custom demo metrics (the GenAI spans already carry token usage; these give
# simple named series that are easy to chart in Bronto).
_meter = get_meter()
_invocation_counter = _meter.create_counter(
    "demo.agent.invocations",
    unit="{call}",
    description="Agent invocations, split by outcome.",
)
_tool_call_counter = _meter.create_counter(
    "demo.agent.tool_calls",
    unit="{call}",
    description="Tool calls the agent made, split by tool.",
)

SYSTEM_PROMPT = """You are a site-reliability triage assistant.
When asked about a service, call get_service_health to fetch its recent metrics,
then use the code interpreter to compute and sanity-check any statistics (e.g.
how far p99 sits above p50, whether the error rate breaches a 2% SLO). If you
recall prior triage runs for this service, briefly note the trend (improving /
worsening / steady) versus what you saw before. If a time tool is available
(e.g. get_time), call it to timestamp your assessment in UTC. Finish with a
short, concrete assessment: healthy / degraded / unhealthy, the single most
important number, and one recommended action."""

app = BedrockAgentCoreApp()

# The Code Interpreter sandbox is created once and shared across invocations.
_code_interpreter = AgentCoreCodeInterpreter(region=REGION)
_TOOLS = [get_service_health, _code_interpreter.code_interpreter]


def _model() -> BedrockModel:
    # streaming=False uses the non-streaming Converse API, which handles Nova's
    # multi-tool-call sequences more reliably than ConverseStream (the streaming
    # path intermittently raises modelStreamErrorException on tool use).
    return BedrockModel(model_id=MODEL_ID, streaming=False)


_DEFAULT_PROMPT = "Triage the health of the 'checkout' service and tell me if we should page anyone."

# AgentCore Gateway (optional) — its client_info secret lets the agent connect to
# the gateway's MCP endpoint and use the Lambda-backed tools it exposes.
GATEWAY_SECRET_ARN = os.getenv("GATEWAY_SECRET_ARN")


def _fetch_gateway():
    """Return (gateway_url, bearer_token) for the MCP gateway, or None."""
    if not GATEWAY_SECRET_ARN:
        return None
    import json
    import urllib.parse
    import urllib.request

    import boto3

    sec = json.loads(
        boto3.client("secretsmanager", region_name=REGION).get_secret_value(
            SecretId=GATEWAY_SECRET_ARN
        )["SecretString"]
    )
    ci = sec["client_info"]
    form = urllib.parse.urlencode(
        {
            "grant_type": "client_credentials",
            "client_id": ci["client_id"],
            "client_secret": ci["client_secret"],
            "scope": ci["scope"],
        }
    ).encode()
    req = urllib.request.Request(
        ci["token_endpoint"],
        data=form,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310
        token = json.loads(resp.read())["access_token"]
    return sec["gateway_url"], token


def _mcp_client(url: str, token: str):
    from mcp.client.streamable_http import streamablehttp_client
    from strands.tools.mcp import MCPClient

    return MCPClient(
        lambda: streamablehttp_client(url, headers={"Authorization": f"Bearer {token}"})
    )


def _build_session_manager(session_id: str):
    """Build an AgentCore Memory session manager, or None if Memory is unset."""
    if not MEMORY_ID:
        return None
    from bedrock_agentcore.memory.integrations.strands.config import (
        AgentCoreMemoryConfig,
        RetrievalConfig,
    )
    from bedrock_agentcore.memory.integrations.strands.session_manager import (
        AgentCoreMemorySessionManager,
    )

    retrieval = None
    if SEMANTIC_STRATEGY_ID:
        # The session manager resolves {memoryStrategyId}/{actorId} in the key.
        retrieval = {
            "/strategies/{memoryStrategyId}/actors/{actorId}/": RetrievalConfig(
                top_k=5, relevance_score=0.3, strategy_id=SEMANTIC_STRATEGY_ID
            )
        }
    config = AgentCoreMemoryConfig(
        memory_id=MEMORY_ID,
        session_id=session_id,
        actor_id=ACTOR_ID,
        retrieval_config=retrieval,
    )
    return AgentCoreMemorySessionManager(config, region_name=REGION)


def _valid_history(msgs) -> bool:
    """True if a restored conversation can be safely continued with a new user turn.

    Converse rejects histories that don't start with a plain user message, don't
    alternate roles, carry unmatched toolUse/toolResult pairs, or end mid-turn.
    Memory restores a truncated window of events, so any of these can happen
    after a crashed/failed turn.
    """
    if not msgs:
        return True
    prev_role = None
    pending_tool_ids: set = set()
    for m in msgs:
        role = m.get("role")
        content = m.get("content") or []
        if role == prev_role:
            return False  # roles must alternate
        tool_uses = {b["toolUse"]["toolUseId"] for b in content if "toolUse" in b}
        tool_results = {b["toolResult"]["toolUseId"] for b in content if "toolResult" in b}
        if tool_results != pending_tool_ids:
            return False  # toolResult must answer exactly the preceding toolUse
        pending_tool_ids = tool_uses
        prev_role = role
    if pending_tool_ids:
        return False  # dangling toolUse at the end
    return msgs[0].get("role") == "user" and msgs[-1].get("role") == "assistant"


def _make_agent(session_id: str, extra_tools=()) -> Agent:
    """Build an agent for the session (Memory-backed if configured)."""
    kwargs = {
        "model": _model(),
        "system_prompt": SYSTEM_PROMPT,
        "tools": [*_TOOLS, *extra_tools],
    }
    session_manager = _build_session_manager(session_id)
    if session_manager is not None:
        kwargs["session_manager"] = session_manager
    agent = Agent(**kwargs)
    # If the restored window is corrupt, start the turn with a clean slate
    # rather than failing the invocation; long-term recall still comes from the
    # semantic Memory strategy, which is retrieved independently of this list.
    if not _valid_history(agent.messages):
        log.warning(
            "session.history_reset",
            extra={
                "event.name": "session.history_reset",
                "session.id": session_id,
                "messages.dropped": len(agent.messages),
            },
        )
        agent.messages.clear()
    return agent


def _run(prompt: str, session_id: str):
    """Run the agent, mounting AgentCore Gateway MCP tools if configured."""
    gateway = _fetch_gateway()
    if gateway is None:
        return _make_agent(session_id)(prompt)
    url, token = gateway
    # The MCP connection must stay open for the duration of the agent call.
    with _mcp_client(url, token) as client:
        return _make_agent(session_id, extra_tools=client.list_tools_sync())(prompt)


def _extract_text(message) -> str:
    """Pull the assistant text out of a Strands result message."""
    try:
        return message["content"][0]["text"]
    except (KeyError, IndexError, TypeError):
        return str(message)


@app.entrypoint
def invoke(payload) -> dict:
    """AgentCore Runtime invocation handler."""
    payload = payload or {}
    prompt = payload.get("prompt") or _DEFAULT_PROMPT
    session_id = payload.get("session_id") or DEFAULT_SESSION_ID
    log.info(
        "agent.invoke",
        extra={
            "event.name": "agent.invoke",
            "prompt.chars": len(prompt),
            "session.id": session_id,
            "memory.enabled": bool(MEMORY_ID),
            "gateway.enabled": bool(GATEWAY_SECRET_ARN),
        },
    )
    try:
        result = _run(prompt, session_id)
    except Exception as exc:  # noqa: BLE001 - record + surface
        _invocation_counter.add(1, {"model": MODEL_ID, "outcome": "error"})
        log.exception("agent.invoke failed")
        return {"error": str(exc)}

    text = _extract_text(result.message)
    _invocation_counter.add(1, {"model": MODEL_ID, "outcome": "success"})
    log.info(
        "agent.result",
        extra={
            "event.name": "agent.result",
            "model": MODEL_ID,
            "response.chars": len(text),
            # Full response as a queryable log field (Bronto doesn't surface
            # span-event content in the traces dataset).
            "agent.response": text,
        },
    )
    return {"result": text}


if __name__ == "__main__":
    app.run()
