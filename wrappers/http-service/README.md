# awreason HTTP Service

Production-ready FastAPI wrapper around the **awreason** assessment engine, designed to run on **Azure Container Apps (ACA)**.

## Architecture at a glance

```
Client
  │
  ├─ POST /assess          ─► download inputs from Blob → run awreason N times → upload artifacts → return JSON
  ├─ POST /assess/upload   ─► multipart file uploads (same pipeline)
  ├─ GET  /assess/status/{requestId} ─► check if a request is actively processing on this replica
  ├─ GET  /assess/status   ─► list active requests on this replica
  ├─ POST /aggregate-runs  ─► download/inline results → compute aggregates → return JSON
  ├─ GET  /healthz         ─► liveness probe
  └─ GET  /ready           ─► readiness probe (workdir + credential check)
```

### Key features

| Feature | Detail |
|---------|--------|
| **Multi-run assessment** | `numruns` parameter runs the same inputs *N* times (matches `frontend/assess-ux.py` logic) and aggregates scores |
| **Aggregation** | Median / mean / trimmed-mean / IQR-mean with variance analysis and 95 % CI |
| **Blob Storage** | `DefaultAzureCredential` (Managed Identity) – **no SAS** per corporate policy |
| **APIM AI Gateway** | Optional routing through Azure API Management; bearer token obtained via MI |
| **AuthN/AuthZ** | Pluggable JWT verifier for Microsoft Entra ID; `AUTH_REQUIRED=false` for dev |
| **Concurrency** | Per-replica `asyncio.Semaphore` (`PER_REPLICA_CONCURRENCY`, default `4`) |
| **Request status** | Disk-backed active request markers with `GET /assess/status/{requestId}` |
| **Telemetry** | OpenTelemetry (OTLP), structured JSON logs, `correlationId` on every log line |
| **Error handling** | RFC 7807 `application/problem+json`; secrets never leaked |
| **Containerised** | Non-root Docker image, ACA manifest with EmptyDir, probes, scaling rules |

---

## Local development

### 1. Prerequisites

- Python 3.11+
- Azure CLI (`az login` for local credential fallback)
- The `o1-assessment/` engine must be accessible (the repo root is expected two levels up)

### 2. Create a virtual environment

```bash
cd wrappers/http-service
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux / macOS
source .venv/bin/activate

pip install -r requirements.txt
```

### 3. Configure environment variables

```bash
cp app/settings.sample.env .env
# Edit .env with your values
```

At minimum set:
- `WORKDIR_BASE` – a writable local path (e.g. `./work`)
- `AZURE_OPENAI_ENDPOINT` and/or `APIM_AOAI_BASE_URL`
- `AZ_STORAGE_NAME` (or leave empty for local-only runs)

### 4. Run the server

```bash
uvicorn app.main:app --reload --port 8080
```

Browse to <http://localhost:8080/docs> for the interactive Swagger UI.

### 5. Quick test

```bash
# Liveness
curl http://localhost:8080/healthz

# Request status (caller should provide X-Request-ID on assess requests)
curl http://localhost:8080/assess/status/my-request-id

# Assess (JSON mode – needs real blob URIs)
curl -X POST http://localhost:8080/assess \
  -H "Content-Type: application/json" \
  -d '{
    "jobId": "demo-1",
    "applicationId": "app-42",
    "promptBlobUri": "https://myacct.blob.core.windows.net/uploads/prompt.txt",
    "cvBlobUris": ["https://myacct.blob.core.windows.net/uploads/cv.pdf"],
    "numruns": 3
  }'
```

---

## Tests

```bash
pip install pytest httpx
pytest tests/ -v
```

Integration tests (require Azure credentials) are skipped by default:

```bash
pytest tests/ -v -m integration
```

---

## Docker build & run

Build from the **repository root** (so the engine code is within the build context):

```bash
docker build -f wrappers/http-service/Dockerfile -t awreason-http-service .
docker run -p 8080:8080 \
  -e WORKDIR_BASE=/work \
  -e AUTH_REQUIRED=false \
  -e AZURE_OPENAI_ENDPOINT=https://... \
  awreason-http-service
```

---

## Deploy to Azure Container Apps

Deployment configuration defaults to the **repo-root `.env`** file. Set
`DEPLOY_ENV_FILE` to use another file; relative paths are resolved from the
repository root by both deployment scripts. The `AZ_*_REUSE` flags control
whether each resource is reused (must exist) or auto-created if missing.

