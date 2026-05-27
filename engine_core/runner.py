# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE.md file in the project root for full license information.
"""
Orchestrate a single AWReason run end-to-end.

``execute_run`` is the main entry point called by the queue worker for
every inbound ``RunMessage``.

Strategy
--------
1. Create a per-run working directory (``/work/run-<GUID>``).
2. Download inputs (CVs, spec, prompt) from Blob Storage.
3. Invoke the ``awreason`` engine (Python import preferred; CLI fallback).
4. Upload output artefacts under ``<results_container>/<prefix>/<run_id>/…``.
5. Compute ``duration_ms``, ``tokens_prompt``, ``tokens_completion``.
6. Return ``(RunResultMessage, artifacts_list)``.
"""
from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Optional, Tuple

from contracts.models import ArtifactItem, RunMessage, RunResultMessage
from engine_core.blob_io import (
    download_blob_to_path,
    file_sha256,
    upload_file_return_uri,
    upload_json_marker,
)
from runtime.config import engine_settings
from runtime.workdir import new_run_workdir

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════
#  Discover awreason.py
# ══════════════════════════════════════════════════════════════════════

_AWREASON_SCRIPT: Optional[str] = None


def _find_awreason_script() -> str:
    global _AWREASON_SCRIPT
    if _AWREASON_SCRIPT:
        return _AWREASON_SCRIPT

    repo_root = Path(__file__).resolve().parents[1]
    candidates = [
        repo_root / "o1-assessment" / "awreason.py",
        Path("awreason.py"),
    ]
    for c in candidates:
        if c.is_file():
            _AWREASON_SCRIPT = str(c.resolve())
            logger.info("awreason script found at %s", _AWREASON_SCRIPT)
            return _AWREASON_SCRIPT

    raise FileNotFoundError(
        f"Cannot locate awreason script.  Searched: {[str(c) for c in candidates]}"
    )


# ══════════════════════════════════════════════════════════════════════
#  Helpers
# ══════════════════════════════════════════════════════════════════════

def _blob_filename(uri: str) -> str:
    """Last path segment of a blob URI."""
    return uri.rstrip("/").rsplit("/", 1)[-1] or "download"


def _extract_tokens(stdout: str) -> Tuple[int, int]:
    """Best-effort extraction of token counts from awreason stdout."""
    inp_m = re.search(r"Input tokens:\s*([\d,]+)", stdout)
    out_m = re.search(r"Output tokens:\s*([\d,]+)", stdout)
    return (
        int(inp_m.group(1).replace(",", "")) if inp_m else 0,
        int(out_m.group(1).replace(",", "")) if out_m else 0,
    )


# ══════════════════════════════════════════════════════════════════════
#  Core execution
# ══════════════════════════════════════════════════════════════════════

