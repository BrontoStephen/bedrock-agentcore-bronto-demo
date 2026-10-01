"""Which Bedrock model the agent talks to, and what a question costs.

A Bedrock-only cut of the Track A lab's llm.py. BEDROCK_MODEL_ID picks the
default; a request may override it with a "model" field (the driver uses that
to compare Nova Pro with Nova Lite in the sub-agent scenario).
"""

import os

from strands.models import BedrockModel

REGION = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "eu-west-1"
MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "eu.amazon.nova-pro-v1:0")
CHEAP_MODEL_ID = os.environ.get("CHEAP_MODEL_ID", "eu.amazon.nova-lite-v1:0")

# USD per million tokens (input, output), on-demand list price. Matched on a
# substring of the model id, so inference-profile prefixes don't matter.
PRICES = {
    "nova-2-lite": (0.33, 2.75),
    "nova-lite": (0.06, 0.24),
    "nova-pro": (0.80, 3.20),
    "nova-micro": (0.035, 0.14),
}


def model(model_id: str | None = None) -> BedrockModel:
    # streaming=False uses the non-streaming Converse API, which handles Nova's
    # multi-tool-call sequences more reliably than ConverseStream.
    return BedrockModel(model_id=model_id or MODEL_ID, region_name=REGION, max_tokens=1024, streaming=False)


def estimated_cost(model_id: str, tokens_in: int, tokens_out: int) -> float | None:
    """Tokens x list price. None when no price is on file for the model."""
    for name, (price_in, price_out) in PRICES.items():
        if name in model_id:
            return round((tokens_in * price_in + tokens_out * price_out) / 1_000_000, 6)
    return None
