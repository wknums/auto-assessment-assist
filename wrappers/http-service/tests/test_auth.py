from __future__ import annotations

import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.testclient import TestClient

import app.deps as auth_dependencies
from app.config import settings
from app.deps import (
    _authorize_entra_claims,
    _jwks_uri_for_issuer,
    _verify_entra_jwt,
)
from app.main import app


@pytest.fixture
def entra_settings(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "auth_mode", "entra")
    monkeypatch.setattr(settings, "aad_issuer", "https://login.microsoftonline.com/tenant/v2.0")
    monkeypatch.setattr(settings, "aad_audience", "api-client-id")
    monkeypatch.setattr(settings, "aad_required_scope", "access_as_user")
    monkeypatch.setattr(settings, "aad_required_app_role", "TalentMatch.Access")


@pytest.fixture(scope="module")
def signing_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _signed_token(signing_key, **claims) -> str:
    payload = {
        "aud": "api-client-id",
        "iss": "https://login.microsoftonline.com/tenant/v2.0",
        "sub": "caller-id",
        "exp": int(time.time()) + 300,
        **claims,
    }
    return jwt.encode(payload, signing_key, algorithm="RS256", headers={"kid": "test-key"})


def _mock_jwks(monkeypatch: pytest.MonkeyPatch, signing_key) -> None:
    class FakeJwksClient:
        def get_signing_key_from_jwt(self, token):
            return type("SigningKey", (), {"key": signing_key.public_key()})()

    monkeypatch.setattr(auth_dependencies, "_get_jwks_client", lambda: FakeJwksClient())


def test_delegated_scope_is_authorized(entra_settings):
    _authorize_entra_claims({"scp": "openid access_as_user profile"})


def test_talent_match_application_role_is_authorized(entra_settings):
    _authorize_entra_claims({"roles": ["TalentMatch.Access"]})


def test_signed_delegated_token_is_verified(
    entra_settings, monkeypatch: pytest.MonkeyPatch, signing_key
):
    _mock_jwks(monkeypatch, signing_key)
    token = _signed_token(signing_key, scp="access_as_user")

    claims = _verify_entra_jwt(
        None,
        HTTPAuthorizationCredentials(scheme="Bearer", credentials=token),
    )

    assert claims["sub"] == "caller-id"


def test_signed_talent_match_token_is_verified(
    entra_settings, monkeypatch: pytest.MonkeyPatch, signing_key
):
    _mock_jwks(monkeypatch, signing_key)
    token = _signed_token(signing_key, roles=["TalentMatch.Access"])

    claims = _verify_entra_jwt(
        None,
        HTTPAuthorizationCredentials(scheme="Bearer", credentials=token),
    )

    assert claims["roles"] == ["TalentMatch.Access"]


def test_signed_token_with_wrong_audience_is_rejected(
    entra_settings, monkeypatch: pytest.MonkeyPatch, signing_key
):
    _mock_jwks(monkeypatch, signing_key)
    token = _signed_token(signing_key, aud="different-api", scp="access_as_user")

    with pytest.raises(HTTPException) as exc_info:
        _verify_entra_jwt(
            None,
            HTTPAuthorizationCredentials(scheme="Bearer", credentials=token),
        )

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Invalid audience."


@pytest.mark.parametrize("claims", [{}, {"scp": "User.Read"}, {"roles": ["Other.Role"]}])
def test_token_without_required_permission_is_forbidden(entra_settings, claims):
    with pytest.raises(HTTPException) as exc_info:
        _authorize_entra_claims(claims)

    assert exc_info.value.status_code == 403


def test_assessment_route_requires_bearer_token(entra_settings):
    with TestClient(app) as client:
        response = client.get("/assess/status/request-1")

    assert response.status_code == 401
    assert response.json()["detail"] == "Missing bearer token."


def test_health_route_remains_anonymous(entra_settings):
    with TestClient(app) as client:
        response = client.get("/healthz")

    assert response.status_code == 200


@pytest.mark.parametrize(
    ("issuer", "expected"),
    [
        (
            "https://login.microsoftonline.com/tenant/v2.0",
            "https://login.microsoftonline.com/tenant/discovery/v2.0/keys",
        ),
        (
            "https://login.microsoftonline.com/tenant/v2.0/",
            "https://login.microsoftonline.com/tenant/discovery/v2.0/keys",
        ),
        (
            "https://login.microsoftonline.com/tenant",
            "https://login.microsoftonline.com/tenant/discovery/v2.0/keys",
        ),
        (
            "https://sts.windows.net/tenant/",
            "https://sts.windows.net/tenant/discovery/v2.0/keys",
        ),
    ],
)
def test_jwks_uri_is_derived_from_the_tenant_authority(issuer: str, expected: str):
    """The ``/v2.0`` issuer suffix must not leak into the JWKS path.

    ``<authority>/v2.0/discovery/v2.0/keys`` returns HTTP 404, which silently
    breaks *every* token validation with a generic "Invalid token." response.
    """
    assert _jwks_uri_for_issuer(issuer) == expected
