"""API-owned, bounded executions independent of SSE sockets; PostgreSQL owns outcomes."""

import asyncio
from dataclasses import replace
from time import perf_counter
from uuid import UUID

from anyio import CancelScope, to_thread
from sqlalchemy.exc import SQLAlchemyError

from docta_api.conversations import AcceptedMessage, ConversationService
from docta_api.rag import (
    RAGFailure,
    ResponseValidator,
    Retriever,
    TutorModel,
    abstention,
)

SAFE_FAILURES = frozenset(
    {
        "COURSE_CORPUS_NOT_READY",
        "MODEL_NOT_CONFIGURED",
        "MODEL_UNAVAILABLE",
        "MODEL_OUTPUT_INVALID",
        "RETRIEVAL_SCOPE_INVALID",
        "PROCESSING_INTERRUPTED",
        "PERSISTENCE_UNAVAILABLE",
    }
)


class ConversationRuntime:
    def __init__(
        self,
        service: ConversationService,
        retriever: Retriever,
        model: TutorModel,
        validator: ResponseValidator,
    ) -> None:
        self.service = service
        self.retriever = retriever
        self.model = model
        self.validator = validator
        self.tasks: dict[UUID, asyncio.Task] = {}
        self.progress: dict[UUID, list[str]] = {}
        self._reaper: asyncio.Task | None = None

    def start(self, accepted: AcceptedMessage) -> None:
        if not accepted.created:
            return
        message_id = accepted.message.id
        self.progress[message_id] = []
        task = asyncio.create_task(self._run(accepted))
        self.tasks[message_id] = task
        task.add_done_callback(lambda _: self.tasks.pop(message_id, None))

    def start_recovery(self) -> None:
        self._reaper = asyncio.create_task(self._recover())

    async def close(self) -> None:
        tasks = [*self.tasks.values()]
        if self._reaper:
            tasks.append(self._reaper)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _recover(self) -> None:
        while True:
            await asyncio.sleep(5)
            try:
                await to_thread.run_sync(self.service.expire)
            except SQLAlchemyError:
                # DB outage is not a reason to discard a question or repeat a model invocation.
                continue

    def _phase(
        self,
        accepted: AcceptedMessage,
        event: str,
        outcome: str = "started",
        started: float | None = None,
    ) -> None:
        self.progress[accepted.message.id].append(f"{event}.{outcome}")
        self.service.log(accepted, event, outcome, started or perf_counter())

    async def _pipeline(self, accepted: AcceptedMessage) -> None:
        request = await to_thread.run_sync(self.service.request, accepted, ())
        started = perf_counter()
        self._phase(accepted, "retrieval")
        evidence = await to_thread.run_sync(
            self.retriever.retrieve, request.scope, request.question
        )
        await to_thread.run_sync(self.service.save_evidence, accepted, evidence)
        for document_version_id in sorted({item.document_version_id for item in evidence}):
            self.service.log(accepted, "retrieve", "completed", started, document_version_id)
        request = replace(request, evidence=evidence)
        self._phase(accepted, "retrieval", "completed", started)
        started = perf_counter()
        if evidence:
            self._phase(accepted, "generation")
            draft = await self.model.generate(request)
            self.service.log(accepted, "generate", "completed", started)
        else:
            draft = abstention()
        self._phase(accepted, "validation")
        started = perf_counter()
        draft = self.validator.validate(draft, request)
        self.service.log(accepted, "validate", "completed", started)
        started = perf_counter()
        await to_thread.run_sync(self.service.complete, accepted.message.id, draft)
        self.service.log(accepted, "persist", "completed", started)

    async def _run(self, accepted: AcceptedMessage) -> None:
        try:
            await asyncio.wait_for(self._pipeline(accepted), self.service.message_timeout_seconds)
        except asyncio.CancelledError:
            await self._fail(accepted, "PROCESSING_INTERRUPTED")
        except TimeoutError:
            await self._fail(accepted, "PROCESSING_INTERRUPTED")
        except RAGFailure as error:
            await self._fail(
                accepted, error.code if error.code in SAFE_FAILURES else "INTERNAL_ERROR"
            )
        except SQLAlchemyError:
            await self._fail(accepted, "PERSISTENCE_UNAVAILABLE")
        except Exception:
            await self._fail(accepted, "INTERNAL_ERROR")
        finally:
            # Progress contains no content; terminal payloads are always read from DB.
            self.progress.pop(accepted.message.id, None)

    async def _fail(self, accepted: AcceptedMessage, code: str) -> None:
        with CancelScope(shield=True):
            try:
                await to_thread.run_sync(self.service.fail, accepted.message.id, code)
                self.service.log(accepted, "persist", code, perf_counter())
            except SQLAlchemyError:
                self.service.log(accepted, "persist", "persistence_unavailable", perf_counter())
                # An unavailable DB is reconciled after deadline/restart, never silently rerun.
