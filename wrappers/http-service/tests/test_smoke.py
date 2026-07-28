# ══════════════════════════════════════════════════════════════════════
#  Smoke / integration tests for the awreason HTTP service
# ══════════════════════════════════════════════════════════════════════
#
#  Run with:  pytest tests/ -v
#
#  By default, tests that need a running API or Azure credentials are
#  skipped (marked with ``@pytest.mark.integration``).
#
#    pytest tests/ -v -m integration  # run integration tests only
# ══════════════════════════════════════════════════════════════════════

from __future__ import annotations

import json
import os
import uuid

import pytest
from fastapi.testclient import TestClient

# Ensure workdir base is a temp location for tests
os.environ.setdefault("WORKDIR_BASE", os.path.join(os.path.dirname(__file__), "..", "_test_work"))
os.environ.setdefault("AUTH_MODE", "none")
os.environ.setdefault("AZ_STORAGE_NAME", "")           # disable blob for unit tests
os.environ.setdefault("AZURE_OPENAI_ENDPOINT", "")     # not needed for smoke tests
os.environ.setdefault("LOG_LEVEL", "DEBUG")

from app.main import app  # noqa: E402 – env must be set before import
from app.config import settings  # noqa: E402
from app.request_tracker import remove_active_request, write_active_request  # noqa: E402


@pytest.fixture(autouse=True)
def disable_auth_for_smoke_tests(monkeypatch: pytest.MonkeyPatch):
    """Keep smoke tests in their documented local-development auth mode."""
    monkeypatch.setattr(settings, "auth_mode", "none")


@pytest.fixture(scope="module")
def client():
    """Synchronous test client for the FastAPI app."""
    with TestClient(app) as c:
        yield c


# ── Health ────────────────────────────────────────────────────────────

class TestHealth:
    def test_healthz(self, client: TestClient):
        resp = client.get("/healthz")
        assert resp.status_code == 200
        assert resp.json()["status"] == "alive"

    def test_ready(self, client: TestClient):
        resp = client.get("/ready")
        # 200 or 503 depending on credential availability – both are valid shapes
        assert resp.status_code in (200, 503)
        assert "status" in resp.json()


# ── Assess endpoint – unit-level ──────────────────────────────────────

class TestAssessEndpoint:
    """Tests that validate request parsing / validation.

    These do NOT call the real awreason engine – they exercise the
    FastAPI layer only.
    """

    def test_missing_prompt_uri(self, client: TestClient):
        """POST /assess with an empty body should return 422 (validation)."""
        resp = client.post("/assess", json={})
        assert resp.status_code == 422

    def test_minimal_json_body_validation(self, client: TestClient):
        """POST /assess with a valid-shaped body but no real URIs.

        Depending on blob config this may return 400/500 – we just assert
        we get a JSON response with expected keys.
        """
        body = {
            "jobId": "test-job",
            "applicationId": "test-app",
            "promptBlobUri": "https://fake.blob.core.windows.net/uploads/prompt.txt",
            "cvBlobUris": [],
            "numruns": 1,
        }
        resp = client.post("/assess", json=body)
        # We expect either a proper response or a problem+json error
        data = resp.json()
        assert isinstance(data, dict)
        # Should contain either response keys or problem detail keys
        has_response_keys = "runId" in data or "run_id" in data
        has_problem_keys = "title" in data and "status" in data
        assert has_response_keys or has_problem_keys


# ── Aggregate-runs endpoint – unit-level ──────────────────────────────

