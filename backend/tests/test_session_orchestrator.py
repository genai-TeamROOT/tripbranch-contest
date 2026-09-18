"""session_orchestrator.ensure_current_context()의 GPS 최신화 회귀 테스트.

(2026-08-05, D-038) 날씨 조회는 이 모듈에서 제거됐다 — api_context.api_weather를
읽는 소비자가 없어서다. 남은 건 GPS 최신화뿐이다.
"""

from __future__ import annotations

import pytest

from app.services.interpret.session_orchestrator import ensure_current_context
from app.state.service import StateApplyRequest, apply
from app.state.store import InMemoryStateStore


@pytest.fixture
def store() -> InMemoryStateStore:
    return InMemoryStateStore()


def _existing_session(store: InMemoryStateStore) -> str:
    return apply(
        StateApplyRequest(session_id=None, intent="RECOMMEND", confirmed=True),
        store=store,
    ).session_id


@pytest.mark.asyncio
async def test_no_session_yet_skips_gps_seeding_even_with_device_location(
    store: InMemoryStateStore,
) -> None:
    """session_id=None인 최초 턴은 세션이 없어 GPS를 심을 수 없다(알려진 한계)."""
    context = await ensure_current_context(None, "37.5788,126.9770", store=store)

    assert context.session_exists is False
    assert context.api_context.gps_expired is True
    assert context.api_context.gps_location is None


@pytest.mark.asyncio
async def test_device_location_is_not_seeded_into_the_session(
    store: InMemoryStateStore,
) -> None:
    """좌표를 받아도 세션에 심지 않는다.

    예전에는 여기서 세션에 GPS를 심어 다음 턴이 재사용했다. 서버가 사용자 좌표를
    저장하지 않게 되면서(state/store.py::for_persistence) 심어도 남지 않아 호출
    자체를 없앴다 — 이번 턴의 좌표는 요청에 실려 와 그대로 쓰이고, 다음 턴은 화면이
    다시 실어 보낸다.
    """
    session_id = _existing_session(store)

    context = await ensure_current_context(session_id, "37.5788,126.9770", store=store)

    assert context.api_context.gps_location is None
    assert context.api_context.gps_expired is True


@pytest.mark.asyncio
async def test_existing_session_without_device_location_stays_gps_missing(
    store: InMemoryStateStore,
) -> None:
    session_id = _existing_session(store)

    context = await ensure_current_context(session_id, None, store=store)

    assert context.api_context.gps_location is None
    assert context.api_context.gps_expired is True


@pytest.mark.asyncio
async def test_a_second_call_does_not_accumulate_coordinates(
    store: InMemoryStateStore,
) -> None:
    """좌표를 연달아 받아도 세션에는 아무것도 쌓이지 않는다.

    예전에는 새 좌표가 오면 TTL과 무관하게 세션 GPS를 교체했다. 지금은 어느 쪽도
    남지 않는다 — 매 턴 요청에 실려 오므로 세션에 둘 이유가 없다.
    """
    session_id = _existing_session(store)

    first = await ensure_current_context(session_id, "37.5788,126.9770", store=store)
    second = await ensure_current_context(session_id, "9.9999,9.9999", store=store)

    assert first.api_context.gps_location is None
    assert second.api_context.gps_location is None
