# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE.md file in the project root for full license information.
"""
Pydantic models for request/response contracts and RFC 7807 problem details.
"""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field
import uuid


# ══════════════════════════════════════════════════════════════════════
#  POST /assess – request models
# ══════════════════════════════════════════════════════════════════════

class RunProfile(BaseModel):
    """Optional per-run tuning knobs."""
    join_mode: Optional[str] = Field(
        None,
        description="'horizontal' or 'vertical' – how to join extracted PDF page images.",
        pattern="^(horizontal|vertical)$",
    )
    json_template_blob_uri: Optional[str] = Field(
        None,
        description="Blob URI of a JSON template for structured output.",
    )


class AssessRequestJSON(BaseModel):
    """JSON body for POST /assess."""
    job_id: str = Field(..., alias="jobId", description="Caller-assigned job identifier.")
    application_id: str = Field(..., alias="applicationId", description="Application/candidate ID.")
    prompt_blob_uri: str = Field(..., alias="promptBlobUri", description="Blob URI of the prompt file.")
    cv_blob_uris: List[str] = Field(
        default_factory=list, alias="cvBlobUris",
        description="List of Blob URIs for CV / submission PDFs.",
    )
    spec_blob_uri: Optional[str] = Field(
        None, alias="specBlobUri",
        description="Blob URI of the specification / rubric document.",
    )
    numruns: int = Field(
        1, ge=1, le=10,
        description="Number of repeated assessment runs for the same inputs (for aggregation).",
    )
    run_profile: Optional[RunProfile] = Field(None, alias="runProfile")
    return_artifacts: bool = Field(True, alias="returnArtifacts")

    model_config = ConfigDict(populate_by_name=True)


# ══════════════════════════════════════════════════════════════════════
#  POST /assess – response
# ══════════════════════════════════════════════════════════════════════

class MustHaveResult(BaseModel):
    name: str
    passed: bool
    reason: str = ""