### Step 1 – Setup identity & app registration

```bash
cd wrappers/http-service/deploy
bash setup-identity.sh              # creates all (prompts before .env update)
bash setup-identity.sh --yes        # creates all, auto-approve .env updates
bash setup-identity.sh mi           # managed identity + roles only
bash setup-identity.sh app          # app registration only
bash setup-identity.sh status       # show current state
bash setup-identity.sh mi --yes     # MI + roles, auto-approve

# Use a different env file for identity setup and deployment
export DEPLOY_ENV_FILE=.env_qa
bash setup-identity.sh mi --yes
bash deploy.sh all
```

This script:
- Creates a **User-Assigned Managed Identity** and assigns *Storage Blob Data Contributor* + *Cognitive Services OpenAI User* roles.
- Creates an **API App Registration** exposing delegated scope `access_as_user`
  and application role `TalentMatch.Access`.
- Creates a separate confidential **Streamlit App Registration**, grants it the
  delegated API scope, and configures localhost plus ACA redirect URIs.
- Assigns `TalentMatch.Access` to the service principal identified by
  `TALENT_MATCH_CLIENT_ID` when that variable is set.
- At the end, displays all pending changes to the selected env file (old → new) and prompts for confirmation before writing.
- Pass `-y` / `--yes` to skip the interactive prompt and auto-approve.
- Respects `AZ_IDENTITIES_REUSE` – when `TRUE`, the MI must already exist.

> **Caveat:** If you decline the `.env` write and re-run, `AAD_CLIENT_SECRET` will
> still be empty in `.env`, so the script will append another client credential.
> Remove the unused credential from Entra after confirming which secret is active.

For production Entra authentication, configure:

```dotenv
AUTH_MODE=entra
AAD_APP_DISPLAY_NAME=awreason-streamlit
AAD_API_APP_DISPLAY_NAME=awreason-http-service-api
STREAMLIT_REDIRECT_URI=https://<streamlit-host>/
TALENT_MATCH_CLIENT_ID=<talent-match-application-client-id>
```

`setup-identity.sh app` writes `AAD_CLIENT_ID`, `AAD_CLIENT_SECRET`,
`AAD_API_CLIENT_ID`, `AAD_API_SCOPE`, and the backend audience/claim settings to
the selected env file. Streamlit requests `AAD_API_SCOPE`. Talent Match requests
`api://<AAD_API_CLIENT_ID>/.default` with client credentials or managed identity;
its token must contain the `TalentMatch.Access` role.

The script attempts tenant-wide delegated consent. If it reports consent as
pending, a tenant administrator must grant admin consent to
`awreason-streamlit` in the Entra admin center, unless tenant policy allows
individual user consent. Run `bash setup-identity.sh status` to verify consent,
scope, role, redirect, and Talent Match assignment state.

The API registration must be dedicated to this deployment. The setup script
refuses to replace an existing registration that contains unrelated scopes or
app roles. Existing Streamlit redirects are preserved, and
`STREAMLIT_REDIRECT_URI` is registered when supplied.

### Step 2 – Deploy (infra + build + apply)

```bash
bash deploy.sh                  # full deploy: infra → build → terraform apply (with confirmation)
bash deploy.sh all              # same as above
bash deploy.sh allforce         # full deploy: infra → build → apply (no confirmation)
bash deploy.sh infra            # ensure ACR, ACA env, storage exist
bash deploy.sh build            # build & push image via ACR Tasks
bash deploy.sh apply            # terraform apply only (with confirmation)
bash deploy.sh applyforce       # terraform apply only (no confirmation)
bash deploy.sh yaml             # deploy via ACA YAML (no Terraform)

# Use a different env file (for example QA)
DEPLOY_ENV_FILE=.env_qa bash deploy.sh yaml
```

Terraform deployments isolate state by env filename: `.env` uses the existing
`default` workspace, while `.env_qa_mcaps` uses `qa_mcaps`. Set
`DEPLOY_TF_WORKSPACE` to choose another stable workspace explicitly.

