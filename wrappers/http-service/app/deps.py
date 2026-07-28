# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE.md file in the project root for full license information.
"""
FastAPI dependencies: auth (none / API-key / Entra ID JWT),
correlation-ID middleware, per-replica concurrency semaphore.
"""
from __future__ import annotations

import asyncio
import hmac
import uuid
from typing import Optional

import jwt  # PyJWT
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import settings
from app.telemetry import (
    correlation_id_var,
    job_id_var,
    application_id_var,
    run_id_var,
    get_logger,
)

logger = get_logger(__name__)

# ══════════════════════════════════════════════════════════════════════
#  Per-replica concurrency gate
# ══════════════════════════════════════════════════════════════════════

_semaphore: Optional[asyncio.Semaphore] = None


def get_semaphore() -> asyncio.Semaphore:
    """Return (or lazily create) the per-replica asyncio.Semaphore."""
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(settings.per_replica_concurrency)
        logger.info(
            "Concurrency semaphore initialised (max=%d).",
            settings.per_replica_concurrency,
        )
    return _semaphore


# ══════════════════════════════════════════════════════════════════════
#  Correlation-ID middleware helper
# ══════════════════════════════════════════════════════════════════════

async def set_correlation_id(request: Request) -> str:
    """Read ``X-Correlation-ID`` header or generate a new one, then set contextvars."""
    cid = (
        request.headers.get("X-Correlation-ID")
        or correlation_id_var.get("")
        or uuid.uuid4().hex
    )
    correlation_id_var.set(cid)
    return cid


# ══════════════════════════════════════════════════════════════════════
#  JWT Bearer verification (Entra ID)
# ══════════════════════════════════════════════════════════════════════

_bearer_scheme = HTTPBearer(auto_error=False)

# JWKS cache (fetched at first call)
_jwks_client: Optional[jwt.PyJWKClient] = None


def _get_jwks_client() -> jwt.PyJWKClient:
    global _jwks_client
    if _jwks_client is None:
        issuer = settings.aad_issuer.rstrip("/")
        # Standard Entra ID OIDC well-known / JWKS URL
        jwks_uri = f"{issuer}/discovery/v2.0/keys"
        _jwks_client = jwt.PyJWKClient(jwks_uri, cache_keys=True)
        logger.info("JWKS client initialised from %s", jwks_uri)
    return _jwks_client


# ══════════════════════════════════════════════════════════════════════
#  API-key verification helpers
# ══════════════════════════════════════════════════════════════════════

def _verify_api_key(request: Request) -> dict:
    """Validate the ``X-Api-Key`` header against the configured secret.

    On success, returns a claims dict built from the ``X-User-Id`` and
    ``X-User-Role`` headers so that downstream code sees the same shape
    as an Entra ID token payload.
    """
    key = request.headers.get("X-Api-Key", "")
    if not key or not hmac.compare_digest(key, settings.api_key):
        raise HTTPException(status_code=401, detail="Invalid or missing API key.")

    return {
        "sub": request.headers.get("X-User-Id", "anonymous"),
        "roles": [request.headers.get("X-User-Role", "user")],
        "auth_mode": "apikey",
    }


# ══════════════════════════════════════════════════════════════════════
#  Entra ID JWT verification
# ══════════════════════════════════════════════════════════════════════

def _verify_entra_jwt(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials],
) -> dict:
    """Validate an Entra ID JWT bearer token."""
    if credentials is None or not credentials.credentials:
        raise HTTPException(status_code=401, detail="Missing bearer token.")

    token = credentials.credentials

    try:
        jwks_client = _get_jwks_client()
        signing_key = jwks_client.get_signing_key_from_jwt(token)

        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=settings.aad_audience,
            issuer=settings.aad_issuer,
            options={"verify_exp": True},
        )
        _authorize_entra_claims(payload)
        logger.debug("Token verified for sub=%s", payload.get("sub"))
        return payload

    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token has expired.")
    except jwt.InvalidAudienceError:
        raise HTTPException(status_code=401, detail="Invalid audience.")
    except jwt.InvalidIssuerError:
        raise HTTPException(status_code=401, detail="Invalid issuer.")
    except jwt.PyJWTError as exc:
        logger.warning("JWT validation failed: %s", exc)
        raise HTTPException(status_code=401, detail="Invalid token.")


def _authorize_entra_claims(payload: dict) -> None:
    """Require the delegated API scope or the service application role."""
    scopes = set(str(payload.get("scp", "")).split())
    raw_roles = payload.get("roles", [])
    roles = {raw_roles} if isinstance(raw_roles, str) else set(raw_roles)

    if settings.aad_required_scope in scopes:
        return
    if settings.aad_required_app_role in roles:
        return

    raise HTTPException(
        status_code=403,
        detail=(
            f"Token requires scope '{settings.aad_required_scope}' or "
            f"application role '{settings.aad_required_app_role}'."
        ),
    )


# ══════════════════════════════════════════════════════════════════════
#  Unified auth dependency
# ══════════════════════════════════════════════════════════════════════

async def verify_token(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
) -> Optional[dict]:
    """Authenticate the request based on ``AUTH_MODE``.

    Modes
    -----
    * ``none``   – no authentication (local development).
    * ``apikey`` – shared secret in ``X-Api-Key`` header; caller identity
      passed via ``X-User-Id`` / ``X-User-Role`` headers.
    * ``entra``  – Entra ID JWT bearer-token validation.
    """
    mode = settings.auth_mode.lower()

    if mode == "none":
        return None  # dev / local – no auth

    if mode == "apikey":
        return _verify_api_key(request)

    if mode == "entra":
        return _verify_entra_jwt(request, credentials)

    logger.error("Unknown AUTH_MODE '%s'; rejecting request.", settings.auth_mode)
    raise HTTPException(status_code=500, detail="Server auth misconfiguration.")
