# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE.md file in the project root for full license information.
"""
API routers for ``/assess``, ``/aggregate-runs``, ``/healthz``, ``/ready``.
"""
from __future__ import annotations

import json
import os
import statistics
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse, Response

from app.awreason_runner import run_assessment, run_passthrough
from app.cleanup import new_run_workdir
from app.config import settings
from app.deps import get_semaphore, set_correlation_id, verify_token
from app.models import (
    AggregateRunsRequest,
    AggregateRunsResponse,
    AggregationStats,
    ArtifactRef,
    AssessRequestJSON,
    AssessResponse,
    MustHaveResult,
    ProblemDetail,
    SingleRunResult,
    TimingsMs,
    TokenUsage,
)
from app.storage_blob import (
    can_resolve_credential,
    download_blob_to_path,
    ensure_container_exists,
    upload_bytes_return_uri,
    upload_file_return_uri,
)
from app.telemetry import (
    application_id_var,
    correlation_id_var,
    get_logger,
    job_id_var,
    run_id_var,
)
from app.utils import elapsed_ms, is_allowed_file, now_ms

logger = get_logger(__name__)

_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg"}
_MARKDOWN_SUFFIXES = {".md", ".txt", ".docx"}
_PDF_SUFFIXES = {".pdf"}

# ══════════════════════════════════════════════════════════════════════
#  Routers
# ══════════════════════════════════════════════════════════════════════

health_router = APIRouter(tags=["health"])
assess_router = APIRouter(tags=["assessment"], dependencies=[Depends(verify_token)])


# ──────────────────────────────────────────────────────────────────────
#  Health endpoints
# ──────────────────────────────────────────────────────────────────────

@health_router.get("/healthz", summary="Liveness probe")
async def healthz():
    return {"status": "alive"}


@health_router.get("/ready", summary="Readiness probe")
async def ready():
    """Check that:
    1. The work directory is writable.
    2. Azure credentials can be resolved (cheap, optional).
    """
    errors: list[str] = []

    # 1. Workdir writable
    try:
        test_path = Path(settings.workdir_base) / ".readiness_check"
        test_path.parent.mkdir(parents=True, exist_ok=True)
        test_path.write_text("ok")
        test_path.unlink()
    except Exception as exc:
        errors.append(f"workdir not writable: {exc}")

    # 2. Credential resolution (only when Blob is configured)
    if settings.blob_account_url:
        try:
            ok = can_resolve_credential()
            if not ok:
                errors.append("Cannot resolve DefaultAzureCredential token.")
        except Exception as exc:
            errors.append(f"credential check failed: {exc}")

    if errors:
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "errors": errors},
        )
    return {"status": "ready"}


# ──────────────────────────────────────────────────────────────────────
#  POST /assess  –  JSON mode
# ──────────────────────────────────────────────────────────────────────