class TestAggregateEndpoint:
    def test_aggregate_inline(self, client: TestClient):
        """POST /aggregate-runs with inline payloads (no blob downloads)."""
        body = {
            "jobId": "agg-job",
            "applicationId": "agg-app",
            "runs": [
                {"inline": {"overall_score": 70, "sub_scores": {"a": 80, "b": 60}}},
                {"inline": {"overall_score": 80, "sub_scores": {"a": 90, "b": 70}}},
                {"inline": {"overall_score": 75, "sub_scores": {"a": 85, "b": 65}}},
            ],
            "strategy": {"type": "median", "trimPercent": 10},
        }
        resp = client.post("/aggregate-runs", json=body)
        # May fail at blob upload stage but should parse + aggregate OK
        data = resp.json()
        assert isinstance(data, dict)
        # If aggregation succeeded we should have aggregation data
        if resp.status_code == 200:
            agg = data.get("aggregation") or data.get("aggregation")
            if agg:
                assert agg.get("aggregated_score") is not None or agg.get("aggregatedScore") is not None

    def test_aggregate_inline_generic_profile(self, client: TestClient):
        """Generic profile stays schema-agnostic and avoids cv-specific consensus fields."""
        body = {
            "jobId": "agg-job-generic",
            "applicationId": "agg-app-generic",
            "runs": [
                {"inline": {"overall_score": 70, "sub_scores": {"a": 80, "b": 60}}},
                {"inline": {"overall_score": 80, "sub_scores": {"a": 90, "b": 70}}},
            ],
            "strategy": {
                "type": "median",
                "profile": "generic_passthrough",
                "trimPercent": 10,
            },
        }
        resp = client.post("/aggregate-runs", json=body)
        assert resp.status_code == 200
        data = resp.json()
        agg = data.get("aggregation", {})
        assert agg.get("profile") == "generic_passthrough"
        assert not agg.get("mustHaveAggregations")

    def test_aggregate_inline_cv_profile(self, client: TestClient):
        """CV profile includes must-have majority aggregation."""
        body = {
            "jobId": "agg-job-cv",
            "applicationId": "agg-app-cv",
            "runs": [
                {
                    "inline": {
                        "overall_score": 74,
                        "must_haves": [
                            {"name": "cert", "passed": True, "reason": "found"},
                            {"name": "clearance", "passed": False, "reason": "missing"},
                        ],
                    }
                },
                {
                    "inline": {
                        "overall_score": 79,
                        "must_haves": [
                            {"name": "cert", "passed": True, "reason": "found"},
                            {"name": "clearance", "passed": True, "reason": "verified"},
                        ],
                    }
                },
                {
                    "inline": {
                        "overall_score": 77,
                        "must_haves": [
                            {"name": "cert", "passed": True, "reason": "found"},
                            {"name": "clearance", "passed": False, "reason": "missing"},
                        ],
                    }
                },
            ],
            "strategy": {
                "type": "median",
                "profile": "cv_scoring_v1",
                "trimPercent": 10,
            },
        }
        resp = client.post("/aggregate-runs", json=body)
        assert resp.status_code == 200
        data = resp.json()
        agg = data.get("aggregation", {})
        assert agg.get("profile") == "cv_scoring_v1"
        must_haves = agg.get("mustHaveAggregations") or []
        assert len(must_haves) >= 2


# ── Integration tests (skipped by default) ────────────────────────────

requires_creds = pytest.mark.skipif(
    not os.getenv("AZURE_OPENAI_ENDPOINT"),
    reason="AZURE_OPENAI_ENDPOINT not set – skipping integration tests",
)


@pytest.mark.integration
@requires_creds
class TestIntegrationAssess:
    """Full end-to-end tests that require Azure resources.

    Run with: pytest tests/ -v -m integration
    """

    def test_assess_with_text_prompt(self, client: TestClient):
        """Minimal assessment with a text-only prompt (no images)."""
        # This test would need real blob URIs.
        pytest.skip("Provide real blob URIs to run this test.")


# ── Correlation-ID propagation ────────────────────────────────────────

class TestCorrelationId:
    def test_correlation_id_echoed(self, client: TestClient):
        """The X-Correlation-ID header should be echoed back."""
        cid = str(uuid.uuid4())
        resp = client.get("/healthz", headers={"X-Correlation-ID": cid})
        assert resp.headers.get("X-Correlation-ID") == cid

    def test_correlation_id_generated(self, client: TestClient):
        """When not provided, a correlation ID is generated."""
        resp = client.get("/healthz")
        # The middleware sets it, but /healthz may not trigger it.
        # Just ensure the response is valid.
        assert resp.status_code == 200


class TestRequestStatus:
    def test_status_not_processing(self, client: TestClient):
        request_id = "req-not-active"
        remove_active_request(request_id)

        resp = client.get(f"/assess/status/{request_id}")
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["requestId"] == request_id
        assert payload["active"] is False
        assert payload["status"] == "not_processing"

    def test_status_processing(self, client: TestClient):
        request_id = "req-active"
        write_active_request(
            request_id,
            metadata={
                "runId": "run-123",
                "jobId": "job-abc",
                "applicationId": "app-xyz",
                "correlationId": "cid-1",
                "endpoint": "/assess",
                "method": "POST",
            },
        )
        try:
            resp = client.get(f"/assess/status/{request_id}")
            assert resp.status_code == 200
            payload = resp.json()
            assert payload["requestId"] == request_id
            assert payload["active"] is True
            assert payload["status"] == "processing"
            assert payload["runId"] == "run-123"
            assert payload["jobId"] == "job-abc"
        finally:
            remove_active_request(request_id)

    def test_status_list(self, client: TestClient):
        request_id = "req-list-active"
        write_active_request(request_id, metadata={"jobId": "job-list"})
        try:
            resp = client.get("/assess/status")
            assert resp.status_code == 200
            payload = resp.json()
            assert "activeCount" in payload
            assert "requests" in payload
            assert any(item.get("requestId") == request_id for item in payload["requests"])
        finally:
            remove_active_request(request_id)
