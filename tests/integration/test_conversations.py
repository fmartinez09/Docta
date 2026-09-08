import asyncio
import json
from dataclasses import replace
from uuid import UUID, uuid4

import psycopg
import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from docta_api.config import Settings
from docta_api.conversation_models import Citation, Message
from docta_api.identity import AuthenticatedIdentity, DeterministicIdentityProvider
from docta_api.main import create_app
from docta_api.rag import DraftCitation, RAGFailure, TutorDraft
from tests.integration.test_courses import _LifespanClient, _upgrade_database
from tests.integration.test_documents import (
    _create_course,
    _create_upload,
    _finish_ingestion,
    _pdf_bytes,
    _put_direct,
    _storage,
)

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


class RecordingTutor:
    version = "deterministic-test-v1"

    def __init__(self):
        self.requests = []
        self.callback = None
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.release.set()

    async def generate(self, request):
        self.requests.append(request)
        # Independent connection proves commit happened before model invocation.
        with psycopg.connect(str(Settings().database_url)) as connection:
            row = connection.execute(
                "SELECT question, state FROM messages WHERE id=%s", (request.scope.message_id,)
            ).fetchone()
            assert row == (request.question, "pending")
            evidence = connection.execute(
                "SELECT chunk_id FROM retrieved_evidence WHERE message_id=%s",
                (request.scope.message_id,),
            ).fetchall()
            assert {row[0] for row in evidence} == {e.chunk_id for e in request.evidence}
        self.entered.set()
        await self.release.wait()
        if self.callback:
            return self.callback(request)
        source = request.evidence[0]
        return TutorDraft(
            mode="hint",
            answer="Identifica el desplazamiento y el tiempo. ¿Cómo se relacionan?",
            citations=[DraftCitation(chunk_id=source.chunk_id, quote=source.content)],
            grounded=True,
        )


@pytest.fixture
async def chat(ingestion_runtime):
    _upgrade_database()
    settings = Settings()
    suffix = uuid4().hex
    identities = {
        token: AuthenticatedIdentity("https://chat.test/", f"{token}-{suffix}")
        for token in ("teacher", "student", "other", "outsider")
    }
    provider = DeterministicIdentityProvider(identities)
    tutor = RecordingTutor()
    storage = _storage(settings)
    app = create_app(
        settings=settings, identity_provider=provider, object_storage=storage, tutor_model=tutor
    )
    async with _LifespanClient(app) as client:
        a = await _create_course(client, "teacher", "Física A")
        b = await _create_course(client, "outsider", "Material privado B")
        with psycopg.connect(str(settings.database_url)) as connection:
            for token in ("student", "other"):
                user = uuid4()
                connection.execute(
                    "INSERT INTO users(id,oidc_issuer,oidc_subject) VALUES(%s,%s,%s)",
                    (user, identities[token].issuer, identities[token].subject),
                )
                connection.execute(
                    "INSERT INTO course_memberships(course_id,user_id,role) "
                    "VALUES(%s,%s,'student')",
                    (a, user),
                )
        corpora = {}
        for course, token, text in (
            (a, "teacher", "La velocidad es desplazamiento dividido por tiempo. ALPHAONLY731."),
            (b, "outsider", "BETAPRIVATE942 es información confidencial del curso B."),
        ):
            content = _pdf_bytes(text)
            upload = await _create_upload(client, course, token, "upload-chat", content)
            await _put_direct(upload, content)
            confirmation = await client.post(
                f"/api/v1/courses/{course}/documents/{upload['document_id']}/versions/"
                f"{upload['version_id']}/complete",
                headers=headers(token, "confirm-chat"),
            )
            done = await _finish_ingestion(client, confirmation, token, ingestion_runtime)
            assert done.json()["state"] == "INDEXED"
            corpora[course] = done.json()["corpus_version_id"]
            activation = await client.post(
                f"/api/v1/courses/{course}/corpus/activate",
                json={
                    "corpus_version_id": corpora[course],
                    "expected_course_version": 0,
                },
                headers=headers(token),
            )
            assert activation.status_code == 200
        conversation = await client.post(
            f"/api/v1/courses/{a}/conversations", headers=headers("student", "create-chat")
        )
        assert conversation.status_code == 201
        yield {
            "client": client,
            "app": app,
            "model": tutor,
            "a": a,
            "b": b,
            "conversation": conversation.json()["id"],
            "corpora": corpora,
            "identities": identities,
            "provider": provider,
            "settings": settings,
        }