@assess_router.post(
    "/assess",
    response_model=AssessResponse,
    summary="Run assessment (JSON/Blob-URI mode)",
    responses={
        400: {"model": ProblemDetail},
        500: {"model": ProblemDetail},
    },
)
async def assess_json(
    body: AssessRequestJSON,
    request: Request,
    cid: str = Depends(set_correlation_id),
):
    """Accept Blob URIs, download inputs, run awreason N times, upload artifacts."""
    correlation_id_var.set(cid)
    job_id_var.set(body.job_id)
    application_id_var.set(body.application_id)

    rid = uuid.uuid4().hex
    run_id_var.set(rid)

    sem = get_semaphore()
    io_start = now_ms()

    async with sem:
        try:
            async with new_run_workdir(base=settings.workdir_base, run_id=rid) as run_dir:
                # ── Download inputs ───────────────────────────────────
                downloads_dir = run_dir / "downloads"
                downloads_dir.mkdir()

                prompt_file = await download_blob_to_path(
                    body.prompt_blob_uri,
                    downloads_dir / _blob_filename(body.prompt_blob_uri),
                )

                pdf_files: List[Path] = []
                for uri in body.cv_blob_uris:
                    local = await download_blob_to_path(
                        uri, downloads_dir / _blob_filename(uri)
                    )
                    pdf_files.append(local)

                spec_file: Optional[Path] = None
                if body.spec_blob_uri:
                    spec_file = await download_blob_to_path(
                        body.spec_blob_uri,
                        downloads_dir / _blob_filename(body.spec_blob_uri),
                    )

                json_template: Optional[Path] = None
                if body.run_profile and body.run_profile.json_template_blob_uri:
                    json_template = await download_blob_to_path(
                        body.run_profile.json_template_blob_uri,
                        downloads_dir / _blob_filename(body.run_profile.json_template_blob_uri),
                    )

                io_elapsed = elapsed_ms(io_start)

                # ── Determine PDF / MD file mapping ───────────────────
                pdf1 = pdf_files[0] if len(pdf_files) >= 1 else None
                pdf2 = pdf_files[1] if len(pdf_files) >= 2 else spec_file

                md_file: Optional[Path] = None
                if spec_file and spec_file.suffix.lower() in (".md", ".txt"):
                    md_file = spec_file
                    pdf2 = None  # don't pass as pdf

                join_mode = body.run_profile.join_mode if body.run_profile else None

                # ── Run awreason N times ──────────────────────────────
                awreason_start = now_ms()
                individual_runs = await run_assessment(
                    run_dir=run_dir,
                    prompt_file=prompt_file,
                    pdf_file1=pdf1,
                    pdf_file2=pdf2,
                    md_file=md_file,
                    json_template=json_template,
                    join_mode=join_mode,
                    numruns=body.numruns,
                )
                awreason_elapsed = elapsed_ms(awreason_start)

                # ── Upload artifacts ──────────────────────────────────
                artifacts: List[ArtifactRef] = []
                if body.return_artifacts:
                    artifacts = await _upload_run_artifacts(run_dir, rid)
                    # Attach artifact URIs to individual runs
                    for a in artifacts:
                        for r in individual_runs:
                            if r.raw_output_path and a.name.endswith(Path(r.raw_output_path).name):
                                r.artifacts.append(a)

                # ── Aggregate if multi-run ────────────────────────────
                aggregation: Optional[AggregationStats] = None
                final_score: Optional[float] = None
                final_sub_scores: Dict[str, Any] = {}
                final_must_haves: List[MustHaveResult] = []
                final_comment: Optional[str] = None

                if body.numruns > 1 and len(individual_runs) >= 2:
                    aggregation = _aggregate_runs(individual_runs, method="median")
                    final_score = aggregation.aggregated_score
                    final_sub_scores = aggregation.sub_score_aggregations
                    final_must_haves = _aggregate_must_haves(individual_runs)
                    aggregation.must_have_aggregations = [
                        mh.model_dump() for mh in final_must_haves
                    ]
                    # Use the comment from the first run as representative
                    final_comment = next((r.comment for r in individual_runs if r.comment), None)
                elif individual_runs:
                    r0 = individual_runs[0]
                    final_score = r0.overall_score
                    final_sub_scores = r0.sub_scores
                    final_must_haves = r0.must_haves
                    final_comment = r0.comment

                # ── Aggregate token usage ─────────────────────────────
                total_input = sum(r.token_usage.input for r in individual_runs)
                total_output = sum(r.token_usage.output for r in individual_runs)
                total_ms = io_elapsed + awreason_elapsed

                return AssessResponse(
                    run_id=rid,
                    job_id=body.job_id,
                    application_id=body.application_id,
                    overall_score=final_score,
                    sub_scores=final_sub_scores,
                    must_haves=final_must_haves,
                    comment=final_comment,
                    artifacts=artifacts,
                    timings_ms=TimingsMs(total=total_ms, awreason=awreason_elapsed, io=io_elapsed),
                    token_usage=TokenUsage(input=total_input, output=total_output),
                    correlation_id=cid,
                    individual_runs=individual_runs,
                    aggregation=aggregation,
                )
        except FileNotFoundError as exc:
            return _problem(400, "Input not found", str(exc), cid, request.url.path)
        except Exception as exc:
            logger.exception("Assessment failed for job=%s", body.job_id)
            return _problem(500, "Assessment failed", str(exc), cid, request.url.path)


# ──────────────────────────────────────────────────────────────────────
#  POST /assess  –  multipart / form-data mode
# ──────────────────────────────────────────────────────────────────────

