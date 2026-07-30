from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from core.errors import AppError, ErrorCode
from domain.sessions import SessionStage
from repositories.session_store import SessionStore


@pytest.mark.asyncio
async def test_session_starts_extracting_and_can_be_replaced() -> None:
    store = SessionStore(ttl_seconds=21_600)
    session = await store.create()

    replacement = session.model_copy(
        update={"stage": SessionStage.REVIEWING_INGREDIENTS}
    )
    replaced = await store.replace(replacement)

    assert session.stage is SessionStage.EXTRACTING
    assert replaced.stage is SessionStage.REVIEWING_INGREDIENTS
    assert (await store.require(session.id)).stage is SessionStage.REVIEWING_INGREDIENTS


@pytest.mark.asyncio
async def test_invalid_unchecked_replacement_preserves_stored_session() -> None:
    store = SessionStore(ttl_seconds=21_600)
    session = await store.create()
    invalid_replacement = session.model_copy(update={"stage": "invalid"})

    with pytest.raises(ValidationError):
        await store.replace(invalid_replacement)

    assert (await store.require(session.id)).stage is SessionStage.EXTRACTING


@pytest.mark.asyncio
async def test_stale_replacement_returns_conflict_and_preserves_committed_update() -> (
    None
):
    store = SessionStore(ttl_seconds=21_600)
    session = await store.create()
    first_update = session.model_copy(
        update={"stage": SessionStage.REVIEWING_INGREDIENTS}
    )
    stale_update = session.model_copy(update={"stage": SessionStage.RECIPES_READY})

    committed = await store.replace(first_update)

    with pytest.raises(AppError) as raised:
        await store.replace(stale_update)

    stored = await store.require(session.id)
    assert raised.value.code is ErrorCode.INVALID_SESSION_TRANSITION
    assert raised.value.status_code == 409
    assert raised.value.retryable is True
    assert raised.value.session_id == session.id
    assert stored.stage is SessionStage.REVIEWING_INGREDIENTS
    assert stored.updated_at == committed.updated_at


@pytest.mark.asyncio
async def test_expired_sessions_are_deleted() -> None:
    store = SessionStore(ttl_seconds=10)
    session = await store.create()
    removed = await store.delete_expired(now=datetime.now(UTC) + timedelta(seconds=11))

    assert removed == [session.id]
    assert await store.get(session.id) is None


@pytest.mark.asyncio
async def test_session_reads_do_not_expose_stored_state() -> None:
    store = SessionStore(ttl_seconds=21_600)
    session = await store.create()

    read = await store.require(session.id)
    read.stage = SessionStage.RECIPES_READY

    stored = await store.require(session.id)

    assert stored.stage is SessionStage.EXTRACTING


@pytest.mark.asyncio
async def test_missing_session_raises_the_standard_not_found_error() -> None:
    store = SessionStore(ttl_seconds=21_600)

    with pytest.raises(AppError) as raised:
        await store.require("missing-session")

    assert raised.value.code is ErrorCode.RESOURCE_NOT_FOUND
    assert raised.value.status_code == 404
    assert raised.value.retryable is False
    assert raised.value.session_id == "missing-session"
