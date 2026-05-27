# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE.md file in the project root for full license information.
"""
Blob-marker based idempotency check.

Before starting a run the worker checks for an existing
``<results_prefix>/<run_id>/_marker.json`` blob.  If the marker exists
the message is a duplicate delivery and processing is skipped.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from azure.core.exceptions import ResourceNotFoundError
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient
from engine_core.blob_io import blob_exists
from runtime.config import engine_settings

logger = logging.getLogger(__name__)


def is_duplicate_run(
    run_id: str,
    results_container: str | None = None,
    results_prefix: str | None = None,
) -> bool:
    """Return ``True`` if the idempotency marker already exists for *run_id*.

    Falls back to ``engine_settings`` for container / prefix if not provided.
    """
    container = results_container or engine_settings.blob_results_container
    prefix = results_prefix or engine_settings.blob_results_prefix
    marker_path = f"{prefix}/{run_id}/_marker.json"

    exists = blob_exists(container, marker_path)
    if exists:
        logger.info(
            "Idempotency marker found for run_id=%s – skipping duplicate.",
            run_id,
        )
    return exists


def load_run_marker(
    run_id: str,
    results_container: str | None = None,
    results_prefix: str | None = None,
) -> dict[str, Any] | None:
    """Load marker payload for *run_id* if available."""
    container = results_container or engine_settings.blob_results_container
    prefix = results_prefix or engine_settings.blob_results_prefix
    marker_path = f"{prefix}/{run_id}/_marker.json"

    if not engine_settings.blob_account_url:
        return None

    service = BlobServiceClient(
        account_url=engine_settings.blob_account_url,
        credential=DefaultAzureCredential(),
    )
    client = service.get_blob_client(container=container, blob=marker_path)
    try:
        raw = client.download_blob().readall()
    except ResourceNotFoundError:
        return None
    except Exception:
        logger.exception("Failed reading marker for run_id=%s", run_id)
        return None

    try:
        parsed = json.loads(raw)
    except Exception:
        logger.exception("Invalid marker json for run_id=%s", run_id)
        return None
    return parsed if isinstance(parsed, dict) else None
