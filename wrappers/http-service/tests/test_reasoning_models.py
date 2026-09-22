from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

import app.api as api
import app.awreason_runner as awreason_runner
from app.config import Settings, settings
from app.main import app
from app.models import RunProfile


REPO_ROOT = Path(__file__).resolve().parents[3]
AWREASON_DIR = REPO_ROOT / "o1-assessment"
if str(AWREASON_DIR) not in sys.path:
    sys.path.insert(0, str(AWREASON_DIR))

import awreason  # noqa: E402


@pytest.fixture
def reasoning_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        settings,
        "azure_openai_deployment_reason01",
        "reasoning-primary",
    )
    monkeypatch.setattr(
        settings,
        "azure_openai_deployment_reason02",
        "reasoning-secondary",
    )
    monkeypatch.setattr(
        settings,
        "azure_openai_deployment_reason03",
        "reasoning-tertiary",
    )
    monkeypatch.setattr(settings, "auth_mode", "none")


def test_primary_reasoning_deployment_is_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for env_name in (
        "AZURE_OPENAI_DEPLOYMENT_REASON01",
        "AZURE_OPENAI_DEPLOYMENT_REASON02",
        "AZURE_OPENAI_DEPLOYMENT_REASON03",
    ):
        monkeypatch.delenv(env_name, raising=False)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_optional_reasoning_deployments_can_be_omitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT_REASON01", "primary")
    monkeypatch.delenv("AZURE_OPENAI_DEPLOYMENT_REASON02", raising=False)
    monkeypatch.delenv("AZURE_OPENAI_DEPLOYMENT_REASON03", raising=False)

    configured = Settings(_env_file=None)

    assert configured.reasoning_models == ["primary"]
    assert configured.resolve_reasoning_model() == "primary"


def test_awreason_defaults_to_reason01_and_accepts_configured_override() -> None:
    environ = {
        "AZURE_OPENAI_DEPLOYMENT_REASON01": "primary",
        "AZURE_OPENAI_DEPLOYMENT_REASON02": "secondary",
        "AZURE_OPENAI_DEPLOYMENT_REASON03": "tertiary",
    }

    assert awreason.resolve_reasoning_model(environ=environ) == "primary"
    assert (
        awreason.resolve_reasoning_model("secondary", environ=environ)
        == "secondary"
    )


def test_awreason_rejects_missing_primary_and_unconfigured_override() -> None:
    with pytest.raises(
        ValueError,
        match="AZURE_OPENAI_DEPLOYMENT_REASON01 environment variable is required",
    ):
        awreason.get_configured_reasoning_models({})

    with pytest.raises(ValueError, match="is not configured"):
        awreason.resolve_reasoning_model(
            "unknown",
            environ={"AZURE_OPENAI_DEPLOYMENT_REASON01": "primary"},
        )


def test_run_profile_defaults_to_high_effort_and_accepts_aliases() -> None:
    default_profile = RunProfile.model_validate({})
    override_profile = RunProfile.model_validate(
        {
            "reasoningModel": "reasoning-secondary",
            "reasoningEffort": "low",
        }
    )

    assert default_profile.reasoning_model is None
    assert default_profile.reasoning_effort == "high"
    assert override_profile.reasoning_model == "reasoning-secondary"
    assert override_profile.reasoning_effort == "low"


def test_model_config_defaults_to_high_effort_and_accepts_override() -> None:
    default_config = awreason.get_model_config(
        model_type="gpt5",
        api_version="2025-03-01-preview",
    )
    override_config = awreason.get_model_config(
        model_type="o1",
        api_version="2024-12-01-preview",
        reasoning_effort="medium",
    )

    assert default_config["reasoning_param"]["reasoning"]["effort"] == "high"
    assert override_config["reasoning_param"] == {"reasoning_effort": "medium"}


def test_structured_output_keeps_requested_reasoning_effort() -> None:
    config = awreason.get_model_config(
        model_type="o1",
        api_version="2024-12-01-preview",
        json_template=object(),
        reasoning_effort="low",
    )

    assert config["reasoning_param"] == {"reasoning_effort": "low"}


def test_cli_args_include_explicit_model_and_effort(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        awreason_runner,
        "_find_awreason_script",
        lambda: str(AWREASON_DIR / "awreason.py"),
    )

    args = awreason_runner._build_cli_args(
        prompt_file=tmp_path / "prompt.txt",
        reasoning_model="reasoning-secondary",
        reasoning_effort="medium",
        output_path=tmp_path / "output.json",
        tempdir=tmp_path,
    )

    assert args[args.index("--model") + 1] == "reasoning-secondary"
    assert args[args.index("--reasoning-effort") + 1] == "medium"


