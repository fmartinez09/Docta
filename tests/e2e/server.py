"""Browser test composition only. Never imported by the production application."""

import asyncio
import base64
import hashlib
import html
import json
import os
import time
from urllib.parse import parse_qs, urlencode
from uuid import uuid4

import jwt
import psycopg
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from docta_api.config import Settings
from docta_api.main import create_app
from docta_api.rag import DraftCitation, RAGFailure, TutorDraft


class BrowserTestTutor:
    version = "browser-test-only-v1"

    async def generate(self, request):
        with psycopg.connect(str(Settings().database_url)) as connection:
            row = connection.execute(
                "SELECT state FROM messages WHERE id=%s", (request.scope.message_id,)
            ).fetchone()
            assert row == ("pending",)
        await asyncio.sleep(3)
        if "fallo" in request.question.lower():
            raise RAGFailure("MODEL_UNAVAILABLE")
        evidence = request.evidence[0]
        return TutorDraft(
            mode="guided_question",
            answer="Identifica el desplazamiento y el tiempo. ¿Cómo se relacionan?",
            citations=[DraftCitation(chunk_id=evidence.chunk_id, quote=evidence.content)],
            grounded=True,
        )


def create_browser_app():
    settings = Settings()
    assert settings.environment == "test"
    assert settings.database_url.path.startswith("/docta_test_")
    app = create_app(settings=settings, tutor_model=BrowserTestTutor())
    issuer = str(settings.oidc_issuer)
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key()))
    public.update(kid="browser-test", alg="RS256", use="sig")
    codes = {}
    subjects = {role: f"{role}-{uuid4().hex}" for role in ("teacher", "student")}
    callback = os.environ["DOCTA_WEB_ORIGIN"] + "/auth/callback"

    @app.get("/test-oidc/.well-known/openid-configuration")
    async def discovery():
        return {
            "issuer": issuer,
            "authorization_endpoint": issuer + "/authorize",
            "token_endpoint": issuer + "/token",
            "jwks_uri": issuer + "/jwks",
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["none"],
        }

    @app.get("/test-oidc/jwks")
    async def jwks():
        return {"keys": [public]}

    @app.get("/test-oidc/authorize")
    async def authorize(request: Request):
        query = dict(request.query_params)
        assert query["redirect_uri"] == callback
        assert query["client_id"] == "browser-test"
        assert query["code_challenge_method"] == "S256"
        if query.get("user") not in subjects:
            links = "".join(
                f'<p><a href="?{html.escape(urlencode(query | {"user": role}))}">{role}</a></p>'
                for role in subjects
            )
            return HTMLResponse("<h1>Test identity provider</h1>" + links)
        code = uuid4().hex
        codes[code] = query
        return RedirectResponse(callback + "?" + urlencode({"code": code, "state": query["state"]}))

    @app.post("/test-oidc/token")
    async def token(request: Request):
        form = {k: v[0] for k, v in parse_qs((await request.body()).decode()).items()}
        query = codes.pop(form.get("code"), None)
        challenge = (
            base64.urlsafe_b64encode(
                hashlib.sha256(form.get("code_verifier", "").encode()).digest()
            )
            .rstrip(b"=")
            .decode()
        )
        if (
            not query
            or query["code_challenge"] != challenge
            or form.get("redirect_uri") != callback
            or form.get("client_id") != "browser-test"
        ):
            return JSONResponse({"error": "invalid_grant"}, status_code=400)
        now = int(time.time())
        encoded = jwt.encode(
            {
                "iss": issuer,
                "aud": settings.oidc_audience,
                "sub": subjects[query["user"]],
                "iat": now,
                "exp": now + 600,
            },
            private_key,
            algorithm="RS256",
            headers={"kid": "browser-test"},
        )
        return {"access_token": encoded, "token_type": "Bearer", "expires_in": 600}

    @app.post("/test-oidc/enroll")
    async def enroll(request: Request):
        # Explicit test fixture control, absent from runtime routes and guarded per run.
        if request.headers.get("x-test-key") != os.environ["DOCTA_E2E_CONTROL_KEY"]:
            return JSONResponse({}, status_code=403)
        course = (await request.json())["course_id"]
        with psycopg.connect(str(settings.database_url)) as connection:
            user_id = uuid4()
            connection.execute(
                "INSERT INTO users(id,oidc_issuer,oidc_subject) VALUES(%s,%s,%s)",
                (user_id, issuer, subjects["student"]),
            )
            connection.execute(
                "INSERT INTO course_memberships(course_id,user_id,role) VALUES(%s,%s,'student')",
                (course, user_id),
            )
        return {"enrolled": True}

    return app