@assess_router.post(
    "/assess/upload",
    response_model=AssessResponse,
    summary="Run assessment (multipart file upload mode)",
    responses={
        400: {"model": ProblemDetail},
        500: {"model": ProblemDetail},
    },
)
async def assess_upload(
    request: Request,
    payload: str = Form(..., description="JSON string matching AssessRequestJSON (without blob URIs)"),
    cv_files: List[UploadFile] = File(default=[], alias="cvFiles[]"),
    spec_file: Optional[UploadFile] = File(default=None, alias="specFile"),
    prompt_file: Optional[UploadFile] = File(default=None, alias="promptFile"),
    cid: str = Depends(set_correlation_id),
):
    """Accept direct file uploads + a JSON ``payload`` part."""
    try:
        body_dict = json.loads(payload)
    except json.JSONDecodeError as exc:
        return _problem(400, "Invalid payload JSON", str(exc), cid, request.url.path)

    # Extract fields from payload
    job_id = body_dict.get("jobId", "")
    application_id = body_dict.get("applicationId", "")
    numruns = int(body_dict.get("numruns", 1))
    run_profile = body_dict.get("runProfile", {})
    return_artifacts = body_dict.get("returnArtifacts", True)

    correlation_id_var.set(cid)
    job_id_var.set(job_id)
    application_id_var.set(application_id)

    rid = uuid.uuid4().hex
    run_id_var.set(rid)

    sem = get_semaphore()
    io_start = now_ms()

    async with sem:
        try:
            async with new_run_workdir(base=settings.workdir_base, run_id=rid) as run_dir:
                downloads_dir = run_dir / "downloads"
                downloads_dir.mkdir()

                # Save uploaded files
                local_prompt: Optional[Path] = None
                if prompt_file:
                    local_prompt = await _save_upload(prompt_file, downloads_dir)
                elif "promptBlobUri" in body_dict:
                    local_prompt = await download_blob_to_path(
                        body_dict["promptBlobUri"],
                        downloads_dir / _blob_filename(body_dict["promptBlobUri"]),
                    )

                if not local_prompt:
                    return _problem(400, "Missing prompt", "Either upload promptFile or provide promptBlobUri.", cid, request.url.path)

                pdf_files: List[Path] = []
                for uf in cv_files:
                    if uf.filename and is_allowed_file(uf.filename):
                        pdf_files.append(await _save_upload(uf, downloads_dir))

                local_spec: Optional[Path] = None
                if spec_file:
                    local_spec = await _save_upload(spec_file, downloads_dir)

                io_elapsed = elapsed_ms(io_start)

                pdf1 = pdf_files[0] if pdf_files else None
                pdf2 = pdf_files[1] if len(pdf_files) >= 2 else local_spec

                md_file: Optional[Path] = None
                if local_spec and local_spec.suffix.lower() in (".md", ".txt"):
                    md_file = local_spec
                    pdf2 = None

                join_mode = run_profile.get("joinMode")
                json_template: Optional[Path] = None
                if "jsonTemplateBlobUri" in run_profile:
                    json_template = await download_blob_to_path(
                        run_profile["jsonTemplateBlobUri"],
                        downloads_dir / _blob_filename(run_profile["jsonTemplateBlobUri"]),
                    )

                awreason_start = now_ms()
                individual_runs = await run_assessment(
                    run_dir=run_dir,
                    prompt_file=local_prompt,
                    pdf_file1=pdf1,
                    pdf_file2=pdf2,
                    md_file=md_file,
                    json_template=json_template,
                    join_mode=join_mode,
                    numruns=numruns,
                )
                awreason_elapsed = elapsed_ms(awreason_start)

                artifacts: List[ArtifactRef] = []
                if return_artifacts:
                    artifacts = await _upload_run_artifacts(run_dir, rid)

                aggregation: Optional[AggregationStats] = None
                final_score: Optional[float] = None
                final_sub_scores: Dict[str, Any] = {}
                final_must_haves: List[MustHaveResult] = []
                final_comment: Optional[str] = None

                if numruns > 1 and len(individual_runs) >= 2:
                    aggregation = _aggregate_runs(individual_runs, method="median")
                    final_score = aggregation.aggregated_score
                    final_sub_scores = aggregation.sub_score_aggregations
                    final_must_haves = _aggregate_must_haves(individual_runs)
                    aggregation.must_have_aggregations = [
                        mh.model_dump() for mh in final_must_haves
                    ]
                    final_comment = next((r.comment for r in individual_runs if r.comment), None)
                elif individual_runs:
                    r0 = individual_runs[0]
                    final_score = r0.overall_score
                    final_sub_scores = r0.sub_scores
                    final_must_haves = r0.must_haves
                    final_comment = r0.comment

                total_input = sum(r.token_usage.input for r in individual_runs)
                total_output = sum(r.token_usage.output for r in individual_runs)
                total_ms = io_elapsed + awreason_elapsed

                return AssessResponse(
                    runId=rid,
                    jobId=job_id,
                    applicationId=application_id,
                    overallScore=final_score,
                    subScores=final_sub_scores,
                    mustHaves=final_must_haves,
                    comment=final_comment,
                    artifacts=artifacts,
                    timingsMs=TimingsMs(total=total_ms, awreason=awreason_elapsed, io=io_elapsed),
                    tokenUsage=TokenUsage(input=total_input, output=total_output),
                    correlationId=cid,
                    individualRuns=individual_runs,
                    aggregation=aggregation,
                )
        except Exception as exc:
            import traceback
            tb = traceback.format_exc()
            logger.exception("Assessment (upload) failed for job=%s", job_id)
            return _problem(500, "Assessment failed", f"{type(exc).__name__}: {exc}\n{tb}", cid, request.url.path)


# ──────────────────────────────────────────────────────────────────────
#  POST /assess/passthrough  –  raw subprocess-equivalent mode
# ──────────────────────────────────────────────────────────────────────

