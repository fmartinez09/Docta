from dataclasses import dataclass
from typing import Protocol

import jwt
from anyio import to_thread
from jwt import PyJWKClient
from jwt.exceptions import PyJWKClientConnectionError, PyJWKClientError


class AuthenticationError(Exception):
    """The request did not contain a valid identity."""


class IdentityProviderUnavailable(Exception):
    """The configured identity provider cannot currently authenticate requests."""


@dataclass(frozen=True)
class AuthenticatedIdentity:
    issuer: str
    subject: str


class IdentityProvider(Protocol):
    async def authenticate(self, authorization: str | None) -> AuthenticatedIdentity: ...

class OIDCJWTIdentityProvider:
    """Validate standard OIDC access-token claims against a remote JWKS."""

    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        jwks_url: str,
        timeout_seconds: float,
    ) -> None:
        self._issuer = issuer
        self._audience = audience
        self._jwks_client = PyJWKClient(
            jwks_url,
            cache_keys=True,
            timeout=timeout_seconds,
        )

    async def authenticate(self, authorization: str | None) -> AuthenticatedIdentity:
        token = _bearer_token(authorization)
        try:
            claims = await to_thread.run_sync(self._decode, token)
        except PyJWKClientConnectionError as error:
            raise IdentityProviderUnavailable from error
        except (jwt.InvalidTokenError, PyJWKClientError) as error:
            raise AuthenticationError from error
        except Exception as error:
            raise IdentityProviderUnavailable from error

        subject = claims.get("sub")
        issuer = claims.get("iss")
        if not isinstance(subject, str) or not subject.strip():
            raise AuthenticationError
        if not isinstance(issuer, str) or not issuer.strip():
            raise AuthenticationError
        return AuthenticatedIdentity(issuer=issuer, subject=subject)

    def _decode(self, token: str) -> dict[str, object]:
        signing_key = self._jwks_client.get_signing_key_from_jwt(token)
        return jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=self._audience,
            issuer=self._issuer,
            options={"require": ["exp", "iat", "iss", "sub", "aud"]},
        )


class UnconfiguredIdentityProvider:
    """Fail closed when local OIDC settings have not been supplied."""

    async def authenticate(self, authorization: str | None) -> AuthenticatedIdentity:
        raise IdentityProviderUnavailable


class DeterministicIdentityProvider:
    """Explicit test adapter; never selected by runtime configuration."""

    def __init__(self, identities_by_token: dict[str, AuthenticatedIdentity]) -> None:
        self._identities_by_token = identities_by_token

    async def authenticate(self, authorization: str | None) -> AuthenticatedIdentity:
        token = _bearer_token(authorization)
        identity = self._identities_by_token.get(token)
        if identity is None:
            raise AuthenticationError
        return identity


def _bearer_token(authorization: str | None) -> str:
    if authorization is None:
        raise AuthenticationError
    scheme, separator, token = authorization.partition(" ")
    if separator != " " or scheme.lower() != "bearer" or not token.strip():
        raise AuthenticationError
    return token.strip()
