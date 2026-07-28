# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE.md file in the project root for full license information.
"""
Pydantic v2 **strict** models that mirror the awr-platform contracts.

Three top-level message types:
- ``RunMessage``         – Platform → Engine (inbound from Service Bus ``engine-runs``)
- ``RunResultMessage``   – Engine → Platform (outbound via Service Bus when REPORT_MODE="servicebus")
- ``FinishRunRequest``   – Engine → Platform (HTTP PATCH /runs/{runId} when REPORT_MODE="http")
"""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


# ══════════════════════════════════════════════════════════════════════
#  Shared / nested models
# ══════════════════════════════════════════════════════════════════════

class RunProfile(BaseModel):
    """Per-run processing hints embedded inside ``RunParameters``."""
    model_config = ConfigDict(strict=True, extra="allow")

    join_mode: Optional[str] = Field(
        None,
        description="'horizontal' | 'vertical' – how to join extracted PDF page images.",
    )
    json_template_blob_uri: Optional[str] = Field(
        None,
        description="Blob URI of a JSON template for structured output.",
    )


class OutputParams(BaseModel):
    """Where the engine should write result artefacts."""
    model_config = ConfigDict(strict=True, extra="allow")

    results_container: Optional[str] = None
    results_prefix: Optional[str] = None


class AoaiParams(BaseModel):
    """Optional per-run AOAI overrides."""
    model_config = ConfigDict(strict=True, extra="allow")

    deployment: Optional[str] = None
    api_version: Optional[str] = None
    max_output_tokens: Optional[int] = None


class RunParameters(BaseModel):
    """Flexible parameter bag carried inside every ``RunMessage``."""
    model_config = ConfigDict(strict=True, extra="allow")

    cv_blob_uris: List[str] = Field(default_factory=list)
    spec_blob_uri: Optional[str] = None
    prompt_blob_uri: Optional[str] = None
    run_profile: Optional[RunProfile] = None
    return_artifacts: Optional[bool] = True
    output: Optional[OutputParams] = None
    aoai: Optional[AoaiParams] = None


class ArtifactItem(BaseModel):
    """A single uploaded artefact reference."""
    model_config = ConfigDict(strict=True)

    name: str
    blob_uri: str
    mime: Optional[str] = None
    size_bytes: Optional[int] = None
    sha256: Optional[str] = None


# ══════════════════════════════════════════════════════════════════════
#  1) RunMessage  –  Platform → Engine  (Service Bus inbound)
# ══════════════════════════════════════════════════════════════════════

class RunMessage(BaseModel):
    """Inbound message from the ``engine-runs`` Service Bus queue.

    Every field name and type **must** match the awr-platform contract
    exactly so that ``RunMessage.model_validate_json(body)`` succeeds
    without transformation.
    """
    model_config = ConfigDict(strict=True, extra="forbid")

    message_id: str = Field(..., description="UUID – unique message identifier.")
    run_id: str = Field(..., description="UUID – platform-assigned run identifier.")
    engine: str = Field(..., description="Expected value: 'awreason'.")
    parameters: RunParameters = Field(
        default_factory=RunParameters,
        description="Flexible parameter bag (cv URIs, prompt URI, AOAI overrides, etc.).",
    )
    correlation_id: str = Field(..., description="UUID – end-to-end correlation.")
    enqueued_at: str = Field(..., description="RFC 3339 / ISO 8601 UTC timestamp.")


# ══════════════════════════════════════════════════════════════════════
#  2) RunResultMessage  –  Engine → Platform  (Service Bus outbound)
# ══════════════════════════════════════════════════════════════════════

class RunResultMessage(BaseModel):
    """Outbound message sent to ``PLATFORM_RESULTS_QUEUE`` when
    ``REPORT_MODE="servicebus"``.
    """
    model_config = ConfigDict(strict=True)

    run_id: str
    status: str = Field(
        ..., description="'Succeeded' | 'Failed' | 'Partial'."
    )
    duration_ms: int
    tokens_prompt: int
    tokens_completion: int
    error_message: Optional[str] = None
    correlation_id: str
    dequeued_at: Optional[str] = None
    started_at: Optional[str] = None
    engine_completed_at: Optional[str] = None
    artifacts: Optional[List[ArtifactItem]] = None


# ══════════════════════════════════════════════════════════════════════
#  3) FinishRunRequest  –  Engine → Platform  (HTTP PATCH)
# ══════════════════════════════════════════════════════════════════════

class FinishRunRequest(BaseModel):
    """Body for ``PATCH /runs/{runId}`` when ``REPORT_MODE="http"``."""
    model_config = ConfigDict(strict=True)

    status: str
    duration_ms: int
    tokens_prompt: int
    tokens_completion: int
    error_message: Optional[str] = None
    dequeued_at: Optional[str] = None
    started_at: Optional[str] = None
    engine_completed_at: Optional[str] = None
    artifacts: Optional[List[ArtifactItem]] = None
