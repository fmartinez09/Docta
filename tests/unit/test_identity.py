from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from docta_api.identity import (
    AuthenticatedIdentity,
    AuthenticationError,
    DeterministicIdentityProvider,
    OIDCJWTIdentityProvider,
)

pytestmark = pytest.mark.anyio


async def test_deterministic_identity_provider_accepts_only_mapped_bearer_tokens() -> None:
    expected = AuthenticatedIdentity(issuer="https://issuer.example/", subject="teacher-1")
    provider = DeterministicIdentityProvider({"known-token": expected})

    assert await provider.authenticate("Bearer known-token") == expected

    with pytest.raises(AuthenticationError):
        await provider.authenticate("Bearer unknown-token")


@pytest.mark.parametrize("authorization", [None, "", "Basic value", "Bearer", "Bearer  "])
async def test_deterministic_identity_provider_rejects_missing_or_malformed_bearer_tokens(
    authorization: str | None,
) -> None:
    provider = DeterministicIdentityProvider({})

    with pytest.raises(AuthenticationError):
        await provider.authenticate(authorization)


async def test_oidc_provider_validates_signature_and_standard_claims() -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    provider = OIDCJWTIdentityProvider(
        issuer="https://identity.example/",
        audience="https://api.docta.example/",
        jwks_url="https://identity.example/.well-known/jwks.json",
        timeout_seconds=1,
    )
    provider._jwks_client = _StaticJWKClient(private_key.public_key())
    now = datetime.now(UTC)
    token = jwt.encode(
        {
            "iss": "https://identity.example/",
            "sub": "teacher-1",
            "aud": ["application-client", "https://api.docta.example/"],
            "iat": now,
            "exp": now + timedelta(minutes=5),
        },
        private_key,
        algorithm="RS256",
        headers={"kid": "test-key"},
    )

    identity = await provider.authenticate(f"Bearer {token}")

    assert identity == AuthenticatedIdentity(
        issuer="https://identity.example/",
        subject="teacher-1",
    )


async def test_oidc_provider_rejects_wrong_audience() -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    provider = OIDCJWTIdentityProvider(
        issuer="https://identity.example/",
        audience="https://api.docta.example/",
        jwks_url="https://identity.example/.well-known/jwks.json",
        timeout_seconds=1,
    )
    provider._jwks_client = _StaticJWKClient(private_key.public_key())
    now = datetime.now(UTC)
    token = jwt.encode(
        {
            "iss": "https://identity.example/",
            "sub": "teacher-1",
            "aud": "https://different-api.example/",
            "iat": now,
            "exp": now + timedelta(minutes=5),
        },
        private_key,
        algorithm="RS256",
        headers={"kid": "test-key"},
    )

    with pytest.raises(AuthenticationError):
        await provider.authenticate(f"Bearer {token}")


@pytest.mark.parametrize(
    "claim_overrides",
    [
        {"iss": "https://other-identity.example/"},
        {"exp": datetime.now(UTC) - timedelta(minutes=1)},
    ],
    ids=["wrong-issuer", "expired"],
)
async def test_oidc_provider_rejects_wrong_issuer_or_expired_token(
    claim_overrides: dict[str, object],
) -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    provider = OIDCJWTIdentityProvider(
        issuer="https://identity.example/",
        audience="389218636289540099",
        jwks_url="https://identity.example/.well-known/jwks.json",
        timeout_seconds=1,
    )
    provider._jwks_client = _StaticJWKClient(private_key.public_key())
    now = datetime.now(UTC)
    claims: dict[str, object] = {
        "iss": "https://identity.example/",
        "sub": "teacher-1",
        "aud": ["application-client", "389218636289540099"],
        "iat": now,
        "exp": now + timedelta(minutes=5),
    }
    claims.update(claim_overrides)
    token = jwt.encode(
        claims,
        private_key,
        algorithm="RS256",
        headers={"kid": "test-key"},
    )

    with pytest.raises(AuthenticationError):
        await provider.authenticate(f"Bearer {token}")


class _StaticJWKClient:
    def __init__(self, public_key: object) -> None:
        self._public_key = public_key

    def get_signing_key_from_jwt(self, token: str) -> SimpleNamespace:
        return SimpleNamespace(key=self._public_key)
