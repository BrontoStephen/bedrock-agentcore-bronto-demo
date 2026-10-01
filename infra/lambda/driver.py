"""Driver Lambda for the AgentCore -> Bronto demo.

Two roles in one function:
  * Lambda Function URL (HTTP): serves a tiny on-demand UI (GET) and invokes the
    AgentCore Runtime on POST, returning the agent's result.
  * EventBridge Scheduler target: on a scheduled event (no HTTP request context),
    invokes the runtime with the next of the three Track A scenarios (no tools,
    flaky tools, sub-agent), so all three stream into Bronto continuously.

Telemetry is emitted by the agent runtime itself (straight to Bronto); this
driver only triggers invocations.
"""

from __future__ import annotations

import json
import os
import random
import time
import uuid

import boto3

RUNTIME_ARN = os.environ["RUNTIME_ARN"]
REGION = os.environ.get("AWS_REGION", "eu-west-1")
BRONTO_DATASET_URL = os.environ.get("BRONTO_DATASET_URL", "")

_client = boto3.client("bedrock-agentcore", region_name=REGION)

SCENARIOS = ("no_tools", "flaky_tools", "subagent")
MODELS = ("eu.amazon.nova-pro-v1:0", "eu.amazon.nova-lite-v1:0")

# Customer questions from the Track A lab, per scenario.
QUESTIONS = {
    "no_tools": (
        "Where is order 1042, when will it arrive, and do you still have the blue ceramic mug?",
        "Has order 1043 shipped yet?",
    ),
    "flaky_tools": (
        "Where is order 1042, when will it arrive, and do you still have the blue ceramic mug?",
        "Order 1044: has it arrived? Can I order another linen apron?",
        "When will order 1042 get here?",
        "Was order 1045 cancelled? Is the cast iron skillet back in stock?",
    ),
    "subagent": (
        "Do you have espresso cups? If not, what would you suggest instead?",
        "I want a french press. What can you offer?",
        "Is the walnut cutting board in stock, and what goes well with it?",
    ),
}


def _invoke(prompt: str, session_id: str, scenario: str | None = None, model: str | None = None) -> dict:
    """Call the AgentCore Runtime and return the parsed result dict."""
    resp = _client.invoke_agent_runtime(
        agentRuntimeArn=RUNTIME_ARN,
        # AgentCore requires a 33+ char session id; two uuid hexes is plenty.
        runtimeSessionId=uuid.uuid4().hex + uuid.uuid4().hex,
        payload=json.dumps(
            {"prompt": prompt, "session_id": session_id, "scenario": scenario, "model": model}
        ).encode(),
        contentType="application/json",
        accept="application/json",
    )
    body = resp["response"].read()
    return json.loads(body) if body else {}


