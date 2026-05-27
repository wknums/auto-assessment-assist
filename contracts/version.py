# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE.md file in the project root for full license information.
"""
Contract version constant and optional startup validator.

On import the module logs the contract version.  If the env var
``PLATFORM_CONTRACT_URL`` is set, ``validate_against_platform()`` will
attempt to fetch the remote schema and *warn* (never hard-fail) when
field differences are detected.
"""
from __future__ import annotations

import logging
import os
from typing import Set

logger = logging.getLogger(__name__)

CONTRACT_VERSION = "v1"


def _our_field_names() -> Set[str]:
    """Return the union of all field names across the public contract models."""
    from contracts.models import FinishRunRequest, RunMessage, RunResultMessage

    names: Set[str] = set()
    for model in (RunMessage, RunResultMessage, FinishRunRequest):
        names.update(model.model_fields.keys())
    return names


def validate_against_platform() -> None:
    """Fetch remote schema and warn on mismatches (best-effort, no hard fail).

    Expects a JSON document at ``PLATFORM_CONTRACT_URL`` containing an
    ``openapi`` or ``$defs`` key with property names.  Only logs warnings
    – never raises.
    """
    url = os.getenv("PLATFORM_CONTRACT_URL", "")
    if not url:
        return

    try:
        import httpx

        resp = httpx.get(url, timeout=10)
        resp.raise_for_status()
        remote = resp.json()

        # Gather property names from OpenAPI components/schemas or JSON Schema $defs
        remote_fields: Set[str] = set()
        schemas = remote.get("components", {}).get("schemas", {})
        if not schemas:
            schemas = remote.get("$defs", {})
        for _name, schema in schemas.items():
            remote_fields.update(schema.get("properties", {}).keys())

        ours = _our_field_names()
        missing_locally = remote_fields - ours
        extra_locally = ours - remote_fields

        if missing_locally:
            logger.warning(
                "Contract drift: platform defines fields we lack: %s",
                sorted(missing_locally),
            )
        if extra_locally:
            logger.warning(
                "Contract drift: we define fields not in platform schema: %s",
                sorted(extra_locally),
            )
        if not missing_locally and not extra_locally:
            logger.info("Contract validation passed – shapes match platform.")

    except Exception:
        logger.warning(
            "Could not validate contracts against %s (non-fatal).", url, exc_info=True
        )


def log_version_and_validate() -> None:
    """Log ``CONTRACT_VERSION`` and optionally validate against the platform."""
    logger.info("AWReason engine contracts %s loaded.", CONTRACT_VERSION)
    validate_against_platform()