The deploy script:
- **Auto-creates** ACR, Container Apps Environment, and Storage Account when `AZ_*_REUSE=FALSE` and the resource doesn't exist.  Generated names are written back to `.env`.
- **Fails fast** when `AZ_*_REUSE=TRUE` and the resource is missing.
- Builds the Docker image **remotely** via `az acr build` (no local Docker needed).
- Generates `terraform.tfvars` from `.env` via `gen-tfvars.sh`, then runs `terraform plan/apply`.
- Loads deployment settings from `DEPLOY_ENV_FILE` when provided; otherwise defaults to repo-root `.env`.
- For `yaml` deploys, injects critical runtime values from the selected env file, including auth mode, API key secret, AOAI deployment/version, retries, concurrency, and active-request tracking folder.

### REUSE flags

| Flag | `TRUE` | `FALSE` |
|------|--------|--------|
| `AZ_STORAGE_REUSE` | Must exist | Creates if missing |
| `AZ_ACR_REUSE` | Must exist | Creates Basic SKU ACR |
| `AZ_CONTAINER_APP_ENV_REUSE` | Must exist | Creates ACA Environment |
| `AZ_IDENTITIES_REUSE` | Must exist | Creates MI + role assignments |

### Manual alternatives

**Option A – YAML manifest** (after `setup-identity.sh` and `deploy.sh infra`):
```bash
bash deploy.sh yaml

# QA example
DEPLOY_ENV_FILE=.env_qa bash deploy.sh yaml
```

Important: `deploy.sh yaml` now updates an existing Container App when present, and only creates a new app when it does not already exist.

**Option B – Terraform only:**
```bash
cd deploy/terraform
bash gen-tfvars.sh
terraform init && terraform apply -var-file=terraform.tfvars
```

---

## Managed Identity & APIM routing

| Requirement | Implementation |
|-------------|----------------|
| **Storage access** | User-Assigned MI with *Storage Blob Data Contributor* role on the storage account |
| **AOAI access** | Same MI with *Cognitive Services OpenAI User* on the AOAI / Cognitive Services resource |
| **APIM → AOAI** | Set `APIM_AOAI_BASE_URL` to route through API Management. The service obtains a bearer token for `https://cognitiveservices.azure.com/.default` using MI and sends it in the `Authorization` header. APIM validates and forwards to the backend AOAI. |
| **No API keys** | `AZURE_OPENAI_API_KEY` is supported as a fallback but **not recommended**. Prefer MI. |

---

## Per-run cleanup

Every assessment request creates a unique folder: `/work/run-<GUID>`.
All downloads, temp images, and awreason outputs live in that folder.
The folder is **always** removed in a `finally` block – even on errors or timeouts.

The `PER_REPLICA_CONCURRENCY` semaphore ensures that only *N* runs execute
concurrently on each replica, preventing resource exhaustion on the EmptyDir volume.

Active request IDs are tracked as marker files under `ACTIVE_REQUEST_IDS_DIR`
(default: `<WORKDIR_BASE>/active-requests`) while requests are executing.
The status endpoints read those marker files to report in-flight request state.

---

## File structure

```
wrappers/http-service/
├── README.md
├── requirements.txt
├── Dockerfile
├── .dockerignore
├── app/
│   ├── __init__.py
│   ├── main.py               # FastAPI app factory, lifespan, middleware
│   ├── api.py                # /assess, /aggregate-runs, /healthz, /ready
│   ├── models.py             # Pydantic request/response models
│   ├── config.py             # pydantic-settings: all env vars
│   ├── storage_blob.py       # Blob download/upload (MI auth)
│   ├── awreason_runner.py    # invoke awreason engine (CLI subprocess)
│   ├── aoai_client.py        # APIM → AOAI caller (MI token)
│   ├── telemetry.py          # OpenTelemetry + JSON logging
│   ├── deps.py               # JWT auth, correlation-ID, semaphore
│   ├── request_tracker.py    # Active request marker file helpers
│   ├── cleanup.py            # per-run workdir context manager
│   ├── utils.py              # GUID, timing, MIME helpers
│   └── settings.sample.env   # example env values
├── supervisord.conf          # runs API + Streamlit in one container
├── deploy/
│   ├── deploy.sh             # main deployment script (defaults to .env, override with DEPLOY_ENV_FILE)
│   ├── setup-identity.sh     # MI, role assignments, App Registration
│   ├── aca-containerapp.yaml # ACA YAML manifest (template)
│   └── terraform/
│       ├── main.tf           # Terraform module
│       └── gen-tfvars.sh     # generates terraform.tfvars from .env
└── tests/
    ├── test_smoke.py         # pytest smoke + integration stubs
    └── test_assess_e2e.py    # end-to-end assessment test
```
