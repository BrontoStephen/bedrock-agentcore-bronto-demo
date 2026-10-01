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
: "${BEDROCK_MODEL_ID:=eu.amazon.nova-pro-v1:0}"
# Required account-specific values (no defaults — set them in scripts/deploy.env):
# The agent no longer talks to Bronto directly - it ships OTLP to the collector
# service (infra/, terraform output collector_otlp_endpoint), which holds the
# Bronto credentials and fans out to both accounts.
: "${COLLECTOR_OTLP_ENDPOINT:?set COLLECTOR_OTLP_ENDPOINT (see scripts/deploy.env.example; terraform output collector_otlp_endpoint from infra/)}"
# Scenario used when a request doesn't name one (the driver always does):
# no_tools | flaky_tools | subagent. See agent/agent.py.
: "${AGENT_SCENARIO:=flaky_tools}"

export AGENTCORE_SUPPRESS_RECOMMENDATION=1

agentcore deploy --auto-update-on-conflict \
  --env DISABLE_ADOT_OBSERVABILITY=true \
  --env OTEL_EXPORTER_OTLP_ENDPOINT="${COLLECTOR_OTLP_ENDPOINT}" \
  --env OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf \
  --env OTEL_SERVICE_NAME="AWS AgentCore" \
  --env OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_latest_experimental,gen_ai_tool_definitions \
  --env SERVICE_NAMESPACE="AWS LLM Services" \
  --env DEPLOYMENT_ENV=aws \
  --env BEDROCK_MODEL_ID="${BEDROCK_MODEL_ID}" \
  --env AGENT_SCENARIO="${AGENT_SCENARIO}"
