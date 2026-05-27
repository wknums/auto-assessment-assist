# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE.md file in the project root for full license information.
"""
Structured JSON logging and OpenTelemetry bootstrap for the queue worker.

Mirrors the pattern used in ``wrappers/http-service/app/telemetry.py`` but
is decoupled so the worker can run without FastAPI.
"""
from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar
from datetime import datetime, timezone

from runtime.config import engine_settings

# ── Context variables (set per-message in the worker loop) ────────────
correlation_id_var: ContextVar[str] = ContextVar("correlation_id", default="")
run_id_var: ContextVar[str] = ContextVar("run_id", default="")


# ══════════════════════════════════════════════════════════════════════
#  JSON log formatter
# ══════════════════════════════════════════════════════════════════════

class JSONFormatter(logging.Formatter):
    """Emit each log record as a single JSON line (ACA log-stream friendly)."""

    def format(self, record: logging.LogRecord) -> str:  # noqa: A003
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "correlationId": correlation_id_var.get(""),
            "runId": run_id_var.get(""),
        }
        if record.exc_info and record.exc_info[1]:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


# ══════════════════════════════════════════════════════════════════════
#  Logging setup
# ══════════════════════════════════════════════════════════════════════

def setup_logging() -> None:
    """Configure the root logger with a JSON console handler."""
    root = logging.getLogger()
    root.setLevel(getattr(logging, engine_settings.log_level.upper(), logging.INFO))
    root.handlers.clear()

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JSONFormatter())
    root.addHandler(handler)

    # Silence noisy libraries
    for noisy in ("azure", "urllib3", "httpcore", "httpx", "opentelemetry", "uamqp"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


# ══════════════════════════════════════════════════════════════════════
#  OpenTelemetry initialisation
# ══════════════════════════════════════════════════════════════════════

def init_opentelemetry() -> None:
    """Bootstrap OTel tracing when an OTLP endpoint is configured."""
    endpoint = engine_settings.otel_exporter_otlp_endpoint
    if not endpoint:
        logging.getLogger(__name__).info(
            "OTEL_EXPORTER_OTLP_ENDPOINT not set – OpenTelemetry disabled."
        )
        return

    try:
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanExporter
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource

        resource = Resource.create({"service.name": "awreason-queue-worker"})
        provider = TracerProvider(resource=resource)
        exporter = OTLPSpanExporter(endpoint=endpoint)
        provider.add_span_processor(BatchSpanExporter(exporter))
        trace.set_tracer_provider(provider)

        logging.getLogger(__name__).info(
            "OpenTelemetry tracing initialised (endpoint=%s).", endpoint
        )
    except ImportError:
        logging.getLogger(__name__).warning(
            "OpenTelemetry packages not installed – telemetry disabled."
        )
    except Exception:
        logging.getLogger(__name__).exception("Failed to initialise OpenTelemetry.")


def get_logger(name: str) -> logging.Logger:
    """Return a named logger (convenience)."""
    return logging.getLogger(name)
