# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE.md file in the project root for full license information.
"""
Wrapper that invokes the *awreason* assessment engine.

Strategy
--------
1. **Library import** (preferred) – if the ``o1-assessment`` package is
   importable we call ``awreason.main()`` in-process.
2. **CLI fallback** – shell out to ``python <AWREASON_CLI_CMD> …`` and
   capture stdout/stderr + exit code.

The wrapper is responsible for:
- Building the CLI argument list from the per-run context.
- Parsing the resulting JSON / HTML output into response models.
- Running *N* repeated runs (``numruns``) and collecting individual results
  (matching the loop logic in ``frontend/assess-ux.py``).
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.config import settings
from app.models import (
    ArtifactRef,
    MustHaveResult,
    SingleRunResult,
    TimingsMs,
    TokenUsage,
)
from app.telemetry import get_logger

logger = get_logger(__name__)


# ──────────────────────────────────────────────────────────────────────
#  Discover the awreason script location (once at import time)
# ──────────────────────────────────────────────────────────────────────

_AWREASON_SCRIPT: Optional[str] = None


def _find_awreason_script() -> str:
    """Resolve the absolute path to ``awreason.py``."""
    global _AWREASON_SCRIPT
    if _AWREASON_SCRIPT:
        return _AWREASON_SCRIPT

    candidates = []
    # Look relative to this repo first (../../o1-assessment/awreason.py)
    resolved = Path(__file__).resolve()
    if len(resolved.parents) > 3:
        # Local dev: wrappers/http-service/app → repo root
        repo_root = resolved.parents[3]
        candidates.append(repo_root / "o1-assessment" / "awreason.py")
    # Docker layout: /app/app/awreason_runner.py → /app/o1-assessment/awreason.py
    candidates.append(resolved.parents[1] / "o1-assessment" / "awreason.py")
    candidates.append(Path(settings.awreason_cli_cmd))
    for c in candidates:
        if c.is_file():
            _AWREASON_SCRIPT = str(c.resolve())
            logger.info("awreason script found at %s", _AWREASON_SCRIPT)
            return _AWREASON_SCRIPT

    raise FileNotFoundError(
        f"Cannot locate awreason script. Searched: {[str(c) for c in candidates]}. "
        "Set AWREASON_CLI_CMD to an absolute path if needed."
    )


# ──────────────────────────────────────────────────────────────────────
#  Build CLI arguments
# ──────────────────────────────────────────────────────────────────────

def _build_cli_args(
    *,
    prompt_file: Path,
    pdf_file1: Optional[Path] = None,
    pdf_file2: Optional[Path] = None,
    md_file: Optional[Path] = None,
    json_template: Optional[Path] = None,
    join_mode: Optional[str] = None,
    images_folder1: Optional[Path] = None,
    images_folder2: Optional[Path] = None,
    output_path: Path,
    tempdir: Path,
) -> List[str]:
    """Return the argument list for a single ``awreason.py`` invocation."""
    args: List[str] = [
        sys.executable,
        _find_awreason_script(),
        "--promptfile", str(prompt_file),
        "--output", str(output_path),
        "--tempdir", str(tempdir),
    ]

    if pdf_file1:
        args += ["--pdf_file1", str(pdf_file1)]
    if pdf_file2:
        args += ["--pdf_file2", str(pdf_file2)]
    if md_file:
        args += ["--md_file", str(md_file)]
    if json_template:
        args += ["--jsonout_template", str(json_template)]
    if join_mode:
        args += ["--join", join_mode]
    if images_folder1:
        args += ["--images_folder1", str(images_folder1)]
    if images_folder2:
        args += ["--images_folder2", str(images_folder2)]

    return args


# ──────────────────────────────────────────────────────────────────────
#  Run a single invocation
# ──────────────────────────────────────────────────────────────────────

async def _run_single(
    cli_args: List[str],
    run_number: int,
    timeout: int,
) -> Tuple[int, str, str, float]:
    """Execute one ``awreason.py`` process.

    Returns ``(exit_code, stdout, stderr, duration_seconds)``.

    Uses ``subprocess.run`` in a thread-pool executor so it works on all
    platforms (``asyncio.create_subprocess_exec`` raises
    ``NotImplementedError`` on Windows with the ProactorEventLoop used by
    uvicorn).
    """
    logger.info("Starting awreason run #%d …", run_number)
    logger.info("CLI command: %s", " ".join(cli_args))
    logger.debug("CLI args (list): %s", cli_args)
    t0 = time.monotonic()

    def _blocking() -> Tuple[int, str, str]:
        try:
            result = subprocess.run(
                cli_args,
                capture_output=True,
                timeout=timeout,
            )
            return (
                result.returncode,
                result.stdout.decode("utf-8", errors="replace"),
                result.stderr.decode("utf-8", errors="replace"),
            )
        except subprocess.TimeoutExpired:
            return -1, "", f"Process timed out after {timeout}s"

    loop = asyncio.get_running_loop()
    exit_code, stdout, stderr = await loop.run_in_executor(None, _blocking)

    elapsed = time.monotonic() - t0

    if exit_code != 0:
        logger.error(
            "awreason run #%d exited with code %d (%.1fs)\nstderr: %s",
            run_number, exit_code, elapsed, stderr[:2000],
        )
    else:
        logger.info("awreason run #%d completed in %.1fs", run_number, elapsed)

    # Always log subprocess output for diagnostics
    if stdout:
        logger.info("awreason run #%d stdout:\n%s", run_number, stdout[:5000])
    if stderr:
        logger.info("awreason run #%d stderr:\n%s", run_number, stderr[:5000])

    return exit_code, stdout, stderr, elapsed


# ──────────────────────────────────────────────────────────────────────
#  Parse awreason output
# ──────────────────────────────────────────────────────────────────────

def _parse_output(output_path: Path, run_number: int) -> SingleRunResult:
    """Read awreason output file and extract structured fields."""
    result = SingleRunResult(run_number=run_number, raw_output_path=str(output_path))

    if not output_path.exists():
        logger.warning("Output file not found: %s", output_path)
        # Also check for files in the parent directory
        if output_path.parent.exists():
            siblings = list(output_path.parent.iterdir())
            logger.warning("Files in %s: %s", output_path.parent, [s.name for s in siblings])
        return result

    content = output_path.read_text(encoding="utf-8", errors="replace")
    logger.info("Parser: read %d chars from %s", len(content), output_path)
    logger.info("Parser: first 500 chars: %s", content[:500])

    # Try JSON first (direct parse)
    try:
        data = json.loads(content)
        return _parse_json_output(data, run_number, output_path, trailing_text="")
    except json.JSONDecodeError:
        pass

    # Try extracting JSON from markdown code fences (```json ... ```)
    fence_match = re.search(r"```(?:json)?\s*\n(.*?)```", content, re.DOTALL)
    if fence_match:
        try:
            data = json.loads(fence_match.group(1))
            trailing = content[fence_match.end():].strip()
            return _parse_json_output(data, run_number, output_path, trailing_text=trailing)
        except json.JSONDecodeError:
            pass

    # Try extracting the outermost { ... } JSON object
    # (handles cases where JSON is followed by trailing text like SUMMARY blocks)
    brace_start = content.find("{")
    if brace_start != -1:
        depth = 0
        end = -1
        for i in range(brace_start, len(content)):
            if content[i] == "{":
                depth += 1
            elif content[i] == "}":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        if end != -1:
            json_slice = content[brace_start : end + 1]
            trailing = content[end + 1 :].strip()
            try:
                data = json.loads(json_slice)
                logger.info("Parser: extracted JSON from brace-matching (%d chars), trailing text: %d chars", len(json_slice), len(trailing))
                return _parse_json_output(data, run_number, output_path, trailing_text=trailing)
            except json.JSONDecodeError:
                pass

    # Fall back to regex extraction from HTML / Markdown
    return _parse_text_output(content, run_number, output_path)


def _parse_json_output(
    data: Dict[str, Any], run_number: int, output_path: Path,
    trailing_text: str = "",
) -> SingleRunResult:
    """Extract fields from a JSON awreason output."""
    overall = _extract_overall_score(data)
    sub_scores = _extract_sub_scores(data)
    must_haves = _extract_must_haves(data)
    comment = _extract_comment(data, trailing_text)

    return SingleRunResult(
        run_number=run_number,
        overall_score=overall,
        sub_scores=sub_scores,
        must_haves=must_haves,
        comment=comment,
        raw_output_path=str(output_path),
    )


def _parse_text_output(
    content: str, run_number: int, output_path: Path
) -> SingleRunResult:
    """Extract scores from HTML / Markdown awreason output using regex."""
    overall: Optional[float] = None

    # Common patterns: "Overall Score: 75/100", "Score: 80%", "Total: 65"
    patterns = [
        r"(?i)overall\s*score\s*[:=]\s*(\d+(?:\.\d+)?)",
        r"(?i)total\s*score\s*[:=]\s*(\d+(?:\.\d+)?)",
        r"(?i)final\s*score\s*[:=]\s*(\d+(?:\.\d+)?)",
        r"(?i)score\s*[:=]\s*(\d+(?:\.\d+)?)\s*[/%]",
    ]
    for pat in patterns:
        m = re.search(pat, content)
        if m:
            overall = float(m.group(1))
            break

    return SingleRunResult(
        run_number=run_number,
        overall_score=overall,
        raw_output_path=str(output_path),
    )


def _extract_overall_score(data: Dict[str, Any]) -> Optional[float]:
    """Walk a JSON dict looking for overall/composite score.

    Handles common patterns:
    - ``{"overall_score": 85}``
    - ``{"composite_score": {"value": 72.3}}``
    - ``{"score": 90}``

    Uses two-pass approach: check direct key matches first, then
    recurse into nested dicts – avoids picking up inner "score" fields
    (e.g. rubric sub-scores) before the intended top-level composite.
    """
    score_keys = {"overall_score", "overallscore", "total_score", "totalscore",
                  "score", "final_score", "composite_score"}

    # --- Pass 1: direct key matches at this level ---
    for key, val in data.items():
        normalised = key.lower().replace("-", "_")
        if normalised in score_keys:
            if isinstance(val, (int, float)):
                try:
                    return float(val)
                except (TypeError, ValueError):
                    pass
            elif isinstance(val, dict):
                for vk in ("value", "score", "total"):
                    if vk in val:
                        try:
                            return float(val[vk])
                        except (TypeError, ValueError):
                            pass

    # --- Pass 2: recurse into nested dicts (fallback) ---
    for key, val in data.items():
        if isinstance(val, dict):
            found = _extract_overall_score(val)
            if found is not None:
                return found
    return None


def _extract_sub_scores(data: Dict[str, Any]) -> Dict[str, Any]:
    """Extract nested score dictionaries.

    Handles:
    - ``{"rubric_scores": {"Leadership": {"score": 80, "justification": "..."}, ...}}``
    - ``{"sub_scores": {"technical": 85, ...}}``
    """
    sub_keys = {"sub_scores", "subscores", "rubric_scores", "criteria", "categories", "sections"}
    for key, val in data.items():
        if key.lower().replace("-", "_") in sub_keys and isinstance(val, dict):
            # Flatten {"Leadership": {"score": 80}} → {"Leadership": 80}
            flat: Dict[str, Any] = {}
            for sk, sv in val.items():
                if isinstance(sv, dict) and "score" in sv:
                    flat[sk] = sv  # keep full dict (score + justification)
                else:
                    flat[sk] = sv
            return flat
    # Fallback: return all numeric top-level fields
    numeric = {}
    for key, val in data.items():
        if isinstance(val, (int, float)):
            numeric[key] = val
    return numeric


def _extract_must_haves(data: Dict[str, Any]) -> List[MustHaveResult]:
    """Extract must-have checklist items.

    Handles:
    - ``{"must_haves": [{"name": "...", "passed": true, "reason": "..."}]}``
    - ``{"eligibility": {"requirements_checklist": {"Cert": {"met": true, "evidence": "..."}}}``
    """
    # Standard list format
    must_keys = {"must_haves", "musthaves", "must_have", "checklist", "requirements"}
    for key, val in data.items():
        if key.lower().replace("-", "_") in must_keys and isinstance(val, list):
            results: List[MustHaveResult] = []
            for item in val:
                if isinstance(item, dict):
                    results.append(MustHaveResult(
                        name=str(item.get("name", item.get("requirement", ""))),
                        passed=bool(item.get("passed", item.get("met", False))),
                        reason=str(item.get("reason", item.get("comment", ""))),
                    ))
            return results

    # Prompt4-style: eligibility.requirements_checklist dict
    eligibility = data.get("eligibility", {})
    if isinstance(eligibility, dict):
        checklist = eligibility.get("requirements_checklist", {})
        if isinstance(checklist, dict) and checklist:
            results = []
            for req_name, req_val in checklist.items():
                if isinstance(req_val, dict):
                    results.append(MustHaveResult(
                        name=req_name,
                        passed=bool(req_val.get("met", False)),
                        reason=str(req_val.get("evidence", "")),
                    ))
            return results

    return []


def _extract_comment(data: Dict[str, Any], trailing_text: str = "") -> Optional[str]:
    """Build a combined comment from JSON fields and any trailing text.

    Sources (in order):
    1. ``notes`` dict → formatted as key-value lines.
    2. ``summary`` / ``comment`` / ``analysis`` string fields.
    3. Trailing text that appeared after the JSON body (e.g. a SUMMARY block).
    """
    parts: List[str] = []

    # 1. notes dict  (e.g. {"evidence_sources": [...], "assumptions": "...", ...})
    notes = data.get("notes")
    if isinstance(notes, dict) and notes:
        note_lines: List[str] = []
        for nk, nv in notes.items():
            label = nk.replace("_", " ").title()
            if isinstance(nv, list):
                nv = "; ".join(str(x) for x in nv)
            note_lines.append(f"{label}: {nv}")
        parts.append("\n".join(note_lines))

    # 2. Top-level string fields
    for key in ("summary", "comment", "analysis", "recommendation", "narrative"):
        val = data.get(key)
        if isinstance(val, str) and val.strip():
            parts.append(val.strip())

    # 3. Trailing text (outside JSON body)
    if trailing_text:
        # Strip common prefixes like "SUMMARY:" or "---"
        cleaned = re.sub(r"^(?:SUMMARY|---)\s*:?\s*", "", trailing_text, flags=re.IGNORECASE).strip()
        if cleaned:
            parts.append(cleaned)

    return "\n\n".join(parts) if parts else None


# ──────────────────────────────────────────────────────────────────────
#  Public: run N assessments (mirrors frontend/assess-ux.py loop)
# ──────────────────────────────────────────────────────────────────────

async def run_assessment(
    *,
    run_dir: Path,
    prompt_file: Path,
    pdf_file1: Optional[Path] = None,
    pdf_file2: Optional[Path] = None,
    md_file: Optional[Path] = None,
    json_template: Optional[Path] = None,
    join_mode: Optional[str] = None,
    images_folder1: Optional[Path] = None,
    images_folder2: Optional[Path] = None,
    numruns: int = 1,
) -> List[SingleRunResult]:
    """Execute *numruns* assessment iterations and return individual results.

    Mirrors the multi-run loop in ``frontend/assess-ux.py``:
    each run gets an identical set of CLI args but a unique output path.
    """
    results_dir = run_dir / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    tempdir = run_dir / "temp"
    tempdir.mkdir(parents=True, exist_ok=True)

    base_name = (pdf_file1 or prompt_file).stem

    all_results: List[SingleRunResult] = []

    logger.info(
        "run_assessment: numruns=%d, prompt=%s, pdf1=%s, pdf2=%s, md=%s, "
        "json_template=%s, join=%s, images1=%s, images2=%s",
        numruns, prompt_file, pdf_file1, pdf_file2, md_file,
        json_template, join_mode, images_folder1, images_folder2,
    )

    for run_num in range(1, numruns + 1):
        # Unique output per run (matches assess-ux.py naming convention)
        if numruns > 1:
            out_file = results_dir / f"{base_name}_run{run_num}.json"
        else:
            out_file = results_dir / f"{base_name}-analysis.json"

        cli_args = _build_cli_args(
            prompt_file=prompt_file,
            pdf_file1=pdf_file1,
            pdf_file2=pdf_file2,
            md_file=md_file,
            json_template=json_template,
            join_mode=join_mode,
            images_folder1=images_folder1,
            images_folder2=images_folder2,
            output_path=out_file,
            tempdir=tempdir,
        )

        # Retry logic (matches assess-ux.py: 3 retries, exponential backoff)
        max_retries = settings.awreason_max_retries
        backoff_base = settings.awreason_retry_backoff
        timeout = settings.awreason_cli_timeout

        for attempt in range(1, max_retries + 1):
            exit_code, stdout, stderr, duration = await _run_single(
                cli_args, run_num, timeout
            )
            if exit_code == 0:
                break

            if attempt < max_retries:
                wait = backoff_base * attempt
                logger.warning(
                    "Run #%d attempt %d failed – retrying in %ds.",
                    run_num, attempt, wait,
                )
                await asyncio.sleep(wait)
            else:
                logger.error(
                    "Run #%d failed after %d attempts.", run_num, max_retries
                )

        # Parse the output regardless of success (best-effort)
        result = _parse_output(out_file, run_num)
        result.timings_ms = TimingsMs(
            total=int(duration * 1000),
            awreason=int(duration * 1000),
            io=0,
        )

        # TODO: Extract token_usage from awreason stdout/logs if available.
        # The awreason.py script logs token counts – a future improvement
        # could parse these from stdout.
        _try_extract_token_usage(stdout, result)

        all_results.append(result)

    return all_results


def _try_extract_token_usage(stdout: str, result: SingleRunResult) -> None:
    """Best-effort extraction of token usage from awreason stdout logs."""
    try:
        inp = re.search(r"Input tokens:\s*([\d,]+)", stdout)
        out = re.search(r"Output tokens:\s*([\d,]+)", stdout)
        if inp:
            result.token_usage = TokenUsage(
                input=int(inp.group(1).replace(",", "")),
                output=int(out.group(1).replace(",", "")) if out else 0,
            )
    except Exception:
        pass


# ──────────────────────────────────────────────────────────────────────
#  Passthrough: run awreason and return raw output (no post-processing)
# ──────────────────────────────────────────────────────────────────────

async def run_passthrough(
    *,
    run_dir: Path,
    prompt_file: Path,
    pdf_file1: Optional[Path] = None,
    pdf_file2: Optional[Path] = None,
    md_file: Optional[Path] = None,
    json_template: Optional[Path] = None,
    join_mode: Optional[str] = None,
    images_folder1: Optional[Path] = None,
    images_folder2: Optional[Path] = None,
) -> Dict[str, Any]:
    """Run awreason.py once and return raw output + metadata.

    This mirrors the UX's direct subprocess invocation exactly:
    same CLI args, same output, no score extraction or structural changes.

    Returns a dict with keys:
        file_bytes, content_type, output_filename, exit_code,
        stdout, stderr, duration_ms
    """
    results_dir = run_dir / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    tempdir = run_dir / "temp"
    tempdir.mkdir(parents=True, exist_ok=True)

    base_name = (pdf_file1 or prompt_file).stem
    ext = ".json" if json_template else ".html"
    out_file = results_dir / f"{base_name}-analysis{ext}"

    cli_args = _build_cli_args(
        prompt_file=prompt_file,
        pdf_file1=pdf_file1,
        pdf_file2=pdf_file2,
        md_file=md_file,
        json_template=json_template,
        join_mode=join_mode,
        images_folder1=images_folder1,
        images_folder2=images_folder2,
        output_path=out_file,
        tempdir=tempdir,
    )

    timeout = settings.awreason_cli_timeout
    exit_code, stdout, stderr, duration = await _run_single(cli_args, 1, timeout)

    # Read the raw output file as bytes
    file_bytes: bytes = b""
    output_filename = ""
    content_type = "text/html; charset=utf-8"

    if out_file.exists():
        file_bytes = out_file.read_bytes()
        output_filename = out_file.name
        # Detect content type from the output
        stripped = file_bytes.lstrip()
        if stripped.startswith(b"{") or stripped.startswith(b"["):
            content_type = "application/json; charset=utf-8"
    else:
        # Check if awreason wrote to a different filename (e.g. .json extension)
        if results_dir.exists():
            result_files = list(results_dir.iterdir())
            if result_files:
                actual = result_files[0]
                file_bytes = actual.read_bytes()
                output_filename = actual.name
                if actual.suffix.lower() == ".json":
                    content_type = "application/json; charset=utf-8"

    return {
        "file_bytes": file_bytes,
        "content_type": content_type,
        "output_filename": output_filename,
        "exit_code": exit_code,
        "stdout": stdout,
        "stderr": stderr,
        "duration_ms": int(duration * 1000),
    }