def execute_run(
    run_msg: RunMessage,
) -> Tuple[RunResultMessage, List[ArtifactItem]]:
    """Orchestrate a single AWReason run.

    Returns ``(RunResultMessage, list_of_artifacts)``.
    Caller is responsible for reporting the result to the platform.
    """
    t0 = time.monotonic()
    params = run_msg.parameters
    run_id = run_msg.run_id
    correlation_id = run_msg.correlation_id

    # Resolve output location
    results_container = (
        params.output.results_container
        if params.output and params.output.results_container
        else engine_settings.blob_results_container
    )
    results_prefix = (
        params.output.results_prefix
        if params.output and params.output.results_prefix
        else engine_settings.blob_results_prefix
    )

    artifacts: List[ArtifactItem] = []
    tokens_prompt = 0
    tokens_completion = 0
    error_message: Optional[str] = None
    status = "Succeeded"

    try:
        with new_run_workdir(
            base=engine_settings.workdir_base, run_id=run_id
        ) as run_dir:
            downloads_dir = run_dir / "downloads"
            downloads_dir.mkdir()
            results_dir = run_dir / "results"
            results_dir.mkdir()
            tempdir = run_dir / "temp"
            tempdir.mkdir()

            # ── Download inputs ───────────────────────────────────────
            pdf_files: List[Path] = []
            for uri in params.cv_blob_uris:
                local = download_blob_to_path(uri, downloads_dir / _blob_filename(uri))
                pdf_files.append(local)

            prompt_file: Optional[Path] = None
            if params.prompt_blob_uri:
                prompt_file = download_blob_to_path(
                    params.prompt_blob_uri,
                    downloads_dir / _blob_filename(params.prompt_blob_uri),
                )

            spec_file: Optional[Path] = None
            if params.spec_blob_uri:
                spec_file = download_blob_to_path(
                    params.spec_blob_uri,
                    downloads_dir / _blob_filename(params.spec_blob_uri),
                )

            json_template: Optional[Path] = None
            if params.run_profile and params.run_profile.json_template_blob_uri:
                json_template = download_blob_to_path(
                    params.run_profile.json_template_blob_uri,
                    downloads_dir / _blob_filename(params.run_profile.json_template_blob_uri),
                )

            # ── Determine PDF / MD mapping ────────────────────────────
            pdf1 = pdf_files[0] if pdf_files else None
            pdf2 = pdf_files[1] if len(pdf_files) >= 2 else spec_file

            md_file: Optional[Path] = None
            if spec_file and spec_file.suffix.lower() in (".md", ".txt"):
                md_file = spec_file
                pdf2 = None

            join_mode = (
                params.run_profile.join_mode if params.run_profile else None
            )

            out_file = results_dir / "output.json"

            # ── Build CLI args ────────────────────────────────────────
            cli_args = [sys.executable, _find_awreason_script()]
            if prompt_file:
                cli_args += ["--promptfile", str(prompt_file)]
            cli_args += ["--output", str(out_file), "--tempdir", str(tempdir)]
            if pdf1:
                cli_args += ["--pdf_file1", str(pdf1)]
            if pdf2:
                cli_args += ["--pdf_file2", str(pdf2)]
            if md_file:
                cli_args += ["--md_file", str(md_file)]
            if json_template:
                cli_args += ["--jsonout_template", str(json_template)]
            if join_mode:
                cli_args += ["--join", join_mode]

            # ── Execute with retries ──────────────────────────────────
            max_retries = engine_settings.awreason_max_retries
            backoff = engine_settings.awreason_retry_backoff
            timeout = engine_settings.awreason_cli_timeout
            stdout_text = ""

            for attempt in range(1, max_retries + 1):
                logger.info(
                    "awreason run_id=%s attempt %d/%d", run_id, attempt, max_retries
                )
                try:
                    result = subprocess.run(
                        cli_args,
                        capture_output=True,
                        timeout=timeout,
                    )
                    stdout_text = result.stdout.decode("utf-8", errors="replace")
                    stderr_text = result.stderr.decode("utf-8", errors="replace")

                    if result.returncode == 0:
                        break
                    logger.error(
                        "awreason exited %d on attempt %d: %s",
                        result.returncode,
                        attempt,
                        stderr_text[:2000],
                    )
                except subprocess.TimeoutExpired:
                    logger.error("awreason timed out after %ds (attempt %d)", timeout, attempt)
                    stderr_text = f"Process timed out after {timeout}s"

                if attempt < max_retries:
                    wait = backoff * attempt
                    logger.warning("Retrying in %ds…", wait)
                    time.sleep(wait)
                else:
                    status = "Failed"
                    error_message = f"awreason failed after {max_retries} attempts: {stderr_text[:500]}"

            # ── Extract token counts ──────────────────────────────────
            tokens_prompt, tokens_completion = _extract_tokens(stdout_text)

            # ── Upload artefacts ──────────────────────────────────────
            if results_dir.exists():
                for f in results_dir.iterdir():
                    if f.is_file():
                        blob_path = f"{results_prefix}/{run_id}/{f.name}"
                        uri = upload_file_return_uri(f, blob_path, results_container)
                        sha = file_sha256(f)
                        mime_type = mimetypes.guess_type(f.name)[0]
                        artifacts.append(ArtifactItem(
                            name=f.name,
                            blob_uri=uri,
                            mime=mime_type,
                            size_bytes=f.stat().st_size,
                            sha256=sha,
                        ))

            # ── Write idempotency marker ──────────────────────────────
            marker_path = f"{results_prefix}/{run_id}/_marker.json"
            marker_data = {
                "run_id": run_id,
                "status": status,
                "correlation_id": correlation_id,
                "duration_ms": int((time.monotonic() - t0) * 1000),
                "tokens_prompt": tokens_prompt,
                "tokens_completion": tokens_completion,
                "error_message": error_message,
                "artifacts_count": len(artifacts),
            }
            try:
                upload_json_marker(results_container, marker_path, marker_data)
            except Exception:
                logger.warning("Failed to write idempotency marker (non-fatal).", exc_info=True)

    except Exception as exc:
        status = "Failed"
        error_message = f"{type(exc).__name__}: {exc}"
        logger.exception("execute_run failed for run_id=%s", run_id)

    duration_ms = int((time.monotonic() - t0) * 1000)

    result_msg = RunResultMessage(
        run_id=run_id,
        status=status,
        duration_ms=duration_ms,
        tokens_prompt=tokens_prompt,
        tokens_completion=tokens_completion,
        error_message=error_message,
        correlation_id=correlation_id,
        artifacts=artifacts if artifacts else None,
    )

    return result_msg, artifacts
