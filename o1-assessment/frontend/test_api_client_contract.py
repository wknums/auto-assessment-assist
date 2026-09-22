from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

FRONTEND_DIR = Path(__file__).parent
sys.path.insert(0, str(FRONTEND_DIR))

import api_client  # noqa: E402


class FakeResponse:
    def __init__(
        self,
        *,
        status_code: int = 200,
        payload: Any = None,
        text: str = "",
        content: bytes = b"result",
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text
        self.content = content
        self.headers = headers or {}

    def json(self) -> Any:
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def reasoning_contract() -> dict[str, Any]:
    return {
        "defaultModel": "primary",
        "defaultReasoningEffort": "high",
        "supportedReasoningEfforts": ["low", "medium", "high"],
        "models": [
            {
                "slot": "reason01",
                "deployment": "primary",
                "isDefault": True,
            },
            {
                "slot": "reason02",
                "deployment": "secondary",
                "isDefault": False,
            },
        ],
    }


def test_environment_contract_uses_reason01_and_high_defaults() -> None:
    contract = api_client.get_reasoning_models_from_environment(
        {
            "AZURE_OPENAI_DEPLOYMENT_REASON01": "primary",
            "AZURE_OPENAI_DEPLOYMENT_REASON02": "secondary",
            "AZURE_OPENAI_DEPLOYMENT_REASON03": "",
        }
    )

    assert contract == reasoning_contract()


def test_environment_contract_requires_reason01() -> None:
    with pytest.raises(
        api_client.ReasoningModelsError,
        match="AZURE_OPENAI_DEPLOYMENT_REASON01 is required",
    ):
        api_client.get_reasoning_models_from_environment({})


def test_api_model_discovery_uses_contract_route_and_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_get(url: str, **kwargs: Any) -> FakeResponse:
        captured["url"] = url
        captured.update(kwargs)
        return FakeResponse(payload=reasoning_contract())

    monkeypatch.setattr(api_client, "_get_auth_headers", lambda: {"X-Api-Key": "key"})
    monkeypatch.setattr(api_client.requests, "get", fake_get)

    contract = api_client.get_reasoning_models("https://service.example/")

    assert contract == reasoning_contract()
    assert captured["url"] == "https://service.example/reasoning-models"
    assert captured["headers"] == {"X-Api-Key": "key"}
    assert captured["timeout"] == 10


def test_api_model_discovery_rejects_invalid_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(api_client, "_get_auth_headers", lambda: {})
    monkeypatch.setattr(
        api_client.requests,
        "get",
        lambda *_args, **_kwargs: FakeResponse(
            payload={
                **reasoning_contract(),
                "defaultModel": "not-in-models",
            }
        ),
    )

    with pytest.raises(
        api_client.ReasoningModelsError,
        match="defaultModel is not included",
    ):
        api_client.get_reasoning_models("https://service.example")


def test_assessment_request_sends_selected_model_and_effort(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prompt_path = tmp_path / "prompt.txt"
    prompt_path.write_text("prompt", encoding="utf-8")
    captured: dict[str, Any] = {}

    def fake_post(url: str, **kwargs: Any) -> FakeResponse:
        captured["url"] = url
        captured.update(kwargs)
        return FakeResponse(
            headers={
                "X-AWR-Exit-Code": "0",
                "X-AWR-Output-Filename": "result.txt",
                "Content-Type": "text/plain",
            }
        )

    monkeypatch.setattr(api_client, "_get_auth_headers", lambda: {})
    monkeypatch.setattr(api_client.requests, "post", fake_post)

    result = api_client.run_assessment_via_api(
        prompt_file_path=str(prompt_path),
        pdf_files=[],
        reasoning_model="secondary",
        reasoning_effort="medium",
        output_dir=str(tmp_path),
        endpoint="https://service.example",
    )

    assert result is not None
    assert captured["url"] == "https://service.example/assess/passthrough"
    assert captured["data"]["reasoningModel"] == "secondary"
    assert captured["data"]["reasoningEffort"] == "medium"
