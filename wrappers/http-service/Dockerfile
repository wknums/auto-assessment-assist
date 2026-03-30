# ══════════════════════════════════════════════════════════════════════
#  awreason HTTP Service – Dockerfile
#  Runtime: Azure Container Apps (Python 3.11-slim)
#  Runs FastAPI API (8080) + Streamlit UX (8501) behind nginx (8000)
# ══════════════════════════════════════════════════════════════════════
FROM mcr.microsoft.com/mirror/docker/library/python:3.11-slim AS base

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
        nginx \
        supervisor \
        libglib2.0-0 \
        libsm6 \
        libxext6 \
        libxrender-dev && \
    rm -rf /var/lib/apt/lists/*

# ── Python deps ──────────────────────────────────────────────────────
WORKDIR /app

COPY wrappers/http-service/requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# ── Smoke-test: fail the build if critical deps are missing ──────────
RUN python -c "import uvicorn; import streamlit; import fastapi; print('✅ deps OK')"

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

# ── Copy nginx config ────────────────────────────────────────────────
COPY wrappers/http-service/nginx.conf /etc/nginx/nginx.conf

# ── Verify the app can be imported (catches missing deps/typos) ──────
RUN WORKDIR_BASE=/tmp PYTHONPATH=/app:/app/o1-assessment python -c "from app.main import app; print('✅ app import OK')"

# ── Create working directory for ephemeral data ──────────────────────
# nginx needs /var dirs writable by appuser, and /tmp for temp files
RUN mkdir -p /work /work/grading_results && chown -R appuser:appuser /work && \
    chown -R appuser:appuser /var/log/nginx /var/lib/nginx /run

# ── Switch to non-root ───────────────────────────────────────────────
USER appuser

# ── Expose nginx port (API + UX behind reverse proxy) ────────────────
EXPOSE 8000

ENV PYTHONPATH="/app:/app/o1-assessment"
ENV WORKDIR_BASE="/work"

CMD ["supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]
