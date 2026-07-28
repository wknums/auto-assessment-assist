# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE.md file in the project root for full license information.
"""
Azure Entra ID authentication for the Streamlit UX.

Behaviour
---------
- **Local development**: auth is skipped unless ``AUTH_MODE=entra``.
- **Azure Container Apps / App Service**:
  users must sign in with a Microsoft Entra ID account that belongs to
  the configured tenant.  The flow uses the OAuth 2.0 Authorization Code
  grant via MSAL.

Required env vars (only when deployed):
    AAD_CLIENT_ID       – App Registration (client) ID
    AAD_CLIENT_SECRET   – App Registration client secret
    AAD_TENANT_ID       – Entra ID tenant ID (restricts sign-in to this tenant)
    AAD_API_SCOPE       – Delegated scope exposed by the backend API registration
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Optional

import streamlit as st

# ── Detect Azure environment ──────────────────────────────────────────
# Azure Container Apps and App Service inject WEBSITE_SITE_NAME.
# When absent we assume local dev → skip auth.
_RUNNING_ON_AZURE = bool(os.environ.get("WEBSITE_SITE_NAME")
                         or os.environ.get("CONTAINER_APP_NAME"))


def is_running_on_azure() -> bool:
    """Return True when the app is hosted on Azure (ACA / App Service)."""
    return _RUNNING_ON_AZURE


def is_auth_enabled() -> bool:
    """Return True when Streamlit must authenticate the current user."""
    return _RUNNING_ON_AZURE or os.environ.get("AUTH_MODE", "none").lower() == "entra"


def require_auth() -> Optional[dict]:
    """Gate the Streamlit app behind Entra ID sign-in when on Azure.

    Returns
    -------
    dict | None
        The user's ID-token claims when authenticated (or mock claims
        when running locally).  Returns ``None`` only while the login
        page is being displayed (caller should ``st.stop()``).
    """
    if not is_auth_enabled():
        # Local dev – no auth needed
        return {"name": "Local Developer", "preferred_username": "local@dev"}

    # ── Lazy-import msal (only needed on Azure) ──────────────────────
    try:
        import msal
    except ImportError:
        st.error(
            "**msal** package is not installed.  "
            "Add `msal` to requirements.txt and rebuild the container."
        )
        st.stop()
        return None

    # ── Configuration ────────────────────────────────────────────────
    client_id = os.environ.get("AAD_CLIENT_ID", "")
    client_secret = os.environ.get("AAD_CLIENT_SECRET", "")
    tenant_id = os.environ.get("AAD_TENANT_ID", "")

    if not all([client_id, client_secret, tenant_id]):
        st.error(
            "Entra ID auth is required but one or more env vars are missing: "
            "`AAD_CLIENT_ID`, `AAD_CLIENT_SECRET`, `AAD_TENANT_ID`."
        )
        st.stop()
        return None

    authority = f"https://login.microsoftonline.com/{tenant_id}"
    scopes = [_get_api_scope()]

    # ── Build MSAL Confidential Client ───────────────────────────────
    cache = _get_token_cache()
    cca = msal.ConfidentialClientApplication(
        client_id,
        authority=authority,
        client_credential=client_secret,
        token_cache=cache,
    )

    # ── Check for existing session ───────────────────────────────────
    if "user_claims" in st.session_state:
        return st.session_state["user_claims"]

    # ── Check for auth callback (code in query params) ───────────────
    query_params = st.query_params
    auth_code = query_params.get("code")

    if auth_code:
        state = query_params.get("state", "")
        if not _validate_auth_state(state):
            st.error("Authentication response state was invalid or expired. Start sign-in again.")
            st.query_params.clear()
            st.stop()
            return None

        try:
            result = cca.acquire_token_by_authorization_code(
                auth_code,
                scopes=scopes,
                redirect_uri=_get_redirect_uri(),
            )
        except ValueError:
            st.error("Authentication response state was invalid. Start sign-in again.")
            st.stop()
            return None

        if "id_token_claims" in result and "access_token" in result:
            claims = _store_token_result(result)
            # Clear the code from the URL
            st.query_params.clear()
            st.rerun()
            return None

        # Token acquisition failed
        st.error(f"Authentication failed: {result.get('error_description', 'Unknown error')}")
        st.stop()
        return None

    # ── No session, no callback → show login button ──────────────────
    auth_uri = cca.get_authorization_request_url(
        scopes=scopes,
        state=_create_auth_state(),
        redirect_uri=_get_redirect_uri(),
    )

    st.markdown("## Sign in required")
    st.markdown(
        "This application requires authentication with your Microsoft work account."
    )
    st.link_button("Sign in with Microsoft", auth_uri, type="primary")
    st.stop()
    return None


def get_user_display(claims: dict) -> str:
    """Return a friendly display string for the logged-in user."""
    name = claims.get("name", "")
    email = claims.get("preferred_username", "")
    if name and email:
        return f"{name} ({email})"
    return name or email or "Authenticated User"


def get_access_token() -> str:
    """Return a current delegated access token for the HTTP backend."""
    token = st.session_state.get("api_access_token", "")
    expires_at = int(st.session_state.get("api_access_token_expires_at", 0))
    if token and expires_at > int(time.time()) + 60:
        return token

    try:
        import msal
    except ImportError:
        return ""

    client_id = os.environ.get("AAD_CLIENT_ID", "")
    client_secret = os.environ.get("AAD_CLIENT_SECRET", "")
    tenant_id = os.environ.get("AAD_TENANT_ID", "")
    if not all([client_id, client_secret, tenant_id]):
        return ""

    client = msal.ConfidentialClientApplication(
        client_id,
        authority=f"https://login.microsoftonline.com/{tenant_id}",
        client_credential=client_secret,
        token_cache=_get_token_cache(),
    )
    accounts = client.get_accounts()
    if not accounts:
        return ""

    result = client.acquire_token_silent([_get_api_scope()], account=accounts[0])
    if not result or "access_token" not in result:
        return ""

    _store_token_result(result)
    return st.session_state.get("api_access_token", "")


def logout():
    """Clear the session to log the user out."""
    for key in (
        "user_claims",
        "api_access_token",
        "api_access_token_expires_at",
        "msal_cache",
    ):
        st.session_state.pop(key, None)
    st.rerun()


# ── Internal helpers ──────────────────────────────────────────────────

def _encode_urlsafe(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _decode_urlsafe(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _create_auth_state(now: Optional[int] = None) -> str:
    """Create a short-lived signed OAuth state that survives a new Streamlit session."""
    payload = json.dumps(
        {
            "iat": int(time.time()) if now is None else now,
            "nonce": secrets.token_urlsafe(24),
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    encoded_payload = _encode_urlsafe(payload)
    signature = hmac.new(
        os.environ.get("AAD_CLIENT_SECRET", "").encode("utf-8"),
        encoded_payload.encode("ascii"),
        hashlib.sha256,
    ).digest()
    return f"v1.{encoded_payload}.{_encode_urlsafe(signature)}"


def _validate_auth_state(
    state: str,
    now: Optional[int] = None,
    max_age_seconds: int = 600,
) -> bool:
    """Validate signature and age of a stateless OAuth callback state."""
    try:
        version, encoded_payload, encoded_signature = state.split(".")
        if version != "v1":
            return False
        expected_signature = hmac.new(
            os.environ.get("AAD_CLIENT_SECRET", "").encode("utf-8"),
            encoded_payload.encode("ascii"),
            hashlib.sha256,
        ).digest()
        if not hmac.compare_digest(
            _decode_urlsafe(encoded_signature),
            expected_signature,
        ):
            return False
        payload = json.loads(_decode_urlsafe(encoded_payload))
        issued_at = int(payload["iat"])
        current_time = int(time.time()) if now is None else now
        return -60 <= current_time - issued_at <= max_age_seconds
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return False

def _get_redirect_uri() -> str:
    """Build the OAuth redirect URI from the current request URL."""
    # In ACA the app is behind HTTPS ingress.
    # Prefer explicit redirect URI, then stable app FQDN components,
    # and only as a last resort fall back to CONTAINER_APP_HOSTNAME.
    redirect = os.environ.get("STREAMLIT_REDIRECT_URI")
    if redirect:
        if not redirect.endswith("/"):
            redirect = f"{redirect}/"
        return redirect

    app_name = os.environ.get("CONTAINER_APP_NAME", "")
    env_dns_suffix = os.environ.get("CONTAINER_APP_ENV_DNS_SUFFIX", "")
    if app_name and env_dns_suffix:
        host = f"{app_name}.{env_dns_suffix}"
    else:
        host = os.environ.get("CONTAINER_APP_HOSTNAME", "localhost:8501")

    scheme = "https" if _RUNNING_ON_AZURE else "http"
    return f"{scheme}://{host}/"


def _get_api_scope() -> str:
    """Return the delegated scope exposed by the HTTP backend registration."""
    configured_scope = os.environ.get("AAD_API_SCOPE", "")
    if configured_scope:
        return configured_scope

    api_client_id = os.environ.get("AAD_API_CLIENT_ID", "")
    if not api_client_id:
        raise RuntimeError(
            "AAD_API_SCOPE or AAD_API_CLIENT_ID is required for Entra API auth."
        )
    return f"api://{api_client_id}/access_as_user"


def _store_token_result(result: dict) -> dict:
    """Persist the user claims and API access token in Streamlit session state."""
    claims = result.get("id_token_claims") or st.session_state.get("user_claims", {})
    if claims:
        st.session_state["user_claims"] = claims
    st.session_state["api_access_token"] = result["access_token"]
    st.session_state["api_access_token_expires_at"] = int(
        result.get("expires_on", int(time.time()) + int(result.get("expires_in", 3600)))
    )
    return claims


def _get_token_cache() -> "msal.TokenCache":
    """Return a per-session MSAL token cache stored in Streamlit session state."""
    import msal
    if "msal_cache" not in st.session_state:
        st.session_state["msal_cache"] = msal.TokenCache()
    return st.session_state["msal_cache"]
