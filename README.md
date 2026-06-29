# Bedrock → Bronto observability demo

A small AWS Bedrock web app that emits **logs, metrics, and traces** to
[Bronto.io](https://bronto.io) via OpenTelemetry. It runs on **ECS Fargate**
with an **ADOT (AWS Distro for OpenTelemetry) Collector sidecar** that forwards
all three signals to Bronto's OTLP endpoints.

```
Browser ──HTTP──► ALB ──► ECS Fargate task
                           ├─ app container (FastAPI + OTel SDK)  ──OTLP──► collector
                           │     └─ boto3 Bedrock Converse (Amazon Nova)
                           └─ ADOT collector sidecar  ──OTLP/HTTP + x-bronto-api-key──►
                                 https://ingestion.eu.bronto.io/v1/{logs,metrics,traces}
```

The app exports to the collector over loopback (`localhost:4318`). The collector
holds the Bronto credential and fans each signal out to the matching Bronto
endpoint, keeping the app code vendor-neutral.

## What gets sent to Bronto

| Signal  | Source | Examples |
|---------|--------|----------|
| Traces  | `opentelemetry-instrumentation-botocore` (GenAI semconv) + FastAPI | `Bedrock Runtime.Converse` spans nested under `POST /chat`, with `gen_ai.*` attributes |
| Metrics | custom meters in `app/bedrock.py` + auto HTTP metrics | `demo.bedrock.tokens`, `demo.bedrock.latency`, `demo.bedrock.invocations`, `http.server.duration` |
| Logs    | Python `logging` bridged to OTel | `bedrock.converse ok …`, `chat request received …` (trace-correlated) |

## Layout

```
app/                      FastAPI app + OTel bootstrap + Bedrock wrapper
collector/                ADOT collector config (otlp receiver → otlphttp to Bronto)
docker/Dockerfile.app     App image
docker-compose.yml        Local smoke test (app + collector)
infra/                    Terraform: ECR, ECS Fargate, ALB, IAM, Secrets Manager, SSM
```

## Prerequisites

- AWS credentials (Bedrock + ECS/IAM/VPC/ALB/ECR/Secrets permissions) in `eu-west-1`.
- Bedrock model access for the chosen model. **Note:** Anthropic Claude models are
  gated on some account types ("channel program accounts"); this demo defaults to
  **Amazon Nova** (`eu.amazon.nova-micro-v1:0`), which works broadly. Override with
  `BEDROCK_MODEL_ID` / the `bedrock_model_id` Terraform variable.
- A Bronto ingestion API key.

## Run locally

```bash
cp .env.example .env            # fill in BRONTO_API_KEY
eval "$(aws configure export-credentials --format env)"   # temp creds for boto3
docker compose up --build
# open http://localhost:8000
```

## Deploy to AWS

```bash
cd infra
terraform init
export TF_VAR_bronto_api_key='<your-bronto-key>'
eval "$(aws configure export-credentials --format env)"

# 1) create the ECR repo
terraform apply -auto-approve -target=aws_ecr_repository.app

# 2) build + push the app image (Fargate is linux/amd64)
REPO=$(terraform output -raw ecr_repository_url)
aws ecr get-login-password --region eu-west-1 \
  | docker login --username AWS --password-stdin "${REPO%/*}"
docker buildx build --platform linux/amd64 -f ../docker/Dockerfile.app -t "${REPO}:latest" --push ..

# 3) deploy everything
terraform apply -auto-approve

# 4) use it
open "$(terraform output -raw alb_url)"
```

To redeploy app code: rebuild/push the image (step 2), then
`aws ecs update-service --cluster bedrock-bronto-demo --service bedrock-bronto-demo --force-new-deployment --region eu-west-1`.

## Always-on + weekly security patching

The ECS service runs `desired_count = 1` and ECS restarts the task if it ever
stops, so the demo stays continuously available at the ALB URL.

A **weekly automated rebuild** keeps the image patched ahead of the account's
security scan:

```
EventBridge Scheduler (Mon 03:00 UTC)
  -> CodeBuild "bedrock-bronto-demo-weekly-rebuild"
       -> docker build --pull   (latest patched python:3.12-slim + apt upgrade)
       -> push :latest + :<date>-<build> to ECR
       -> ecs update-service --force-new-deployment
```

- OS patching happens in `docker/Dockerfile.app` (`apt-get upgrade`) combined
  with `docker build --pull` in `buildspec.yml`.
- Build source is a snapshot of this repo zipped to S3 by Terraform. After
  editing app code, run `terraform apply` again to refresh the snapshot.
- Run it on demand:
  `aws codebuild start-build --project-name bedrock-bronto-demo-weekly-rebuild --region eu-west-1`
- Change the cadence via `aws_scheduler_schedule.weekly_rebuild` in `infra/cicd.tf`.

## Structured logs

App logs use a short event name as the body plus structured `extra={...}`
attributes (e.g. `gen_ai.usage.input_tokens`, `latency_ms`, `outcome`). The OTel
`LoggingHandler` maps these into log-record attributes, so Bronto indexes them
as first-class, correctly-typed fields (NUMBER/STRING) rather than parsing a
flat text line. Avoid printf-style `log.info("... %s", x)` for data you want to
query — put it in `extra` instead.

## Verify

- App: `curl "$(terraform output -raw alb_url)/healthz"` and POST to `/chat`.
- Collector exports: check CloudWatch log group `/ecs/bedrock-bronto-demo/collector`
  for per-signal summaries and the absence of `Exporting failed`.
- Bronto: confirm traces/metrics/logs for `service.name=bedrock-bronto-demo` arrive.

## Teardown

```bash
cd infra && terraform destroy -auto-approve
```

## Notes

- Collector config is delivered to the sidecar via the ADOT `AOT_CONFIG_CONTENT`
  env var, sourced from an SSM parameter; the Bronto key comes from Secrets Manager.
- Bronto OTLP metrics ingestion (`/v1/metrics`) is currently a closed beta.
- The collector logs a harmless deprecation warning: `"otlphttp" alias is
  deprecated; use "otlp_http"`. Either key works on the current ADOT image.
