"""Idempotently provision the AgentCore Memory resource for the demo.

Creates (or fetches) a Memory resource named ``agentcore_bronto_demo`` with a
semantic long-term strategy, then prints the env vars the agent/runtime need:

    AGENTCORE_MEMORY_ID=...
    AGENTCORE_SEMANTIC_STRATEGY_ID=...

Run once locally (or from Terraform/CI):  python provision_memory.py
"""

from __future__ import annotations

import json
import os
import sys

from bedrock_agentcore.memory.client import MemoryClient

REGION = os.getenv("AWS_REGION", "eu-west-1")
MEMORY_NAME = os.getenv("AGENTCORE_MEMORY_NAME", "agentcore_bronto_demo")
SEMANTIC_STRATEGY_NAME = "triage_semantic"


def _memory_id(mem: dict) -> str:
    for k in ("id", "memoryId"):
        if mem.get(k):
            return mem[k]
    if isinstance(mem.get("memory"), dict):
        return mem["memory"].get("id") or mem["memory"].get("memoryId")
    raise SystemExit(f"could not find memory id in: {json.dumps(mem)[:400]}")


def _strategy_id(strat: dict):
    for k in ("strategyId", "memoryStrategyId", "id"):
        if strat.get(k):
            return strat[k]
    return None


def main() -> None:
    client = MemoryClient(region_name=REGION)

    mem = client.create_or_get_memory(
        name=MEMORY_NAME,
        description="Telemetry-triage agent memory (AgentCore -> Bronto demo).",
    )
    memory_id = _memory_id(mem)
    print(f"# memory resource: {memory_id}", file=sys.stderr)

    def _is_semantic(s: dict) -> bool:
        return "semantic" in json.dumps(s, default=str).lower()

    strategies = client.get_memory_strategies(memory_id) or []
    print(f"# existing strategies: {json.dumps(strategies, default=str)[:600]}", file=sys.stderr)
    semantic = next((s for s in strategies if _is_semantic(s)), None)
    if semantic is None:
        print("# adding semantic strategy (waits for ACTIVE)…", file=sys.stderr)
        client.add_semantic_strategy_and_wait(memory_id, name=SEMANTIC_STRATEGY_NAME)
        strategies = client.get_memory_strategies(memory_id) or []
        semantic = next((s for s in strategies if _is_semantic(s)), None)

    strategy_id = _strategy_id(semantic or {})
    print(f"AGENTCORE_MEMORY_ID={memory_id}")
    print(f"AGENTCORE_SEMANTIC_STRATEGY_ID={strategy_id or ''}")


if __name__ == "__main__":
    main()
