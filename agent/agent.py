"""AgentCore Runtime entrypoint: the Track A storefront assistant, three ways.

Each invocation runs one of the three steps from the "Observability for AI"
talk, picked per request with the payload's "scenario" field (default
AGENT_SCENARIO), so a single deployment streams all three into Bronto:

  no_tools     step 1  a model behind an endpoint, no tools. It invents.
  flaky_tools  step 2  + three back-office tools, one of them flaky
  subagent     step 3  + a product_researcher sub-agent, called as a tool

Every scenario gets its own span names, so they are easy to tell apart:
a parent `scenario.<name>` span, and an `invoke_agent <agent name>` span from
Strands under it (gen_ai.operation.name stays the standard invoke_agent).
"""

from __future__ import annotations

# Telemetry must be configured before Strands / instrumented libraries are used.
from telemetry import setup_telemetry

setup_telemetry()

import logging  # noqa: E402
import os  # noqa: E402
import time  # noqa: E402

from bedrock_agentcore.runtime import BedrockAgentCoreApp  # noqa: E402
from opentelemetry import trace  # noqa: E402
from strands import Agent, tool  # noqa: E402

from llm import MODEL_ID, estimated_cost, model  # noqa: E402
from tools import BACK_OFFICE_TOOLS, check_inventory  # noqa: E402

log = logging.getLogger("storefront-assistant")
tracer = trace.get_tracer("storefront-assistant")
app = BedrockAgentCoreApp()

SYSTEM_PROMPT = """You are the customer assistant for Storefront, a small online kitchenware shop.
Answer customers briefly and politely. Use your tools for anything about orders, stock or shipping;
never guess an order status or stock level. If a tool fails, say what you could not check."""

RESEARCHER_PROMPT = """You are Storefront's product researcher. Given a product a customer wants,
check stock and suggest up to two in-stock alternatives from the catalogue with one line on why.
Catalogue: blue ceramic mug, espresso cups, walnut cutting board, linen apron, cast iron skillet, french press."""

_sub_usage = {"input": 0, "output": 0, "calls": 0}
_researcher_model: list[str] = [MODEL_ID]


@tool
def product_researcher(request: str) -> str:
    """Hand a product question to the research sub-agent: stock plus in-stock alternatives."""
    researcher = Agent(
        name="product_researcher",
        model=model(_researcher_model[0]),
        system_prompt=RESEARCHER_PROMPT,
        tools=[check_inventory],
        callback_handler=None,
    )
    result = researcher(request)
    usage = result.metrics.accumulated_usage
    _sub_usage["input"] += usage.get("inputTokens", 0)
    _sub_usage["output"] += usage.get("outputTokens", 0)
    _sub_usage["calls"] += 1
    return str(result)


# scenario -> (talk step, agent name, tools)
SCENARIOS = {
    "no_tools": (1, "storefront_no_tools", []),
    "flaky_tools": (2, "storefront_with_tools", BACK_OFFICE_TOOLS),
    "subagent": (3, "storefront_with_researcher", [*BACK_OFFICE_TOOLS, product_researcher]),
}
DEFAULT_SCENARIO = os.environ.get("AGENT_SCENARIO", "flaky_tools")
DEFAULT_PROMPT = "Where is order 1042, when will it arrive, and do you still have the blue ceramic mug?"


@app.entrypoint
def invoke(payload) -> dict:
    payload = payload or {}
    scenario = payload.get("scenario") or DEFAULT_SCENARIO
    if scenario not in SCENARIOS:
        return {"error": f"unknown scenario {scenario!r}: use one of {', '.join(SCENARIOS)}"}
    step, agent_name, tools = SCENARIOS[scenario]
    prompt = payload.get("prompt") or DEFAULT_PROMPT
    model_id = payload.get("model") or MODEL_ID
    _researcher_model[0] = model_id
    _sub_usage.update(input=0, output=0, calls=0)

    with tracer.start_as_current_span(
        f"scenario.{scenario}", attributes={"scenario": scenario, "lab.step": step}
    ) as span:
        agent = Agent(
            name=agent_name,
            model=model(model_id),
            system_prompt=SYSTEM_PROMPT,
            tools=tools,
            callback_handler=None,
            trace_attributes={"scenario": scenario, "lab.step": step},
        )
        started = time.perf_counter()
        status = "ok"
        try:
            result = agent(prompt)
            answer = str(result).strip()
        except Exception as e:  # a failed model call is data too
            status, answer, result = "error", f"{type(e).__name__}: {e}", None
            span.record_exception(e)
            span.set_status(trace.Status(trace.StatusCode.ERROR, str(e)))
        latency_ms = round((time.perf_counter() - started) * 1000)

        m = result.metrics if result else None
        usage = m.accumulated_usage if m else {}
        tokens_in = usage.get("inputTokens", 0) + _sub_usage["input"]
        tokens_out = usage.get("outputTokens", 0) + _sub_usage["output"]
        tool_calls = sum(t.call_count for t in m.tool_metrics.values()) if m else 0
        tool_errors = sum(t.error_count for t in m.tool_metrics.values()) if m else 0
        failed_tools = sorted(name for name, t in m.tool_metrics.items() if t.error_count) if m else []

        # One structured event per request: the row the dashboard's cost widget reads.
        summary = {
            "event.name": "agent.invocation",
            "scenario": scenario,
            "lab.step": step,
            "status": status,
            "gen_ai.provider.name": "aws.bedrock",
            "gen_ai.operation.name": "invoke_agent",
            "gen_ai.agent.name": agent_name,
            "gen_ai.request.model": model_id,
            "gen_ai.usage.input_tokens": tokens_in,
            "gen_ai.usage.output_tokens": tokens_out,
            "gen_ai.usage.total_tokens": tokens_in + tokens_out,
            "latency_ms": latency_ms,
            "model_calls": m.cycle_count if m else 0,
            "tool_calls": tool_calls,
            "tool_errors": tool_errors,
            "subagent_calls": _sub_usage["calls"],
            "cost_usd_estimate": estimated_cost(model_id, tokens_in, tokens_out),
            "stop_reason": str(getattr(result, "stop_reason", "")) if result else "error",
            # The conversation, as searchable fields (content capture: treat as sensitive).
            "gen_ai.input.messages": prompt,
            "gen_ai.output.messages": answer,
            "tools.failed": ",".join(failed_tools),
        }
        # OTel log attributes can't be None: leave the cost out when there's no list price.
        log.info("agent.invocation", extra={k: v for k, v in summary.items() if v is not None})

    return {"result": answer, "stats": {k: summary[k] for k in (
        "scenario", "gen_ai.request.model", "gen_ai.usage.input_tokens", "gen_ai.usage.output_tokens",
        "latency_ms", "model_calls", "tool_calls", "tool_errors", "subagent_calls", "cost_usd_estimate")}}


if __name__ == "__main__":
    app.run()
