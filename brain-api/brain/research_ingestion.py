"""Coordinate durable receipts with one explicitly bounded Cognee writer."""

import asyncio
import json

from .registry import DATASETS, USERS, dataset_name, user_name
from .research_contract import DATASET, IngestionError, ResearchAcknowledgment, prepare


class ResearchIngestion:
    def __init__(self, ledger, remember_document, access, writer_lock):
        self.ledger = ledger
        self.remember_document = remember_document
        self.access = access
        self.writer_lock = writer_lock
        self.ready = False
        self.inflight = {}

    async def open(self):
        await asyncio.to_thread(self.ledger.open)
        self.ready = True

    async def close(self):
        self.ready = False
        if self.inflight:
            await asyncio.gather(*self.inflight.values(), return_exceptions=True)
        await asyncio.to_thread(self.ledger.close)

    async def submit(self, body, ingestion_id):
        """HTTP cancellation cannot abandon an already started provider write."""
        if body.as_user not in USERS:
            raise IngestionError(403, "research_writer_not_authorized")
        request_hash, _, _ = prepare(body, ingestion_id)
        identity = (ingestion_id, request_hash)
        task = self.inflight.get(identity)
        if task is None:
            if not self.ready or len(self.inflight) >= 64:
                raise IngestionError(503, "research_ingestion_unavailable")
            task = asyncio.create_task(self.remember(body, ingestion_id))
            self.inflight[identity] = task

            def finished(completed):
                self.inflight.pop(identity, None)
                if not completed.cancelled():
                    completed.exception()  # Consume errors after disconnected callers.

            task.add_done_callback(finished)
        return await asyncio.shield(task)

    async def capabilities(self):
        # Initialization is complete before serving requests. These are ACL reads,
        # like /access, so they need not queue behind a multi-minute graph build.
        writers = []
        if self.ready:
            for user in USERS:
                access = await self.access(user)
                if DATASET in access["readable"] and DATASET in access.get("writable", []):
                    writers.append(user)
        return {"research_idempotency": self.ready, "research_writers": writers}

    async def remember(self, body, ingestion_id):
        if body.as_user not in USERS:
            raise IngestionError(403, "research_writer_not_authorized")
        request_hash, payload, rendered = prepare(body, ingestion_id)
        async with self.writer_lock:
            if not self.ready:
                raise IngestionError(503, "research_ingestion_unavailable")
            access = await self.access(body.as_user)
            if DATASET not in access["readable"] or DATASET not in access.get("writable", []):
                raise IngestionError(403, "research_writer_not_authorized")
            row = await asyncio.to_thread(
                self.ledger.reserve,
                ingestion_id,
                request_hash,
                payload,
                rendered,
            )
            if row["status"] == "completed":
                return json.loads(row["acknowledgment_json"])
            if row["status"] == "forgotten":
                raise IngestionError(409, "research_ingestion_forgotten")
            if row["status"] == "uncertain" or row["active_document"] is not None:
                raise IngestionError(503, "research_ingestion_uncertain")
            documents = json.loads(row["documents_json"])
            # Reuse the durable document projection even after a code deployment.
            for index in range(row["completed"], len(documents)):
                await asyncio.to_thread(self.ledger.start_document, ingestion_id, index)
                try:
                    completion = await self.remember_document(documents[index], body.as_user)
                    await asyncio.to_thread(
                        self.ledger.finish_document, ingestion_id, index, completion
                    )
                except BaseException as error:
                    # This includes cancellation and a failed local commit after
                    # Cognee succeeded. Neither case permits an automatic retry.
                    try:
                        await asyncio.shield(asyncio.to_thread(self.ledger.uncertain, ingestion_id))
                    except BaseException:
                        pass  # The committed active_document marker also blocks replay.
                    if isinstance(error, asyncio.CancelledError):
                        raise
                    if not isinstance(error, Exception):
                        raise
                    raise IngestionError(503, "research_ingestion_uncertain") from None
            acknowledgment = ResearchAcknowledgment(
                ingestion_id=ingestion_id,
                documents=len(documents),
            ).model_dump()
            # If this final commit fails, all document receipts are still durable;
            # a retry can finish the acknowledgment without another Cognee call.
            return await asyncio.to_thread(self.ledger.finish, ingestion_id, acknowledgment)

    async def before_forget(self, email, dataset):
        """Caller holds writer_lock. Do not acknowledge memory that was deleted."""
        user_name(email)
        if dataset is not None:
            dataset_name(dataset)
            if DATASETS[dataset].owner != email:
                raise PermissionError("not_dataset_owner")
        if DATASETS[DATASET].owner == email and dataset in (None, DATASET):
            await asyncio.to_thread(self.ledger.invalidate)

    async def status(self, ingestion_id):
        if not self.ready:
            raise IngestionError(503, "research_ingestion_unavailable")
        return await asyncio.to_thread(self.ledger.status, ingestion_id)
