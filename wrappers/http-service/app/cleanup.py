# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE.md file in the project root for full license information.
"""
Per-run working-directory lifecycle management.

Provides ``new_run_workdir`` – an async context manager that:

1. Creates ``<base>/run-<GUID>`` (one unique folder per set of inputs).
2. Yields the path for use during the run.
3. Defers cleanup so run folders remain available for debugging:
   - Individual folders are removed only after **24 hours**.
   - If the ``<base>`` folder exceeds **1 GB**, folders older than
     **12 hours** are pruned to reclaim space.
"""
from __future__ import annotations

import os
import shutil
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

from app.telemetry import get_logger

logger = get_logger(__name__)

# Minimum free space (bytes) we require before starting a run.
_MIN_FREE_BYTES = 50 * 1024 * 1024  # 50 MiB

# Cleanup thresholds
_MAX_AGE_SECONDS = 24 * 60 * 60       # 24 h – default max folder lifetime
_PRESSURE_AGE_SECONDS = 12 * 60 * 60  # 12 h – reduced lifetime under space pressure
_MAX_BASE_SIZE_BYTES = 1 * 1024 * 1024 * 1024  # 1 GB – triggers pressure cleanup


def _check_disk_space(path: str) -> None:
    """Raise if the volume has less than ``_MIN_FREE_BYTES`` free."""
    try:
        stat = shutil.disk_usage(path)
        if stat.free < _MIN_FREE_BYTES:
            raise OSError(
                f"Insufficient ephemeral space on {path}: "
                f"{stat.free / (1024 * 1024):.1f} MiB free, "
                f"need at least {_MIN_FREE_BYTES / (1024 * 1024):.0f} MiB."
            )
    except FileNotFoundError:
        # disk_usage may fail if the path doesn't exist yet – that's OK.
        pass


def _dir_size(path: Path) -> int:
    """Return total size in bytes of all files under *path*."""
    total = 0
    try:
        for entry in path.rglob("*"):
            if entry.is_file():
                try:
                    total += entry.stat().st_size
                except OSError:
                    pass
    except OSError:
        pass
    return total


def _folder_age_seconds(path: Path) -> float:
    """Return the age of *path* in seconds based on its creation/birth time."""
    try:
        st = path.stat()
        # Prefer birth time (st_birthtime on macOS / st_ctime on Windows);
        # fall back to st_mtime on Linux where ctime means inode-change time.
        created = getattr(st, "st_birthtime", None) or st.st_ctime
        return time.time() - created
    except OSError:
        return 0.0


def _cleanup_old_runs(base: Path) -> None:
    """Remove stale ``run-*`` folders under *base*.

    Two modes:
    1. **Normal** – remove folders older than 24 h.
    2. **Pressure** – if total size of *base* exceeds 1 GB, also remove
       folders older than 12 h.
    """
    now = time.time()
    run_dirs = sorted(base.glob("run-*"), key=lambda p: p.stat().st_ctime if p.exists() else 0)
    if not run_dirs:
        return

    base_size = _dir_size(base)
    under_pressure = base_size > _MAX_BASE_SIZE_BYTES
    age_threshold = _PRESSURE_AGE_SECONDS if under_pressure else _MAX_AGE_SECONDS

    if under_pressure:
        logger.info(
            "Workdir pressure cleanup: %s is %.1f MB (> %.0f MB threshold). "
            "Removing run folders older than %d h.",
            base, base_size / (1024 * 1024),
            _MAX_BASE_SIZE_BYTES / (1024 * 1024),
            _PRESSURE_AGE_SECONDS // 3600,
        )

    for d in run_dirs:
        if not d.is_dir():
            continue
        age = _folder_age_seconds(d)
        if age > age_threshold:
            logger.info(
                "Removing stale run folder %s (age %.1f h, threshold %d h).",
                d.name, age / 3600, age_threshold // 3600,
            )
            _safe_rmtree(d)


@asynccontextmanager
async def new_run_workdir(
    base: str = "/work",
    run_id: str | None = None,
) -> AsyncGenerator[Path, None]:
    """Create a unique working directory; cleanup is deferred.

    Parameters
    ----------
    base : str
        Root of the ephemeral volume (``/work`` in ACA).
    run_id : str, optional
        If provided the folder is named ``run-<run_id>``; otherwise a new
        UUID is generated.

    Yields
    ------
    Path
        The created directory, e.g. ``/work/run-abc123``.

    Notes
    -----
    Run folders are **not** deleted on context-manager exit.  Instead a
    background sweep removes folders older than 24 h (or 12 h when the
    base exceeds 1 GB).  This keeps run artefacts available for debugging
    and log correlation.
    """
    rid = run_id or uuid.uuid4().hex
    run_dir = Path(base) / f"run-{rid}"

    # Ensure base exists (e.g. first run on a fresh replica)
    os.makedirs(base, exist_ok=True)
    _check_disk_space(base)

    run_dir.mkdir(parents=True, exist_ok=True)
    logger.info("Created run workdir: %s", run_dir)

    try:
        yield run_dir
    finally:
        # Don't delete immediately – sweep old folders instead.
        _cleanup_old_runs(Path(base))


def _safe_rmtree(path: Path) -> None:
    """Remove a directory tree, swallowing errors but logging them."""
    try:
        if path.exists():
            shutil.rmtree(path)
            logger.info("Cleaned up workdir: %s", path)
    except Exception:
        logger.exception("Failed to clean up workdir %s", path)