def headers(token="student", key=None):
    result = {"authorization": f"Bearer {token}", "x-request-id": "conversation-integration"}
    if key:
        result["idempotency-key"] = key
    return result


async def ask(chat, question="velocidad", key="ask-1", token="student"):
    return await chat["client"].post(
        f"/api/v1/conversations/{chat['conversation']}/messages",
        json={"question": question},
        headers=headers(token, key),
    )


async def test_workspace_discovery_is_scoped_and_conversations_are_private(chat):
    client = chat["client"]
    courses = await client.get("/api/v1/courses", headers=headers())
    assert [row["id"] for row in courses.json()] == [str(chat["a"])]
    own = await client.get(f"/api/v1/courses/{chat['a']}/conversations", headers=headers())
    assert [row["id"] for row in own.json()] == [chat["conversation"]]
    teacher = await client.get(
        f"/api/v1/courses/{chat['a']}/conversations", headers=headers("teacher")
    )
    assert teacher.json() == []
    for token, course in (("student", chat["a"]), ("teacher", chat["b"])):
        denied = await client.get(f"/api/v1/courses/{course}/documents", headers=headers(token))
        assert denied.status_code == 404
    documents = await client.get(
        f"/api/v1/courses/{chat['a']}/documents", headers=headers("teacher")
    )
    assert len(documents.json()) == 1
    assert documents.json()[0]["state"] == "INDEXED"
    assert "storage_key" not in documents.text


async def test_course_creation_retry_is_durable_and_key_is_bound_to_payload(chat):
    client = chat["client"]
    first = await client.post(
        "/api/v1/courses",
        json={"title": "Retry course"},
        headers=headers("teacher", "retry-course"),
    )
    repeated = await client.post(
        "/api/v1/courses",
        json={"title": "Retry course"},
        headers=headers("teacher", "retry-course"),
    )
    assert first.status_code == repeated.status_code == 201
    assert first.json()["id"] == repeated.json()["id"]
    conflict = await client.post(
        "/api/v1/courses", json={"title": "Other title"}, headers=headers("teacher", "retry-course")
    )
    assert conflict.status_code == 409


def events(response):
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    result = []
    for block in response.text.strip().split("\n\n"):
        lines = block.splitlines()
        if lines[0].startswith("event:"):
            result.append(
                (
                    lines[0][7:],
                    json.loads(next(line[6:] for line in lines if line.startswith("data: "))),
                )
            )
    return result


async def test_pdf_to_student_response_has_durable_scoped_citations_and_retry(chat, caplog):
    import logging

    caplog.set_level(logging.INFO, logger="docta.conversation")
    response = await ask(chat)
    stream = events(response)
    assert stream[0][0] == "message.accepted"
    assert stream[-1][0] == "message.completed"
    result = stream[-1][1]
    assert result["state"] == "completed" and result["response"]["grounded"]
    assert result["course_id"] == str(chat["a"])
    citation = result["response"]["citations"][0]
    assert citation["page"] == 1 and citation["fragment"] == "chunk-0"
    assert len(citation["document_sha256"]) == 64
    assert "storage" not in json.dumps(citation)
    retry = events(await ask(chat))[-1][1]
    assert retry == result and len(chat["model"].requests) == 1
    history = await chat["client"].get(
        f"/api/v1/conversations/{chat['conversation']}", headers=headers()
    )
    assert history.json()["messages"] == [result]
    for _name, payload in stream[:-1]:
        assert "answer" not in json.dumps(payload) and "ALPHAONLY731" not in json.dumps(payload)
    logs = [
        json.loads(record.message)
        for record in caplog.records
        if record.name == "docta.conversation"
    ]
    assert {event["operation"] for event in logs} >= {
        "persist",
        "retrieval",
        "generate",
        "validate",
        "respond",
    }
    assert all(event["correlation_id"] == "conversation-integration" for event in logs)
    assert "ALPHAONLY731" not in caplog.text
    assert "Identifica el desplazamiento" not in caplog.text


