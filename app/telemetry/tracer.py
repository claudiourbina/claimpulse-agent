"""OpenTelemetry distributed tracing, span decoration, and structured JSON logging."""

from __future__ import annotations

import contextlib
import functools
import inspect
import json
import logging
import os
import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from typing import Any

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    SimpleSpanProcessor,
    SpanExporter,
    SpanExportResult,
)

from app.telemetry.scrubber import PIIScrubber


class InMemoryRedactedSpanExporter(SpanExporter):
    """Stores recent exported spans in memory for inspection and evaluation verification."""

    def __init__(self, max_spans: int = 500) -> None:
        self._spans: list[dict[str, Any]] = []
        self._max_spans = max_spans

    def export(self, spans: Any) -> SpanExportResult:
        for span in spans:
            attrs = dict(span.attributes or {})
            scrubbed_attrs = PIIScrubber.scrub_value(attrs)
            self._spans.append(
                {
                    "name": span.name,
                    "trace_id": f"{span.context.trace_id:032x}",
                    "span_id": f"{span.context.span_id:016x}",
                    "attributes": scrubbed_attrs,
                    "start_time": span.start_time,
                    "end_time": span.end_time,
                    "status": span.status.status_code.name,
                }
            )
            if len(self._spans) > self._max_spans:
                self._spans.pop(0)
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        return None

    def get_recorded_spans(self) -> list[dict[str, Any]]:
        return list(self._spans)

    def clear(self) -> None:
        self._spans.clear()


class ScrubbedJsonFormatter(logging.Formatter):
    """Formats log records as structured JSON with automatic PII scrubbing."""

    def format(self, record: logging.LogRecord) -> str:
        message = PIIScrubber.scrub_text(record.getMessage())
        span = trace.get_current_span()
        span_ctx = span.get_span_context() if span else None
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "severity": record.levelname,
            "logger": record.name,
            "message": message,
            "service": os.environ.get("OTEL_SERVICE_NAME", "claimpulse-agent"),
            "agent_role": os.environ.get("CLAIM_PULSE_AGENT_ROLE", "supervisor"),
        }
        if span_ctx and span_ctx.is_valid:
            payload["logging.googleapis.com/trace"] = f"{span_ctx.trace_id:032x}"
            payload["logging.googleapis.com/spanId"] = f"{span_ctx.span_id:016x}"
        if hasattr(record, "extra_fields") and isinstance(record.extra_fields, dict):
            payload["metadata"] = PIIScrubber.scrub_value(record.extra_fields)
        return json.dumps(payload, ensure_ascii=False)


_MEMORY_EXPORTER = InMemoryRedactedSpanExporter()
_INITIALIZED = False


def configure_telemetry(service_name: str = "claimpulse-agent") -> InMemoryRedactedSpanExporter:
    """Configures OpenTelemetry TracerProvider and PII-scrubbed JSON logging idempotently."""
    global _INITIALIZED
    if not _INITIALIZED:
        current_provider = trace.get_tracer_provider()
        if not isinstance(current_provider, TracerProvider):
            resource = Resource.create(
                {
                    "service.name": os.environ.get("OTEL_SERVICE_NAME", service_name),
                    "service.version": "0.1.0",
                    "agent.role": os.environ.get("CLAIM_PULSE_AGENT_ROLE", "supervisor"),
                }
            )
            provider = TracerProvider(resource=resource)
            provider.add_span_processor(SimpleSpanProcessor(_MEMORY_EXPORTER))
            trace.set_tracer_provider(provider)
        elif hasattr(current_provider, "add_span_processor"):
            current_provider.add_span_processor(SimpleSpanProcessor(_MEMORY_EXPORTER))
        _INITIALIZED = True
    return _MEMORY_EXPORTER


def get_tracer(name: str = "claimpulse.telemetry") -> trace.Tracer:
    """Returns a configured OpenTelemetry tracer."""
    configure_telemetry()
    return trace.get_tracer(name)


def get_memory_span_exporter() -> InMemoryRedactedSpanExporter:
    """Returns the in-memory redacted span exporter for test/diagnostic assertions."""
    configure_telemetry()
    return _MEMORY_EXPORTER


def get_logger(name: str = "claimpulse") -> logging.Logger:
    """Returns a structured JSON logger with automatic PII scrubbing."""
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(ScrubbedJsonFormatter())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger


@contextlib.contextmanager
def traced_operation(
    span_name: str, attributes: dict[str, Any] | None = None
) -> Iterator[trace.Span]:
    """Context manager that starts an OpenTelemetry span and scrubs attributes."""
    tracer = get_tracer()
    start_ns = time.perf_counter_ns()
    with tracer.start_as_current_span(span_name) as span:
        if attributes:
            scrubbed = PIIScrubber.scrub_value(attributes)
            for key, val in scrubbed.items():
                if isinstance(val, (str, int, float, bool)):
                    span.set_attribute(key, val)
                else:
                    span.set_attribute(key, json.dumps(val, ensure_ascii=False))
        try:
            yield span
        except Exception as exc:
            span.record_exception(exc)
            span.set_attribute("error.message", PIIScrubber.scrub_text(str(exc)))
            raise
        finally:
            elapsed_ms = (time.perf_counter_ns() - start_ns) / 1_000_000.0
            span.set_attribute("duration_ms", round(elapsed_ms, 3))


def trace_tool(tool_name: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator that wraps a synchronous or asynchronous ADK tool with OpenTelemetry tracing."""

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        if inspect.iscoroutinefunction(func):

            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                safe_kwargs = {
                    k: v for k, v in kwargs.items() if k != "tool_context"
                }
                with traced_operation(
                    f"tool.{tool_name}", {"tool.name": tool_name, "tool.args": safe_kwargs}
                ) as span:
                    result = await func(*args, **kwargs)
                    if isinstance(result, dict):
                        span.set_attribute(
                            "tool.status", str(result.get("status", "unknown"))
                        )
                    return result

            return async_wrapper

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            safe_kwargs = {k: v for k, v in kwargs.items() if k != "tool_context"}
            with traced_operation(
                f"tool.{tool_name}", {"tool.name": tool_name, "tool.args": safe_kwargs}
            ) as span:
                result = func(*args, **kwargs)
                if isinstance(result, dict):
                    span.set_attribute(
                        "tool.status", str(result.get("status", "unknown"))
                    )
                return result

        return sync_wrapper

    return decorator


def record_metric_event(event_type: str, details: dict[str, Any]) -> None:
    """Logs a scrubbed structured telemetry event and attaches it to the active span."""
    logger = get_logger("claimpulse.metrics")
    scrubbed = PIIScrubber.scrub_value(details)
    span = trace.get_current_span()
    if span and span.is_recording():
        span.add_event(
            event_type,
            attributes={
                k: (v if isinstance(v, (str, int, float, bool)) else json.dumps(v))
                for k, v in scrubbed.items()
            },
        )
    logger.info(f"metric_event:{event_type}", extra={"extra_fields": scrubbed})
