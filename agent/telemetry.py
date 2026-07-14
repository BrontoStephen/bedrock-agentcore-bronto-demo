"""OpenTelemetry bootstrap for the AgentCore -> Bronto demo.

Configures the three signal providers and points them at the OTLP/HTTP
collector - locally the docker-compose service, in AWS the standalone
ECS Fargate + ALB collector service (``infra/``) that the
AgentCore Runtime reaches over the internet. The agent never holds Bronto
credentials or talks to Bronto directly; the collector does, and fans each
signal out to both configured Bronto accounts (see
``../collector/otel-collector-config.yaml``).

Design choice: we configure telemetry **explicitly** rather than relying on
`opentelemetry-instrument` + aws-opentelemetry-distro. The AgentCore Runtime is
launched with ``DISABLE_ADOT_OBSERVABILITY=true`` so AWS's managed pipeline is
out of the way; everything below ships straight to our collector.

- Traces: owned by Strands (``StrandsTelemetry``), which emits GenAI
  semantic-convention spans for the agent loop, model calls (token usage,
  model id) and tool calls, exported via OTLP to ``OTEL_EXPORTER_OTLP_ENDPOINT``.
  We opt into the latest experimental GenAI conventions (see
  ``OTEL_SEMCONV_STABILITY_OPT_IN`` below) so spans carry
  ``gen_ai.provider.name`` and ``gen_ai.input.messages`` /
  ``gen_ai.output.messages`` rather than the deprecated 2024-era shape.
- Metrics + logs: configured here with their own OTLP exporters so our custom
  meters and structured ``extra=`` logs arrive in Bronto as first-class fields.
- ``BotocoreInstrumentor`` traces the boto3 calls the agent makes to AgentCore
  primitives (Memory, Code Interpreter, Gateway) so they appear as child spans.

All exporters read ``OTEL_EXPORTER_OTLP_ENDPOINT`` (default
``http://localhost:4318``) and append the per-signal path automatically.
"""

from __future__ import annotations

import logging
import os

# Capture prompt/response content on GenAI spans/events for the demo. Strands
# ignores this (it captures content unless redaction is opted into via
# OTEL_SEMCONV_STABILITY_OPT_IN); the botocore Bedrock extension reads it.
os.environ.setdefault("OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT", "true")

# Emit the latest (experimental) OTel GenAI semantic conventions instead of the
# legacy v1.36 shape Strands defaults to:
#   gen_ai_latest_experimental - gen_ai.provider.name instead of the deprecated
#     gen_ai.system, and message content on gen_ai.client.inference.operation.details
#     events as gen_ai.input.messages / gen_ai.output.messages /
#     gen_ai.system_instructions instead of the deprecated per-role
#     gen_ai.{system,user,assistant,tool}.message / gen_ai.choice events.
#   gen_ai_tool_definitions - adds gen_ai.tool.definitions to invoke_agent spans.
# Merged (not overwritten) so platform-set opt-ins (e.g. http) survive.
_GENAI_OPT_INS = ("gen_ai_latest_experimental", "gen_ai_tool_definitions")


def _opt_in_latest_genai_semconv() -> None:
    current = [v.strip() for v in os.getenv("OTEL_SEMCONV_STABILITY_OPT_IN", "").split(",") if v.strip()]
    merged = current + [t for t in _GENAI_OPT_INS if t not in current]
    os.environ["OTEL_SEMCONV_STABILITY_OPT_IN"] = ",".join(merged)


_opt_in_latest_genai_semconv()

from opentelemetry import metrics
from opentelemetry._logs import set_logger_provider
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.instrumentation.botocore import BotocoreInstrumentor
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource

_CONFIGURED = False


def _build_resource() -> Resource:
    return Resource.create(
        {
            "service.name": os.getenv("OTEL_SERVICE_NAME", "AWS AgentCore"),
            "service.namespace": os.getenv("SERVICE_NAMESPACE", "AWS LLM Services"),
            "deployment.environment": os.getenv("DEPLOYMENT_ENV", "demo"),
        }
    )


def setup_telemetry() -> None:
    """Idempotently configure the three signal providers and instrumentations."""
    global _CONFIGURED
    if _CONFIGURED:
        return

    # Strands builds its trace resource from OTEL_RESOURCE_ATTRIBUTES; derive it
    # from the simpler scalar env vars so deployment only needs OTEL_SERVICE_NAME
    # (avoids passing a comma-laden value through the deploy CLI).
    svc = os.getenv("OTEL_SERVICE_NAME", "AWS AgentCore")
    ns = os.getenv("SERVICE_NAMESPACE", "AWS LLM Services")
    env = os.getenv("DEPLOYMENT_ENV", "demo")
    os.environ.setdefault(
        "OTEL_RESOURCE_ATTRIBUTES",
        f"service.name={svc},service.namespace={ns},deployment.environment={env}",
    )

    resource = _build_resource()

    # --- Traces (Strands owns the tracer provider + OTLP span export) --------
    # setup_otlp_exporter() reads OTEL_EXPORTER_OTLP_ENDPOINT and builds its
    # resource from OTEL_RESOURCE_ATTRIBUTES / OTEL_SERVICE_NAME, so make sure
    # those env vars are set (see docker-compose.yml / the runtime env).
    from strands.telemetry import StrandsTelemetry

    StrandsTelemetry().setup_otlp_exporter()

    # --- Metrics -------------------------------------------------------------
    metric_reader = PeriodicExportingMetricReader(
        OTLPMetricExporter(),
        export_interval_millis=int(os.getenv("OTEL_METRIC_EXPORT_INTERVAL", "15000")),
    )
    metrics.set_meter_provider(
        MeterProvider(resource=resource, metric_readers=[metric_reader])
    )

    # --- Logs ----------------------------------------------------------------
    logger_provider = LoggerProvider(resource=resource)
    logger_provider.add_log_record_processor(BatchLogRecordProcessor(OTLPLogExporter()))
    set_logger_provider(logger_provider)

    otel_handler = LoggingHandler(level=logging.INFO, logger_provider=logger_provider)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(otel_handler)
    root.addHandler(logging.StreamHandler())

    # --- Auto-instrumentation ------------------------------------------------
    # Traces the boto3 calls to AgentCore primitives (Memory / Code Interpreter
    # / Gateway) as child spans of the agent invocation.
    BotocoreInstrumentor().instrument()

    _CONFIGURED = True
    logging.getLogger(__name__).info("OpenTelemetry configured for AgentCore -> Bronto demo")


def get_meter(name: str = "agentcore-bronto-demo"):
    return metrics.get_meter(name)
