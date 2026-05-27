# ══════════════════════════════════════════════════════════════════════
#  AWReason Queue Worker – Dockerfile
#  Runtime: Azure Container Apps (Python 3.11-slim)
#  Scaled by KEDA on Azure Service Bus queue depth.
# ══════════════════════════════════════════════════════════════════════
FROM python:3.11-slim AS base

# ── Metadata ─────────────────────────────────────────────────────────
LABEL maintainer="auto-assessment-assist" \
      description="Service Bus queue worker for the AWReason engine"

# ── Non-root user ────────────────────────────────────────────────────
RUN groupadd --gid 1000 appuser && \
    useradd  --uid 1000 --gid 1000 --create-home appuser

# ── System deps (minimal – same base as the HTTP service) ────────────
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        curl \
        libglib2.0-0 \
        libsm6 \
        libxext6 \
        libxrender-dev && \
    rm -rf /var/lib/apt/lists/*

# ── Python deps ──────────────────────────────────────────────────────
WORKDIR /app

COPY docker/worker-requirements.txt ./requirements.txt
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# ── Copy shared packages ─────────────────────────────────────────────
# Build context is the repo root:
#   docker build -f docker/worker.Dockerfile .
COPY contracts/       /app/contracts/
COPY runtime/         /app/runtime/
COPY engine_core/     /app/engine_core/

# ── Copy the awreason engine ─────────────────────────────────────────
COPY o1-assessment/   /app/o1-assessment/

# ── Copy the queue worker ────────────────────────────────────────────
COPY wrappers/queue-worker/ /app/wrappers/queue-worker/

# ── Create working directory for ephemeral data ──────────────────────
RUN mkdir -p /work && chown appuser:appuser /work

# ── Switch to non-root ───────────────────────────────────────────────
USER appuser

# ── Runtime environment ──────────────────────────────────────────────
ENV PYTHONPATH="/app:/app/o1-assessment"
ENV WORKDIR_BASE="/work"

CMD ["python", "-m", "wrappers.queue-worker.main"]