async def test_creation_history_and_asking_are_owner_and_membership_scoped(chat):
    client, course = chat["client"], chat["a"]
    repeated = await client.post(
        f"/api/v1/courses/{course}/conversations", headers=headers("student", "create-chat")
    )
    assert repeated.json()["id"] == chat["conversation"]
    for token in ("outsider", "other", "teacher"):
        assert (await ask(chat, token=token)).status_code == 404
        history = await client.get(
            f"/api/v1/conversations/{chat['conversation']}", headers=headers(token)
        )
        assert history.status_code == 404
    foreign = await client.post(
        f"/api/v1/courses/{chat['b']}/conversations", headers=headers("student", "foreign")
    )
    assert foreign.status_code == 404
    assert (await ask(chat, key=None)).status_code == 400
    assert (await ask(chat, question="  ")).status_code == 422
    assert (await ask(chat, question="x" * 4001)).status_code == 422
    assert (await ask(chat, question="velocidad")).status_code == 200
    assert (await ask(chat, question="changed")).status_code == 409
    with psycopg.connect(str(chat["settings"].database_url)) as connection:
        connection.execute(
            "DELETE FROM course_memberships WHERE course_id=%s AND user_id="
            "(SELECT owner_user_id FROM conversations WHERE id=%s)",
            (course, chat["conversation"]),
        )
    assert (await ask(chat)).status_code == 404


async def test_foreign_or_insufficient_evidence_abstains_without_model(chat):
    result = events(await ask(chat, "BETAPRIVATE942"))[-1][1]
    assert result["state"] == "completed" and result["response"]["mode"] == "abstain"
    assert result["response"]["citations"] == [] and not result["response"]["grounded"]
    assert "BETAPRIVATE942" not in result["response"]["answer"]
    assert chat["model"].requests == []


@pytest.mark.parametrize("failure", ["unavailable", "foreign_citation", "unretrieved", "quote"])
async def test_terminal_model_failures_leave_question_and_no_partial_answer(chat, failure):
    def invalid(request):
        if failure == "unavailable":
            raise RAGFailure("MODEL_UNAVAILABLE")
        chunk_id = request.evidence[0].chunk_id
        quote = request.evidence[0].content
        if failure in ("foreign_citation", "unretrieved"):
            with psycopg.connect(str(chat["settings"].database_url)) as connection:
                chunk_id = connection.execute(
                    "SELECT id FROM chunks WHERE course_id=%s LIMIT 1", (chat["b"],)
                ).fetchone()[0]
            if failure == "unretrieved":
                chunk_id = uuid4()
        else:
            quote = "fabricated quote with secret payload"
        return TutorDraft(
            mode="hint",
            answer="provider-secret",
            citations=[DraftCitation(chunk_id=chunk_id, quote=quote)],
            grounded=True,
        )

    chat["model"].callback = invalid
    result = events(await ask(chat))[-1][1]
    assert result["state"] == "failed" and result["response"] is None
    assert result["failure_code"] == (
        "MODEL_UNAVAILABLE" if failure == "unavailable" else "MODEL_OUTPUT_INVALID"
    )
    assert result["question"] == "velocidad" and "provider-secret" not in json.dumps(result)
    assert events(await ask(chat))[-1][1] == result
    assert len(chat["model"].requests) == 1
    with psycopg.connect(str(chat["settings"].database_url)) as connection:
        assert connection.execute(
            "SELECT count(*) FROM citations WHERE message_id=%s", (result["id"],)
        ).fetchone() == (0,)


async def test_pending_duplicates_and_concurrent_question_do_not_repeat_model(chat):
    model = chat["model"]
    model.release.clear()
    first = asyncio.create_task(ask(chat))
    await asyncio.wait_for(model.entered.wait(), 10)
    second = asyncio.create_task(ask(chat))
    busy = await ask(chat, key="different-key")
    assert busy.status_code == 409 and busy.json()["code"] == "conversation_busy"
    history = await chat["client"].get(
        f"/api/v1/conversations/{chat['conversation']}", headers=headers()
    )
    assert len(history.json()["messages"]) == 1
    assert history.json()["messages"][0]["state"] == "pending"
    model.release.set()
    results = await asyncio.gather(first, second)
    assert events(results[0])[-1][1] == events(results[1])[-1][1]
    assert len(model.requests) == 1


async def test_captured_corpus_and_historical_citations_survive_withdrawal_and_restart(chat):
    result = events(await ask(chat))[-1][1]
    with psycopg.connect(str(chat["settings"].database_url)) as connection:
        connection.execute(
            "UPDATE courses SET active_corpus_version_id=NULL WHERE id=%s", (chat["a"],)
        )
        connection.execute(
            "UPDATE corpus_versions SET state='RETIRED' WHERE id=%s", (chat["corpora"][chat["a"]],)
        )
    app = create_app(
        settings=chat["settings"], identity_provider=chat["provider"], tutor_model=chat["model"]
    )
    async with _LifespanClient(app) as client:
        history = await client.get(
            f"/api/v1/conversations/{chat['conversation']}", headers=headers()
        )
        assert history.json()["messages"] == [result]
        response = await client.post(
            f"/api/v1/conversations/{chat['conversation']}/messages",
            json={"question": "velocidad"},
            headers=headers(key="new"),
        )
        failure = events(response)[-1][1]
        assert failure["failure_code"] == "COURSE_CORPUS_NOT_READY"
        assert len(chat["model"].requests) == 1


