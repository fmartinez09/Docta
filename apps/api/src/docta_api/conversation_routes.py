import asyncio
import json
from time import perf_counter
from typing import Annotated
from uuid import UUID

from anyio import CancelScope, to_thread
from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.exc import SQLAlchemyError

from docta_api.api_errors import APIError
from docta_api.conversation_models import MessageState
from docta_api.conversation_runtime import ConversationRuntime
from docta_api.conversations import ConversationView, HistoryView, MessageView, QuestionRequest
from docta_api.courses import RequiredIdentity

router = APIRouter(prefix="/api/v1", tags=["conversations"])
IdempotencyKey = Annotated[str | None, Header(alias="Idempotency-Key")]


async def _database_call(function, *args):
    try:
        return await to_thread.run_sync(function, *args)
    except SQLAlchemyError as error:
        raise APIError(
            503, "persistence_unavailable", "Conversation storage is temporarily unavailable."
        ) from error


@router.post("/courses/{course_id}/conversations", response_model=ConversationView, status_code=201)
async def create_conversation(
    course_id: UUID,
    request: Request,
    identity: RequiredIdentity,
    idempotency_key: IdempotencyKey = None,
) -> ConversationView:
    runtime: ConversationRuntime = request.app.state.conversation_runtime
    return await _database_call(runtime.service.create, identity, course_id, idempotency_key)


@router.get("/conversations/{conversation_id}", response_model=HistoryView)
async def conversation_history(
    conversation_id: UUID,
    request: Request,
    identity: RequiredIdentity,
    after_sequence: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> HistoryView:
    runtime: ConversationRuntime = request.app.state.conversation_runtime
    return await _database_call(
        runtime.service.history, identity, conversation_id, after_sequence, limit
    )


@router.get("/conversations/{conversation_id}/messages/{message_id}", response_model=MessageView)
async def message_status(
    conversation_id: UUID, message_id: UUID, request: Request, identity: RequiredIdentity
) -> MessageView:
    runtime: ConversationRuntime = request.app.state.conversation_runtime
    return await _database_call(runtime.service.read, identity, conversation_id, message_id)


def _event(name: str, message_id: UUID, data: dict) -> str:
    return (
        f"event: {name}\nid: {message_id}:{name}\n"
        f"data: {json.dumps(data, ensure_ascii=False, separators=(',', ':'))}\n\n"
    )


@router.post(
    "/conversations/{conversation_id}/messages",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {"schema": {"type": "string"}}}}},
)
async def ask_question(
    conversation_id: UUID,
    payload: QuestionRequest,
    request: Request,
    identity: RequiredIdentity,
    idempotency_key: IdempotencyKey = None,
) -> StreamingResponse:
    runtime: ConversationRuntime = request.app.state.conversation_runtime
    # Commit and register execution together despite a disconnect during the DB await.
    with CancelScope(shield=True):
        accepted = await _database_call(
            runtime.service.accept,
            identity,
            conversation_id,
            payload.question,
            idempotency_key,
            request.state.request_id,
        )
        runtime.start(accepted)
    progress = runtime.progress.get(accepted.message.id, [])

    async def events():
        message_id = accepted.message.id
        identifiers = {"conversation_id": str(conversation_id), "message_id": str(message_id)}
        yield _event(
            "message.accepted", message_id, {**identifiers, "state": accepted.message.state}
        )
        seen = 0
        heartbeat = 0
        while True:
            # This read rechecks ownership/membership, including on a repeated pending request.
            try:
                current = await to_thread.run_sync(
                    runtime.service.read, identity, conversation_id, message_id
                )
            except (APIError, SQLAlchemyError):
                # No unaudited terminal event on a failed read. Reconnect through durable history.
                return
            for phase in progress[seen:]:
                yield _event(phase, message_id, identifiers)
            seen = len(progress)
            if current.state != MessageState.PENDING:
                yield _event(
                    f"message.{current.state}", message_id, current.model_dump(mode="json")
                )
                runtime.service.log(accepted, "respond", current.state, perf_counter())
                return
            await asyncio.sleep(0.2)
            heartbeat += 1
            if heartbeat % 50 == 0:
                yield ": heartbeat\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-store",
            "X-Accel-Buffering": "no",
        },
    )
