from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import httpx
import psycopg
import pytest
from alembic import command
from alembic.config import Config

from docta_api.config import Settings
from docta_api.identity import AuthenticatedIdentity, DeterministicIdentityProvider
from docta_api.main import create_app

pytestmark = pytest.mark.integration


@pytest.mark.anyio
async def test_course_creation_persists_teacher_membership_and_requires_authentication() -> None:
    settings = Settings()
    _upgrade_database()
    suffix = uuid4().hex
    identity = AuthenticatedIdentity(
        issuer="https://integration.test/",
        subject=f"teacher-{suffix}",
    )
    provider = DeterministicIdentityProvider({"teacher-token": identity})
    created_course_id: UUID | None = None

    try:
        async with _client(settings, provider) as client:
            unauthenticated = await client.post(
                "/api/v1/courses",
                json={"title": "Physics"},
            )
            created = await client.post(
                "/api/v1/courses",
                json={"title": "  Physics  "},
                headers={
                    "authorization": "Bearer teacher-token",
                    "x-request-id": "create-course-test",
                },
            )

        assert unauthenticated.status_code == 401
        assert unauthenticated.json()["code"] == "authentication_required"
        assert created.status_code == 201
        assert created.headers["x-request-id"] == "create-course-test"
        payload = created.json()
        created_course_id = UUID(payload["id"])
        assert payload["title"] == "Physics"
        assert payload["role"] == "teacher"

        with psycopg.connect(str(settings.database_url)) as connection:
            membership = connection.execute(
                """
                SELECT users.oidc_issuer, users.oidc_subject, course_memberships.role
                FROM course_memberships
                JOIN users ON users.id = course_memberships.user_id
                WHERE course_memberships.course_id = %s
                """,
                (created_course_id,),
            ).fetchone()
        assert membership == (identity.issuer, identity.subject, "teacher")
    finally:
        _delete_test_records(settings, [created_course_id], [identity])


@pytest.mark.anyio
async def test_course_reads_fail_closed_across_identity_and_course_boundaries() -> None:
    settings = Settings()
    _upgrade_database()
    suffix = uuid4().hex
    identity_a = AuthenticatedIdentity(
        issuer="https://integration.test/",
        subject=f"teacher-a-{suffix}",
    )
    identity_b = AuthenticatedIdentity(
        issuer="https://integration.test/",
        subject=f"teacher-b-{suffix}",
    )
    provider = DeterministicIdentityProvider(
        {"token-a": identity_a, "token-b": identity_b}
    )
    created_course_ids: list[UUID | None] = []

    try:
        async with _client(settings, provider) as client:
            course_a = await client.post(
                "/api/v1/courses",
                json={"title": "Course A private title"},
                headers={"authorization": "Bearer token-a"},
            )
            course_b = await client.post(
                "/api/v1/courses",
                json={"title": "Course B private title"},
                headers={"authorization": "Bearer token-b"},
            )
            assert course_a.status_code == 201
            assert course_b.status_code == 201
            course_a_id = UUID(course_a.json()["id"])
            course_b_id = UUID(course_b.json()["id"])
            created_course_ids.extend([course_a_id, course_b_id])

            own_course = await client.get(
                f"/api/v1/courses/{course_a_id}",
                headers={"authorization": "Bearer token-a"},
            )
            cross_course = await client.get(
                f"/api/v1/courses/{course_b_id}",
                headers={"authorization": "Bearer token-a"},
            )
            missing_course = await client.get(
                f"/api/v1/courses/{uuid4()}",
                headers={"authorization": "Bearer token-a"},
            )

        assert own_course.status_code == 200
        assert own_course.json()["title"] == "Course A private title"
        assert cross_course.status_code == 404
        assert cross_course.json()["code"] == "course_not_found"
        assert "Course B private title" not in cross_course.text
        assert missing_course.status_code == cross_course.status_code
        assert missing_course.json()["code"] == cross_course.json()["code"]
    finally:
        _delete_test_records(settings, created_course_ids, [identity_a, identity_b])


def _upgrade_database() -> None:
    command.upgrade(Config("apps/api/alembic.ini"), "head")


def _client(
    settings: Settings,
    provider: DeterministicIdentityProvider,
) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(settings=settings, identity_provider=provider)
    return _LifespanClient(app)


class _LifespanClient:
    def __init__(self, app) -> None:
        self._app = app
        self._lifespan = app.router.lifespan_context(app)
        self._client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        )

    async def __aenter__(self) -> httpx.AsyncClient:
        await self._lifespan.__aenter__()
        return await self._client.__aenter__()

    async def __aexit__(self, *args: object) -> None:
        await self._client.__aexit__(*args)
        await self._lifespan.__aexit__(*args)


def _delete_test_records(
    settings: Settings,
    course_ids: list[UUID | None],
    identities: list[AuthenticatedIdentity],
) -> None:
    with psycopg.connect(str(settings.database_url)) as connection:
        for course_id in course_ids:
            if course_id is not None:
                connection.execute("DELETE FROM courses WHERE id = %s", (course_id,))
        for identity in identities:
            connection.execute(
                "DELETE FROM users WHERE oidc_issuer = %s AND oidc_subject = %s",
                (identity.issuer, identity.subject),
            )
