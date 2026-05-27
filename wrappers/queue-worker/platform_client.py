# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE.md file in the project root for full license information.
"""
HTTP client for reporting run results to the awr-platform via
``PATCH /runs/{runId}``.

Used when ``REPORT_MODE="http"``.  Acquires an AAD bearer token for the
configured ``PLATFORM_AUDIENCE`` and applies simple exponential backoff
on 429/5xx responses.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

import httpx
from azure.identity import DefaultAzureCredential

from contracts.models import FinishRunRequest
from runtime.config import engine_settings

logger = logging.getLogger(__name__)

_credential: Optional[DefaultAzureCredential] = None


def _get_credential() -> DefaultAzureCredential:
    global _credential
    if _credential is None:
        _credential = DefaultAzureCredential()
    return _credential


def _get_bearer_token() -> str:
    """Acquire an AAD token for the platform audience."""
    audience = engine_settings.platform_audience
    if not audience:
        raise RuntimeError("PLATFORM_AUDIENCE must be set when REPORT_MODE='http'.")
    token = _get_credential().get_token(f"{audience}/.default")
    return token.token


def patch_run(
    run_id: str,
    body: FinishRunRequest,
    correlation_id: str,
    traceparent: str = "",
    *,
    max_retries: int = 4,
    base_backoff: float = 2.0,
) -> None:
    """Send ``PATCH /runs/{run_id}`` to the platform with retry/backoff.

    Raises on exhausted retries so the caller can dead-letter the message.
    """
    base_url = engine_settings.platform_api_base_url
    if not base_url:
        raise RuntimeError("PLATFORM_API_BASE_URL must be set when REPORT_MODE='http'.")

    url = f"{base_url.rstrip('/')}/runs/{run_id}"
    headers = {
        "Content-Type": "application/json",
        "X-Correlation-ID": correlation_id,
    }
    if traceparent:
        headers["traceparent"] = traceparent

    for attempt in range(1, max_retries + 1):
        try:
            token = _get_bearer_token()
            headers["Authorization"] = f"Bearer {token}"

            with httpx.Client(timeout=30) as client:
                resp = client.patch(
                    url,
                    content=body.model_dump_json(),
                    headers=headers,
                )

            if resp.status_code < 300:
                logger.info(
                    "PATCH /runs/%s succeeded (status=%d).", run_id, resp.status_code
                )
                return

            # Retryable status codes
            if resp.status_code in (429, 500, 502, 503, 504):
                retry_after = _parse_retry_after(resp)
                wait = retry_after or (base_backoff * (2 ** (attempt - 1)))
                logger.warning(
                    "PATCH /runs/%s returned %d – retrying in %.1fs (attempt %d/%d).",
                    run_id,
                    resp.status_code,
                    wait,
                    attempt,
                    max_retries,
                )
                time.sleep(wait)
                continue

            # Non-retryable error
            resp.raise_for_status()

        except httpx.HTTPStatusError:
            raise
        except Exception as exc:
            if attempt < max_retries:
                wait = base_backoff * (2 ** (attempt - 1))
                logger.warning(
                    "PATCH /runs/%s failed (%s) – retrying in %.1fs (attempt %d/%d).",
                    run_id,
                    exc,
                    wait,
                    attempt,
                    max_retries,
                )
                time.sleep(wait)
            else:
                raise

    raise RuntimeError(
        f"PATCH /runs/{run_id} failed after {max_retries} attempts."
    )


def _parse_retry_after(resp: httpx.Response) -> Optional[float]:
    """Parse ``Retry-After`` header if present."""
    val = resp.headers.get("Retry-After")
    if val:
        try:
            return float(val)
        except ValueError:
            pass
    return None
