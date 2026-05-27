# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE.md file in the project root for full license information.
"""
Per-run working-directory lifecycle management.

Provides ``new_run_workdir`` – a **synchronous** context manager that:

1. Creates ``<base>/run-<GUID>`` (one unique folder per set of inputs).
2. Yields the ``Path`` for use during the run.
3. Recursively removes the folder in a ``finally`` block, regardless of
   success or failure.

An async variant ``anew_run_workdir`` is included for callers running
inside an ``asyncio`` event loop.
"""
from __future__ import annotations

import os
import shutil
import uuid
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import AsyncGenerator, Generator

from runtime.telemetry import get_logger

logger = get_logger(__name__)

# Minimum free space (bytes) we require before starting a run.
_MIN_FREE_BYTES = 50 * 1024 * 1024  # 50 MiB


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
        pass


def _safe_rmtree(path: Path) -> None:
    """Remove a directory tree, swallowing errors but logging them."""
    try:
        if path.exists():
            shutil.rmtree(path)
            logger.info("Cleaned up workdir: %s", path)
    except Exception:
        logger.exception("Failed to clean up workdir %s", path)


@contextmanager
def new_run_workdir(
    base: str = "/work",
    run_id: str | None = None,
) -> Generator[Path, None, None]:
    """Create a unique working directory and guarantee cleanup.

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
    """
    rid = run_id or uuid.uuid4().hex
    run_dir = Path(base) / f"run-{rid}"

    os.makedirs(base, exist_ok=True)
    _check_disk_space(base)

    run_dir.mkdir(parents=True, exist_ok=True)
    logger.info("Created run workdir: %s", run_dir)

    try:
        yield run_dir
    finally:
        _safe_rmtree(run_dir)


@asynccontextmanager
async def anew_run_workdir(
    base: str = "/work",
    run_id: str | None = None,
) -> AsyncGenerator[Path, None]:
    """Async wrapper around ``new_run_workdir`` for convenience."""
    with new_run_workdir(base=base, run_id=run_id) as run_dir:
        yield run_dir
