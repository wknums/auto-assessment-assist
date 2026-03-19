# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE.md file in the project root for full license information.
"""
Azure Entra ID authentication for the Streamlit UX.

Behaviour
---------
- **Local development** (no ``WEBSITE_SITE_NAME`` env var): auth is
  skipped entirely – the app runs without login.
- **Azure Container Apps** (``WEBSITE_SITE_NAME`` is set by the platform):
  users must sign in with a Microsoft Entra ID account that belongs to
  the configured tenant.  The flow uses the OAuth 2.0 Authorization Code
  grant via MSAL.

Required env vars (only when deployed):
    AAD_CLIENT_ID       – App Registration (client) ID
    AAD_CLIENT_SECRET   – App Registration client secret
    AAD_TENANT_ID       – Entra ID tenant ID (restricts sign-in to this tenant)
"""
from __future__ import annotations

import os
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


def require_auth() -> Optional[dict]:
    """Gate the Streamlit app behind Entra ID sign-in when on Azure.

    Returns
    -------
    dict | None
        The user's ID-token claims when authenticated (or mock claims
        when running locally).  Returns ``None`` only while the login
        page is being displayed (caller should ``st.stop()``).
    """
    if not _RUNNING_ON_AZURE:
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
    scopes = ["User.Read"]  # minimal scope – just need the ID token

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
        result = cca.acquire_token_by_authorization_code(
            auth_code,
            scopes=scopes,
            redirect_uri=_get_redirect_uri(),
        )
        if "id_token_claims" in result:
            claims = result["id_token_claims"]
            st.session_state["user_claims"] = claims
            # Clear the code from the URL
            st.query_params.clear()
            st.rerun()
            return None

        # Token acquisition failed
        st.error(f"Authentication failed: {result.get('error_description', 'Unknown error')}")
        st.stop()
        return None

    # ── No session, no callback → show login button ──────────────────
    auth_url = cca.get_authorization_request_url(
        scopes=scopes,
        redirect_uri=_get_redirect_uri(),
    )

    st.markdown("## Sign in required")
    st.markdown(
        "This application requires authentication with your Microsoft work account."
    )
    st.link_button("Sign in with Microsoft", auth_url, type="primary")
    st.stop()
    return None


def get_user_display(claims: dict) -> str:
    """Return a friendly display string for the logged-in user."""
    name = claims.get("name", "")
    email = claims.get("preferred_username", "")
    if name and email:
        return f"{name} ({email})"
    return name or email or "Authenticated User"


def logout():
    """Clear the session to log the user out."""
    for key in ("user_claims",):
        st.session_state.pop(key, None)
    st.rerun()


# ── Internal helpers ──────────────────────────────────────────────────

def _get_redirect_uri() -> str:
    """Build the OAuth redirect URI from the current request URL."""
    # In ACA the app is behind HTTPS ingress
    # Use the STREAMLIT_REDIRECT_URI env var if set, otherwise derive
    redirect = os.environ.get("STREAMLIT_REDIRECT_URI")
    if redirect:
        return redirect
    # Fallback: assume the root URL of the app
    host = os.environ.get("CONTAINER_APP_HOSTNAME", "localhost:8501")
    scheme = "https" if _RUNNING_ON_AZURE else "http"
    return f"{scheme}://{host}/"


def _get_token_cache() -> "msal.TokenCache":
    """Return a per-session MSAL token cache stored in Streamlit session state."""
    import msal
    if "msal_cache" not in st.session_state:
        st.session_state["msal_cache"] = msal.TokenCache()
    return st.session_state["msal_cache"]