class ArtifactRef(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str
    blob_uri: str = Field("", alias="blobUri")


class TimingsMs(BaseModel):
    total: int = 0
    awreason: int = 0
    io: int = 0


class TokenUsage(BaseModel):
    input: int = 0
    output: int = 0


class SingleRunResult(BaseModel):
    """Result of a single awreason invocation."""
    run_number: int = Field(0, alias="runNumber")
    overall_score: Optional[float] = Field(None, alias="overallScore")
    sub_scores: Dict[str, Any] = Field(default_factory=dict, alias="subScores")
    must_haves: List[MustHaveResult] = Field(default_factory=list, alias="mustHaves")
    comment: Optional[str] = Field(None, description="Model commentary / summary extracted from the assessment output.")
    artifacts: List[ArtifactRef] = Field(default_factory=list)
    timings_ms: TimingsMs = Field(default_factory=TimingsMs, alias="timingsMs")
    token_usage: TokenUsage = Field(default_factory=TokenUsage, alias="tokenUsage")
    raw_output_path: Optional[str] = Field(None, exclude=True)

    model_config = ConfigDict(populate_by_name=True)


class AggregationStats(BaseModel):
    """Statistics produced when numruns > 1."""
    method: str = "median"
    run_count: int = 0
    aggregated_score: Optional[float] = Field(None, alias="aggregatedScore")
    mean: Optional[float] = None
    median: Optional[float] = None
    std_dev: Optional[float] = Field(None, alias="stdDev")
    variance: Optional[float] = None
    min_score: Optional[float] = Field(None, alias="minScore")
    max_score: Optional[float] = Field(None, alias="maxScore")
    confidence_interval_95: Optional[List[float]] = Field(None, alias="confidenceInterval95")
    sub_score_aggregations: Dict[str, Any] = Field(default_factory=dict, alias="subScoreAggregations")
    must_have_aggregations: List[Dict[str, Any]] = Field(default_factory=list, alias="mustHaveAggregations")

    model_config = ConfigDict(populate_by_name=True)


class AssessResponse(BaseModel):
    """Full response for POST /assess."""
    run_id: str = Field(default_factory=lambda: str(uuid.uuid4()), alias="runId")
    job_id: str = Field("", alias="jobId")
    application_id: str = Field("", alias="applicationId")
    overall_score: Optional[float] = Field(None, alias="overallScore")
    sub_scores: Dict[str, Any] = Field(default_factory=dict, alias="subScores")
    must_haves: List[MustHaveResult] = Field(default_factory=list, alias="mustHaves")
    comment: Optional[str] = Field(None, description="Aggregated / representative model commentary.")
    artifacts: List[ArtifactRef] = Field(default_factory=list)
    timings_ms: TimingsMs = Field(default_factory=TimingsMs, alias="timingsMs")
    token_usage: TokenUsage = Field(default_factory=TokenUsage, alias="tokenUsage")
    correlation_id: str = Field("", alias="correlationId")
    # Multi-run fields
    individual_runs: List[SingleRunResult] = Field(default_factory=list, alias="individualRuns")
    aggregation: Optional[AggregationStats] = Field(None)

    model_config = ConfigDict(populate_by_name=True)


# ══════════════════════════════════════════════════════════════════════
#  POST /aggregate-runs – request / response
# ══════════════════════════════════════════════════════════════════════

class RunReference(BaseModel):
    """Reference to an existing run result – either by Blob URI or inline."""
    model_config = ConfigDict(populate_by_name=True)

    blob_uri: Optional[str] = Field(None, alias="blobUri")
    inline: Optional[Dict[str, Any]] = None


class AggregationStrategy(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    type: str = "median"
    trim_percent: float = Field(10.0, alias="trimPercent")


class AggregateRunsRequest(BaseModel):
    job_id: str = Field(..., alias="jobId")
    application_id: str = Field(..., alias="applicationId")
    runs: List[RunReference] = Field(..., min_length=2)
    strategy: AggregationStrategy = Field(default_factory=lambda: AggregationStrategy())

    model_config = ConfigDict(populate_by_name=True)


class AggregateRunsResponse(BaseModel):
    job_id: str = Field("", alias="jobId")
    application_id: str = Field("", alias="applicationId")
    aggregation: AggregationStats = Field(default_factory=lambda: AggregationStats())
    artifacts: List[ArtifactRef] = Field(default_factory=list)
    parameters_used: AggregationStrategy = Field(
        default_factory=lambda: AggregationStrategy(), alias="parametersUsed"
    )
    correlation_id: str = Field("", alias="correlationId")

    model_config = ConfigDict(populate_by_name=True)


# ══════════════════════════════════════════════════════════════════════
#  POST /assess/passthrough – response
# ══════════════════════════════════════════════════════════════════════

class PassthroughResponse(BaseModel):
    """Raw passthrough response – returns awreason output without any
    post-processing, score extraction, or structural transformation."""
    run_id: str = Field(default_factory=lambda: str(uuid.uuid4()), alias="runId")
    exit_code: int = Field(0, alias="exitCode")
    raw_output: str = Field("", alias="rawOutput", description="Raw content of the awreason output file.")
    content_type: str = Field("text/html", alias="contentType", description="Detected content type (text/html or application/json).")
    stdout: str = ""
    stderr: str = ""
    duration_ms: int = Field(0, alias="durationMs")
    output_filename: str = Field("", alias="outputFilename")
    correlation_id: str = Field("", alias="correlationId")

    model_config = ConfigDict(populate_by_name=True)


# ══════════════════════════════════════════════════════════════════════
#  RFC 7807 Problem Detail
# ══════════════════════════════════════════════════════════════════════

class ProblemDetail(BaseModel):
    """RFC 7807 JSON problem detail body."""
    model_config = ConfigDict(populate_by_name=True)

    type: str = "about:blank"
    title: str = ""
    status: int = 500
    detail: str = ""
    instance: str = ""
    correlation_id: str = Field("", alias="correlationId")