async def test_expired_execution_is_failed_and_late_completion_is_fenced(chat):
    runtime = chat["app"].state.conversation_runtime
    accepted = runtime.service.accept(
        chat["identities"]["student"],
        UUID(chat["conversation"]),
        "velocidad",
        "interrupted",
        "crashed-process",
    )
    with psycopg.connect(str(chat["settings"].database_url)) as connection:
        connection.execute(
            "UPDATE messages SET deadline_at=now()-interval '1 second' WHERE id=%s",
            (accepted.message.id,),
        )
    history = await chat["client"].get(
        f"/api/v1/conversations/{chat['conversation']}", headers=headers()
    )
    assert history.json()["messages"][0]["failure_code"] == "PROCESSING_INTERRUPTED"
    with pytest.raises(RAGFailure, match="PROCESSING_INTERRUPTED"):
        runtime.service.complete(
            accepted.message.id,
            TutorDraft(mode="abstain", answer="late", citations=[], grounded=False),
        )
    assert events(await ask(chat, key="interrupted"))[-1][1]["state"] == "failed"
    assert chat["model"].requests == []


async def test_disconnect_after_acceptance_does_not_cancel_processing(chat):
    model = chat["model"]
    model.release.clear()
    runtime = chat["app"].state.conversation_runtime
    path = f"/api/v1/conversations/{chat['conversation']}/messages"
    disconnected = asyncio.Event()
    body_sent = False
    bodies = []

    async def receive():
        nonlocal body_sent
        if not body_sent:
            body_sent = True
            return {"type": "http.request", "body": b'{"question":"velocidad"}', "more_body": False}
        await disconnected.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        if message["type"] == "http.response.body":
            bodies.append(message.get("body", b""))
            if b"message.accepted" in message.get("body", b""):
                disconnected.set()

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "server": ("testserver", 80),
        "client": ("127.0.0.1", 12345),
        "headers": [
            (key.encode(), value.encode())
            for key, value in {
                **headers(key="disconnect"),
                "content-type": "application/json",
            }.items()
        ],
    }
    await asyncio.wait_for(chat["app"](scope, receive, send), 10)
    await asyncio.wait_for(model.entered.wait(), 10)
    assert len(runtime.tasks) == 1
    assert b"Identifica" not in b"".join(bodies)
    pending = list(runtime.tasks.values())
    model.release.set()
    await asyncio.wait_for(asyncio.gather(*pending), 10)
    result = events(await ask(chat, key="disconnect"))[-1][1]
    assert result["state"] == "completed" and len(model.requests) == 1


async def test_corpus_is_captured_once_and_future_questions_use_new_active_pointer(chat):
    model = chat["model"]
    model.release.clear()
    first = asyncio.create_task(ask(chat))
    await asyncio.wait_for(model.entered.wait(), 10)
    with psycopg.connect(str(chat["settings"].database_url)) as connection:
        connection.execute(
            "UPDATE courses SET active_corpus_version_id=NULL WHERE id=%s", (chat["a"],)
        )
    model.release.set()
    result = events(await first)[-1][1]
    assert result["state"] == "completed"
    assert result["corpus_version_id"] == chat["corpora"][chat["a"]]
    next_result = events(await ask(chat, key="next"))[-1][1]
    assert next_result["failure_code"] == "COURSE_CORPUS_NOT_READY"


async def test_retrieval_fails_closed_on_forged_scope_and_evidence_metadata(chat):
    runtime = chat["app"].state.conversation_runtime
    accepted = runtime.service.accept(
        chat["identities"]["student"],
        UUID(chat["conversation"]),
        "velocidad",
        "scope",
        "scope-test",
    )
    request = runtime.service.request(accepted, ())
    for invalid in (
        None,
        replace(request.scope, course_id=chat["b"]),
        replace(request.scope, corpus_version_id=UUID(chat["corpora"][chat["b"]])),
        replace(request.scope, principal_user_id=uuid4()),
        replace(request.scope, message_id=uuid4()),
    ):
        with pytest.raises(RAGFailure, match="RETRIEVAL_SCOPE_INVALID"):
            runtime.retriever.retrieve(invalid, "velocidad")
    evidence = runtime.retriever.retrieve(request.scope, "velocidad")
    assert evidence and all(e.course_id == chat["a"] for e in evidence)
    with pytest.raises(RAGFailure, match="RETRIEVAL_SCOPE_INVALID"):
        runtime.service.save_evidence(accepted, (replace(evidence[0], page_start=99),))
    runtime.service.fail(accepted.message.id, "PROCESSING_INTERRUPTED")


