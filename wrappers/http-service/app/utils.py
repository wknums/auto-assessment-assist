# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE.md file in the project root for full license information.
"""
Small utility helpers shared across the service.
"""
from __future__ import annotations

import mimetypes
import time
import uuid
from typing import Set


def new_guid() -> str:
    """Return a new UUID-4 hex string (no dashes)."""
    return uuid.uuid4().hex


def now_ms() -> int:
    """Return the current wall-clock time in milliseconds."""
    return int(time.time() * 1000)


def elapsed_ms(start_ms: int) -> int:
    """Return elapsed time in milliseconds since *start_ms*."""
    return now_ms() - start_ms


# ── MIME / extension checks ──────────────────────────────────────────

_ALLOWED_CONTENT_TYPES: Set[str] = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",  # .docx
    "text/markdown",
    "text/plain",
    "application/json",
    "application/octet-stream",
}

_ALLOWED_EXTENSIONS: Set[str] = {
    ".pdf", ".docx", ".md", ".txt", ".json",
}


def is_allowed_file(filename: str) -> bool:
    """Check whether the file extension is in the allow-list."""
    ext = _extension(filename)
    return ext in _ALLOWED_EXTENSIONS


def guess_content_type(filename: str) -> str:
    """Best-effort MIME type from extension."""
    ctype, _ = mimetypes.guess_type(filename)
    return ctype or "application/octet-stream"


def _extension(filename: str) -> str:
    return "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
