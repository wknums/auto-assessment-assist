# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE.md file in the project root for full license information.
"""Track actively processing request IDs in a filesystem folder."""
from __future__ import annotations

import hashlib
import json
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from app.config import settings
from app.telemetry import get_logger

logger = get_logger(__name__)


def get_tracking_dir() -> Path:
    """Return the request-tracking folder and ensure it exists."""
    configured = settings.active_request_ids_dir.strip()
    tracking_dir = Path(configured) if configured else Path(settings.workdir_base) / "active-requests"
    tracking_dir.mkdir(parents=True, exist_ok=True)
    return tracking_dir


def _marker_path(request_id: str) -> Path:
    digest = hashlib.sha256(request_id.encode("utf-8")).hexdigest()
    return get_tracking_dir() / f"{digest}.json"


def _load_marker(path: Path) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("Ignoring unreadable request marker file: %s", path)
        return None


def write_active_request(request_id: str, *, metadata: Optional[Dict[str, Any]] = None) -> None:
    """Write/update the marker file for an active request."""
    if not request_id:
        return

    payload: Dict[str, Any] = {
        "requestId": request_id,
        "active": True,
        "status": "processing",
        "startedAtEpochMs": int(time.time() * 1000),
    }
    if metadata:
        payload.update(metadata)

    marker = _marker_path(request_id)
    tmp = marker.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=True), encoding="utf-8")
    tmp.replace(marker)


def remove_active_request(request_id: str) -> None:
    """Remove the marker file for a request if present."""
    if not request_id:
        return

    marker = _marker_path(request_id)
    try:
        marker.unlink(missing_ok=True)
    except Exception:
        logger.warning("Failed to remove request marker file: %s", marker)


def get_request_status(request_id: str) -> Dict[str, Any]:
    """Return the status payload for a request ID."""
    base = {
        "requestId": request_id,
        "active": False,
        "status": "not_processing",
    }
    if not request_id:
        return base

    marker = _marker_path(request_id)
    if not marker.exists():
        return base

    marker_payload = _load_marker(marker) or {}
    marker_payload.setdefault("requestId", request_id)
    marker_payload["active"] = True
    marker_payload.setdefault("status", "processing")
    return marker_payload


def list_active_requests(limit: int = 200) -> List[Dict[str, Any]]:
    """Return active request payloads discovered in the tracking folder."""
    active: List[Dict[str, Any]] = []
    for marker in sorted(get_tracking_dir().glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        payload = _load_marker(marker)
        if not payload:
            continue
        payload.setdefault("active", True)
        payload.setdefault("status", "processing")
        active.append(payload)
        if len(active) >= limit:
            break
    return active


@contextmanager
def track_active_request(
    request_id: str,
    *,
    metadata: Optional[Dict[str, Any]] = None,
) -> Iterator[None]:
    """Create/remove a request marker around a processing block."""
    write_active_request(request_id, metadata=metadata)
    try:
        yield
    finally:
        remove_active_request(request_id)
