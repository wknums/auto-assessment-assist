# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE.md file in the project root for full license information.
"""
Azure Blob Storage helpers authenticated with Managed Identity.

Corporate policy: **SAS tokens are not permitted**. All access uses
``DefaultAzureCredential`` (MI in ACA, ``az login`` locally).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient, ContainerClient

from app.config import settings
from app.telemetry import get_logger

logger = get_logger(__name__)

# Module-level lazy singleton
_credential: Optional[DefaultAzureCredential] = None
_blob_service: Optional[BlobServiceClient] = None


def _get_credential() -> DefaultAzureCredential:
    global _credential
    if _credential is None:
        kwargs = {}
        if settings.azure_tenant_id:
            kwargs["exclude_visual_studio_code_credential"] = False
            kwargs["exclude_cli_credential"] = False
        _credential = DefaultAzureCredential(**kwargs)
    return _credential


def _get_blob_service() -> BlobServiceClient:
    global _blob_service
    if _blob_service is None:
        if not settings.blob_account_url:
            raise RuntimeError(
                "AZ_STORAGE_NAME is not configured. "
                "Set it to the storage account name (e.g. mystorageaccount)."
            )
        _blob_service = BlobServiceClient(
            account_url=settings.blob_account_url,
            credential=_get_credential(),
        )
        logger.info("BlobServiceClient initialised for %s", settings.blob_account_url)
    return _blob_service


def _container_client(container_name: str) -> ContainerClient:
    return _get_blob_service().get_container_client(container_name)


# ══════════════════════════════════════════════════════════════════════
#  Public helpers
# ══════════════════════════════════════════════════════════════════════

def parse_blob_uri(uri: str) -> tuple[str, str, str]:
    """Parse ``https://<acct>.blob.core.windows.net/<container>/<blob>``
    and return ``(account_url, container, blob_path)``.
    """
    parsed = urlparse(uri)
    account_url = f"{parsed.scheme}://{parsed.hostname}"
    parts = parsed.path.lstrip("/").split("/", 1)
    if len(parts) < 2:
        raise ValueError(f"Cannot parse blob URI – expected /<container>/<blob>: {uri}")
    return account_url, parts[0], parts[1]


async def download_blob_to_path(uri: str, dest_path: str | Path) -> Path:
    """Download a blob (by full URI) to a local file.

    Uses the service-level credential so the caller does not need to handle
    SAS / keys.  Returns the destination ``Path``.
    """
    account_url, container, blob_name = parse_blob_uri(uri)
    dest = Path(dest_path)
    dest.parent.mkdir(parents=True, exist_ok=True)

    # If the URI points at a different account we create a one-off client,
    # otherwise reuse the singleton.
    if account_url.rstrip("/") == settings.blob_account_url.rstrip("/"):
        client = _container_client(container)
    else:
        client = BlobServiceClient(
            account_url=account_url, credential=_get_credential()
        ).get_container_client(container)

    blob_client = client.get_blob_client(blob_name)
    logger.info("Downloading blob %s/%s → %s", container, blob_name, dest)

    with open(dest, "wb") as fh:
        stream = blob_client.download_blob()
        stream.readinto(fh)

    logger.info("Downloaded %s (%d bytes)", dest.name, dest.stat().st_size)
    return dest


async def upload_file_return_uri(
    local_path: str | Path,
    dest_blob_path: str,
    container_name: str | None = None,
) -> str:
    """Upload a local file to Blob Storage and return its public URI (no SAS).

    Parameters
    ----------
    local_path : str | Path
        File on the local file-system.
    dest_blob_path : str
        Blob name (including any virtual directory prefix).
    container_name : str, optional
        Target container; defaults to ``BLOB_CONTAINER_RESULTS``.

    Returns
    -------
    str
        Full blob URI (``https://<acct>.blob.core.windows.net/<container>/<blob>``).
    """
    container = container_name or settings.blob_container_results
    client = _container_client(container).get_blob_client(dest_blob_path)

    local = Path(local_path)
    logger.info("Uploading %s → %s/%s", local.name, container, dest_blob_path)

    with open(local, "rb") as fh:
        client.upload_blob(fh, overwrite=True)

    uri = f"{settings.blob_account_url.rstrip('/')}/{container}/{dest_blob_path}"
    logger.info("Uploaded → %s", uri)
    return uri


async def upload_bytes_return_uri(
    data: bytes,
    dest_blob_path: str,
    container_name: str | None = None,
) -> str:
    """Upload raw bytes to Blob Storage and return its URI (no SAS).

    Like :func:`upload_file_return_uri` but accepts in-memory ``bytes``
    instead of a file path.
    """
    container = container_name or settings.blob_container_results
    client = _container_client(container).get_blob_client(dest_blob_path)

    logger.info("Uploading %d bytes → %s/%s", len(data), container, dest_blob_path)
    client.upload_blob(data, overwrite=True)

    uri = f"{settings.blob_account_url.rstrip('/')}/{container}/{dest_blob_path}"
    logger.info("Uploaded → %s", uri)
    return uri


def ensure_container_exists(container_name: str | None = None) -> None:
    """Create the blob container if it does not already exist.

    Requires the identity to hold **Storage Blob Data Contributor** (or
    higher) on the storage account – consistent with our RBAC-only policy.
    """
    container = container_name or settings.blob_container_results
    cc = _container_client(container)
    try:
        cc.get_container_properties()
        logger.debug("Container '%s' already exists.", container)
    except Exception:
        logger.info("Container '%s' not found – creating.", container)
        cc.create_container()
        logger.info("Container '%s' created.", container)


def can_resolve_credential() -> bool:
    """Cheap liveness check – can we obtain a token?  Used by ``/ready``."""
    try:
        _get_credential().get_token("https://storage.azure.com/.default")
        return True
    except Exception:
        logger.warning("Credential resolution check failed.", exc_info=True)
        return False
