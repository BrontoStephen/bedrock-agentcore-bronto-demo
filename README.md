# AgentCore → Bronto observability demo

A long-running demo that runs an AI agent on **AWS Bedrock AgentCore** and streams its
**OpenTelemetry** traces, logs and metrics into [Bronto.io](https://bronto.io). It is the
agentic sibling of the single-call
[Bedrock demo](https://github.com/BrontoStephen/bedrock-bronto-demo): where that emits one
`Converse` span, this runs a multi-step agent loop with tool calls, producing deep, nested
GenAI traces — a richer, continuous observability showcase.

```
                         AgentCore Runtime (eu-west-1, PUBLIC egress)
 EventBridge (10m) ─┐      Strands agent (Amazon Nova Pro)
 Lambda Function URL├─►  ├─ Code Interpreter  (sandboxed Python)
 (on-demand UI)     ┘    ├─ Memory            (cross-session recall)
                         └─ Gateway (MCP)     (Lambda-backed tool)
                              │ OpenTelemetry (DISABLE_ADOT_OBSERVABILITY=true)
                              ▼  OTLP/HTTP (public internet)
                         ALB ──► ECS Fargate: ADOT collector (infra/)
                              ├─ x-bronto-api-key  ──► Bronto account #1
                              └─ x-bronto-api-key-2 ─► Bronto account #2 (optional)
                                 https://ingestion.<region>.bronto.io/v1/{logs,metrics,traces}
                                 dataset: agentcore-bronto-demo
```

The agent is a **site-reliability "telemetry triage" assistant**: each invocation it pulls
synthetic service-health metrics, analyses them in the Code Interpreter, recalls prior runs
from Memory to spot trends, and (via the Gateway MCP tool) timestamps its assessment.

## Collector architecture (and dual Bronto accounts)

The deployed runtime never holds Bronto credentials or talks to Bronto directly. It ships
OTLP/HTTP to a small always-on **ECS Fargate + ALB collector-only service**
(`infra/network.tf`, `alb.tf`, `ecs.tf`, `iam.tf`, `secrets.tf`) — the same ADOT
collector image and config (`collector/otel-collector-config.yaml`) used for local dev, just
reachable over the public internet instead of `localhost:4318`. The ALB has to accept from
`0.0.0.0/0` because the AgentCore Runtime runs in AWS's managed PUBLIC network mode with no
fixed egress IP to allowlist.

The collector broadcasts every signal to **up to two Bronto accounts** — a second
`otlphttp/bronto2` exporter runs alongside the first in every pipeline. Leave
`bronto_api_key_2` / `bronto_otlp_base_2` unset (the default) to only export to one account;
fill them in via `terraform.tfvars` to activate the second, no code changes needed.

> A prior iteration of this demo had the runtime export straight to Bronto (no collector) —
> preserved as-is on the `agentcore-direct-otlp-single-account` branch for reference.

## Layout

```
agent/            Strands agent (BedrockAgentCoreApp entrypoint), telemetry, tools,
                  AgentCore Memory provisioning (provision_memory.py)
gateway/          AgentCore Gateway + Lambda target provisioning (provision_gateway.py)
collector/        ADOT collector config (otlp receiver -> otlphttp to both Bronto accounts) -
                  used both locally (docker-compose) and by the deployed collector service
docker/           Agent image (also used by the local smoke test)
docker-compose.yml  Local smoke test: agent + collector
infra/            Terraform: collector ECS Fargate + ALB, driver Lambda (UI Function URL +
                  EventBridge schedule), weekly CodeBuild patch pipeline
scripts/redeploy.sh + buildspec.agent.yml   Reproducible deploy / weekly dependency patch
```

## Prerequisites

- AWS credentials for `eu-west-1` with Bedrock + AgentCore + ECS/ALB/Lambda/IAM/CodeBuild
  access.
- Bedrock model access. Anthropic Claude is gated on some accounts; this demo uses **Amazon
  Nova Pro** (`eu.amazon.nova-pro-v1:0`), which supports tool use.
- A Bronto ingestion API key (a second is optional — see "Collector architecture" above).
- `pip install bedrock-agentcore-starter-toolkit` for the `agentcore` CLI.

## Run locally

```bash
cp .env.example .env                 # fill in BRONTO_API_KEY (+ optional 2nd account, Memory ids)
eval "$(aws configure export-credentials --format env)"
docker compose up --build
curl -s localhost:8080/invocations -H 'content-type: application/json' \
  -d '{"prompt":"Triage the checkout service"}'
```

Traces/logs/metrics land in the Bronto `agentcore-bronto-demo` dataset (routed by
`service.name`), in both accounts if the second is configured.

## Deploy to AgentCore Runtime

```bash
# 1) stand up the collector service (+ driver, see below) — first apply only has
#    ecs/alb/secrets resources you care about; run again once collector_otlp_endpoint exists
cd infra
terraform init
export TF_VAR_bronto_api_key='<your-first-bronto-key>'
# export TF_VAR_bronto_api_key_2='<your-second-bronto-key>'   # optional
terraform apply
terraform output collector_otlp_endpoint    # feed this into scripts/deploy.env below

# 2) (optional) provision AgentCore Memory + Gateway, note the printed ids/ARNs
cd ..
python agent/provision_memory.py
python gateway/provision_gateway.py

# 3) configure + deploy the runtime (OTLP -> collector service, not Bronto directly)
cd agent
agentcore configure -e agent.py -n agentcore_bronto_demo -r eu-west-1 \
  -rf requirements.txt --disable-otel --disable-memory --non-interactive
cd ..
cp scripts/deploy.env.example scripts/deploy.env   # fill in COLLECTOR_OTLP_ENDPOINT + Memory id
./scripts/redeploy.sh        # wraps `agentcore deploy` with the env vars

# 4) invoke
agentcore invoke '{"prompt":"Triage the payments service","session_id":"demo"}'
```

The runtime execution role needs `bedrock:InvokeModel*` and `bedrock-agentcore:*` — it no
longer needs Secrets Manager access, since it never touches the Bronto credentials.

## Drivers + always-on patching

```bash
cd infra
terraform apply        # collector ECS+ALB + driver Lambda + UI Function URL + EventBridge (10m)
                       # + weekly CodeBuild that re-runs the deploy
```

- **On-demand UI**: an IAM-authenticated Lambda Function URL (call it SigV4-signed).
- **Periodic**: EventBridge invokes the agent every 10 minutes → a steady trace stream.
- **Weekly patch**: CodeBuild re-runs `scripts/redeploy.sh`; because the runtime uses
  `direct_code_deploy` with an unpinned `requirements.txt`, this pulls the latest patched
  dependencies and rolls the runtime — keeping it ahead of vulnerability scans. Set
  `collector_otlp_endpoint` (and the other account-specific vars) in `terraform.tfvars` so
  the CodeBuild job can pass it through.

## Notes / gotchas

- **Nova streaming tool-use** is flaky (`modelStreamErrorException`); the agent uses
  `BedrockModel(streaming=False)` (non-streaming Converse) for reliable multi-tool turns.
- **Bronto indexes log-record attributes with a `$` prefix** (query `"$event.name"='agent.result'`).
  Bronto's trace ingestion does not surface span-event content, so prompt/response/tool IO is
  also written as structured `extra=` logs for queryability.
- Bronto **metrics** ingestion is currently a closed beta.
- AgentCore Gateway prefixes target tool names (e.g. `tools___get_time`).
- The collector's ALB target group health-checks port `13133` (the ADOT `health_check`
  extension) while routing OTLP traffic to port `4318` — they're different ports on the same
  container.
```
