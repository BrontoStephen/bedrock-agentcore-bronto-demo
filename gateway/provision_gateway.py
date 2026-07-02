"""Provision an AgentCore Gateway (MCP) with a Lambda target for the demo.

We create the Cognito OAuth authorizer first (so we capture the ``client_info``
the agent needs to mint a bearer token), then create the gateway with that
authorizer, then a ``lambda`` target (the toolkit auto-creates a sample tool
Lambda exposing get_weather/get_time). The gateway URL + client_info (which
contains a client secret) are persisted to Secrets Manager so the agent runtime
can connect to the gateway as an MCP client.

Run once:  python gateway/provision_gateway.py
"""

from __future__ import annotations

import json
import os
import time

import boto3
from bedrock_agentcore_starter_toolkit.operations.gateway.client import GatewayClient

REGION = os.getenv("AWS_REGION", "eu-west-1")
GATEWAY_NAME = os.getenv("GATEWAY_NAME", "agentcorebrontogw")
SECRET_NAME = os.getenv("GATEWAY_SECRET_NAME", "agentcore-bronto-demo/gateway-client")


def main() -> None:
    gc = GatewayClient(region_name=REGION)

    # 1) Authorizer first — capture client_info (client_id/secret/token endpoint).
    cognito = gc.create_oauth_authorizer_with_cognito(GATEWAY_NAME)
    client_info = cognito["client_info"]

    # 2) Gateway using that authorizer.
    gw = gc.create_mcp_gateway(
        name=GATEWAY_NAME, authorizer_config=cognito["authorizer_config"]
    )
    gateway_url = gw["gatewayUrl"]

    # 3) Lambda target (auto-creates a sample tool Lambda); retry the transient
    #    "Lambda not ready" race.
    for attempt in range(5):
        try:
            gc.create_mcp_gateway_target(gateway=gw, name="tools", target_type="lambda")
            break
        except Exception as exc:  # noqa: BLE001
            if "not ready" in str(exc).lower() and attempt < 4:
                time.sleep(8)
                continue
            raise

    # 4) Persist for the runtime.
    sm = boto3.client("secretsmanager", region_name=REGION)
    payload = json.dumps({"gateway_url": gateway_url, "client_info": client_info}, default=str)
    try:
        arn = sm.create_secret(Name=SECRET_NAME, SecretString=payload)["ARN"]
    except sm.exceptions.ResourceExistsException:
        sm.put_secret_value(SecretId=SECRET_NAME, SecretString=payload)
        arn = sm.describe_secret(SecretId=SECRET_NAME)["ARN"]

    # 5) Smoke-test the token.
    token = gc.get_access_token_for_cognito(client_info)
    print(f"# token ok: {bool(token)} (len {len(token)})")
    print(f"GATEWAY_URL={gateway_url}")
    print(f"GATEWAY_SECRET_ARN={arn}")


if __name__ == "__main__":
    main()