@assess_router.post(
    "/assess/passthrough",
    summary="Run assessment (passthrough mode – returns the output file directly)",
    responses={
        200: {
            "description": "The raw assessment output file (HTML or JSON).",
            "content": {
                "text/html": {},
                "application/json": {},
            },
        },
        400: {"model": ProblemDetail},
        500: {"model": ProblemDetail},
    },
)
async def assess_passthrough(
    request: Request,
    prompt_file: UploadFile = File(..., alias="promptFile",
                                   description="The prompt / instructions file."),
    cv_files: List[UploadFile] = File(default=[], alias="cvFiles[]",
                                      description="PDF or image files to assess."),
    spec_file: Optional[UploadFile] = File(default=None, alias="specFile",
                                           description="Optional context file (.md, .docx, .txt)."),
    json_template: Optional[UploadFile] = File(default=None, alias="jsonTemplate",
                                               description="Optional JSON output template."),
    join_mode: Optional[str] = Form(default=None, alias="joinMode",
                                    description="'horizontal' or 'vertical' image joining."),
    reasoning_effort: str = Form(default="high", alias="reasoningEffort",
                                 description="Reasoning effort for supported O3 and GPT-5.x models."),
    batch_id: Optional[str] = Form(default=None, alias="batchId",
                                   description="Batch identifier – groups runs for the same input."),
    run_number: Optional[int] = Form(default=None, alias="runNumber",
                                     description="Run number within the batch (1-based)."),
    total_runs: Optional[int] = Form(default=None, alias="totalRuns",
                                     description="Total number of runs in the batch."),
    aggregation_method: Optional[str] = Form(default=None, alias="aggregationMethod",
                                             description="Aggregation method (median, mean, trimmed_mean, etc.)."),
    cid: str = Depends(set_correlation_id),
):
    """Run awreason.py exactly as the direct subprocess does and return the
    raw output file directly in the response body.

    The output file content is returned as-is with the appropriate
    Content-Type (text/html or application/json).  Metadata is provided
    via response headers:

    - ``X-AWR-Exit-Code`` – subprocess exit code
    - ``X-AWR-Duration-Ms`` – wall-clock execution time in milliseconds
    - ``X-AWR-Output-Filename`` – original output filename
    - ``X-AWR-Run-Id`` – unique run identifier
    - ``X-Correlation-Id`` – correlation ID for tracing

    This endpoint is designed to produce identical results to the UX's
    direct (subprocess) mode.
    """
    correlation_id_var.set(cid)
    rid = uuid.uuid4().hex
    run_id_var.set(rid)

    sem = get_semaphore()

    async with sem:
        try:
            async with new_run_workdir(base=settings.workdir_base, run_id=rid) as run_dir:
                downloads_dir = run_dir / "downloads"
                downloads_dir.mkdir()

                # Save uploaded files
                local_prompt = await _save_upload(prompt_file, downloads_dir)
                prompt_for_run = local_prompt

                pdf_files: List[Path] = []
                image_files: List[Path] = []
                markdown_cv_files: List[Path] = []
                for uf in cv_files:
                    if uf.filename:
                        if not is_allowed_file(uf.filename):
                            return _problem(
                                400,
                                "Invalid cvFiles input",
                                f"Unsupported file type for cvFiles[]: {uf.filename}",
                                cid,
                                request.url.path,
                            )
                        saved = await _save_upload(uf, downloads_dir)
                        suffix = saved.suffix.lower()
                        if suffix in _IMAGE_SUFFIXES:
                            image_files.append(saved)
                        elif suffix in _PDF_SUFFIXES:
                            pdf_files.append(saved)
                        elif suffix in _MARKDOWN_SUFFIXES:
                            markdown_cv_files.append(saved)
                        else:
                            return _problem(
                                400,
                                "Invalid cvFiles input",
                                f"Unsupported file type for cvFiles[]: {uf.filename}",
                                cid,
                                request.url.path,
                            )

                local_spec: Optional[Path] = None
                if spec_file:
                    local_spec = await _save_upload(spec_file, downloads_dir)

                local_json_template: Optional[Path] = None
                if json_template:
                    local_json_template = await _save_upload(json_template, downloads_dir)

                # Map files to awreason CLI args (same logic as the UX)
                pdf1 = pdf_files[0] if pdf_files else None
                pdf2 = pdf_files[1] if len(pdf_files) >= 2 else None

                md_file: Optional[Path] = None
                markdown_sources: List[Path] = []
                if local_spec and local_spec.suffix.lower() in _MARKDOWN_SUFFIXES:
                    markdown_sources.append(local_spec)
                elif local_spec:
                    # Spec is a PDF — use as pdf2 if slot is free
                    if pdf2 is None:
                        pdf2 = local_spec

                markdown_sources.extend(markdown_cv_files)

                if markdown_cv_files:
                    prompt_for_run = _merge_prompt_and_context(
                        prompt_file=local_prompt,
                        sources=markdown_sources,
                        output_path=downloads_dir / "merged-prompt.md",
                    )
                elif markdown_sources:
                    md_file = _merge_markdown_sources(
                        sources=markdown_sources,
                        output_path=downloads_dir / "merged-context.md",
                    )

                # If images were uploaded, put them in a folder
                images_folder: Optional[Path] = None
                if image_files:
                    img_dir = downloads_dir / "images"
                    img_dir.mkdir(exist_ok=True)
                    for img in image_files:
                        img.rename(img_dir / img.name)
                    images_folder = img_dir

                # Validate join_mode
                if join_mode and join_mode not in ("horizontal", "vertical"):
                    return _problem(400, "Invalid joinMode",
                                    f"joinMode must be 'horizontal' or 'vertical', got '{join_mode}'",
                                    cid, request.url.path)

                if reasoning_effort not in ("low", "medium", "high"):
                    return _problem(400, "Invalid reasoningEffort",
                                    f"reasoningEffort must be 'low', 'medium', or 'high', got '{reasoning_effort}'",
                                    cid, request.url.path)

                # Run awreason as passthrough
                result = await run_passthrough(
                    run_dir=run_dir,
                    prompt_file=prompt_for_run,
                    pdf_file1=pdf1,
                    pdf_file2=pdf2,
                    md_file=md_file,
                    json_template=local_json_template,
                    join_mode=join_mode,
                    reasoning_effort=reasoning_effort,
                    images_folder1=images_folder,
                )

                file_bytes = result["file_bytes"]
                exit_code = result["exit_code"]

                # If no output was produced and the process failed, return an error
                if not file_bytes and exit_code != 0:
                    detail = result["stderr"] or result["stdout"] or "No output produced"
                    return _problem(
                        500, "Assessment failed",
                        f"awreason.py exited with code {exit_code}.\n{detail}",
                        cid, request.url.path,
                    )

                # ── Persist artifacts to blob (when configured) ───────
                artifact_uris: List[str] = []
                run_artifacts = await _upload_run_artifacts(
                    run_dir, rid,
                    batch_id=batch_id,
                    run_number=run_number,
                )
                artifact_uris = [a.blob_uri for a in run_artifacts]

                # ── Server-side aggregation on the last batch run ─────
                aggregation_uri: Optional[str] = None
                if (
                    batch_id
                    and run_number is not None
                    and total_runs is not None
                    and total_runs > 1
                    and run_number == total_runs
                    and settings.blob_account_url
                ):
                    aggregation_uri = await _run_batch_aggregation(
                        batch_id=batch_id,
                        total_runs=total_runs,
                        method=aggregation_method or "median",
                        run_dir=run_dir,
                    )

                # Return the file content directly with metadata headers
                output_filename = result["output_filename"] or "assessment-output.html"
                headers = {
                    "X-AWR-Exit-Code": str(exit_code),
                    "X-AWR-Duration-Ms": str(result["duration_ms"]),
                    "X-AWR-Output-Filename": output_filename,
                    "X-AWR-Run-Id": rid,
                    "X-Correlation-Id": cid,
                    "Content-Disposition": f'attachment; filename="{output_filename}"',
                    "Access-Control-Expose-Headers": "X-AWR-Exit-Code, X-AWR-Duration-Ms, X-AWR-Output-Filename, X-AWR-Run-Id, X-AWR-Batch-Id, X-Correlation-Id, X-AWR-Artifact-URIs, X-AWR-Aggregation-URI, Content-Disposition",
                }
                if batch_id:
                    headers["X-AWR-Batch-Id"] = batch_id
                if artifact_uris:
                    headers["X-AWR-Artifact-URIs"] = ",".join(artifact_uris)
                if aggregation_uri:
                    headers["X-AWR-Aggregation-URI"] = aggregation_uri

                return Response(
                    content=file_bytes,
                    media_type=result["content_type"],
                    headers=headers,
                )

        except Exception as exc:
            import traceback
            tb = traceback.format_exc()
            logger.exception("Assessment (passthrough) failed")
            return _problem(500, "Assessment failed", f"{type(exc).__name__}: {exc}\n{tb}", cid, request.url.path)


