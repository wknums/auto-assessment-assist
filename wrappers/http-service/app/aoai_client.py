# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE.md file in the project root for full license information.
"""
Azure OpenAI client that optionally routes through Azure API Management
(APIM) acting as an AI Gateway.

Authentication uses Managed Identity (``DefaultAzureCredential``) to obtain
a bearer token for ``https://cognitiveservices.azure.com/.default`` –
no API keys are embedded in code.

Features
--------
- Transparent retry with exponential back-off for 429 and 5xx responses.
- Correlation-ID header propagation (``X-Correlation-ID``).
- Configurable via environment variables (see ``config.py``).
"""
from __future__ import annotations

import time
import logging
from typing import Any, Dict, Optional

from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from openai import AzureOpenAI

from app.config import settings
from app.telemetry import correlation_id_var, get_logger

logger = get_logger(__name__)

_COGNITIVE_SCOPE = "https://cognitiveservices.azure.com/.default"


class AOAIClient:
    """Thin wrapper around :class:`AzureOpenAI` with APIM + MI support."""

    def __init__(self) -> None:
        self._client: Optional[AzureOpenAI] = None
        self._credential: Optional[DefaultAzureCredential] = None

    # ── lazy init ─────────────────────────────────────────────────────

    def _ensure_client(self) -> AzureOpenAI:
        if self._client is not None:
            return self._client

        # Determine the endpoint: prefer APIM if configured, else direct.
        base_url = settings.apim_aoai_base_url or settings.azure_openai_endpoint
        if not base_url:
            raise RuntimeError(
                "Neither APIM_AOAI_BASE_URL nor AZURE_OPENAI_ENDPOINT is set."
            )

        if settings.use_aad_for_aoai or not settings.azure_openai_api_key:
            logger.info("AOAI client: using Entra ID (Managed Identity) auth.")
            self._credential = DefaultAzureCredential()
            token_provider = get_bearer_token_provider(
                self._credential, _COGNITIVE_SCOPE
            )
            self._client = AzureOpenAI(
                azure_endpoint=base_url,
                azure_ad_token_provider=token_provider,
                api_version=settings.aoai_api_version,
                default_headers=self._default_headers(),
            )
        else:
            logger.info("AOAI client: using API-key auth.")
            self._client = AzureOpenAI(
                azure_endpoint=base_url,
                api_key=settings.azure_openai_api_key,
                api_version=settings.aoai_api_version,
                default_headers=self._default_headers(),
            )

        logger.info(
            "AzureOpenAI client ready (endpoint=%s, deployment=%s, api_version=%s).",
            base_url,
            settings.aoai_deployment,
            settings.aoai_api_version,
        )
        return self._client

    # ── helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _default_headers() -> Dict[str, str]:
        """Headers added to every request (correlation, etc.)."""
        headers: Dict[str, str] = {}
        cid = correlation_id_var.get("")
        if cid:
            headers["X-Correlation-ID"] = cid
        return headers

    # ── public API ────────────────────────────────────────────────────

    def chat_completion(
        self,
        messages: list[dict[str, Any]],
        *,
        deployment: str | None = None,
        max_completion_tokens: int = 15000,
        reasoning_effort: str = "high",
        response_format: dict[str, str] | None = None,
        extra_params: dict[str, Any] | None = None,
    ) -> Any:
        """Call Azure OpenAI chat completions with retry logic.

        Parameters mirror the fields passed to
        ``client.chat.completions.create()``.

        Returns the raw ``ChatCompletion`` object.
        """
        client = self._ensure_client()
        model = deployment or settings.aoai_deployment

        params: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_completion_tokens": max_completion_tokens,
        }

        # reasoning_effort is only valid for some models/api versions
        if reasoning_effort and not response_format:
            params["reasoning_effort"] = reasoning_effort

        if response_format:
            params["response_format"] = response_format

        if extra_params:
            params.update(extra_params)

        # Retry loop with exponential back-off
        max_retries = settings.awreason_max_retries
        backoff = settings.awreason_retry_backoff

        last_exc: Exception | None = None
        for attempt in range(1, max_retries + 1):
            try:
                # Refresh correlation header each attempt
                cid = correlation_id_var.get("")
                if cid:
                    client._custom_headers["X-Correlation-ID"] = cid  # type: ignore[attr-defined]

                logger.info(
                    "AOAI request attempt %d/%d (model=%s, tokens_cap=%d).",
                    attempt, max_retries, model, max_completion_tokens,
                )
                completion = client.chat.completions.create(**params)
                logger.info("AOAI request succeeded on attempt %d.", attempt)
                return completion

            except Exception as exc:
                last_exc = exc
                status = getattr(exc, "status_code", None)
                retryable = status in (429, 500, 502, 503, 504) if status else True

                if attempt < max_retries and retryable:
                    wait = backoff * (2 ** (attempt - 1))
                    logger.warning(
                        "AOAI attempt %d failed (status=%s): %s – retrying in %ds.",
                        attempt, status, exc, wait,
                    )
                    time.sleep(wait)
                else:
                    logger.error(
                        "AOAI request failed after %d attempt(s): %s", attempt, exc
                    )
                    raise

        # Should not reach here, but just in case.
        raise last_exc  # type: ignore[misc]


# Module-level singleton
aoai_client = AOAIClient()
