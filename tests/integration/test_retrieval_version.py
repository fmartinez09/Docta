"""AC-07-07: the persisted retrieval_config_version is the one reported by the active retriever."""

import asyncio
from uuid import uuid4

import psycopg
import pytest

from docta_api.config import Settings
from docta_api.identity import AuthenticatedIdentity, DeterministicIdentityProvider
from docta_api.main import create_app
from tests.integration.test_conversations import events, headers
from tests.integration.test_courses import _LifespanClient, _upgrade_database
from tests.integration.test_documents import _create_course

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


class StubRetriever:
    version = "stub-retriever-v1"

    def retrieve(self, scope, question):
        return ()


class RecordingTutor:
    version = "unused-tutor-v1"

    def __init__(self):
        self.called = False

    async def generate(self, request):
        self.called = True
        raise AssertionError("The model must not be called in this scenario")


async def _persisted_version(**overrides) -> str:
    _upgrade_database()
    settings = Settings()
    identity = AuthenticatedIdentity("https://version.test/", f"teacher-{uuid4().hex}")
    provider = DeterministicIdentityProvider({"teacher": identity})
    tutor = RecordingTutor()
    app = create_app(settings=settings, identity_provider=provider, tutor_model=tutor, **overrides)
    async with _LifespanClient(app) as client:
        course = await _create_course(client, "teacher", "Version course")
        conversation = await client.post(
            f"/api/v1/courses/{course}/conversations",
            headers=headers("teacher", "create-version"),
        )
        assert conversation.status_code == 201
        conversation_id = conversation.json()["id"]
        # The course has no READY corpus, so the turn is accepted (and the version persisted) and
        # then fails with COURSE_CORPUS_NOT_READY before retrieval and the tutor are reached.
        response = await asyncio.wait_for(
            client.post(
                f"/api/v1/conversations/{conversation_id}/messages",
                json={"question": "velocidad"},
                headers=headers("teacher", "ask-version"),
            ),
            10,
        )
        terminal = events(response)[-1][1]
        assert terminal["state"] == "failed"
        assert terminal["failure_code"] == "COURSE_CORPUS_NOT_READY"
        assert not tutor.called
        with psycopg.connect(str(settings.database_url)) as connection:
            rows = connection.execute(
                "SELECT retrieval_config_version FROM messages WHERE conversation_id=%s",
                (conversation_id,),
            ).fetchall()
    assert len(rows) == 1
    return rows[0][0]


async def test_default_app_persists_the_fts_retrieval_version():
    assert await _persisted_version() == "spanish-fts-coverage-top5-v2"


async def test_injected_retriever_version_is_persisted():
    assert await _persisted_version(retriever=StubRetriever()) == "stub-retriever-v1"