# ──────────────────────────────────────────────────────────────────────
#  POST /aggregate-runs
# ──────────────────────────────────────────────────────────────────────

@assess_router.post(
    "/aggregate-runs",
    response_model=AggregateRunsResponse,
    summary="Aggregate multiple assessment run results",
    responses={
        400: {"model": ProblemDetail},
        500: {"model": ProblemDetail},
    },
)
async def aggregate_runs(
    body: AggregateRunsRequest,
    request: Request,
    cid: str = Depends(set_correlation_id),
):
    """Download / read inline payloads and compute aggregate metrics."""
    correlation_id_var.set(cid)
    job_id_var.set(body.job_id)
    application_id_var.set(body.application_id)

    try:
        # Collect individual run data
        run_data: List[Dict[str, Any]] = []
        rid = uuid.uuid4().hex
        run_id_var.set(rid)

        async with new_run_workdir(base=settings.workdir_base, run_id=rid) as run_dir:
            dl_dir = run_dir / "downloads"
            dl_dir.mkdir()

            for idx, ref in enumerate(body.runs):
                if ref.inline:
                    run_data.append(ref.inline)
                elif ref.blob_uri:
                    dest = dl_dir / f"run_{idx}.json"
                    await download_blob_to_path(ref.blob_uri, dest)
                    content = dest.read_text(encoding="utf-8")
                    try:
                        run_data.append(json.loads(content))
                    except json.JSONDecodeError:
                        run_data.append({"raw": content})
                else:
                    return _problem(
                        400, "Invalid run reference",
                        f"Run at index {idx} has neither blobUri nor inline data.",
                        cid, request.url.path,
                    )

            # Build pseudo-SingleRunResults for aggregation
            pseudo_runs: List[SingleRunResult] = []
            from app.awreason_runner import _extract_overall_score, _extract_sub_scores, _extract_must_haves

            for i, d in enumerate(run_data):
                pseudo_runs.append(SingleRunResult(
                    run_number=i + 1,
                    overall_score=_extract_overall_score(d),
                    sub_scores=_extract_sub_scores(d),
                    must_haves=_extract_must_haves(d),
                ))

            method = body.strategy.type
            if method == "trimmed_mean":
                agg = _aggregate_runs(pseudo_runs, method="trimmed_mean", trim_pct=body.strategy.trim_percent)
            else:
                agg = _aggregate_runs(pseudo_runs, method=method)

            must_haves = _aggregate_must_haves(pseudo_runs)
            agg.must_have_aggregations = [mh.model_dump() for mh in must_haves]

            # Optionally upload aggregation result and all artifacts
            artifacts: List[ArtifactRef] = []
            if settings.blob_account_url:
                # Save aggregation result into the results dir for unified upload
                results_dir = run_dir / "results"
                results_dir.mkdir(exist_ok=True)
                agg_file = results_dir / "aggregated_result.json"
                agg_file.write_text(
                    json.dumps(agg.model_dump(by_alias=True), indent=2, default=str),
                    encoding="utf-8",
                )
                # Upload everything: individual run downloads + aggregation result
                artifacts = await _upload_run_artifacts(run_dir, rid)

        return AggregateRunsResponse(
            job_id=body.job_id,
            application_id=body.application_id,
            aggregation=agg,
            artifacts=artifacts,
            parameters_used=body.strategy,
            correlation_id=cid,
        )

    except Exception as exc:
        logger.exception("Aggregation failed for job=%s", body.job_id)
        return _problem(500, "Aggregation failed", str(exc), cid, request.url.path)


