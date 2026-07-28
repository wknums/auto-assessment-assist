# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE.md file in the project root for full license information.
"""
Centralised configuration loaded **exclusively** from environment variables.

Uses ``pydantic-settings`` so every field maps to an env var.  Secrets
**must not** appear in source – they are injected at runtime by Azure
Container Apps (or a local ``.env`` for dev).
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import Field
from pydantic_settings import BaseSettings


class EngineSettings(BaseSettings):
    """Shared configuration for both the HTTP service and the queue worker.

    Every field is driven by an env var whose name matches the alias.
    """

    # ── Service Bus ───────────────────────────────────────────────────
    sb_namespace: str = Field("", alias="SB_NAMESPACE")
    sb_queue: str = Field("engine-runs", alias="SB_QUEUE")
    sb_results_queue: Optional[str] = Field(None, alias="SB_RESULTS_QUEUE")

    # ── Platform callback ─────────────────────────────────────────────
    report_mode: Literal["servicebus", "http"] = Field("servicebus", alias="REPORT_MODE")
    platform_api_base_url: Optional[str] = Field(None, alias="PLATFORM_API_BASE_URL")
    platform_audience: Optional[str] = Field(None, alias="PLATFORM_AUDIENCE")

    # ── Blob outputs ──────────────────────────────────────────────────
    blob_account_url: str = Field("", alias="BLOB_ACCOUNT_URL")
    blob_results_container: str = Field("results", alias="BLOB_RESULTS_CONTAINER")
    blob_results_prefix: str = Field("runs", alias="BLOB_RESULTS_PREFIX")

    # ── Azure OpenAI / APIM (optional – used by engine core) ─────────
    apim_aoai_base_url: Optional[str] = Field(None, alias="APIM_AOAI_BASE_URL")
    aoai_deployment: Optional[str] = Field(None, alias="AOAI_DEPLOYMENT")
    aoai_api_version: Optional[str] = Field(None, alias="AOAI_API_VERSION")

    # ── Execution ─────────────────────────────────────────────────────
    per_replica_concurrency: int = Field(2, alias="PER_REPLICA_CONCURRENCY")
    workdir_base: str = Field("/work", alias="WORKDIR_BASE")

    # ── Telemetry ─────────────────────────────────────────────────────
    otel_exporter_otlp_endpoint: Optional[str] = Field(None, alias="OTEL_EXPORTER_OTLP_ENDPOINT")
    log_level: str = Field("INFO", alias="LOG_LEVEL")

    # ── Contract validation ───────────────────────────────────────────
    platform_contract_url: Optional[str] = Field(None, alias="PLATFORM_CONTRACT_URL")

    # ── Azure identity ────────────────────────────────────────────────
    azure_tenant_id: str = Field("", alias="AZURE_TENANT_ID")

    # ── awreason engine ───────────────────────────────────────────────
    awreason_cli_timeout: int = Field(300, alias="AWREASON_CLI_TIMEOUT")
    awreason_max_retries: int = Field(3, alias="AWREASON_MAX_RETRIES")
    awreason_retry_backoff: int = Field(10, alias="AWREASON_RETRY_BACKOFF")

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        populate_by_name = True
        extra = "ignore"


# Module-level singleton – ``from runtime.config import engine_settings``
engine_settings = EngineSettings()
