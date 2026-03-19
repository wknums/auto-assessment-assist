# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE.md file in the project root for full license information.
"""
OpenTelemetry bootstrap and structured JSON logging.

- Traces and metrics are exported via OTLP when ``OTEL_EXPORTER_OTLP_ENDPOINT`` is set.
- Logs are always emitted as JSON lines to stdout for ACA log-stream ingestion.
- Every log record carries ``correlationId``, ``jobId``, ``applicationId``,
  and ``runId`` when available (pulled from contextvars).
"""
from __future__ import annotations

import logging
import logging.handlers
import os
import sys
import json
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app.config import settings

# ── Context variables (set per-request by middleware / deps) ──────────
correlation_id_var: ContextVar[str] = ContextVar("correlation_id", default="")
job_id_var: ContextVar[str] = ContextVar("job_id", default="")
application_id_var: ContextVar[str] = ContextVar("application_id", default="")
run_id_var: ContextVar[str] = ContextVar("run_id", default="")


# ══════════════════════════════════════════════════════════════════════
#  JSON log formatter
# ══════════════════════════════════════════════════════════════════════

class JSONFormatter(logging.Formatter):
    """Emit each log record as a single JSON line."""

    def format(self, record: logging.LogRecord) -> str:  # noqa: A003
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "correlationId": correlation_id_var.get(""),
            "jobId": job_id_var.get(""),
            "applicationId": application_id_var.get(""),
            "runId": run_id_var.get(""),
        }
        if record.exc_info and record.exc_info[1]:
            log_entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_entry, default=str)


# ══════════════════════════════════════════════════════════════════════
#  Logging setup
# ══════════════════════════════════════════════════════════════════════

def setup_logging() -> None:
    """Configure the root logger with a JSON console handler and a file handler."""
    root = logging.getLogger()
    root.setLevel(getattr(logging, settings.log_level.upper(), logging.INFO))
    root.handlers.clear()

    # ── Console handler (JSON to stdout) ──────────────────────────────
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JSONFormatter())
    root.addHandler(handler)

    # ── File handler (logs/ folder at repo root) ─────────────────────
    # Resolve logs dir relative to the repo root (3 levels up from this file)
    repo_root = Path(__file__).resolve().parents[3]
    logs_dir = repo_root / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    from datetime import date as _date
    log_filename = logs_dir / f"awreason_api_{_date.today().strftime('%Y%m%d')}.log"
    file_handler = logging.handlers.RotatingFileHandler(
        log_filename,
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=30,
        encoding="utf-8",
    )
    file_formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s - %(message)s"
    )
    file_handler.setFormatter(file_formatter)
    root.addHandler(file_handler)

    # Silence noisy libraries
    for noisy in ("azure", "urllib3", "httpcore", "httpx", "opentelemetry"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    root.info("API logging initialised: level=%s, file=%s", settings.log_level, log_filename)


# ══════════════════════════════════════════════════════════════════════
#  OpenTelemetry initialisation
# ══════════════════════════════════════════════════════════════════════

def init_opentelemetry() -> None:
    """Bootstrap OTel tracing + metrics when an OTLP endpoint is configured."""
    endpoint = settings.otel_exporter_otlp_endpoint
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
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor  # noqa: F401 – kept for ref

        resource = Resource.create({"service.name": settings.otel_service_name})
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