# ══════════════════════════════════════════════════════════════════════
#  Internal helpers
# ══════════════════════════════════════════════════════════════════════


def _blob_filename(uri: str) -> str:
    """Extract the last path segment from a blob URI as a filename."""
    return uri.rstrip("/").rsplit("/", 1)[-1] or "download"


def _read_markdown_source(source: Path) -> str:
    """Return markdown-compatible text from a supported source file."""
    suffix = source.suffix.lower()
    if suffix in {".md", ".txt"}:
        return source.read_text(encoding="utf-8", errors="replace").strip()
    if suffix == ".docx":
        from docx import Document  # type: ignore
        from markdownify import markdownify as markdownify_text  # type: ignore

        doc = Document(source)
        doc_text = "\n".join(paragraph.text for paragraph in doc.paragraphs)
        return markdownify_text(doc_text).strip()
    raise ValueError(f"Unsupported markdown source: {source.name}")


def _merge_markdown_sources(*, sources: List[Path], output_path: Path) -> Path:
    """Merge markdown-like inputs into one temp markdown file."""
    sections: List[str] = []

    for index, source in enumerate(sources, start=1):
        content = _read_markdown_source(source)
        if not content:
            continue
        sections.append(
            f"\n\n---\n\n# Context {index}: {source.name}\n\n{content}\n"
        )

    output_path.write_text("".join(sections), encoding="utf-8")
    logger.info(
        "Created merged markdown context %s from %d sources",
        output_path,
        len(sources),
    )
    return output_path


def _merge_prompt_and_context(*, prompt_file: Path, sources: List[Path], output_path: Path) -> Path:
    """Create a merged prompt markdown in prompt, spec, then CV order."""
    prompt_text = prompt_file.read_text(encoding="utf-8", errors="replace").strip()
    sections: List[str] = [f"# Prompt File: {prompt_file.name}\n\n{prompt_text}\n"]

    if sources:
        sections.append("\n\n---\n\n# Additional Context\n")

    for index, source in enumerate(sources, start=1):
        content = _read_markdown_source(source)
        if not content:
            continue
        sections.append(
            f"\n\n---\n\n# Context {index}: {source.name}\n\n{content}\n"
        )

    output_path.write_text("".join(sections), encoding="utf-8")
    logger.info(
        "Created merged prompt markdown %s from prompt %s and %d sources",
        output_path,
        prompt_file.name,
        len(sources),
    )
    return output_path


async def _save_upload(upload: UploadFile, dest_dir: Path) -> Path:
    """Stream an UploadFile to disk and return the local path."""
    fname = upload.filename or f"upload_{uuid.uuid4().hex[:8]}"
    dest = dest_dir / fname
    content = await upload.read()
    dest.write_bytes(content)
    logger.info("Saved upload %s (%d bytes)", fname, len(content))
    return dest


async def _upload_run_artifacts(
    run_dir: Path,
    rid: str,
    *,
    batch_id: Optional[str] = None,
    run_number: Optional[int] = None,
) -> List[ArtifactRef]:
    """Upload all run artifacts (inputs + results) to blob storage.

    When ``batch_id`` is provided, organises blobs under a batch folder so
    that multiple runs for the same input are grouped together::

        batches/{batch_id}/inputs/<filename>
        batches/{batch_id}/runs/run{N}/<filename>
        batches/{batch_id}/aggregated/<filename>

    Without ``batch_id`` (single-run mode), falls back to::

        runs/{rid}/inputs/<filename>
        runs/{rid}/results/<filename>

    Returns the list of :class:`ArtifactRef` for every uploaded file.
    Silently returns an empty list when blob storage is not configured
    or if the upload fails.
    """
    if not settings.blob_account_url:
        return []

    artifacts: List[ArtifactRef] = []
    try:
        ensure_container_exists(settings.blob_container_results)

        if batch_id:
            # ── Batch mode: group under batches/{batch_id}/ ───────
            base_prefix = f"batches/{batch_id}"

            # Upload inputs only for the first run (run_number == 1)
            downloads_dir = run_dir / "downloads"
            if downloads_dir.exists() and (run_number is None or run_number == 1):
                for f in downloads_dir.rglob("*"):
                    if f.is_file():
                        rel = f.relative_to(downloads_dir)
                        blob_path = f"{base_prefix}/inputs/{rel.as_posix()}"
                        uri = await upload_file_return_uri(f, blob_path)
                        artifacts.append(ArtifactRef(name=f"inputs/{rel.as_posix()}", blob_uri=uri))
                        logger.info("Artifact persisted → %s", uri)

            # Upload results into runs/run{N}/ subfolder
            results_dir = run_dir / "results"
            if results_dir.exists():
                run_label = f"run{run_number}" if run_number else f"run_{rid[:8]}"
                for f in results_dir.rglob("*"):
                    if f.is_file():
                        rel = f.relative_to(results_dir)
                        blob_path = f"{base_prefix}/runs/{run_label}/{rel.as_posix()}"
                        uri = await upload_file_return_uri(f, blob_path)
                        artifacts.append(ArtifactRef(name=f"runs/{run_label}/{rel.as_posix()}", blob_uri=uri))
                        logger.info("Artifact persisted → %s", uri)
        else:
            # ── Single-run mode: original layout ──────────────────
            downloads_dir = run_dir / "downloads"
            if downloads_dir.exists():
                for f in downloads_dir.rglob("*"):
                    if f.is_file():
                        rel = f.relative_to(downloads_dir)
                        blob_path = f"runs/{rid}/inputs/{rel.as_posix()}"
                        uri = await upload_file_return_uri(f, blob_path)
                        artifacts.append(ArtifactRef(name=f"inputs/{rel.as_posix()}", blob_uri=uri))
                        logger.info("Artifact persisted → %s", uri)

            results_dir = run_dir / "results"
            if results_dir.exists():
                for f in results_dir.rglob("*"):
                    if f.is_file():
                        rel = f.relative_to(results_dir)
                        blob_path = f"runs/{rid}/results/{rel.as_posix()}"
                        uri = await upload_file_return_uri(f, blob_path)
                        artifacts.append(ArtifactRef(name=f"results/{rel.as_posix()}", blob_uri=uri))
                        logger.info("Artifact persisted → %s", uri)

    except Exception as blob_exc:
        logger.warning(
            "Blob artifact upload failed (results still returned inline): %s",
            blob_exc,
        )

    return artifacts


