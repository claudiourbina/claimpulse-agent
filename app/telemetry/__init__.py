"""Observability, OpenTelemetry distributed tracing, structured JSON logging, and PII redaction."""

from app.telemetry.scrubber import PIIScrubber, scrub_pii
from app.telemetry.tracer import (
     configure_telemetry,
    get_logger,
    get_tracer,
    record_metric_event,
    traced_operation,
)

__all__ = [
    "PIIScrubber",
    "configure_telemetry",
    "get_logger",
    "get_tracer",
    "record_metric_event",
    "scrub_pii",
    "traced_operation",
]
