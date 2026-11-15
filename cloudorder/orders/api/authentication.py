from dataclasses import dataclass
from functools import lru_cache

import jwt
from django.conf import settings
from jwt import PyJWKClient
from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework.exceptions import AuthenticationFailed


@dataclass(frozen=True)
class Principal:
    customer_id: str
    groups: tuple[str, ...]

    @property
    def is_authenticated(self) -> bool:
        return True

    @property
    def is_anonymous(self) -> bool:
        return False

    @property
    def is_admin(self) -> bool:
        return "Admin" in self.groups


@lru_cache(maxsize=8)
def _jwks_client(issuer: str) -> PyJWKClient:
    return PyJWKClient(f"{issuer.rstrip('/')}/.well-known/jwks.json")


def verify_cognito_token(token: str) -> dict:
    issuer = settings.COGNITO_ISSUER
    client_id = settings.COGNITO_APP_CLIENT_ID
    if not issuer or not client_id:
        raise AuthenticationFailed("Cognito authentication is not configured.")
    try:
        signing_key = _jwks_client(issuer).get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            issuer=issuer,
            options={"verify_aud": False},
            leeway=30,
        )
    except (jwt.PyJWTError, ValueError) as error:
        raise AuthenticationFailed("Bearer token is invalid or expired.") from error
    if claims.get("token_use") != "access":
        raise AuthenticationFailed("A Cognito access token is required.")
    if claims.get("client_id") != client_id:
        raise AuthenticationFailed("Token was not issued to this application.")
    if not claims.get("sub"):
        raise AuthenticationFailed("Token is missing its customer identity.")
    return claims


def principal_from_token(token: str) -> Principal:
    claims = verify_cognito_token(token)
    groups = tuple(claims.get("cognito:groups", []))
    if not {"Customer", "Admin"}.intersection(groups):
        raise AuthenticationFailed("Cognito user must belong to the Customer or Admin group.")
    return Principal(
        customer_id=claims["sub"],
        groups=groups,
    )


class CognitoJWTAuthentication(BaseAuthentication):
    def authenticate(self, request):
        parts = get_authorization_header(request).split()
        if not parts:
            return None
        if len(parts) != 2 or parts[0].lower() != b"bearer":
            raise AuthenticationFailed("Authorization header must use Bearer token format.")
        try:
            token = parts[1].decode("ascii")
        except UnicodeDecodeError as error:
            raise AuthenticationFailed("Bearer token is malformed.") from error
        return principal_from_token(token), token