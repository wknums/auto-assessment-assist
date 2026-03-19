# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE.md file in the project root for full license information.
"""
FastAPI application factory, lifespan hooks, and router registration.

This is the entry-point module:  ``uvicorn app.main:app``
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import assess_router, health_router
from app.config import settings
from app.models import ProblemDetail
from app.telemetry import (
    correlation_id_var,
    init_opentelemetry,
    setup_logging,
    get_logger,
)

logger = get_logger(__name__)


# ══════════════════════════════════════════════════════════════════════
#  Lifespan (startup / shutdown)
# ══════════════════════════════════════════════════════════════════════

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Run setup on startup, yield while accepting requests, then tear down."""
    # ── Startup ──────────────────────────────────────────────────────
    setup_logging()
    init_opentelemetry()

    # Create workdir base if it doesn't exist yet
    os.makedirs(settings.workdir_base, exist_ok=True)

    logger.info(
        "Service started (concurrency=%d, workdir=%s, auth=%s, apim=%s).",
        settings.per_replica_concurrency,
        settings.workdir_base,
        settings.auth_mode,
        bool(settings.apim_aoai_base_url),
    )

    yield  # ← application serves requests here

    # ── Shutdown ─────────────────────────────────────────────────────
    logger.info("Service shutting down.")


# ══════════════════════════════════════════════════════════════════════
#  Application instance
# ══════════════════════════════════════════════════════════════════════

app = FastAPI(
    title="awreason HTTP Service",
    description="Cloud-ready HTTP wrapper around the awreason assessment engine.",
    version="0.1.0",
    lifespan=lifespan,
)


# ── CORS (adjust origins in production) ──────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # TODO: restrict to allowed origins in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=[
        "X-AWR-Exit-Code",
        "X-AWR-Duration-Ms",
        "X-AWR-Output-Filename",
        "X-AWR-Run-Id",
        "X-Correlation-Id",
        "X-Correlation-ID",
        "Content-Disposition",
    ],
)


# ── Correlation-ID response header ───────────────────────────────────

@app.middleware("http")
async def add_correlation_header(request: Request, call_next):
    """Echo the correlation ID back in every response."""
    response = await call_next(request)
    cid = correlation_id_var.get("")
    if cid:
        response.headers["X-Correlation-ID"] = cid
    return response


# ── Global exception handler → problem+json ──────────────────────────

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Catch-all returning RFC 7807 problem+json; never leaks secrets."""
    logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
    body = ProblemDetail(
        title="Internal Server Error",
        status=500,
        detail="An unexpected error occurred. Check correlationId in logs.",
        instance=str(request.url.path),
        correlation_id=correlation_id_var.get(""),
    )
    return JSONResponse(
        status_code=500,
        content=body.model_dump(by_alias=True),
        media_type="application/problem+json",
    )


# ── Register routers ─────────────────────────────────────────────────

app.include_router(health_router)
app.include_router(assess_router)
