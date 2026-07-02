#!/usr/bin/env bash
# Reproducible (re)deploy of the AgentCore Runtime. Also the weekly PATCH action:
# direct_code_deploy reinstalls requirements.txt (unpinned -> latest) on every
# deploy, so running this on a schedule keeps the agent's deps current ahead of
# the account's vulnerability scan. Env vars are NOT persisted in
# .bedrock_agentcore.yaml, so they must be re-passed here each time.
#
# Usage:  scripts/redeploy.sh   (reads scripts/deploy.env, or env vars in CI)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Account-specific config: sourced from scripts/deploy.env locally (gitignored),
# or provided as env vars by CI (the CodeBuild patch job sets them). Copy
# scripts/deploy.env.example -> scripts/deploy.env and fill in your values.
[ -f "${SCRIPT_DIR}/deploy.env" ] && . "${SCRIPT_DIR}/deploy.env"

cd "${SCRIPT_DIR}/../agent"

# .bedrock_agentcore.yaml stores absolute entrypoint/source_path from wherever it
# was first configured. Rewrite them to THIS checkout so the deploy works from
# any machine or CI runner (CodeBuild checks out to a different path).
ABS="$(pwd)"
sed -i.bak -E "s#^( *entrypoint:).*#\1 ${ABS}/agent.py#; s#^( *source_path:).*#\1 ${ABS}#" .bedrock_agentcore.yaml
rm -f .bedrock_agentcore.yaml.bak

: "${AWS_REGION:=eu-west-1}"
: "${BRONTO_OTLP_BASE:=https://ingestion.eu.bronto.io}"
: "${BEDROCK_MODEL_ID:=eu.amazon.nova-pro-v1:0}"
: "${AGENT_ACTOR_ID:=telemetry-triage}"
# Required account-specific values (no defaults — set them in scripts/deploy.env):
: "${BRONTO_API_KEY_SECRET_ARN:?set BRONTO_API_KEY_SECRET_ARN (see scripts/deploy.env.example)}"
: "${AGENTCORE_MEMORY_ID:?set AGENTCORE_MEMORY_ID (run agent/provision_memory.py)}"
# Optional (agent degrades gracefully if unset):
: "${AGENTCORE_SEMANTIC_STRATEGY_ID:=}"
: "${GATEWAY_SECRET_ARN:=}"

export AGENTCORE_SUPPRESS_RECOMMENDATION=1

agentcore deploy --auto-update-on-conflict \
  --env DISABLE_ADOT_OBSERVABILITY=true \
  --env OTEL_EXPORTER_OTLP_ENDPOINT="${BRONTO_OTLP_BASE}" \
  --env OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf \
  --env OTEL_SERVICE_NAME=agentcore-bronto-demo \
  --env SERVICE_NAMESPACE=bronto-demos \
  --env DEPLOYMENT_ENV=aws \
  --env BRONTO_API_KEY_SECRET_ARN="${BRONTO_API_KEY_SECRET_ARN}" \
  --env AGENTCORE_MEMORY_ID="${AGENTCORE_MEMORY_ID}" \
  --env AGENTCORE_SEMANTIC_STRATEGY_ID="${AGENTCORE_SEMANTIC_STRATEGY_ID}" \
  --env AGENT_ACTOR_ID="${AGENT_ACTOR_ID}" \
  --env BEDROCK_MODEL_ID="${BEDROCK_MODEL_ID}" \
  --env GATEWAY_SECRET_ARN="${GATEWAY_SECRET_ARN}"