async def _run_batch_aggregation(
    *,
    batch_id: str,
    total_runs: int,
    method: str,
    run_dir: Path,
) -> Optional[str]:
    """Download all run results from blob, aggregate, upload the result.

    Called on the last run of a batch (``run_number == total_runs``).
    Re-uses :func:`aggregate_multiple_runs` from ``o1-assessment/aggregate_scores.py``.

    Returns the blob URI of the aggregated result, or ``None`` on failure.
    """
    import sys

    # Make aggregate_scores importable
    resolved = Path(__file__).resolve()
    # Local dev: wrappers/http-service/app → repo root (parents[3])
    # Docker:    /app/app/ → /app (parents[1])
    if len(resolved.parents) > 3:
        repo_root = resolved.parents[3]
    else:
        repo_root = resolved.parents[1]
    o1_dir = repo_root / "o1-assessment"
    if str(o1_dir) not in sys.path:
        sys.path.insert(0, str(o1_dir))

    try:
        from aggregate_scores import aggregate_multiple_runs
    except ImportError as exc:
        logger.warning("Cannot import aggregate_scores – skipping aggregation: %s", exc)
        return None

    try:
        container = settings.blob_container_results
        base_prefix = f"batches/{batch_id}"

        # List blobs under batches/{batch_id}/runs/ to find result files
        from app.storage_blob import _container_client
        cc = _container_client(container)

        # Download each run's result file(s) to a temp directory
        agg_tmp = run_dir / "aggregation_tmp"
        agg_tmp.mkdir(exist_ok=True)

        downloaded_files: List[Path] = []
        for run_num in range(1, total_runs + 1):
            run_prefix = f"{base_prefix}/runs/run{run_num}/"
            blobs = list(cc.list_blobs(name_starts_with=run_prefix))
            for blob in blobs:
                local_dest = agg_tmp / f"run{run_num}_{blob.name.rsplit('/', 1)[-1]}"
                blob_client = cc.get_blob_client(blob.name)
                with open(local_dest, "wb") as fh:
                    stream = blob_client.download_blob()
                    stream.readinto(fh)
                downloaded_files.append(local_dest)
                logger.info("Downloaded for aggregation: %s → %s", blob.name, local_dest.name)

        if len(downloaded_files) < 2:
            logger.warning(
                "Only %d result files found for batch %s – need ≥2 for aggregation",
                len(downloaded_files), batch_id,
            )
            return None

        # Run aggregation
        agg_result = aggregate_multiple_runs(downloaded_files, method=method)

        if "error" in agg_result:
            logger.warning("Aggregation returned error: %s", agg_result["error"])
            return None

        # Serialize the aggregated result
        agg_type = agg_result.get("type", "json")
        if agg_type == "json":
            agg_content = json.dumps(
                agg_result.get("aggregated_result", agg_result),
                indent=2,
                default=str,
            ).encode("utf-8")
            agg_filename = "aggregated_result.json"
        else:
            # HTML aggregation — store whatever the aggregator produced
            agg_content = json.dumps(agg_result, indent=2, default=str).encode("utf-8")
            agg_filename = "aggregated_result.json"

        # Upload to blob
        blob_path = f"{base_prefix}/aggregated/{agg_filename}"
        uri = await upload_bytes_return_uri(agg_content, blob_path)
        logger.info("Batch aggregation uploaded → %s", uri)

        # Also upload variance analysis if present
        variance = agg_result.get("variance_analysis")
        if variance:
            var_bytes = json.dumps(variance, indent=2, default=str).encode("utf-8")
            var_blob = f"{base_prefix}/aggregated/variance_analysis.json"
            await upload_bytes_return_uri(var_bytes, var_blob)
            logger.info("Variance analysis uploaded → %s", var_blob)

        return uri

    except Exception as exc:
        logger.warning("Batch aggregation failed (individual results still saved): %s", exc)
        return None


