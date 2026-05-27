# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE.md file in the project root for full license information.
"""
MI-based Azure Blob Storage I/O helpers.

Corporate policy: **SAS tokens are not permitted**.  All access uses
``DefaultAzureCredential`` (Managed Identity in ACA, ``az login`` locally).
"""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Optional, Tuple
from urllib.parse import urlparse

from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient, ContainerClient

from runtime.config import engine_settings

logger = logging.getLogger(__name__)

# Module-level lazy singletons
_credential: Optional[DefaultAzureCredential] = None
_blob_service: Optional[BlobServiceClient] = None


def _get_credential() -> DefaultAzureCredential:
    global _credential
    if _credential is None:
        kwargs: dict = {}
        if engine_settings.azure_tenant_id:
            kwargs["exclude_visual_studio_code_credential"] = False
            kwargs["exclude_cli_credential"] = False
        _credential = DefaultAzureCredential(**kwargs)
    return _credential


def _get_blob_service() -> BlobServiceClient:
    global _blob_service
    if _blob_service is None:
        if not engine_settings.blob_account_url:
            raise RuntimeError(
                "BLOB_ACCOUNT_URL is not configured.  "
                "Set it to the storage account URL (e.g. https://<account>.blob.core.windows.net)."
            )
        _blob_service = BlobServiceClient(
            account_url=engine_settings.blob_account_url,
            credential=_get_credential(),
        )
        logger.info("BlobServiceClient initialised for %s", engine_settings.blob_account_url)
    return _blob_service


def _container_client(container_name: str) -> ContainerClient:
    return _get_blob_service().get_container_client(container_name)


# ══════════════════════════════════════════════════════════════════════
#  URI helpers
# ══════════════════════════════════════════════════════════════════════

def parse_blob_uri(uri: str) -> Tuple[str, str, str]:
    """Parse ``https://<acct>.blob.core.windows.net/<container>/<blob>``
    and return ``(account_url, container, blob_path)``.
    """
    parsed = urlparse(uri)
    account_url = f"{parsed.scheme}://{parsed.hostname}"
    parts = parsed.path.lstrip("/").split("/", 1)
    if len(parts) < 2:
        raise ValueError(f"Cannot parse blob URI – expected /<container>/<blob>: {uri}")
    return account_url, parts[0], parts[1]


# ══════════════════════════════════════════════════════════════════════
#  Download
# ══════════════════════════════════════════════════════════════════════

def download_blob_to_path(uri: str, dest_path: str | Path) -> Path:
    """Download a blob (by full URI) to a local file.  Returns the ``Path``."""
    account_url, container, blob_name = parse_blob_uri(uri)
    dest = Path(dest_path)
    dest.parent.mkdir(parents=True, exist_ok=True)

    # Reuse singleton when same account, else one-off client
    if account_url.rstrip("/") == engine_settings.blob_account_url.rstrip("/"):
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


# ══════════════════════════════════════════════════════════════════════
#  Upload
# ══════════════════════════════════════════════════════════════════════

def upload_file_return_uri(
    local_path: str | Path,
    dest_blob_path: str,
    container_name: str | None = None,
) -> str:
    """Upload a local file to Blob Storage and return its full URI (no SAS)."""
    container = container_name or engine_settings.blob_results_container
    client = _container_client(container).get_blob_client(dest_blob_path)

    local = Path(local_path)
    logger.info("Uploading %s → %s/%s", local.name, container, dest_blob_path)

    with open(local, "rb") as fh:
        client.upload_blob(fh, overwrite=True)

    uri = f"{engine_settings.blob_account_url.rstrip('/')}/{container}/{dest_blob_path}"
    logger.info("Uploaded → %s", uri)
    return uri


# ══════════════════════════════════════════════════════════════════════
#  Utilities
# ══════════════════════════════════════════════════════════════════════

def file_sha256(path: Path) -> str:
    """Return hex SHA-256 digest of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def blob_exists(container: str, blob_path: str) -> bool:
    """Check whether a blob exists (cheap HEAD call)."""
    try:
        client = _container_client(container).get_blob_client(blob_path)
        client.get_blob_properties()
        return True
    except Exception:
        return False


def upload_json_marker(container: str, blob_path: str, data: dict) -> str:
    """Upload a small JSON marker blob and return its URI."""
    import json

    client = _container_client(container).get_blob_client(blob_path)
    body = json.dumps(data, default=str).encode("utf-8")
    client.upload_blob(body, overwrite=True)

    uri = f"{engine_settings.blob_account_url.rstrip('/')}/{container}/{blob_path}"
    logger.info("Marker uploaded → %s", uri)
    return uri
