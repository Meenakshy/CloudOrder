import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from django.test import override_settings
from rest_framework.exceptions import AuthenticationFailed

from cloudorder.orders.api import authentication


@pytest.fixture
def token_verifier(monkeypatch):
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key()

    class FakeJwksClient:
        def get_signing_key_from_jwt(self, token):
            return type("SigningKey", (), {"key": public_key})()

    monkeypatch.setattr(authentication, "_jwks_client", lambda issuer: FakeJwksClient())
    now = int(time.time())

    def create_token(**overrides):
        claims = {
            "iss": "https://cognito-idp.us-east-1.amazonaws.com/test-pool",
            "sub": "customer-1",
            "token_use": "access",
            "client_id": "cloudorder-client",
            "iat": now,
            "exp": now + 60,
        }
        claims.update(overrides)
        return jwt.encode(
            claims,
            private_key,
            algorithm="RS256",
            headers={"kid": "test-key"},
        )

    return create_token


@override_settings(
    COGNITO_ISSUER="https://cognito-idp.us-east-1.amazonaws.com/test-pool",
    COGNITO_APP_CLIENT_ID="cloudorder-client",
)
def test_cognito_token_signature_issuer_expiry_and_token_use(token_verifier):
    claims = authentication.verify_cognito_token(token_verifier())
    assert claims["sub"] == "customer-1"

    with pytest.raises(AuthenticationFailed, match="invalid or expired"):
        authentication.verify_cognito_token(token_verifier(exp=int(time.time()) - 60))
    with pytest.raises(AuthenticationFailed, match="access token is required"):
        authentication.verify_cognito_token(token_verifier(token_use="id"))
    with pytest.raises(AuthenticationFailed, match="invalid or expired"):
        authentication.verify_cognito_token(
            token_verifier(iss="https://cognito-idp.us-east-1.amazonaws.com/other-pool")
        )


def test_cognito_token_rejects_wrong_application_client(token_verifier):
    with override_settings(
        COGNITO_ISSUER="https://cognito-idp.us-east-1.amazonaws.com/test-pool",
        COGNITO_APP_CLIENT_ID="cloudorder-client",
    ):
        with pytest.raises(AuthenticationFailed, match="not issued to this application"):
            authentication.verify_cognito_token(token_verifier(client_id="another-client"))


def test_principal_requires_customer_or_admin_group(monkeypatch):
    monkeypatch.setattr(
        authentication,
        "verify_cognito_token",
        lambda token: {"sub": "unassigned-user", "cognito:groups": []},
    )

    with pytest.raises(AuthenticationFailed, match="Customer or Admin group"):
        authentication.principal_from_token("signed-token")