def _problem(
    status: int, title: str, detail: str, cid: str, instance: str
) -> JSONResponse:
    """Return an RFC 7807 problem+json response – never leak secrets."""
    # Sanitise detail: strip anything that looks like a key/token/secret
    import re
    sanitised = re.sub(r"(key|token|secret|password|credential)[=:]\S+", r"\1=[REDACTED]", detail, flags=re.IGNORECASE)
    body = ProblemDetail(
        type="about:blank",
        title=title,
        status=status,
        detail=sanitised,
        instance=instance,
        correlation_id=cid,
    )
    return JSONResponse(
        status_code=status,
        content=body.model_dump(by_alias=True),
        media_type="application/problem+json",
    )


# ──────────────────────────────────────────────────────────────────────
#  Aggregation helpers (mirrors aggregate_scores.py from o1-assessment)
# ──────────────────────────────────────────────────────────────────────

def _aggregate_runs(
    runs: List[SingleRunResult],
    method: str = "median",
    trim_pct: float = 10.0,
) -> AggregationStats:
    """Aggregate individual run scores using the requested strategy.

    Supports: ``median``, ``mean``, ``trimmed_mean``, ``interquartile_mean``.
    """
    scores = [r.overall_score for r in runs if r.overall_score is not None]
    n = len(scores)

    agg = AggregationStats(method=method, run_count=len(runs))

    if n == 0:
        return agg

    agg.mean = round(statistics.mean(scores), 2)
    agg.median = round(statistics.median(scores), 2)
    agg.min_score = round(min(scores), 2)
    agg.max_score = round(max(scores), 2)

    if n >= 2:
        agg.std_dev = round(statistics.stdev(scores), 2)
        agg.variance = round(statistics.variance(scores), 2)
        # 95% confidence interval (z=1.96 for large-n; t-approx for small-n)
        import math
        se = agg.std_dev / math.sqrt(n)
        z = 1.96
        agg.confidence_interval_95 = [
            round(agg.mean - z * se, 2),
            round(agg.mean + z * se, 2),
        ]

    # Compute aggregated score per method
    if method == "mean":
        agg.aggregated_score = agg.mean
    elif method == "trimmed_mean":
        agg.aggregated_score = round(_trimmed_mean(scores, trim_pct), 2)
    elif method == "interquartile_mean":
        agg.aggregated_score = round(_interquartile_mean(scores), 2)
    else:  # default: median
        agg.aggregated_score = agg.median

    # Aggregate sub-scores
    agg.sub_score_aggregations = _aggregate_sub_scores(runs, method, trim_pct)

    return agg


def _trimmed_mean(values: List[float], trim_pct: float = 10.0) -> float:
    """Symmetrically trim ``trim_pct``% from each end and return the mean."""
    if len(values) <= 2:
        return statistics.mean(values)
    sorted_v = sorted(values)
    n = len(sorted_v)
    trim_count = max(1, int(n * trim_pct / 100))
    trimmed = sorted_v[trim_count: n - trim_count]
    return statistics.mean(trimmed) if trimmed else statistics.mean(values)


def _interquartile_mean(values: List[float]) -> float:
    """Mean of values within the interquartile range (Q1–Q3)."""
    if len(values) < 4:
        return statistics.mean(values)
    sorted_v = sorted(values)
    n = len(sorted_v)
    q1_idx = n // 4
    q3_idx = 3 * n // 4
    iq = sorted_v[q1_idx: q3_idx + 1]
    return statistics.mean(iq) if iq else statistics.mean(values)


def _aggregate_sub_scores(
    runs: List[SingleRunResult], method: str, trim_pct: float
) -> Dict[str, Any]:
    """Collect all sub-score keys across runs and aggregate each."""
    all_keys: set[str] = set()
    for r in runs:
        all_keys.update(r.sub_scores.keys())

    result: Dict[str, Any] = {}
    for key in sorted(all_keys):
        vals = []
        for r in runs:
            v = r.sub_scores.get(key)
            if isinstance(v, (int, float)):
                vals.append(float(v))
            elif isinstance(v, dict) and "score" in v:
                try:
                    vals.append(float(v["score"]))
                except (TypeError, ValueError):
                    pass
        if vals:
            if method == "mean":
                result[key] = round(statistics.mean(vals), 2)
            elif method == "trimmed_mean":
                result[key] = round(_trimmed_mean(vals, trim_pct), 2)
            elif method == "interquartile_mean":
                result[key] = round(_interquartile_mean(vals), 2)
            else:
                result[key] = round(statistics.median(vals), 2)
    return result


def _aggregate_must_haves(runs: List[SingleRunResult]) -> List[MustHaveResult]:
    """Majority-vote aggregation for must-have items across runs."""
    # Collect all unique must-have names
    name_map: Dict[str, List[bool]] = {}
    reason_map: Dict[str, List[str]] = {}
    for r in runs:
        for mh in r.must_haves:
            name_map.setdefault(mh.name, []).append(mh.passed)
            reason_map.setdefault(mh.name, []).append(mh.reason)

    results: List[MustHaveResult] = []
    for name, passes in name_map.items():
        passed_count = sum(passes)
        total = len(passes)
        majority = passed_count > total / 2
        reasons = reason_map.get(name, [])
        # Pick the most common reason or concatenate
        reason = reasons[0] if reasons else ""
        results.append(MustHaveResult(
            name=name,
            passed=majority,
            reason=f"{reason} [{passed_count}/{total} runs passed]",
        ))
    return results
