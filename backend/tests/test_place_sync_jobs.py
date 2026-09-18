"""동기화 job이 실패 사유를 어떻게 적는가.

`SupabaseRepositoryError`의 메시지는 "장소 데이터 저장 중 문제가 발생했어요"라는
안내문이고, 실제 원인(제약 위반 같은 것)은 details에만 있다. 그것을 안 꺼내면 로그가
무엇이 잘못됐는지 말하지 못한다 — 2026-08-30 중구 반영 실패에서 원인을 찾으려고 DB를
직접 뒤져야 했다.
"""

from __future__ import annotations

import asyncio

import pytest

from app.errors import AppError
from app.repositories.supabase_places import SupabaseRepositoryError
from app.services.place_sync_jobs import SyncJobRegistry, _failure_detail


def test_상류_원문을_실패_사유에_붙인다() -> None:
    detail = _failure_detail(
        SupabaseRepositoryError("HTTP 400: 23514 - places_active_state_valid")
    )

    assert "SupabaseRepositoryError" in detail
    assert "23514" in detail
    assert "places_active_state_valid" in detail


def test_상류_원문이_없으면_예외_문구만_적는다() -> None:
    assert _failure_detail(ValueError("그냥 오류")) == "ValueError: 그냥 오류"


def test_details가_있어도_상류_원문이_비면_붙이지_않는다() -> None:
    """details를 가진 AppError가 전부 상류 원문을 담지는 않는다."""
    error = AppError(code="x", message="무언가 실패했어요.", details={"other": "값"})

    assert _failure_detail(error) == "AppError: 무언가 실패했어요."


@pytest.mark.asyncio
async def test_실패한_job이_상류_원문을_들고_있다() -> None:
    registry = SyncJobRegistry()

    async def run(_on_progress):
        raise SupabaseRepositoryError("HTTP 400: 23514 - places_active_state_valid")

    job = registry.start({"area_code": "11", "district_code": "140"}, run)
    for _ in range(100):
        if job.status != "running":
            break
        await asyncio.sleep(0.01)

    assert job.status == "failed"
    assert job.error is not None
    # 화면이 job.error를 그대로 보여주므로 여기에 원인이 있어야 한다.
    assert "places_active_state_valid" in job.error
    registry.reset()
