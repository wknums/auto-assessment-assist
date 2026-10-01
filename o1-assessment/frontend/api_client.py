"""
API client for calling the AWReason HTTP service (wrappers/http-service).

Used by the Streamlit UX when execution mode is set to "API" instead of direct
subprocess invocation.  Uses the ``/assess/passthrough`` endpoint which runs
awreason.py identically to the direct subprocess mode and returns the raw
output file directly in the response body (not embedded in JSON).
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

import requests

from auth import get_access_token

REASONING_DEPLOYMENT_ENV_VARS = (
    "AZURE_OPENAI_DEPLOYMENT_REASON01",
    "AZURE_OPENAI_DEPLOYMENT_REASON02",
    "AZURE_OPENAI_DEPLOYMENT_REASON03",
)
DEFAULT_REASONING_EFFORT = "high"
SUPPORTED_REASONING_EFFORTS = ("low", "medium", "high", "xhigh")


class ReasoningModelsError(RuntimeError):
    """Raised when reasoning-model discovery or validation fails."""


def _validate_reasoning_models_contract(payload: Any) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise ReasoningModelsError("Reasoning-model response must be a JSON object.")

    default_model = payload.get("defaultModel")
    models = payload.get("models")
    efforts = payload.get("supportedReasoningEfforts")
    default_effort = payload.get("defaultReasoningEffort")

    if not isinstance(default_model, str) or not default_model.strip():
        raise ReasoningModelsError("Reasoning-model response has no valid defaultModel.")
    if not isinstance(models, list) or not models:
        raise ReasoningModelsError("Reasoning-model response contains no models.")
    if not isinstance(efforts, list) or not efforts:
        raise ReasoningModelsError(
            "Reasoning-model response contains no supportedReasoningEfforts."
        )
    if default_effort not in efforts:
        raise ReasoningModelsError(
            "defaultReasoningEffort is not included in supportedReasoningEfforts."
        )

    normalized_models: List[Dict[str, Any]] = []
    deployments: List[str] = []
    for model in models:
        if not isinstance(model, dict):
            raise ReasoningModelsError("Each reasoning model must be a JSON object.")
        slot = model.get("slot")
        deployment = model.get("deployment")
        if slot not in ("reason01", "reason02", "reason03"):
            raise ReasoningModelsError(f"Invalid reasoning model slot: {slot!r}.")
        if not isinstance(deployment, str) or not deployment.strip():
            raise ReasoningModelsError(
                f"Reasoning model slot {slot!r} has no deployment."
            )
        deployment = deployment.strip()
        if deployment in deployments:
            continue
        deployments.append(deployment)
        normalized_models.append(
            {
                "slot": slot,
                "deployment": deployment,
                "isDefault": deployment == default_model,
            }
        )

    normalized_efforts = [
        effort for effort in efforts if effort in SUPPORTED_REASONING_EFFORTS
    ]
    if len(normalized_efforts) != len(efforts):
        raise ReasoningModelsError(
            "Reasoning-model response contains an unsupported reasoning effort."
        )
    if default_model not in deployments:
        raise ReasoningModelsError(
            "defaultModel is not included in the configured reasoning models."
        )

    return {
        "defaultModel": default_model,
        "defaultReasoningEffort": default_effort,
        "supportedReasoningEfforts": normalized_efforts,
        "models": normalized_models,
    }


def get_reasoning_models_from_environment(
    environ: Optional[Mapping[str, str]] = None,
) -> Dict[str, Any]:
    """Build the reasoning-model contract used by direct execution mode."""
    environ = environ if environ is not None else os.environ
    models: List[Dict[str, Any]] = []
    for index, env_name in enumerate(REASONING_DEPLOYMENT_ENV_VARS, start=1):
        deployment = environ.get(env_name, "").strip()
        if index == 1 and not deployment:
            raise ReasoningModelsError(
                "AZURE_OPENAI_DEPLOYMENT_REASON01 is required for direct execution."
            )
        if deployment and deployment not in {
            model["deployment"] for model in models
        }:
            models.append(
                {
                    "slot": f"reason{index:02d}",
                    "deployment": deployment,
                    "isDefault": index == 1,
                }
            )

    return _validate_reasoning_models_contract(
        {
            "defaultModel": models[0]["deployment"],
            "defaultReasoningEffort": DEFAULT_REASONING_EFFORT,
            "supportedReasoningEfforts": list(SUPPORTED_REASONING_EFFORTS),
            "models": models,
        }
    )


def _get_api_endpoint() -> str:
    """Return the AWR API base URL from env, stripping any trailing slash."""
    url = os.environ.get("AWR_API_ENDPOINT", "http://127.0.0.1:8080")
    return url.rstrip("/")


def _get_auth_headers() -> Dict[str, str]:
    """Build auth headers based on AUTH_MODE.

    * ``none``   – no headers (default)
    * ``apikey`` – ``X-Api-Key`` (+ optional ``X-User-Id`` / ``X-User-Role``)
    * ``entra``  – delegated Entra access token from the Streamlit session
    """
    mode = os.environ.get("AUTH_MODE", "none").lower()
    if mode == "apikey":
        headers: Dict[str, str] = {}
        api_key = os.environ.get("API_KEY", "")
        if api_key:
            headers["X-Api-Key"] = api_key
        user_id = os.environ.get("API_USER_ID", "")
        if user_id:
            headers["X-User-Id"] = user_id
        user_role = os.environ.get("API_USER_ROLE", "")
        if user_role:
            headers["X-User-Role"] = user_role
        return headers
    if mode == "entra":
        access_token = get_access_token()
        if not access_token:
            raise RuntimeError(
                "No Entra access token is available. Sign in again before calling the API."
            )
        return {"Authorization": f"Bearer {access_token}"}
    return {}


def check_api_health(endpoint: Optional[str] = None) -> Dict[str, Any]:
    """Ping /healthz and /ready. Returns a dict with keys 'alive' and 'ready'."""
    base = (endpoint or _get_api_endpoint()).rstrip("/")
    result: Dict[str, Any] = {"alive": False, "ready": False, "errors": []}
    try:
        r = requests.get(f"{base}/healthz", timeout=5)
        result["alive"] = r.status_code == 200
    except requests.RequestException as exc:
        result["errors"].append(f"healthz failed: {exc}")
    try:
        r = requests.get(f"{base}/ready", timeout=5)
        result["ready"] = r.status_code == 200
        if r.status_code != 200:
            result["errors"].append(f"ready returned {r.status_code}: {r.text}")
    except requests.RequestException as exc:
        result["errors"].append(f"ready failed: {exc}")
    return result


def get_reasoning_models(endpoint: Optional[str] = None) -> Dict[str, Any]:
    """Fetch and validate the HTTP service reasoning-model contract."""
    base = (endpoint or _get_api_endpoint()).rstrip("/")
    url = f"{base}/reasoning-models"
    try:
        response = requests.get(
            url,
            headers=_get_auth_headers(),
            timeout=10,
        )
    except requests.RequestException as exc:
        raise ReasoningModelsError(
            f"Could not query reasoning models from {url}: {exc}"
        ) from exc

    if response.status_code != 200:
        detail = response.text[:500]
        raise ReasoningModelsError(
            f"Reasoning-model discovery returned HTTP {response.status_code}: {detail}"
        )

    try:
        payload = response.json()
    except ValueError as exc:
        raise ReasoningModelsError(
            "Reasoning-model discovery returned invalid JSON."
        ) from exc
    return _validate_reasoning_models_contract(payload)


def run_assessment_via_api(
    *,
    prompt_file_path: str,
    pdf_files: List[str],
    join_option: Optional[str] = None,
    reasoning_model: Optional[str] = None,
    reasoning_effort: str = "high",
    json_template_path: Optional[str] = None,
    md_file_path: Optional[str] = None,
    image_folder: Optional[str] = None,
    output_dir: str,
    endpoint: Optional[str] = None,
    batch_id: Optional[str] = None,
    run_number: Optional[int] = None,
    total_runs: Optional[int] = None,
    aggregation_method: Optional[str] = None,
    status_callback=None,
    console_callback=None,
    out_metadata: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """Call POST /assess/passthrough on the AWReason API and save the result.

    The passthrough endpoint returns the output file directly in the
    response body with the appropriate Content-Type.  Metadata (exit code,
    duration, run ID) is conveyed via response headers.

    Returns the path to the saved result file, or None on failure.

    If *out_metadata* is provided (a mutable dict), it is populated with
    extra response metadata such as ``aggregation_uri``.  Existing callers
    that omit this parameter are unaffected.
    """
    base = (endpoint or _get_api_endpoint()).rstrip("/")
    url = f"{base}/assess/passthrough"

    def _status(msg: str):
        if status_callback:
            status_callback(msg)

    def _console(msg: str):
        if console_callback:
            console_callback(msg)

    _console(f"API mode (passthrough): endpoint = {base}\n")
    _console(f"Uploading to POST {url}\n")

    # Build multipart files and form data
    files_list: List[Any] = []
    form_data: Dict[str, str] = {}

    # Prompt file (required)
    prompt_path = Path(prompt_file_path)
    files_list.append(
        ("promptFile", (prompt_path.name, open(prompt_path, "rb"), "application/octet-stream"))
    )
    _console(f"  promptFile: {prompt_path.name}\n")

    # CV / PDF files
    for pdf_path_str in pdf_files:
        p = Path(pdf_path_str)
        files_list.append(
            ("cvFiles[]", (p.name, open(p, "rb"), "application/pdf"))
        )
        _console(f"  cvFile: {p.name}\n")

    # Image files from image folder
    if image_folder:
        img_dir = Path(image_folder)
        if img_dir.is_dir():
            for img_file in sorted(img_dir.iterdir()):
                if img_file.suffix.lower() in (".png", ".jpg", ".jpeg"):
                    files_list.append(
                        ("cvFiles[]", (img_file.name, open(img_file, "rb"), "application/octet-stream"))
                    )
                    _console(f"  imageFile: {img_file.name}\n")

    # Spec / context file (md, docx, txt)
    if md_file_path:
        md_p = Path(md_file_path)
        files_list.append(
            ("specFile", (md_p.name, open(md_p, "rb"), "application/octet-stream"))
        )
        _console(f"  specFile: {md_p.name}\n")

    # JSON template
    if json_template_path:
        jt_p = Path(json_template_path)
        files_list.append(
            ("jsonTemplate", (jt_p.name, open(jt_p, "rb"), "application/json"))
        )
        _console(f"  jsonTemplate: {jt_p.name}\n")

    # Join mode
    if join_option:
        form_data["joinMode"] = join_option
        _console(f"  joinMode: {join_option}\n")

    if reasoning_model:
        form_data["reasoningModel"] = reasoning_model
        _console(f"  reasoningModel: {reasoning_model}\n")

    if reasoning_effort:
        form_data["reasoningEffort"] = reasoning_effort
        _console(f"  reasoningEffort: {reasoning_effort}\n")

    # Batch params
    if batch_id:
        form_data["batchId"] = batch_id
        _console(f"  batchId: {batch_id}\n")
    if run_number is not None:
        form_data["runNumber"] = str(run_number)
        _console(f"  runNumber: {run_number}\n")
    if total_runs is not None:
        form_data["totalRuns"] = str(total_runs)
        _console(f"  totalRuns: {total_runs}\n")
    if aggregation_method:
        form_data["aggregationMethod"] = aggregation_method
        _console(f"  aggregationMethod: {aggregation_method}\n")

    _status("Sending assessment request to API (passthrough)...")
    auth_headers = _get_auth_headers()
    if auth_headers:
        _console(f"  auth: {os.environ.get('AUTH_MODE', 'none')} mode\n")
    _console(f"\nSending request...\n")

    try:
        response = requests.post(url, data=form_data, files=files_list, headers=auth_headers, timeout=600)
    except requests.RequestException as exc:
        _status(f"API request failed: {exc}")
        _console(f"\nERROR: API request failed: {exc}\n")
        return None
    finally:
        # Close all opened file handles
        for item in files_list:
            try:
                item[1][1].close()
            except Exception:
                pass

    _console(f"Response status: {response.status_code}\n")

    if response.status_code != 200:
        detail = response.text[:2000]
        _status(f"API returned error {response.status_code}")
        _console(f"\nERROR: API returned {response.status_code}:\n{detail}\n")
        return None

    # Read metadata from response headers
    exit_code = response.headers.get("X-AWR-Exit-Code", "0")
    duration_ms = response.headers.get("X-AWR-Duration-Ms", "0")
    output_filename = response.headers.get("X-AWR-Output-Filename", "")
    run_id = response.headers.get("X-AWR-Run-Id", "unknown")
    content_type = response.headers.get("Content-Type", "text/html")
    aggregation_uri = response.headers.get("X-AWR-Aggregation-URI", "")

    _console(f"\nAssessment completed via API (passthrough).\n")
    _console(f"  Run ID: {run_id}\n")
    _console(f"  Exit code: {exit_code}\n")
    _console(f"  Duration: {duration_ms}ms\n")
    _console(f"  Content-Type: {content_type}\n")
    _console(f"  Server filename: {output_filename}\n")
    if aggregation_uri:
        _console(f"  Aggregation URI: {aggregation_uri}\n")

    if exit_code != "0":
        _console(f"\nWARNING: awreason.py exited with code {exit_code}\n")

    # Save the response body (the output file) directly to disk
    os.makedirs(output_dir, exist_ok=True)

    # Determine file extension from the original filename or content type
    if output_filename:
        ext = Path(output_filename).suffix
    elif "json" in content_type:
        ext = ".json"
    else:
        ext = ".html"

    result_filename = f"assessment_{uuid.uuid4().hex[:12]}{ext}"
    result_path = os.path.join(output_dir, result_filename)

    with open(result_path, "wb") as f:
        f.write(response.content)
    _console(f"Result saved to: {result_path}\n")

    _status("Assessment completed successfully via API!")
    if out_metadata is not None:
        out_metadata["aggregation_uri"] = aggregation_uri or None
        out_metadata["run_id"] = run_id
        out_metadata["batch_id"] = response.headers.get("X-AWR-Batch-Id", "")
    return result_path