def handler(event, context):
    # --- Scheduled (EventBridge) path: no HTTP request context --------------
    if not isinstance(event, dict) or "requestContext" not in event:
        # One scenario per 10-minute slot, cycling 1 -> 2 -> 3. In the sub-agent
        # scenario, alternate the expensive and the cheap model (step 3's comparison).
        slot = int(time.time()) // 600
        scenario = SCENARIOS[slot % len(SCENARIOS)]
        model = MODELS[(slot // len(SCENARIOS)) % len(MODELS)] if scenario == "subagent" else None
        prompt = random.choice(QUESTIONS[scenario])
        result = _invoke(prompt, session_id="storefront-periodic", scenario=scenario, model=model)
        return {"ok": True, "scenario": scenario, "model": model, "result": result}

    # --- Function URL HTTP path ---------------------------------------------
    method = event["requestContext"]["http"]["method"]
    if method == "GET":
        return {
            "statusCode": 200,
            "headers": {"content-type": "text/html; charset=utf-8"},
            "body": _html(),
        }

    try:
        payload = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        payload = {}
    prompt = (payload.get("prompt") or "").strip() or QUESTIONS["flaky_tools"][0]
    session_id = payload.get("session_id") or "ui-session"
    scenario = payload.get("scenario") if payload.get("scenario") in SCENARIOS else "flaky_tools"
    model = payload.get("model") if payload.get("model") in MODELS else None
    try:
        result = _invoke(prompt, session_id, scenario, model)
        return {
            "statusCode": 200,
            "headers": {"content-type": "application/json"},
            "body": json.dumps(result),
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "statusCode": 502,
            "headers": {"content-type": "application/json"},
            "body": json.dumps({"error": str(exc)}),
        }


def _html() -> str:
    bronto_link = (
        f'<a href="{BRONTO_DATASET_URL}" target="_blank">open the Bronto dataset</a>'
        if BRONTO_DATASET_URL
        else "your Bronto <code>AWS AgentCore</code> dataset"
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>AgentCore → Bronto demo</title>
<style>
  :root {{ color-scheme: light dark; }}
  * {{ box-sizing: border-box; }}
  body {{ font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
    max-width: 760px; margin: 0 auto; padding: 24px; background: #0d1117; color: #e6edf3; }}
  h1 {{ font-size: 1.3rem; }}
  .sub {{ color: #8b949e; font-size: .85rem; margin-top: -8px; }}
  textarea {{ width: 100%; min-height: 90px; padding: 12px; border-radius: 8px;
    border: 1px solid #30363d; background: #161b22; color: inherit; font: inherit; }}
  button {{ margin-top: 10px; padding: 10px 18px; border: 0; border-radius: 8px;
    background: #2f81f7; color: white; font-weight: 600; cursor: pointer; }}
  button:disabled {{ opacity: .5; cursor: default; }}
  label {{ display: inline-block; margin: 0 16px 10px 0; color: #8b949e; font-size: .85rem; }}
  select {{ margin-left: 6px; padding: 6px; border-radius: 6px; border: 1px solid #30363d;
    background: #161b22; color: inherit; font: inherit; }}
  .answer {{ margin-top: 20px; padding: 16px; border-radius: 8px; background: #161b22;
    border: 1px solid #30363d; white-space: pre-wrap; }}
</style></head>
<body>
  <h1>AgentCore → Bronto demo</h1>
  <p class="sub">The Track A storefront assistant (Strands + Amazon Nova) on AWS Bedrock AgentCore
  Runtime. Traces, logs &amp; metrics flow to Bronto via OpenTelemetry. See {bronto_link}.</p>
  <label>Scenario
    <select id="scenario">
      <option value="no_tools">1 · no tools (it invents)</option>
      <option value="flaky_tools" selected>2 · tools, one flaky</option>
      <option value="subagent">3 · sub-agent</option>
    </select></label>
  <label>Model
    <select id="model">
      <option value="eu.amazon.nova-pro-v1:0">Nova Pro</option>
      <option value="eu.amazon.nova-lite-v1:0">Nova Lite</option>
    </select></label>
  <textarea id="prompt">Where is order 1042, when will it arrive, and do you still have the blue ceramic mug?</textarea>
  <br/><button id="send">Invoke agent</button>
  <div id="out" class="answer" hidden></div>
  <script>
    const btn = document.getElementById("send"), out = document.getElementById("out");
    btn.addEventListener("click", async () => {{
      const prompt = document.getElementById("prompt").value.trim();
      if (!prompt) return;
      btn.disabled = true; out.hidden = false; out.textContent = "Running multi-step agent…";
      try {{
        const res = await fetch("", {{ method: "POST",
          headers: {{ "content-type": "application/json" }},
          body: JSON.stringify({{ prompt, session_id: "ui-session",
            scenario: document.getElementById("scenario").value,
            model: document.getElementById("model").value }}) }});
        const data = await res.json();
        out.textContent = data.error ? data.error
          : data.result + (data.stats ? "\n\n" + JSON.stringify(data.stats, null, 2) : "");
      }} catch (e) {{ out.textContent = "Error: " + e.message; }}
      finally {{ btn.disabled = false; }}
    }});
  </script>
</body></html>"""
