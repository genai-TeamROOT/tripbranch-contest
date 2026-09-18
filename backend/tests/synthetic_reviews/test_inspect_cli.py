from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence

import httpx
import pytest

from app.config import Settings
from app.repositories.supabase_places import SupabasePlaceRepository
from scripts.inspect_synthetic_review_plans import (
    INSPECTION_COLUMNS,
    build_parser,
    build_place_report,
    inspect,
)


def _row(content_id: str = "126508") -> dict[str, object]:
    return {
        "content_id": content_id,
        "content_type_id": "14",
        "title": "테스트 문화시설",
        "operating_hours_raw": "09:00~18:00",
        "rest_date_raw": "매주 화요일",
        "parking_info_raw": "불가능",
        "parking_fee_raw": "무료",
        "use_fee_raw": "3,000원",
        "discount_info_raw": None,
        "info_center_raw": "02-000-0000",
        "baby_carriage_raw": "없음",
        "pet_raw": "불가",
        "credit_card_raw": "가능",
        "restroom_raw": "있음",
    }


class _Repository:
    def __init__(self, rows: list[Mapping[str, object]]) -> None:
        self.rows = rows
        self.region_call: tuple[object, ...] | None = None
        self.id_call: tuple[object, ...] | None = None

    async def list_region_place_rows(
        self,
        area_code: str,
        district_code: str,
        columns: Sequence[str],
        *,
        active_only: bool = True,
    ) -> list[Mapping[str, object]]:
        self.region_call = (area_code, district_code, tuple(columns), active_only)
        return self.rows

    async def list_active_place_rows_by_ids(
        self, content_ids: Sequence[str], columns: Sequence[str]
    ) -> list[Mapping[str, object]]:
        self.id_call = (tuple(content_ids), tuple(columns))
        requested = set(content_ids)
        return [row for row in self.rows if str(row["content_id"]) in requested]


def _args(**overrides: object) -> argparse.Namespace:
    values = {
        "place_id": [],
        "limit": 5,
        "area_code": None,
        "district_code": None,
        "max_personas": 4,
        "reviews_per_place": None,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def test_장소_한_건을_페르소나와_리뷰_계획으로_변환한다() -> None:
    report = build_place_report(_row(), max_personas=4, review_count=None)

    assert report["contentId"] == "126508"
    assert report["title"] == "테스트 문화시설"
    # review_count를 생략하면 페르소나마다 리뷰 한 건씩이다.
    assert len(report["personas"]) == 4
    assert len(report["reviews"]) == 4
    sentiments = [review["sentiment"]["sentiment"] for review in report["reviews"]]
    assert "NEGATIVE" in sentiments
    assert "NEUTRAL" in sentiments


@pytest.mark.asyncio
async def test_기본은_설정의_지역을_읽고_limit을_적용한다() -> None:
    repository = _Repository([_row("1"), _row("2"), _row("3")])
    settings = Settings(place_sync_area_code="11", place_sync_district_code="110")

    reports = await inspect(_args(limit=2), settings, repository)

    assert [report["contentId"] for report in reports] == ["1", "2"]
    assert repository.region_call == ("11", "110", INSPECTION_COLUMNS, True)
    assert repository.id_call is None


@pytest.mark.asyncio
async def test_place_id는_지역_조회_없이_요청_순서로_읽는다() -> None:
    repository = _Repository([_row("2"), _row("1")])

    reports = await inspect(
        _args(place_id=["2", "1"]), Settings(), repository
    )

    assert [report["contentId"] for report in reports] == ["2", "1"]
    assert repository.id_call == (("2", "1"), INSPECTION_COLUMNS)
    assert repository.region_call is None


@pytest.mark.asyncio
async def test_없는_place_id는_조용히_누락하지_않는다() -> None:
    repository = _Repository([_row("1")])

    with pytest.raises(ValueError, match="missing"):
        await inspect(_args(place_id=["1", "missing"]), Settings(), repository)


def test_cli가_검증_옵션을_파싱한다() -> None:
    args = build_parser().parse_args(
        [
            "--place-id",
            "1",
            "--place-id",
            "2",
            "--limit",
            "2",
            "--max-personas",
            "5",
            "--reviews-per-place",
            "5",
        ]
    )

    assert args.place_id == ["1", "2"]
    assert args.limit == 2
    assert args.max_personas == 5
    assert args.reviews_per_place == 5


@pytest.mark.asyncio
async def test_id_조회는_활성_필터와_요청_순서를_보존한다() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=[_row("1"), _row("2")])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        repository = SupabasePlaceRepository(
            "https://project.supabase.co", "sb_secret_test", client
        )
        rows = await repository.list_active_place_rows_by_ids(
            ["2", "1", "2"], INSPECTION_COLUMNS
        )

    assert [row["content_id"] for row in rows] == ["2", "1"]
    assert captured[0].url.params["content_id"] == 'in.("2","1")'
    assert captured[0].url.params["is_active"] == "eq.true"
