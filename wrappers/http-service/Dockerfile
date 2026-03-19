# ══════════════════════════════════════════════════════════════════════
#  awreason HTTP Service – Dockerfile
#  Runtime: Azure Container Apps (Python 3.11-slim)
#  Runs both the FastAPI API (port 8080) and Streamlit UX (port 8501)
# ══════════════════════════════════════════════════════════════════════
FROM python:3.11-slim AS base

# ── Metadata ─────────────────────────────────────────────────────────
LABEL maintainer="auto-assessment-assist" \
      description="FastAPI HTTP wrapper + Streamlit UX for the awreason assessment engine"

# ── Non-root user ────────────────────────────────────────────────────
RUN groupadd --gid 1000 appuser && \
    useradd  --uid 1000 --gid 1000 --create-home appuser

# ── System deps (minimal) ────────────────────────────────────────────
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        curl \
        supervisor \
        libglib2.0-0 \
        libsm6 \
        libxext6 \
        libxrender-dev && \
    rm -rf /var/lib/apt/lists/*

# ── Python deps ──────────────────────────────────────────────────────
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# ── Copy the awreason engine from the main repo ─────────────────────
# The engine lives at ../../o1-assessment relative to the Dockerfile.
# In a CI pipeline the whole repo is typically the build context:
#   docker build -f wrappers/http-service/Dockerfile .
# Adjust COPY paths if the build context differs.
COPY o1-assessment/ /app/o1-assessment/

# ── Copy the service code ────────────────────────────────────────────
COPY wrappers/http-service/app/ /app/app/

# ── Copy supervisord config ──────────────────────────────────────────
COPY wrappers/http-service/supervisord.conf /etc/supervisor/conf.d/supervisord.conf

# ── Create working directory for ephemeral data ──────────────────────
RUN mkdir -p /work && chown appuser:appuser /work

# ── Switch to non-root ───────────────────────────────────────────────
USER appuser

# ── Expose both ports ────────────────────────────────────────────────
EXPOSE 8080 8501

ENV PYTHONPATH="/app:/app/o1-assessment"
ENV WORKDIR_BASE="/work"

CMD ["supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]