def test_reasoning_models_route_lists_configured_models(
    reasoning_settings: None,
) -> None:
    with TestClient(app) as client:
        response = client.get("/reasoning-models")

    assert response.status_code == 200
    assert response.json() == {
        "defaultModel": "reasoning-primary",
        "defaultReasoningEffort": "high",
        "supportedReasoningEfforts": ["low", "medium", "high"],
        "models": [
            {
                "slot": "reason01",
                "deployment": "reasoning-primary",
                "isDefault": True,
            },
            {
                "slot": "reason02",
                "deployment": "reasoning-secondary",
                "isDefault": False,
            },
            {
                "slot": "reason03",
                "deployment": "reasoning-tertiary",
                "isDefault": False,
            },
        ],
    }


@pytest.mark.parametrize(
    ("form_data", "expected_model", "expected_effort"),
    [
        ({}, "reasoning-primary", "high"),
        (
            {
                "reasoningModel": "reasoning-secondary",
                "reasoningEffort": "low",
            },
            "reasoning-secondary",
            "low",
        ),
    ],
)
def test_passthrough_propagates_reasoning_selection(
    reasoning_settings: None,
    monkeypatch: pytest.MonkeyPatch,
    form_data: dict[str, str],
    expected_model: str,
    expected_effort: str,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_run_passthrough(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {
            "file_bytes": b"result",
            "content_type": "text/plain",
            "output_filename": "result.txt",
            "exit_code": 0,
            "stdout": "",
            "stderr": "",
            "duration_ms": 1,
        }

    monkeypatch.setattr(api, "run_passthrough", fake_run_passthrough)
    with TestClient(app) as client:
        response = client.post(
            "/assess/passthrough",
            data=form_data,
            files={"promptFile": ("prompt.txt", b"prompt", "text/plain")},
        )

    assert response.status_code == 200
    assert captured["reasoning_model"] == expected_model
    assert captured["reasoning_effort"] == expected_effort
    assert response.headers["X-AWR-Reasoning-Model"] == expected_model
    assert response.headers["X-AWR-Reasoning-Effort"] == expected_effort


def test_passthrough_rejects_unconfigured_reasoning_model(
    reasoning_settings: None,
) -> None:
    with TestClient(app) as client:
        response = client.post(
            "/assess/passthrough",
            data={"reasoningModel": "not-configured"},
            files={"promptFile": ("prompt.txt", b"prompt", "text/plain")},
        )

    assert response.status_code == 400
    assert response.json()["title"] == "Invalid reasoningModel"


def test_upload_propagates_reasoning_selection(
    reasoning_settings: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_run_assessment(**kwargs: Any) -> list[Any]:
        captured.update(kwargs)
        return []

    monkeypatch.setattr(api, "run_assessment", fake_run_assessment)
    payload = {
        "jobId": "job",
        "applicationId": "app",
        "numruns": 1,
        "runProfile": {
            "reasoningModel": "reasoning-tertiary",
            "reasoningEffort": "medium",
        },
        "returnArtifacts": False,
    }

    with TestClient(app) as client:
        response = client.post(
            "/assess/upload",
            data={"payload": json.dumps(payload)},
            files={"promptFile": ("prompt.txt", b"prompt", "text/plain")},
        )

    assert response.status_code == 200
    assert captured["reasoning_model"] == "reasoning-tertiary"
    assert captured["reasoning_effort"] == "medium"


def test_json_assess_propagates_default_reasoning_settings(
    reasoning_settings: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_download(_uri: str, destination: Path) -> Path:
        destination.write_text("prompt", encoding="utf-8")
        return destination

    async def fake_run_assessment(**kwargs: Any) -> list[Any]:
        captured.update(kwargs)
        return []

    monkeypatch.setattr(api, "download_blob_to_path", fake_download)
    monkeypatch.setattr(api, "run_assessment", fake_run_assessment)

    with TestClient(app) as client:
        response = client.post(
            "/assess",
            json={
                "jobId": "job",
                "applicationId": "app",
                "promptBlobUri": "https://example.invalid/prompt.txt",
                "returnArtifacts": False,
            },
        )

    assert response.status_code == 200
    assert captured["reasoning_model"] == "reasoning-primary"
    assert captured["reasoning_effort"] == "high"
