import asyncio
import base64
import hashlib
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from pydantic import ValidationError

from docta_api.dev_token import (
    DevTokenError,
    DevTokenSettings,
    OIDCDiscovery,
    fetch_discovery,
    obtain_dev_token,
    validate_access_token,
    validate_callback_query,
)
from docta_api.identity import AuthenticatedIdentity, AuthenticationError

ISSUER = "https://identity.example"
PROJECT_ID = "389218636289540099"
CLIENT_ID = "client-id@example"
REDIRECT_URI = "http://127.0.0.1:8765/callback"
JWT_SHAPED_TOKEN = "eyJhbGciOiJSUzI1NiJ9.e30.c2ln"


def _discovery_document(*, issuer: str = ISSUER) -> dict[str, object]:
    return {
        "issuer": issuer,
        "authorization_endpoint": f"{ISSUER}/oauth/v2/authorize",
        "token_endpoint": f"{ISSUER}/oauth/v2/token",
        "jwks_uri": f"{ISSUER}/oauth/v2/keys",
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
    }


def _settings() -> DevTokenSettings:
    return DevTokenSettings(
        _env_file=None,
        issuer=ISSUER,
        project_id=PROJECT_ID,
        client_id=CLIENT_ID,
        redirect_uri=REDIRECT_URI,
        login_hint="fernando.dev",
    )


def test_settings_require_the_oidc_client_id() -> None:
    with pytest.raises(ValidationError, match="client_id"):
        DevTokenSettings(_env_file=None)  # type: ignore[call-arg]


def test_fetch_discovery_rejects_an_unexpected_issuer() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json=_discovery_document(issuer="https://other.example"),
            request=request,
        )
    )

    with httpx.Client(transport=transport) as client:
        with pytest.raises(DevTokenError, match="does not match"):
            fetch_discovery(client, issuer=ISSUER)


@pytest.mark.parametrize(
    "query",
    [
        "code=authorization-code",
        "code=authorization-code&state=wrong-state",
        "code=authorization-code&state=expected&state=duplicate",
    ],
)
def test_callback_rejects_missing_mismatched_or_duplicate_state(query: str) -> None:
    with pytest.raises(DevTokenError, match="state"):
        validate_callback_query(query, expected_state="expected")


def test_callback_accepts_one_code_after_state_validation() -> None:
    assert (
        validate_callback_query("code=authorization-code&state=expected", expected_state="expected")
        == "authorization-code"
    )


def test_obtain_dev_token_uses_discovery_pkce_and_project_audience() -> None:
    authorization: dict[str, str] = {}
    token_request: dict[str, str] = {}
    validation: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            assert str(request.url) == f"{ISSUER}/.well-known/openid-configuration"
            return httpx.Response(200, json=_discovery_document(), request=request)
        token_request.update(
            {key: values[0] for key, values in parse_qs(request.content.decode()).items()}
        )
        return httpx.Response(
            200,
            json={"access_token": "signed-access-token", "token_type": "Bearer"},
            request=request,
        )

    def callback_receiver(
        redirect_uri: str,
        authorization_url: str,
        expected_state: str,
        timeout_seconds: int,
    ) -> str:
        authorization.update(
            {key: values[0] for key, values in parse_qs(urlsplit(authorization_url).query).items()}
        )
        assert redirect_uri == REDIRECT_URI
        assert authorization["state"] == expected_state
        assert timeout_seconds == 180
        return "authorization-code"

    def token_validator(
        token: str,
        discovery: OIDCDiscovery,
        project_id: str,
        timeout_seconds: float,
    ) -> None:
        validation.update(
            token=token,
            issuer=discovery.issuer,
            audience=project_id,
            timeout=timeout_seconds,
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        token = obtain_dev_token(
            _settings(),
            http_client=client,
            callback_receiver=callback_receiver,
            token_validator=token_validator,
        )

    assert token == "signed-access-token"
    assert authorization["response_type"] == "code"
    assert authorization["client_id"] == CLIENT_ID
    assert authorization["redirect_uri"] == REDIRECT_URI
    assert authorization["code_challenge_method"] == "S256"
    assert f"urn:zitadel:iam:org:project:id:{PROJECT_ID}:aud" in authorization["scope"].split()
    assert token_request["grant_type"] == "authorization_code"
    assert token_request["client_id"] == CLIENT_ID
    assert token_request["redirect_uri"] == REDIRECT_URI
    assert token_request["code"] == "authorization-code"
    expected_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(token_request["code_verifier"].encode("ascii")).digest()
    ).rstrip(b"=").decode("ascii")
    assert authorization["code_challenge"] == expected_challenge
    assert validation == {
        "token": "signed-access-token",
        "issuer": ISSUER,
        "audience": PROJECT_ID,
        "timeout": 5.0,
    }


def test_access_token_validation_reuses_api_identity_provider() -> None:
    created: dict[str, object] = {}

    class RecordingIdentityProvider:
        async def authenticate(self, authorization: str | None) -> AuthenticatedIdentity:
            created["authorization"] = authorization
            return AuthenticatedIdentity(issuer=ISSUER, subject="teacher-1")

    def provider_factory(**kwargs: object) -> RecordingIdentityProvider:
        created.update(kwargs)
        return RecordingIdentityProvider()

    validate_access_token(
        JWT_SHAPED_TOKEN,
        OIDCDiscovery(
            issuer=ISSUER,
            authorization_endpoint=f"{ISSUER}/authorize",
            token_endpoint=f"{ISSUER}/token",
            jwks_uri=f"{ISSUER}/keys",
        ),
        PROJECT_ID,
        3.0,
        identity_provider_factory=provider_factory,
    )

    assert created == {
        "issuer": ISSUER,
        "audience": PROJECT_ID,
        "jwks_url": f"{ISSUER}/keys",
        "timeout_seconds": 3.0,
        "authorization": f"Bearer {JWT_SHAPED_TOKEN}",
    }


def test_access_token_validation_explains_opaque_zitadel_tokens() -> None:
    with pytest.raises(DevTokenError, match=r"opaque access token.*Token Type to JWT"):
        validate_access_token(
            "opaque-token-value",
            OIDCDiscovery(
                issuer=ISSUER,
                authorization_endpoint=f"{ISSUER}/authorize",
                token_endpoint=f"{ISSUER}/token",
                jwks_uri=f"{ISSUER}/keys",
            ),
            PROJECT_ID,
            3.0,
        )


def test_access_token_validation_fails_closed() -> None:
    class RejectingIdentityProvider:
        async def authenticate(self, authorization: str | None) -> AuthenticatedIdentity:
            await asyncio.sleep(0)
            raise AuthenticationError

    with pytest.raises(DevTokenError, match="issuer, audience, expiry"):
        validate_access_token(
            JWT_SHAPED_TOKEN,
            OIDCDiscovery(
                issuer=ISSUER,
                authorization_endpoint=f"{ISSUER}/authorize",
                token_endpoint=f"{ISSUER}/token",
                jwks_uri=f"{ISSUER}/keys",
            ),
            PROJECT_ID,
            3.0,
            identity_provider_factory=lambda **kwargs: RejectingIdentityProvider(),
        )
