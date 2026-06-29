"""OpenTelemetry bootstrap for the Bedrock -> Bronto demo.

Configures traces, metrics and logs providers and wires them to the local
OTLP/HTTP collector (the ADOT sidecar). Also turns on the botocore
auto-instrumentation, which emits Bedrock GenAI spans + metrics following the
OpenTelemetry GenAI semantic conventions, and the FastAPI instrumentation for
HTTP server spans.

Exporters read their endpoint from the standard env var
``OTEL_EXPORTER_OTLP_ENDPOINT`` (default ``http://localhost:4318``) and append
the per-signal path (/v1/traces, /v1/metrics, /v1/logs). In this demo the app
talks only to the collector; the collector holds the Bronto credentials and
fans each signal out to the matching Bronto endpoint.
"""

from __future__ import annotations

import logging
import os

# Must be set before the botocore instrumentation is imported/instrumented:
# the Bedrock GenAI semantic conventions (token usage, model, prompt/response)
# are experimental and only activate when this opt-in is present. Without it,
# Bedrock calls are traced only as generic AWS-API spans (rpc.* attributes).
os.environ.setdefault("OTEL_SEMCONV_STABILITY_OPT_IN", "gen_ai_latest_experimental")
os.environ.setdefault("OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT", "true")

from opentelemetry import metrics, trace
from opentelemetry._logs import set_logger_provider
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.botocore import BotocoreInstrumentor
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

_CONFIGURED = False


def _build_resource() -> Resource:
    return Resource.create(
        {
            "service.name": os.getenv("OTEL_SERVICE_NAME", "bedrock-bronto-demo"),
            "service.namespace": os.getenv("SERVICE_NAMESPACE", "bronto-demos"),
            "deployment.environment": os.getenv("DEPLOYMENT_ENV", "demo"),
        }
    )


def setup_telemetry() -> None:
    """Idempotently configure the three signal providers and instrumentations."""
    global _CONFIGURED
    if _CONFIGURED:
        return

    resource = _build_resource()

    # --- Traces -----------------------------------------------------------
    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(tracer_provider)

    # --- Metrics ----------------------------------------------------------
    metric_reader = PeriodicExportingMetricReader(
        OTLPMetricExporter(),
        export_interval_millis=int(os.getenv("OTEL_METRIC_EXPORT_INTERVAL", "15000")),
    )
    meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
    metrics.set_meter_provider(meter_provider)

    # --- Logs -------------------------------------------------------------
    logger_provider = LoggerProvider(resource=resource)
    logger_provider.add_log_record_processor(
        BatchLogRecordProcessor(OTLPLogExporter())
    )
    set_logger_provider(logger_provider)

    # Bridge Python's stdlib logging into OTel so app logs ship to Bronto,
    # while still printing to stdout (captured by CloudWatch on ECS).
    otel_handler = LoggingHandler(level=logging.INFO, logger_provider=logger_provider)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(otel_handler)
    root.addHandler(logging.StreamHandler())

    # --- Auto-instrumentation --------------------------------------------
    # Emits Bedrock GenAI spans + metrics (token usage, latency, model id).
    BotocoreInstrumentor().instrument()

    _CONFIGURED = True
    logging.getLogger(__name__).info("OpenTelemetry configured for Bedrock -> Bronto demo")


def get_meter(name: str = "bedrock-bronto-demo"):
    return metrics.get_meter(name)


def get_tracer(name: str = "bedrock-bronto-demo"):
    return trace.get_tracer(name)
