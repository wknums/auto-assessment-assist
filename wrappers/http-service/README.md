# awreason HTTP Service

Production-ready FastAPI wrapper around the **awreason** assessment engine, designed to run on **Azure Container Apps (ACA)**.

## Architecture at a glance

```
Client
  │
  ├─ POST /assess          ─► download inputs from Blob → run awreason N times → upload artifacts → return JSON
  ├─ POST /assess/upload   ─► multipart file uploads (same pipeline)
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
| **Concurrency** | Per-replica `asyncio.Semaphore` (`PER_REPLICA_CONCURRENCY`) |
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

### Option A – YAML manifest

1. Push the image to your ACR:
   ```bash
   az acr build --registry <acr> --image awreason-http-service:latest \
       -f wrappers/http-service/Dockerfile .
   ```

2. Replace the `{{ }}` placeholders in `deploy/aca-containerapp.yaml`.

3. Deploy:
   ```bash
   az containerapp create --resource-group <rg> \
       --yaml deploy/aca-containerapp.yaml
   ```

### Option B – Terraform

```bash
cd deploy/terraform
terraform init
terraform plan -var-file=terraform.tfvars
terraform apply
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
│   ├── cleanup.py            # per-run workdir context manager
│   ├── utils.py              # GUID, timing, MIME helpers
│   └── settings.sample.env   # example env values
├── deploy/
│   ├── aca-containerapp.yaml # ACA YAML manifest
│   └── terraform/
│       └── main.tf           # optional Terraform module
└── tests/
    └── test_smoke.py         # pytest smoke + integration stubs
```
