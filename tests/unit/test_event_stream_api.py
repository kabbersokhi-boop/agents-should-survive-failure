from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast
from uuid import UUID

import pytest
from fastapi import HTTPException, Request

from agents_should_survive_failure import api
from agents_should_survive_failure.persistence.models import RunStatus
from agents_should_survive_failure.persistence.session import Database

RUN_ID = UUID("00000000-0000-0000-0000-000000000020")


class FakeDatabase:
    @asynccontextmanager
    async def session(self):  # type: ignore[no-untyped-def]
        yield object()


def request(*, last_event_id: str | None = None) -> Request:
    headers = {} if last_event_id is None else {"last-event-id": last_event_id}

    async def is_disconnected() -> bool:
        return False

    return cast(Request, SimpleNamespace(headers=headers, is_disconnected=is_disconnected))


def test_event_stream_cursor_rejects_invalid_last_event_id() -> None:
    with pytest.raises(HTTPException) as raised:
        api.event_stream_cursor(request(last_event_id="not-a-sequence"), 0)

    assert raised.value.status_code == 422


@pytest.mark.asyncio
async def test_event_stream_replays_after_cursor_and_finishes_terminal_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event = SimpleNamespace(
        sequence=30,
        event_type="approval.requested",
        summary="Human approval requested.",
        payload={"risk_score": 25},
        occurred_at=datetime(2026, 7, 15, tzinfo=UTC),
    )
    calls: list[tuple[int, int]] = []

    async def get(self: object, run_id: UUID) -> object | None:
        del self
        assert run_id == RUN_ID
        return SimpleNamespace(status=RunStatus.SUCCEEDED)

    async def events_after(
        self: object, run_id: UUID, *, after_sequence: int, limit: int
    ) -> list[object]:
        del self
        assert run_id == RUN_ID
        calls.append((after_sequence, limit))
        return [event] if after_sequence < event.sequence else []

    monkeypatch.setattr(api.WorkflowRunRepository, "get", get)
    monkeypatch.setattr(api.WorkflowRunRepository, "events_after", events_after)
    response = await api.stream_run_events(
        RUN_ID,
        request(last_event_id="20"),
        cast(Database, FakeDatabase()),
        after_sequence=10,
    )

    chunks = [chunk async for chunk in cast(AsyncIterator[str], response.body_iterator)]

    assert calls == [(20, 100)]
    assert chunks == [
        'id: 30\nevent: workflow_event\ndata: {"sequence": 30, "event_type": '
        '"approval.requested", "summary": "Human approval requested.", '
        '"payload": {"risk_score": 25}, "occurred_at": "2026-07-15T00:00:00+00:00"}\n\n'
    ]
    assert response.media_type == "text/event-stream"
    assert response.headers["cache-control"] == "no-cache"
