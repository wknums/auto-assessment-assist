"""
End-to-end test for POST /assess/upload endpoint.

Inputs
------
- TEST_PROMPT_FILE: path to a synthetic prompt
- TEST_SPEC_FILE:   path to a synthetic job specification
- TEST_CV_FILE:     path to a synthetic CV

Prerequisites
-------------
1. The FastAPI server must be running:
       cd wrappers/http-service
       python -m uvicorn app.main:app --host 127.0.0.1 --port 8080
2. Azure OpenAI credentials must be configured (.env or environment).
3. RUN_HTTP_E2E=1 and all input paths above must be configured.

Usage
-----
    RUN_HTTP_E2E=1 TEST_PROMPT_FILE=<path> TEST_SPEC_FILE=<path> \
      TEST_CV_FILE=<path> pytest wrappers/http-service/tests/test_assess_e2e.py
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import httpx
import pytest

# ── Configuration ─────────────────────────────────────────────────────
BASE_URL = os.getenv("TEST_BASE_URL", "http://127.0.0.1:8080")
TIMEOUT = int(os.getenv("TEST_TIMEOUT", "600"))
NUM_RUNS = int(os.getenv("TEST_NUM_RUNS", "3"))
JOB_ID = os.getenv("TEST_JOB_ID", "synthetic-e2e-job")
APPLICATION_ID = os.getenv("TEST_APPLICATION_ID", "synthetic-e2e-application")
RUN_HTTP_E2E = os.getenv("RUN_HTTP_E2E") == "1"

pytestmark = pytest.mark.skipif(
    not RUN_HTTP_E2E,
    reason="Set RUN_HTTP_E2E=1 to run tests against a live service.",
)


def required_input_file(variable_name: str) -> Path:
    value = os.getenv(variable_name)
    assert value, f"{variable_name} must point to a synthetic test fixture"

    path = Path(value).expanduser()
    assert path.is_file(), f"{variable_name} file not found: {path}"
    return path


def banner(msg: str) -> None:
    print(f"\n{'='*70}\n  {msg}\n{'='*70}")


def check_server() -> bool:
    """Quick liveness check before running the expensive assessment."""
    try:
        r = httpx.get(f"{BASE_URL}/healthz", timeout=5)
        return r.status_code == 200
    except httpx.ConnectError:
        return False


def test_healthz() -> None:
    banner("1/5  GET /healthz")
    r = httpx.get(f"{BASE_URL}/healthz", timeout=10)
    print(f"  Status: {r.status_code}")
    print(f"  Body:   {r.json()}")
    assert r.status_code == 200, f"Expected 200, got {r.status_code}"
    assert r.json()["status"] == "alive"
    print("  ✅ PASSED")


def test_ready() -> None:
    banner("2/5  GET /ready")
    r = httpx.get(f"{BASE_URL}/ready", timeout=10)
    print(f"  Status: {r.status_code}")
    print(f"  Body:   {r.json()}")
    assert r.status_code == 200, f"Expected 200, got {r.status_code}"
    assert r.json()["status"] == "ready"
    print("  ✅ PASSED")


def test_docs() -> None:
    banner("3/5  GET /docs (OpenAPI)")
    r = httpx.get(f"{BASE_URL}/docs", timeout=10)
    print(f"  Status: {r.status_code}")
    assert r.status_code == 200, f"Expected 200, got {r.status_code}"
    print("  ✅ PASSED")


def test_aggregate_inline() -> None:
    banner("4/5  POST /aggregate-runs (inline data)")
    payload = {
        "jobId": "e2e-agg-test",
        "applicationId": "app-001",
        "runs": [
            {"inline": {"overall_score": 72, "sub_scores": {"technical": 80, "leadership": 65}}},
            {"inline": {"overall_score": 80, "sub_scores": {"technical": 85, "leadership": 74}}},
            {"inline": {"overall_score": 76, "sub_scores": {"technical": 78, "leadership": 72}}},
        ],
        "strategy": {"type": "median", "trimPercent": 10},
    }
    r = httpx.post(f"{BASE_URL}/aggregate-runs", json=payload, timeout=15)
    print(f"  Status: {r.status_code}")
    data = r.json()
    print(f"  Aggregated score: {data.get('aggregation', {}).get('aggregatedScore')}")
    print(f"  Method:           {data.get('aggregation', {}).get('method')}")
    print(f"  Sub-scores:       {data.get('aggregation', {}).get('subScoreAggregations')}")
    assert r.status_code == 200, f"Expected 200, got {r.status_code}"
    assert data["aggregation"]["aggregatedScore"] is not None
    print("  ✅ PASSED")


def test_assess_upload() -> None:
    prompt_file = required_input_file("TEST_PROMPT_FILE")
    spec_file = required_input_file("TEST_SPEC_FILE")
    cv_file = required_input_file("TEST_CV_FILE")

    banner(f"5/5  POST /assess/upload  (numruns={NUM_RUNS})")
    print(f"  Prompt:  {prompt_file}")
    print(f"  Spec:    {spec_file}")
    print(f"  CV:      {cv_file}")

    # Build the multipart payload
    payload_json = json.dumps({
        "jobId": JOB_ID,
        "applicationId": APPLICATION_ID,
        "numruns": NUM_RUNS,
        "runProfile": {
            "joinMode": "vertical",
        },
        "returnArtifacts": False,       # no blob configured locally
    })

    # Open files for upload
    with open(prompt_file, "rb") as pf, \
         open(spec_file, "rb") as sf, \
         open(cv_file, "rb") as cf:

        files = [
            ("promptFile", (prompt_file.name, pf, "text/plain")),
            ("specFile", (spec_file.name, sf, "application/pdf")),
            ("cvFiles[]", (cv_file.name, cf, "application/pdf")),
        ]

        print(f"\n  Sending request (timeout={TIMEOUT}s, ~{TIMEOUT//60} min) …")
        t0 = time.time()

        r = httpx.post(
            f"{BASE_URL}/assess/upload",
            data={"payload": payload_json},
            files=files,
            timeout=TIMEOUT,
            headers={"X-Correlation-ID": "e2e-test-run"},
        )

    elapsed = time.time() - t0
    print(f"\n  Response received in {elapsed:.1f}s")
    print(f"  Status: {r.status_code}")

    if r.status_code != 200:
        print(f"  Error body:\n{json.dumps(r.json(), indent=2)}")
        print("  ❌ FAILED")
        raise AssertionError(f"Expected 200, got {r.status_code}")

    data = r.json()

    # ── Summary ───────────────────────────────────────────────────────
    print(f"\n  Run ID:          {data.get('runId')}")
    print(f"  Correlation ID:  {data.get('correlationId')}")
    print(f"  Overall Score:   {data.get('overallScore')}")
    print(f"  Sub-scores:      {json.dumps(data.get('subScores', {}), indent=4)}")

    # Must-haves
    must_haves = data.get("mustHaves", [])
    if must_haves:
        print(f"  Must-haves ({len(must_haves)}):")
        for mh in must_haves:
            icon = "✅" if mh.get("passed") else "❌"
            print(f"    {icon} {mh.get('name')}: {mh.get('reason', '')[:80]}")

    # Comment / notes
    comment = data.get("comment")
    if comment:
        print(f"\n  Comment:")
        for line in comment.splitlines()[:8]:
            print(f"    {line[:120]}")
        if len(comment.splitlines()) > 8:
            print(f"    … ({len(comment.splitlines())} lines total)")

    # Timings
    timings = data.get("timingsMs", {})
    print(f"\n  Timings:")
    print(f"    Total:    {timings.get('total', 0) / 1000:.1f}s")
    print(f"    awreason: {timings.get('awreason', 0) / 1000:.1f}s")
    print(f"    I/O:      {timings.get('io', 0) / 1000:.1f}s")

    # Token usage
    tokens = data.get("tokenUsage", {})
    print(f"  Tokens:  input={tokens.get('input', 0):,}  output={tokens.get('output', 0):,}")

    # Individual runs
    runs = data.get("individualRuns", [])
    print(f"\n  Individual runs ({len(runs)}):")
    for run in runs:
        print(f"    Run #{run.get('runNumber')}: score={run.get('overallScore')}  "
              f"time={run.get('timingsMs', {}).get('total', 0) / 1000:.1f}s  "
              f"tokens_in={run.get('tokenUsage', {}).get('input', 0):,}")

    # Aggregation (if numruns > 1)
    agg = data.get("aggregation")
    if agg:
        print(f"\n  Aggregation ({agg.get('method')}):")
        print(f"    Aggregated score: {agg.get('aggregatedScore')}")
        print(f"    Mean:     {agg.get('mean')}")
        print(f"    Median:   {agg.get('median')}")
        print(f"    Std Dev:  {agg.get('stdDev')}")
        print(f"    Range:    [{agg.get('minScore')} – {agg.get('maxScore')}]")
        print(f"    95% CI:   {agg.get('confidenceInterval95')}")
        sub_agg = agg.get("subScoreAggregations", {})
        if sub_agg:
            print(f"    Sub-score aggregations: {json.dumps(sub_agg, indent=6)}")

    print(f"\n  ✅ PASSED  ({elapsed:.1f}s)")


# ══════════════════════════════════════════════════════════════════════
#  Main
# ══════════════════════════════════════════════════════════════════════

def main() -> int:
    print(f"Base URL: {BASE_URL}")
    print(f"Timeout:  {TIMEOUT}s")

    if not RUN_HTTP_E2E:
        print("\nSet RUN_HTTP_E2E=1 to run tests against a live service.")
        return 1

    if not check_server():
        print(f"\n❌ Cannot reach {BASE_URL}/healthz – is the server running?")
        return 1

    passed = 0
    failed = 0

    for test_fn in [test_healthz, test_ready, test_docs, test_aggregate_inline, test_assess_upload]:
        try:
            test_fn()
            passed += 1
        except Exception as exc:
            print(f"  ❌ FAILED: {exc}")
            failed += 1

    banner("RESULTS")
    print(f"  Passed: {passed}")
    print(f"  Failed: {failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
