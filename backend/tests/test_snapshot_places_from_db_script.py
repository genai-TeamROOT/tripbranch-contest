"""DB에서 스냅샷 CSV를 만드는 스크립트 테스트.

스냅샷이 없는 구는 첫 대조에서 전량이 신규로 잡혀, 이미 DB에 있는 장소에
detailIntro2를 한 번씩 더 쓴다. 그 낭비를 막으려고 DB로 기준을 세우는 경로다.
"""

from __future__ import annotations

from datetime import datetime

import httpx
import pytest

from app.repositories.supabase_places import SupabasePlaceRepository
from app.services.place_snapshot import (
    KST,
    SNAPSHOT_COLUMNS,
    load_snapshot,
    snapshot_rows_from_db,
)
from scripts.snapshot_places_from_db import resolve_date


def _db_row(content_id: str, **overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "content_id": content_id,
        "content_type_id": "12",
        "title": f"장소 {content_id}",
        "address": "서울특별시 용산구",
        "latitude": 37.5,
        "longitude": 127.0,
        "area_code": "11",
        "district_code": "170",
        "lcls_systm1": "VE",
        "lcls_systm2": "VE01",
        "lcls_systm3": "VE010100",
        "source_modified_at": "2026-08-21T03:00:00+00:00",
        "first_image_url": None,
        "thumbnail_url": None,
        "list_fetched_at": "2026-08-21T03:59:09+00:00",
    }
    row.update(overrides)
    return row


def test_db_rows_become_snapshot_rows_with_empty_string_for_null() -> None:
    """None은 빈 문자열로 쓴다 — API 스냅샷이 비어 있는 값을 그렇게 남긴다."""
    rows = snapshot_rows_from_db([_db_row("1")])

    assert list(rows["1"]) == list(SNAPSHOT_COLUMNS)
    assert rows["1"]["first_image_url"] == ""
    assert rows["1"]["latitude"] == "37.5"


def test_snapshot_written_from_db_rows_reloads(tmp_path) -> None:
    """쓴 파일이 대조 쪽 로더로 그대로 읽혀야 기준으로 쓸 수 있다."""
    from app.services.place_snapshot import write_snapshot

    path = tmp_path / "snapshot.csv"
    write_snapshot(snapshot_rows_from_db([_db_row("1"), _db_row("2")]), path)

    assert set(load_snapshot(path)) == {"1", "2"}


def test_date_comes_from_the_last_list_fetch_not_today() -> None:
    """파일명 날짜는 자료가 실제로 언제 것인지를 말해야 한다.

    오늘 날짜를 박으면 두 달 전 DB 상태에 오늘 날짜가 붙어, 나중에 그 파일을
    "오늘 조회한 목록"으로 오해한다.
    """
    rows = snapshot_rows_from_db(
        [
            _db_row("1", list_fetched_at="2026-08-20T07:25:21+00:00"),
            _db_row("2", list_fetched_at="2026-08-21T03:59:09+00:00"),
        ]
    )

    assert resolve_date(rows, None).strftime("%Y%m%d") == "20260821"
    assert resolve_date(rows, "20260101").strftime("%Y%m%d") == "20260101"


def test_date_falls_back_to_today_when_column_is_empty() -> None:
    rows = snapshot_rows_from_db([_db_row("1", list_fetched_at=None)])

    assert resolve_date(rows, None).date() == datetime.now(KST).date()


@pytest.mark.asyncio
async def test_region_rows_query_filters_district_and_inactive() -> None:
    """구를 안 걸면 다른 구 장소가 스냅샷에 섞이고, 비활성을 넣으면 매번 삭제로 잡힌다."""
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=[_db_row("1")])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        repository = SupabasePlaceRepository(
            "https://project.supabase.co/", "sb_secret_test", client
        )
        rows = await repository.list_region_place_rows(
            "11", "170", SNAPSHOT_COLUMNS
        )

    assert len(rows) == 1
    params = captured[0].url.params
    assert params["area_code"] == "eq.11"
    assert params["district_code"] == "eq.170"
    assert params["is_active"] == "eq.true"


@pytest.mark.asyncio
async def test_region_rows_can_include_inactive_when_asked() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=[])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        repository = SupabasePlaceRepository(
            "https://project.supabase.co/", "sb_secret_test", client
        )
        await repository.list_region_place_rows(
            "11", "170", SNAPSHOT_COLUMNS, active_only=False
        )

    assert "is_active" not in captured[0].url.params
