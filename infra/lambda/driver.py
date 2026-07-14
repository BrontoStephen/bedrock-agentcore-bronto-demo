"""Driver Lambda for the AgentCore -> Bronto demo.

Two roles in one function:
  * Lambda Function URL (HTTP): serves a tiny on-demand UI (GET) and invokes the
    AgentCore Runtime on POST, returning the agent's result.
  * EventBridge Scheduler target: on a scheduled event (no HTTP request context),
    invokes the runtime with a rotating service so a steady stream of multi-step
    traces flows into Bronto continuously.

Telemetry is emitted by the agent runtime itself (straight to Bronto); this
driver only triggers invocations.
"""

from __future__ import annotations

import json
import os
import random
import uuid

import boto3

RUNTIME_ARN = os.environ["RUNTIME_ARN"]
REGION = os.environ.get("AWS_REGION", "eu-west-1")
BRONTO_DATASET_URL = os.environ.get("BRONTO_DATASET_URL", "")

_client = boto3.client("bedrock-agentcore", region_name=REGION)

_SERVICES = ("checkout", "search", "payments", "recommendations", "auth")


def _invoke(prompt: str, session_id: str) -> dict:
    """Call the AgentCore Runtime and return the parsed result dict."""
    resp = _client.invoke_agent_runtime(
        agentRuntimeArn=RUNTIME_ARN,
        # AgentCore requires a 33+ char session id; two uuid hexes is plenty.
        runtimeSessionId=uuid.uuid4().hex + uuid.uuid4().hex,
        payload=json.dumps({"prompt": prompt, "session_id": session_id}).encode(),
        contentType="application/json",
        accept="application/json",
    )
    body = resp["response"].read()
    return json.loads(body) if body else {}


def handler(event, context):
    # --- Scheduled (EventBridge) path: no HTTP request context --------------
    if not isinstance(event, dict) or "requestContext" not in event:
        service = random.choice(_SERVICES)
        result = _invoke(
            f"Triage the {service} service and flag anything worth paging on.",
            session_id="triage-periodic",
        )
        return {"ok": True, "service": service, "result": result}

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
    prompt = (payload.get("prompt") or "").strip() or "Triage the checkout service."
    session_id = payload.get("session_id") or "ui-session"
    try:
        result = _invoke(prompt, session_id)
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
  .answer {{ margin-top: 20px; padding: 16px; border-radius: 8px; background: #161b22;
    border: 1px solid #30363d; white-space: pre-wrap; }}
</style></head>
<body>
  <h1>AgentCore → Bronto demo</h1>
  <p class="sub">Invokes a Strands agent (Amazon Nova Pro) on AWS Bedrock AgentCore Runtime —
  Code Interpreter + Memory. Traces, logs &amp; metrics flow to Bronto via OpenTelemetry.
  See {bronto_link}.</p>
  <textarea id="prompt">Triage the checkout service and tell me if we should page anyone.</textarea>
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
          body: JSON.stringify({{ prompt, session_id: "ui-session" }}) }});
        const data = await res.json();
        out.textContent = data.result || data.error || JSON.stringify(data);
      }} catch (e) {{ out.textContent = "Error: " + e.message; }}
      finally {{ btn.disabled = false; }}
    }});
  </script>
</body></html>"""
