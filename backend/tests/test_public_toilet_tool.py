"""근처 공중화장실 Tool의 거리·개방 판정과 조회 상한을 검증한다.

상한 회귀 테스트가 있는 이유: 처음 상한을 60으로 뒀을 때 인사동에서 55m 24시간
화장실이 빠지고 230m가 1순위로 나왔다. 저장소 질의에 거리 정렬이 없어(PostGIS 미사용)
상한에 걸리면 박스 안 임의의 일부만 오기 때문이다.
"""

from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.domain.models import PublicToilet
from app.repositories.fake_public_toilet import FakePublicToiletRepository
from app.tools.contracts import ToolStatus
from app.tools.public_toilet import GetPublicToiletTool, PublicToiletQuery

_KST = ZoneInfo("Asia/Seoul")
_ORIGIN = (37.57390, 126.98520)
# 실측 기준: 1km 반경 박스 안 화장실은 중앙값 39곳·최대 126곳이다. 상한이 그보다
# 넉넉해야 밀집 지역에서 가장 가까운 곳이 빠지지 않는다.
_OBSERVED_MAX_IN_BOX = 126


def _toilet(
    toilet_id: str,
    *,
    latitude: float,
    longitude: float,
    hours: str | None = "상시(24시간)|",
    name: str | None = None,
) -> PublicToilet:
    return PublicToilet(
        toilet_id=toilet_id,
        name=name or toilet_id,
        address_new="서울특별시 종로구 인사동길 1",
        address_old=None,
        latitude=latitude,
        longitude=longitude,
        district="종로구",
        tel=None,
        open_type="공공개방|",
        open_hours_raw=hours,
        restroom_status="남자|여자|",
        accessible_status=None,
        amenities=None,
        safety_signs=None,
        location_type="공공시설|",
        manager=None,
    )


def _tool(*toilets: PublicToilet) -> GetPublicToiletTool:
    return GetPublicToiletTool(FakePublicToiletRepository(toilets))


def _query(now: datetime | None = None) -> PublicToiletQuery:
    latitude, longitude = _ORIGIN
    return PublicToiletQuery(
        latitude=latitude,
        longitude=longitude,
        radius_km=1.0,
        now=now or datetime(2026, 9, 5, 2, 7, tzinfo=_KST),
    )


@pytest.mark.asyncio
async def test_nearest_open_toilet_wins_regardless_of_repository_order() -> None:
    """저장소가 임의 순서로 줘도 가장 가까운 열린 곳이 1순위여야 한다.

    더블이 삽입 순서를 그대로 주므로, 가장 가까운 곳을 마지막에 넣어 정렬 책임이
    Tool에 있는지 확인한다.
    """

    latitude, longitude = _ORIGIN
    result = await _tool(
        _toilet("far", latitude=latitude + 0.005, longitude=longitude),
        _toilet("mid", latitude=latitude + 0.002, longitude=longitude),
        _toilet("nearest", latitude=latitude + 0.0002, longitude=longitude),
    ).execute(_query())

    assert result.status is ToolStatus.SUCCESS
    assert [item.toilet.toilet_id for item in result.toilets] == ["nearest", "mid", "far"]


@pytest.mark.asyncio
async def test_dense_area_does_not_drop_the_nearest_toilet() -> None:
    """밀집 지역(실측 최대 126곳)에서도 가장 가까운 곳이 상한에 잘려 나가면 안 된다.

    상한이 실측 밀도보다 작으면 이 테스트가 실패한다 — 가장 가까운 곳을 목록
    마지막에 두었으므로, 앞에서 잘리면 결과에서 사라진다.
    """

    latitude, longitude = _ORIGIN
    # 박스 안에 실측 최대보다 많은 곳을 채운다. 전부 반지름 안(약 220m 이내)이다.
    crowd = [
        _toilet(f"crowd-{index}", latitude=latitude + 0.0018, longitude=longitude + 0.00001 * index)
        for index in range(_OBSERVED_MAX_IN_BOX + 10)
    ]
    nearest = _toilet("nearest", latitude=latitude + 0.0002, longitude=longitude)

    result = await _tool(*crowd, nearest).execute(_query())

    assert result.toilets[0].toilet.toilet_id == "nearest"


@pytest.mark.asyncio
async def test_open_now_outranks_a_closer_closed_toilet() -> None:
    latitude, longitude = _ORIGIN
    result = await _tool(
        # 더 가깝지만 새벽에는 닫혀 있다.
        _toilet(
            "closer-closed",
            latitude=latitude + 0.0002,
            longitude=longitude,
            hours="기타|10:30~20:30",
        ),
        _toilet("farther-open", latitude=latitude + 0.002, longitude=longitude),
    ).execute(_query())

    assert [item.toilet.toilet_id for item in result.toilets] == [
        "farther-open",
        "closer-closed",
    ]
    assert result.toilets[0].open_now is True
    assert result.toilets[1].open_now is False


@pytest.mark.asyncio
async def test_unknown_hours_rank_between_open_and_closed() -> None:
    """개방시간을 못 읽은 곳은 열린 곳 뒤·닫힌 곳 앞이다 — 가능성은 있으니까."""

    latitude, longitude = _ORIGIN
    result = await _tool(
        _toilet(
            "closed", latitude=latitude + 0.0001, longitude=longitude, hours="기타|10:30~20:30"
        ),
        _toilet(
            "unknown",
            latitude=latitude + 0.0003,
            longitude=longitude,
            hours="정시(영업시작~종료)",
        ),
        _toilet("open", latitude=latitude + 0.0005, longitude=longitude),
    ).execute(_query())

    assert [item.toilet.toilet_id for item in result.toilets] == ["open", "unknown", "closed"]
    assert result.toilets[1].open_now is None


@pytest.mark.asyncio
async def test_toilets_outside_radius_are_dropped() -> None:
    # 더블은 박스처럼 반지름보다 넓게 주므로, 걸러내는 것은 Tool의 몫이다.
    latitude, longitude = _ORIGIN
    result = await _tool(
        _toilet("inside", latitude=latitude + 0.002, longitude=longitude),
        _toilet("outside", latitude=latitude + 0.012, longitude=longitude),
    ).execute(_query())

    assert [item.toilet.toilet_id for item in result.toilets] == ["inside"]


@pytest.mark.asyncio
async def test_empty_result_is_no_data_not_error() -> None:
    result = await _tool().execute(_query())

    assert result.status is ToolStatus.NO_DATA
    assert result.error is None


@pytest.mark.asyncio
async def test_hitting_the_fetch_limit_logs_a_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """상한에 닿으면 답은 주되 상한을 올릴 신호를 남긴다."""

    latitude, longitude = _ORIGIN
    crowd = [
        _toilet(f"crowd-{index}", latitude=latitude + 0.001, longitude=longitude + 0.00001 * index)
        for index in range(600)
    ]

    with caplog.at_level(logging.WARNING, logger="app.tools.public_toilet"):
        result = await _tool(*crowd).execute(_query())

    assert result.status is ToolStatus.SUCCESS
    assert any("상한" in record.message for record in caplog.records)