async def test_citation_constraint_failure_rolls_back_entire_completion(chat):
    runtime = chat["app"].state.conversation_runtime
    accepted = runtime.service.accept(
        chat["identities"]["student"],
        UUID(chat["conversation"]),
        "velocidad",
        "atomic",
        "atomic-test",
    )
    request = runtime.service.request(accepted, ())
    evidence = runtime.retriever.retrieve(request.scope, "velocidad")
    runtime.service.save_evidence(accepted, evidence)
    valid = DraftCitation(chunk_id=evidence[0].chunk_id, quote=evidence[0].content)
    invalid = DraftCitation(chunk_id=uuid4(), quote="citation absent from retrieved evidence")
    draft = TutorDraft(
        mode="hint", answer="must roll back", citations=[valid, invalid], grounded=True
    )
    with pytest.raises(IntegrityError):
        runtime.service.complete(accepted.message.id, draft)
    with runtime.service.database.session() as session:
        message = session.get(Message, accepted.message.id)
        assert message.state == "pending" and message.answer is None
        assert (
            session.scalar(
                select(func.count()).select_from(Citation).where(Citation.message_id == message.id)
            )
            == 0
        )
    runtime.service.fail(accepted.message.id, "PERSISTENCE_UNAVAILABLE")


async def test_full_history_paginates_but_model_window_is_bounded(chat):
    runtime = chat["app"].state.conversation_runtime
    identity = chat["identities"]["student"]
    conversation_id = UUID(chat["conversation"])
    for index in range(8):
        accepted = runtime.service.accept(
            identity, conversation_id, f"velocidad {index}", f"history-{index}", "history-test"
        )
        runtime.service.complete(
            accepted.message.id,
            TutorDraft(mode="abstain", answer="context " * 500, grounded=False, citations=[]),
        )
    result = events(await ask(chat, key="history-final"))[-1][1]
    assert result["state"] == "completed"
    history = chat["model"].requests[-1].history
    assert len(history) <= 6
    assert sum(len(turn.question) + len(turn.answer) for turn in history) <= 12000
    assert history[-1].question == "velocidad 7"
    page1 = await chat["client"].get(
        f"/api/v1/conversations/{conversation_id}?limit=5", headers=headers()
    )
    assert len(page1.json()["messages"]) == 5
    cursor = page1.json()["next_after_sequence"]
    page2 = await chat["client"].get(
        f"/api/v1/conversations/{conversation_id}?limit=5&after_sequence={cursor}",
        headers=headers(),
    )
    assert len(page2.json()["messages"]) == 4 and page2.json()["next_after_sequence"] is None
    assert [row["sequence"] for row in page1.json()["messages"] + page2.json()["messages"]] == list(
        range(1, 10)
    )


async def test_new_questions_ignore_inactive_content_from_the_same_course(chat, ingestion_runtime):
    client, course = chat["client"], chat["a"]
    content = _pdf_bytes(
        "La aceleración relaciona el cambio de rapidez con el tiempo transcurrido."
    )
    upload = await _create_upload(client, course, "teacher", "replacement", content)
    await _put_direct(upload, content)
    confirmed = await client.post(
        f"/api/v1/courses/{course}/documents/{upload['document_id']}/versions/"
        f"{upload['version_id']}/complete",
        headers=headers("teacher", "confirm-replacement"),
    )
    done = await _finish_ingestion(client, confirmed, "teacher", ingestion_runtime)
    replacement = done.json()["corpus_version_id"]
    activated = await client.post(
        f"/api/v1/courses/{course}/corpus/activate",
        json={
            "corpus_version_id": replacement,
            "expected_course_version": 1,
        },
        headers=headers("teacher"),
    )
    assert activated.status_code == 200
    old_question = events(await ask(chat, question="ALPHAONLY731"))[-1][1]
    assert old_question["corpus_version_id"] == replacement
    assert old_question["response"]["mode"] == "abstain"
    assert old_question["response"]["citations"] == []
    assert chat["model"].requests == []
    new_question = events(await ask(chat, question="aceleración", key="replacement-question"))[-1][
        1
    ]
    assert new_question["response"]["grounded"]
    assert new_question["response"]["citations"][0]["document_version_id"] == upload["version_id"]
