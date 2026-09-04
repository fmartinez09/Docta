"""Obtain a short-lived local development token with Authorization Code + PKCE."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import secrets
import sys
import time
import webbrowser
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, cast
from urllib.parse import parse_qs, parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
import jwt
from pydantic import AnyHttpUrl, Field, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from docta_api.identity import (
    AuthenticationError,
    IdentityProvider,
    IdentityProviderUnavailable,
    OIDCJWTIdentityProvider,
)

DEFAULT_ISSUER = "http://localhost:8080"
DEFAULT_PROJECT_ID = "389218636289540099"
DEFAULT_REDIRECT_URI = "http://127.0.0.1:8765/callback"
DEFAULT_LOGIN_HINT = "fernando.dev"


class DevTokenError(Exception):
    """A safe, user-facing development token error."""


class DevTokenSettings(BaseSettings):
    """Configuration read from `.env` and `DOCTA_DEV_OIDC_*` environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="DOCTA_DEV_OIDC_",
        extra="ignore",
        frozen=True,
    )

    issuer: AnyHttpUrl = DEFAULT_ISSUER
    project_id: str = Field(default=DEFAULT_PROJECT_ID, pattern=r"^[1-9][0-9]*$")
    client_id: str = Field(min_length=1)
    redirect_uri: AnyHttpUrl = DEFAULT_REDIRECT_URI
    login_hint: str | None = DEFAULT_LOGIN_HINT
    request_timeout_seconds: float = Field(default=5.0, gt=0, le=30)
    callback_timeout_seconds: int = Field(default=180, gt=0, le=600)

    @field_validator("client_id", "project_id")
    @classmethod
    def strip_required_values(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    @field_validator("login_hint")
    @classmethod
    def strip_optional_value(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None

    @model_validator(mode="after")
    def validate_urls(self) -> DevTokenSettings:
        issuer = urlsplit(str(self.issuer))
        if issuer.query or issuer.fragment or issuer.username or issuer.password:
            raise ValueError("OIDC issuer must not contain credentials, query, or fragment")
        if issuer.scheme == "http" and not _is_loopback_host(issuer.hostname):
            raise ValueError("plain HTTP is allowed only for a loopback OIDC issuer")

        redirect = urlsplit(str(self.redirect_uri))
        if redirect.scheme != "http" or not _is_loopback_host(redirect.hostname):
            raise ValueError("redirect URI must use HTTP on localhost or a loopback address")
        if redirect.query or redirect.fragment or redirect.username or redirect.password:
            raise ValueError("redirect URI must not contain credentials, query, or fragment")
        return self

    @property
    def normalized_issuer(self) -> str:
        return str(self.issuer).rstrip("/")


@dataclass(frozen=True)
class OIDCDiscovery:
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str

    @classmethod
    def from_document(
        cls,
        document: Mapping[str, object],
        *,
        expected_issuer: str,
    ) -> OIDCDiscovery:
        issuer = _required_url(document, "issuer")
        if issuer != expected_issuer:
            raise DevTokenError(
                "OIDC Discovery returned an issuer that does not match "
                f"the configured issuer ({expected_issuer})."
            )

        challenge_methods = document.get("code_challenge_methods_supported")
        if not _string_list_contains(challenge_methods, "S256"):
            raise DevTokenError("The OIDC provider does not advertise PKCE with S256.")

        auth_methods = document.get("token_endpoint_auth_methods_supported")
        if not _string_list_contains(auth_methods, "none"):
            raise DevTokenError(
                "The OIDC provider does not advertise public-client token exchange."
            )

        return cls(
            issuer=issuer,
            authorization_endpoint=_required_url(document, "authorization_endpoint"),
            token_endpoint=_required_url(document, "token_endpoint"),
            jwks_uri=_required_url(document, "jwks_uri"),
        )


CallbackReceiver = Callable[[str, str, str, int], str]
TokenValidator = Callable[[str, OIDCDiscovery, str, float], None]
IdentityProviderFactory = Callable[..., IdentityProvider]


def fetch_discovery(
    client: httpx.Client,
    *,
    issuer: str,
) -> OIDCDiscovery:
    discovery_url = f"{issuer}/.well-known/openid-configuration"
    try:
        response = client.get(discovery_url, headers={"Accept": "application/json"})
        response.raise_for_status()
        document = response.json()
    except (httpx.HTTPError, ValueError) as error:
        raise DevTokenError("OIDC Discovery could not be loaded or parsed.") from error
    if not isinstance(document, Mapping):
        raise DevTokenError("OIDC Discovery did not return a JSON object.")
    return OIDCDiscovery.from_document(document, expected_issuer=issuer)


def create_pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def build_authorization_url(
    discovery: OIDCDiscovery,
    settings: DevTokenSettings,
    *,
    state: str,
    code_challenge: str,
) -> str:
    endpoint = urlsplit(discovery.authorization_endpoint)
    query = list(parse_qsl(endpoint.query, keep_blank_values=True))
    query.extend(
        [
            ("response_type", "code"),
            ("client_id", settings.client_id),
            ("redirect_uri", str(settings.redirect_uri)),
            (
                "scope",
                " ".join(
                    (
                        "openid",
                        "profile",
                        f"urn:zitadel:iam:org:project:id:{settings.project_id}:aud",
                    )
                ),
            ),
            ("state", state),
            ("code_challenge", code_challenge),
            ("code_challenge_method", "S256"),
        ]
    )
    if settings.login_hint:
        query.append(("login_hint", settings.login_hint))
    return urlunsplit(endpoint._replace(query=urlencode(query)))


def validate_callback_query(query: str, *, expected_state: str) -> str:
    parameters = parse_qs(query, keep_blank_values=True)
    state_values = parameters.get("state", [])
    if len(state_values) != 1 or not hmac.compare_digest(state_values[0], expected_state):
        raise DevTokenError("The authorization callback contained an invalid state value.")

    error_values = parameters.get("error", [])
    if error_values:
        error_code = error_values[0] if len(error_values) == 1 else "invalid_response"
        raise DevTokenError(f"Authorization was not completed (error={error_code}).")

    code_values = parameters.get("code", [])
    if len(code_values) != 1 or not code_values[0]:
        raise DevTokenError("The authorization callback did not contain exactly one code.")
    return code_values[0]


def receive_authorization_code(
    redirect_uri: str,
    authorization_url: str,
    expected_state: str,
    timeout_seconds: int,
    *,
    browser_open: Callable[[str], bool] = webbrowser.open,
) -> str:
    redirect = urlsplit(redirect_uri)
    if not _is_loopback_host(redirect.hostname) or redirect.scheme != "http":
        raise DevTokenError("The callback listener only accepts loopback HTTP redirect URIs.")

    host = cast(str, redirect.hostname)
    port = redirect.port or 80
    expected_path = redirect.path or "/"
    try:
        server = _CallbackHTTPServer((host, port), expected_path, expected_state)
    except OSError as error:
        raise DevTokenError(f"Could not listen for the OIDC callback on {host}:{port}.") from error

    with server:
        print("Opening the ZITADEL login in your browser...", file=sys.stderr)
        try:
            opened = browser_open(authorization_url)
        except webbrowser.Error:
            opened = False
        if not opened:
            print(
                "No browser was opened automatically. Open this URL manually:\n"
                f"{authorization_url}",
                file=sys.stderr,
            )

        deadline = time.monotonic() + timeout_seconds
        while server.authorization_code is None and server.callback_error is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise DevTokenError("Timed out waiting for the local authorization callback.")
            server.timeout = min(1.0, remaining)
            server.handle_request()

        if server.callback_error is not None:
            raise server.callback_error
        if server.authorization_code is None:
            raise DevTokenError("The local authorization callback did not return a code.")
        return server.authorization_code


def exchange_authorization_code(
    client: httpx.Client,
    discovery: OIDCDiscovery,
    settings: DevTokenSettings,
    *,
    code: str,
    code_verifier: str,
) -> str:
    try:
        response = client.post(
            discovery.token_endpoint,
            data={
                "grant_type": "authorization_code",
                "client_id": settings.client_id,
                "redirect_uri": str(settings.redirect_uri),
                "code": code,
                "code_verifier": code_verifier,
            },
            headers={"Accept": "application/json"},
        )
        response.raise_for_status()
        document = response.json()
    except httpx.HTTPStatusError as error:
        raise DevTokenError(
            f"The token endpoint rejected the code (HTTP {error.response.status_code})."
        ) from error
    except (httpx.HTTPError, ValueError) as error:
        raise DevTokenError("The authorization code could not be exchanged for a token.") from error

    if not isinstance(document, Mapping):
        raise DevTokenError("The token endpoint did not return a JSON object.")
    token = document.get("access_token")
    token_type = document.get("token_type")
    if not isinstance(token, str) or not token.strip():
        raise DevTokenError("The token endpoint response did not contain an access token.")
    if not isinstance(token_type, str) or token_type.lower() != "bearer":
        raise DevTokenError("The token endpoint response did not contain a Bearer token.")
    return token.strip()


def validate_access_token(
    token: str,
    discovery: OIDCDiscovery,
    project_id: str,
    timeout_seconds: float,
    *,
    identity_provider_factory: IdentityProviderFactory = OIDCJWTIdentityProvider,
) -> None:
    if token.count(".") != 2:
        raise DevTokenError(
            "ZITADEL returned an opaque access token. In the application's Token Settings, "
            "set Token Type to JWT and request a new token."
        )
    try:
        header = jwt.get_unverified_header(token)
    except jwt.InvalidTokenError as error:
        raise DevTokenError("ZITADEL returned a malformed JWT access token.") from error
    if header.get("alg") != "RS256":
        raise DevTokenError(
            "ZITADEL returned a JWT access token whose signing algorithm is not RS256."
        )

    provider = identity_provider_factory(
        issuer=discovery.issuer,
        audience=project_id,
        jwks_url=discovery.jwks_uri,
        timeout_seconds=timeout_seconds,
    )
    try:
        asyncio.run(provider.authenticate(f"Bearer {token}"))
    except AuthenticationError as error:
        raise DevTokenError(
            "The access token failed signature, issuer, audience, expiry, or subject validation."
        ) from error
    except IdentityProviderUnavailable as error:
        raise DevTokenError("The access token signing keys could not be loaded.") from error


def obtain_dev_token(
    settings: DevTokenSettings,
    *,
    http_client: httpx.Client | None = None,
    callback_receiver: CallbackReceiver = receive_authorization_code,
    token_validator: TokenValidator = validate_access_token,
) -> str:
    if http_client is None:
        with httpx.Client(
            timeout=settings.request_timeout_seconds,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            return _obtain_dev_token(settings, client, callback_receiver, token_validator)
    return _obtain_dev_token(settings, http_client, callback_receiver, token_validator)


def _obtain_dev_token(
    settings: DevTokenSettings,
    client: httpx.Client,
    callback_receiver: CallbackReceiver,
    token_validator: TokenValidator,
) -> str:
    discovery = fetch_discovery(client, issuer=settings.normalized_issuer)
    code_verifier, code_challenge = create_pkce_pair()
    state = secrets.token_urlsafe(32)
    authorization_url = build_authorization_url(
        discovery,
        settings,
        state=state,
        code_challenge=code_challenge,
    )
    code = callback_receiver(
        str(settings.redirect_uri),
        authorization_url,
        state,
        settings.callback_timeout_seconds,
    )
    token = exchange_authorization_code(
        client,
        discovery,
        settings,
        code=code,
        code_verifier=code_verifier,
    )
    token_validator(token, discovery, settings.project_id, settings.request_timeout_seconds)
    return token


class _CallbackHTTPServer(HTTPServer):
    allow_reuse_address = True

    def __init__(
        self,
        server_address: tuple[str, int],
        expected_path: str,
        expected_state: str,
    ) -> None:
        self.expected_path = expected_path
        self.expected_state = expected_state
        self.authorization_code: str | None = None
        self.callback_error: DevTokenError | None = None
        super().__init__(server_address, _CallbackHandler)


class _CallbackHandler(BaseHTTPRequestHandler):
    server: _CallbackHTTPServer

    def do_GET(self) -> None:
        target = urlsplit(self.path)
        if target.path != self.server.expected_path:
            self.send_error(404)
            return
        if len(target.query) > 8192:
            self.send_error(414)
            return

        try:
            code = validate_callback_query(
                target.query,
                expected_state=self.server.expected_state,
            )
        except DevTokenError as error:
            self.server.callback_error = error
            self._send_html(400, "Authorization failed. Return to the terminal for details.")
            return

        self.server.authorization_code = code
        self._send_html(200, "Authorization completed. You can close this window.")

    def _send_html(self, status: int, message: str) -> None:
        body = (
            "<!doctype html><html><head><meta charset='utf-8'><title>Docta</title></head>"
            f"<body><p>{message}</p></body></html>"
        ).encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        return


def _required_url(document: Mapping[str, object], name: str) -> str:
    value = document.get(name)
    if not isinstance(value, str) or not value:
        raise DevTokenError(f"OIDC Discovery is missing {name}.")
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.fragment:
        raise DevTokenError(f"OIDC Discovery returned an invalid {name}.")
    return value.rstrip("/") if name == "issuer" else value


def _string_list_contains(value: object, expected: str) -> bool:
    return (
        isinstance(value, list)
        and all(isinstance(item, str) for item in value)
        and expected in value
    )


def _is_loopback_host(host: str | None) -> bool:
    return host in {"localhost", "127.0.0.1", "::1"}


def _client_id_is_missing(error: ValidationError) -> bool:
    return any(
        item["loc"] == ("client_id",) and item["type"] == "missing"
        for item in error.errors()
    )


def main() -> int:
    try:
        settings = DevTokenSettings()  # type: ignore[call-arg]
    except ValidationError as error:
        if _client_id_is_missing(error):
            print(
                "DOCTA_DEV_OIDC_CLIENT_ID is required. The ZITADEL Application ID is not "
                "the OIDC Client ID; copy the Client ID from the application's configuration "
                "and add it to .env.",
                file=sys.stderr,
            )
        else:
            print("The DOCTA_DEV_OIDC_* configuration is invalid.", file=sys.stderr)
        return 2

    try:
        token = obtain_dev_token(settings)
    except DevTokenError as error:
        print(f"Could not obtain a development token: {error}", file=sys.stderr)
        return 1

    print(token)
    return 0
