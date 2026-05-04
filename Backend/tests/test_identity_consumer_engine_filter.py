from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest

from api_fakes import scalar_one_result
from identity_consumer.main import (
    _session_uses_ultimate_path,
    _should_skip_crop_processing,
    _should_skip_track_processing,
)


@pytest.mark.asyncio
async def test_session_uses_ultimate_path_returns_true_for_ultimate_selector():
    session_id = uuid.uuid4()
    db = type("DB", (), {"execute": AsyncMock(return_value=scalar_one_result({"reid_profile": "ultimate"}))})()
    cache: dict[str, bool] = {}

    result = await _session_uses_ultimate_path(db, session_id=session_id, cache=cache)

    assert result is True
    assert cache[str(session_id)] is True


@pytest.mark.asyncio
async def test_session_uses_ultimate_path_normalizes_legacy_selector():
    session_id = uuid.uuid4()
    db = type("DB", (), {"execute": AsyncMock(return_value=scalar_one_result({"reid_profile": "ultimate_reid"}))})()

    result = await _session_uses_ultimate_path(db, session_id=session_id, cache={})

    assert result is True


@pytest.mark.asyncio
async def test_session_uses_ultimate_path_returns_false_for_standard_or_missing_config():
    session_id = uuid.uuid4()
    db = type("DB", (), {"execute": AsyncMock(return_value=scalar_one_result({}))})()

    result = await _session_uses_ultimate_path(db, session_id=session_id, cache={})

    assert result is False


def test_ultimate_sessions_keep_track_processing_in_adapter_only():
    assert _should_skip_track_processing(uses_ultimate_path=True) is True
    assert _should_skip_track_processing(uses_ultimate_path=False) is False


def test_ultimate_sessions_still_allow_crop_processing_for_recognition():
    assert _should_skip_crop_processing(uses_ultimate_path=True) is False
    assert _should_skip_crop_processing(uses_ultimate_path=False) is False
