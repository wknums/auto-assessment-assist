# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE.md file in the project root for full license information.
"""
Application configuration loaded exclusively from environment variables.

Uses pydantic-settings so every field maps to an env var.
Secrets **must not** appear in source; they are injected at runtime by
Azure Container Apps or a local .env file.
"""
from __future__ import annotations

from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    """Central configuration – every field is driven by an env var."""

    # ── General ───────────────────────────────────────────────────────
    app_name: str = Field("awreason-http-service", alias="APP_NAME")
    log_level: str = Field("INFO", alias="LOG_LEVEL")

    # ── Working directory (EmptyDir in ACA) ───────────────────────────
    workdir_base: str = Field("/work", alias="WORKDIR_BASE")

    # ── Concurrency ───────────────────────────────────────────────────
    per_replica_concurrency: int = Field(4, alias="PER_REPLICA_CONCURRENCY")

    # ── Request status tracking ───────────────────────────────────────
    active_request_ids_dir: str = Field("", alias="ACTIVE_REQUEST_IDS_DIR")

    # ── Azure Blob Storage (MI-based auth – no SAS) ───────────────────
    az_storage_name: str = Field("", alias="AZ_STORAGE_NAME")
    az_storage_rg: str = Field("", alias="AZ_STORAGE_RG")
    blob_container_results: str = Field("results", alias="BLOB_CONTAINER_RESULTS")
    blob_container_uploads: str = Field("uploads", alias="BLOB_CONTAINER_UPLOADS")

    @property
    def blob_account_url(self) -> str:
        """Derive the Blob account URL from AZ_STORAGE_NAME.

        Returns an empty string when no storage account is configured,
        which disables blob operations (local-only mode).
        """
        if self.az_storage_name:
            return f"https://{self.az_storage_name}.blob.core.windows.net"
        return ""

    # ── Azure OpenAI / APIM AI Gateway ────────────────────────────────
    apim_aoai_base_url: str = Field("", alias="APIM_AOAI_BASE_URL")
    aoai_deployment: str = Field("o1", alias="AOAI_DEPLOYMENT")
    aoai_api_version: str = Field("2024-12-01-preview", alias="AOAI_API_VERSION")
    use_aad_for_aoai: bool = Field(True, alias="USE_AAD_FOR_AOAI")

    # Direct Azure OpenAI (used when APIM is not configured)
    azure_openai_endpoint: str = Field("", alias="AZURE_OPENAI_ENDPOINT")
    azure_openai_api_key: str = Field("", alias="AZURE_OPENAI_API_KEY")

    # ── Auth ─────────────────────────────────────────────────────────
    # AUTH_MODE controls how the API authenticates requests:
    #   "none"   – no auth (local dev)
    #   "apikey" – shared secret via X-Api-Key header (test / staging)
    #   "entra"  – Entra ID JWT bearer token (production)
    auth_mode: str = Field("none", alias="AUTH_MODE")
    api_key: str = Field("", alias="API_KEY")
    aad_issuer: str = Field("", alias="AAD_ISSUER")
    aad_audience: str = Field("", alias="AAD_AUDIENCE")
    aad_required_scope: str = Field("access_as_user", alias="AAD_REQUIRED_SCOPE")
    aad_required_app_role: str = Field(
        "TalentMatch.Access", alias="AAD_REQUIRED_APP_ROLE"
    )

    # ── Awreason runner ───────────────────────────────────────────────
    awreason_cli_cmd: str = Field("awreason.py", alias="AWREASON_CLI_CMD")
    awreason_cli_timeout: int = Field(500, alias="AWREASON_CLI_TIMEOUT")
    awreason_max_retries: int = Field(3, alias="AWREASON_MAX_RETRIES")
    awreason_retry_backoff: int = Field(10, alias="AWREASON_RETRY_BACKOFF")

    # ── Telemetry (OTLP) ─────────────────────────────────────────────
    otel_exporter_otlp_endpoint: str = Field("", alias="OTEL_EXPORTER_OTLP_ENDPOINT")
    otel_service_name: str = Field("awreason-http-service", alias="OTEL_SERVICE_NAME")

    # ── Azure identity ────────────────────────────────────────────────
    azure_tenant_id: str = Field("", alias="AZURE_TENANT_ID")
    azure_subscription_id: str = Field("", alias="AZURE_SUBSCRIPTION_ID")

    # ── Aggregation defaults ──────────────────────────────────────────
    default_aggregation_method: str = Field("median", alias="DEFAULT_AGGREGATION_METHOD")

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        populate_by_name = True  # allow both alias and field name
        extra = "ignore"  # ignore env vars not defined in this model


# Module-level singleton – import `settings` elsewhere.
settings = Settings()
