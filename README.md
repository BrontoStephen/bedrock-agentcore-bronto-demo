# AgentCore → Bronto observability demo

A long-running demo that runs an AI agent on **AWS Bedrock AgentCore** and streams its
**OpenTelemetry** traces, logs and metrics into [Bronto.io](https://bronto.io). It is the
agentic sibling of the single-call
[Bedrock demo](https://github.com/BrontoStephen/bedrock-bronto-demo): where that emits one
`Converse` span, this runs a multi-step agent loop with tool calls, producing deep, nested
GenAI traces — a richer, continuous observability showcase.

```
                         AgentCore Runtime (eu-west-1, PUBLIC egress)
 EventBridge (10m) ─┐      Strands storefront assistant (Amazon Nova Pro / Lite)
 Lambda Function URL├─►  ├─ scenario.no_tools     (talk step 1)
 (on-demand UI)     ┘    ├─ scenario.flaky_tools  (talk step 2)
                         └─ scenario.subagent     (talk step 3)
                              │ OpenTelemetry (DISABLE_ADOT_OBSERVABILITY=true)
                              ▼  OTLP/HTTP (public internet)
                         ALB ──► ECS Fargate: ADOT collector (infra/)
                              ├─ x-bronto-api-key  ──► Bronto account #1
                              └─ x-bronto-api-key-2 ─► Bronto account #2 (optional)
                                 https://ingestion.<region>.bronto.io/v1/{logs,metrics,traces}
                                 dataset: AWS AgentCore
```

The agent is the **Storefront customer assistant** from the Track A "Observability for AI"
talk (`~/Code/Security/AWS AI Observability`, `bronto-community/track-a-observability-for-ai`).
It answers questions about orders, stock and shipping for a small kitchenware shop. Each
invocation runs one of the talk's three steps, picked by the request's `scenario` field. Each
scenario has its own span names:

| `scenario` | Talk step | Parent span | Agent span | What it shows |
|---|---|---|---|---|
| `no_tools` | 1 | `scenario.no_tools` | `invoke_agent storefront_no_tools` | No tools: the model writes the tool calls it wishes it had, as text, and invents an answer. ~80 input tokens. |
| `flaky_tools` | 2 | `scenario.flaky_tools` | `invoke_agent storefront_with_tools` | `lookup_order`, `check_inventory`, and `get_shipping_eta`, which times out ~1 call in 4 (`SHIPPING_FAILURE_RATE`). Look for `execute_tool` with `gen_ai.tool.status=error`. Input tokens jump to ~2,400. |
| `subagent` | 3 | `scenario.subagent` | `invoke_agent storefront_with_researcher` | Adds `product_researcher`, a second agent called as a tool (`execute_tool product_researcher` with an `invoke_agent` nested inside). The driver alternates Nova Pro and Nova Lite to compare cost and answer quality. The sub-agent's tokens are not in the parent span's total. |

The EventBridge driver moves to the next scenario every 10 minutes (1 → 2 → 3). The Function
URL UI has scenario and model dropdowns. AgentCore Memory, Code Interpreter and Gateway are no
longer used by the agent; their provisioning scripts are kept for reference.

## What data it generates

Telemetry flows **automatically, with no user action**: EventBridge invokes the agent every
10 minutes, so all of the below streams into Bronto continuously (dataset
`AWS AgentCore`, routed by `service.name`, reporting as `AWS LLM Services` via `service.namespace`). Every invocation produces:

| Signal | Source | What lands in Bronto |
|---|---|---|
| **Traces (GenAI)** | Strands' built-in telemetry | `invoke_agent` (agent tools + `gen_ai.tool.definitions`), one `chat` span per model turn (model id, finish reason, `gen_ai.usage.input_tokens`/`output_tokens`), `execute_tool <name>` per tool call, `execute_event_loop_cycle` per reasoning step. Prompt/response content rides on `gen_ai.client.inference.operation.details` span events as `gen_ai.input.messages` / `gen_ai.output.messages` / `gen_ai.system_instructions`. `gen_ai.provider.name=strands-agents`. |
| **Traces (AWS SDK)** | `opentelemetry-instrumentation-botocore` | A `chat` child span per `Bedrock Runtime.Converse` call (with its own GenAI attributes, `gen_ai.provider.name=aws.bedrock`, and `gen_ai.response.finish_reasons`), RPC semconv (`rpc.system`, `rpc.service`, `rpc.method`). |
| **Logs** | OTel `LoggingHandler` on the `storefront-assistant` logger ([agent/telemetry.py](agent/telemetry.py)) | One `event.name=agent.invocation` record per question, as in Track A: `scenario`, tokens in/out (sub-agent included), `latency_ms`, `model_calls`, `tool_calls`, `tool_errors`, `subagent_calls`, `cost_usd_estimate`, `tools.failed`, and the question and answer text. Plus botocore's GenAI events (`gen_ai.system.message` / `gen_ai.user.message` / `gen_ai.choice`), flattened by `GenAIEventFlattener` (see below). |
| **Metrics** | botocore + Strands | botocore's `gen_ai.client.token.usage` / `gen_ai.client.operation.duration` histograms per model call, and Strands' own event-loop and token counters. |

All three signals share `trace_id`/`span_id` correlation, so a Bronto log line links back to
the exact span (and vice versa).

### BRONTO-3347 workaround: `GenAIEventFlattener`

Botocore's GenAI events put their text in a **map-valued log body** and their type in the OTLP
`event_name` field. Bronto keeps neither today, so without help they arrive as empty rows that
keep only `gen_ai.provider.name`. `GenAIEventFlattener` (a `LogRecordProcessor` in
[agent/telemetry.py](agent/telemetry.py), registered before the batch exporter) copies
`event_name` to `event.name`, flattens the body into `body.*` attributes (arrays as `.0`,
`.1`, …, matching Bronto's own flattening), and makes the body a JSON string. Query
`"$event.name" = 'gen_ai.choice'` and read `$body.message.content.0.text`. It's the same code
as the Track A lab and the Bedrock demo; remove it once BRONTO-3347 is fixed.

### Dashboard

`dashboard/create_dashboard.py` builds "LLM KPIs — AgentCore Storefront", the Track A KPI
dashboard pointed at the `AWS AgentCore` datasets, with per-scenario requests, latency and
tokens:

```bash
BRONTO_API_KEY=<key with dashboard write> python3 dashboard/create_dashboard.py          # create
BRONTO_API_KEY=<key> python3 dashboard/create_dashboard.py --check                       # latest values
BRONTO_API_KEY=<key> python3 dashboard/create_dashboard.py --delete                      # remove
```

## GenAI semantic conventions (latest)

The OTel GenAI conventions are still experimental and Strands defaults to the older 2024
(v1.36) shape — deprecated `gen_ai.system` and per-role message events. This demo opts into
the **latest experimental conventions** end to end:

- **Opt-in env var** (the sanctioned migration switch, read by Strands ≥ 1.47):

  ```bash
  OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_latest_experimental,gen_ai_tool_definitions
  ```

  It is defaulted in [agent/telemetry.py](agent/telemetry.py) (merged with any pre-set
  value) and set explicitly in [docker-compose.yml](docker-compose.yml) and
  [scripts/redeploy.sh](scripts/redeploy.sh), so local and deployed runs behave identically.
  `gen_ai_latest_experimental` switches to `gen_ai.provider.name` +
  `gen_ai.input.messages`/`gen_ai.output.messages`; `gen_ai_tool_definitions` adds the
  agent's full tool schemas to `invoke_agent` spans.
- **Collector normalisation** ([collector/otel-collector-config.yaml](collector/otel-collector-config.yaml),
  `attributes/genai_semconv` processor): strips the deprecated
  `gen_ai.usage.prompt_tokens`/`completion_tokens` duplicates Strands still hardcodes, and
  renames the botocore Bedrock extension's legacy `gen_ai.system=aws.bedrock` to
  `gen_ai.provider.name` (that package hasn't migrated yet; insert-only, so Strands' own
  value is never overwritten).

Net effect: **nothing keyed on deprecated names reaches Bronto** — query
`gen_ai.provider.name`, `gen_ai.usage.input_tokens`/`output_tokens`, never `gen_ai.system` or
`gen_ai.usage.prompt_tokens`. Since both the conventions and Strands' implementation are
experimental and the weekly patch pipeline installs latest, check the
[strands-agents release notes](https://github.com/strands-agents/sdk-python/releases) if
`gen_ai.*` dashboards ever go quiet.

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
  -d '{"scenario":"flaky_tools","prompt":"Where is order 1042?"}'
```

Traces/logs/metrics land in the Bronto `AWS AgentCore` dataset (routed by
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
agentcore invoke '{"scenario":"subagent","prompt":"Do you have espresso cups?"}'
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
