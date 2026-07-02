# AgentCore → Bronto observability demo

A long-running demo that runs an AI agent on **AWS Bedrock AgentCore** and streams its
**OpenTelemetry** traces, logs and metrics into [Bronto.io](https://bronto.io). It is the
agentic sibling of the single-call Bedrock demo in the repo root: where that emits one
`Converse` span, this runs a multi-step agent loop with tool calls, producing deep, nested
GenAI traces — a richer, continuous observability showcase.

```
                         AgentCore Runtime (eu-west-1, PUBLIC egress)
 EventBridge (10m) ─┐      Strands agent (Amazon Nova Pro)
 Lambda Function URL├─►  ├─ Code Interpreter  (sandboxed Python)
 (on-demand UI)     ┘    ├─ Memory            (cross-session recall)
                         └─ Gateway (MCP)     (Lambda-backed tool)
                              │ OpenTelemetry (DISABLE_ADOT_OBSERVABILITY=true)
                              ▼  direct OTLP/HTTP + x-bronto-api-key (from Secrets Manager)
                         https://ingestion.eu.bronto.io/v1/{logs,metrics,traces}
                              dataset: agentcore-bronto-demo
```

The agent is a **site-reliability "telemetry triage" assistant**: each invocation it pulls
synthetic service-health metrics, analyses them in the Code Interpreter, recalls prior runs
from Memory to spot trends, and (via the Gateway MCP tool) timestamps its assessment.

## Why direct OTLP (no collector in the cloud)

The agent's own telemetry is the richest signal and is emitted at 100% fidelity over OTLP.
The deployed runtime exports **straight to Bronto** — no collector, VPC, NAT or ECS. The
ADOT collector (`collector/`, `docker-compose.yml`) is used only for **local** development.
`telemetry.py` fetches the Bronto key from Secrets Manager at startup so it never lives in
the image or plaintext config.

## Layout

```
agent/            Strands agent (BedrockAgentCoreApp entrypoint), telemetry, tools,
                  AgentCore Memory provisioning (provision_memory.py)
gateway/          AgentCore Gateway + Lambda target provisioning (provision_gateway.py)
collector/        ADOT collector config — LOCAL dev only (otlp -> Bronto)
docker/           Agent image (also used by the local smoke test)
docker-compose.yml  Local smoke test: agent + collector
infra/            Terraform: driver Lambda (UI Function URL + EventBridge schedule) + weekly
                  CodeBuild patch pipeline
scripts/redeploy.sh + buildspec.agent.yml   Reproducible deploy / weekly dependency patch
```

## Prerequisites

- AWS credentials for `eu-west-1` with Bedrock + AgentCore + Lambda/IAM/CodeBuild access.
- Bedrock model access. Anthropic Claude is gated on some accounts; this demo uses **Amazon
  Nova Pro** (`eu.amazon.nova-pro-v1:0`), which supports tool use.
- A Bronto ingestion API key in Secrets Manager (the agent reads it via
  `BRONTO_API_KEY_SECRET_ARN`).
- `pip install bedrock-agentcore-starter-toolkit` for the `agentcore` CLI.

## Run locally

```bash
cd agentcore-demo
cp .env.example .env                 # fill in BRONTO_API_KEY (+ optional Memory ids)
eval "$(aws configure export-credentials --format env)"
docker compose up --build
curl -s localhost:8080/invocations -H 'content-type: application/json' \
  -d '{"prompt":"Triage the checkout service"}'
```

Traces/logs/metrics land in the Bronto `agentcore-bronto-demo` dataset (routed by
`service.name`).

## Deploy to AgentCore Runtime

```bash
# 1) (optional) provision AgentCore Memory + Gateway, note the printed ids/ARNs
python agent/provision_memory.py
python gateway/provision_gateway.py

# 2) configure + deploy the runtime (direct OTLP to Bronto)
cd agent
agentcore configure -e agent.py -n agentcore_bronto_demo -r eu-west-1 \
  -rf requirements.txt --disable-otel --disable-memory --non-interactive
cd .. && ./scripts/redeploy.sh        # wraps `agentcore deploy` with the env vars

# 3) invoke
agentcore invoke '{"prompt":"Triage the payments service","session_id":"demo"}'
```

The runtime execution role needs `bedrock:InvokeModel*`, `bedrock-agentcore:*`, and
`secretsmanager:GetSecretValue` on the Bronto (and Gateway) secrets.

## Drivers + always-on patching

```bash
cd infra
terraform init && terraform apply        # driver Lambda + UI Function URL + EventBridge (10m)
                                         # + weekly CodeBuild that re-runs the deploy
```

- **On-demand UI**: an IAM-authenticated Lambda Function URL (call it SigV4-signed).
- **Periodic**: EventBridge invokes the agent every 10 minutes → a steady trace stream.
- **Weekly patch**: CodeBuild re-runs `scripts/redeploy.sh`; because the runtime uses
  `direct_code_deploy` with an unpinned `requirements.txt`, this pulls the latest patched
  dependencies and rolls the runtime — keeping it ahead of vulnerability scans.

## Notes / gotchas

- **Nova streaming tool-use** is flaky (`modelStreamErrorException`); the agent uses
  `BedrockModel(streaming=False)` (non-streaming Converse) for reliable multi-tool turns.
- **Bronto indexes log-record attributes with a `$` prefix** (query `"$event.name"='agent.result'`).
  Bronto's trace ingestion does not surface span-event content, so prompt/response/tool IO is
  also written as structured `extra=` logs for queryability.
- Bronto **metrics** ingestion is currently a closed beta.
- AgentCore Gateway prefixes target tool names (e.g. `tools___get_time`).
```
