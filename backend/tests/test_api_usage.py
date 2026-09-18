"""외부 API 호출량 집계(app.observability.api_usage) 테스트."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest

from app.config import settings
from app.observability import api_usage
from app.observability.api_usage import (
    UNKNOWN_PROVIDER,
    ApiUsageRegistry,
    MeteredTransport,
    classify,
    provider_modes,
)

_KST = ZoneInfo("Asia/Seoul")


@pytest.fixture(autouse=True)
def _clean_registry():
    api_usage.reset_usage()
    yield
    api_usage.reset_usage()


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=MeteredTransport(httpx.MockTransport(handler))
    )


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (
            "https://apis.data.go.kr/B551011/KorService2/areaBasedList2?ServiceKey=SECRET",
            ("tour_api", "areaBasedList2"),
        ),
        (
            "https://apis.data.go.kr/B551011/KorService2/detailIntro2?contentId=1",
            ("tour_api", "detailIntro2"),
        ),
        (
            "https://apis.data.go.kr/B551011/TatsCnctrRateService/tatsCnctrRatedList",
            ("concentration", "tatsCnctrRatedList"),
        ),
        (
            "https://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getUltraSrtFcst",
            ("kma_weather", "getUltraSrtFcst"),
        ),
        (
            "https://apis.data.go.kr/B090041/openapi/service/SpcdeInfoService/getRestDeInfo",
            ("kasi_holiday", "getRestDeInfo"),
        ),
        (
            "https://maps.apigw.ntruss.com/map-geocode/v2/geocode?query=x",
            ("naver_geocoding", "geocode"),
        ),
        (
            "https://maps.apigw.ntruss.com/map-direction/v1/driving?start=1,2",
            ("naver_driving", "driving"),
        ),
        (
            "https://naverapihub.apigw.ntruss.com/search/v1/local?query=x",
            ("naver_local_search", "local"),
        ),
        (
            "https://dapi.kakao.com/v2/routing/walk?start_x=127.1&start_y=37.4",
            ("kakao_map", "walk"),
        ),
    ],
)
def test_classify_maps_known_hosts(url: str, expected: tuple[str, str]) -> None:
    parsed = httpx.URL(url)
    assert classify(parsed.host, parsed.path) == expected


def test_classify_splits_naver_maps_host_by_path_prefix() -> None:
    """자동차 경로와 지오코딩은 호스트가 같아도 provider가 갈려야 한다(TP-131).

    operation만 갈리고 provider가 하나로 묶이면 Ops 패널에서 호출 수·실패 수·
    지연이 두 API의 합으로 보여 어느 쪽이 얼마나 나갔는지 읽을 수 없다.
    """
    driving = classify("maps.apigw.ntruss.com", "/map-direction/v1/driving")
    geocode = classify("maps.apigw.ntruss.com", "/map-geocode/v2/geocode")
    assert driving == ("naver_driving", "driving")
    assert geocode == ("naver_geocoding", "geocode")
    assert driving[0] != geocode[0]


def test_classify_marks_unmapped_naver_maps_path_as_unknown() -> None:
    """접두사 표에 없는 네이버 지도 API는 기존 provider에 붙이지 않는다.

    붙여 두면 새 API를 추가했을 때 남의 집계에 조용히 섞인다 — TP-131이 바로
    그 사례였다. unknown으로 떨어뜨리면 패널에 path 원문이 그대로 보인다.
    """
    provider, operation = classify(
        "maps.apigw.ntruss.com", "/map-reversegeocode/v2/gc"
    )
    assert provider == UNKNOWN_PROVIDER
    assert operation == "maps.apigw.ntruss.com/map-reversegeocode/v2/gc"


def test_classify_marks_unknown_host_instead_of_dropping() -> None:
    parsed = httpx.URL("https://example.test/some/path")
    provider, operation = classify(parsed.host, parsed.path)
    assert provider == UNKNOWN_PROVIDER
    assert operation == "example.test/some/path"


def test_classify_splits_supabase_tables_and_rpc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "supabase_url", "https://proj.supabase.co")
    assert classify("proj.supabase.co", "/rest/v1/places") == ("supabase", "places")
    assert classify("proj.supabase.co", "/rest/v1/rpc/try_acquire_place_sync_lock") == (
        "supabase",
        "rpc/try_acquire_place_sync_lock",
    )


@pytest.mark.asyncio
async def test_metered_transport_never_stores_query_string() -> None:
    """ServiceKey가 실린 쿼리는 어떤 형태로도 집계에 남지 않아야 한다."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    async with _client(handler) as client:
        await client.get(
            "https://apis.data.go.kr/B551011/KorService2/areaBasedList2",
            params={"ServiceKey": "SUPER-SECRET-KEY", "areaCode": "11"},
        )

    snapshot = api_usage.get_usage_snapshot()
    assert "SUPER-SECRET-KEY" not in repr(snapshot)
    entry = snapshot["entries"][0]
    assert (entry["provider"], entry["operation"]) == ("tour_api", "areaBasedList2")
    assert entry["count"] == 1
    assert entry["ok"] == 1


@pytest.mark.asyncio
async def test_metered_transport_counts_http_error_as_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    async with _client(handler) as client:
        await client.get(
            "https://apis.data.go.kr/B551011/KorService2/detailIntro2"
        )

    entry = api_usage.get_usage_snapshot()["entries"][0]
    assert (entry["count"], entry["ok"], entry["error"]) == (1, 0, 1)
    assert entry["last_status"] == "500"


@pytest.mark.asyncio
async def test_metered_transport_counts_timeout() -> None:
    """타임아웃도 상대 서버의 일일 한도는 이미 소모했을 수 있어 반드시 센다.

    응답 event_hook만 걸면 이 경로가 통째로 누락돼 게이지가 실제보다 낮게 보인다.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out", request=request)

    async with _client(handler) as client:
        with pytest.raises(httpx.ConnectTimeout):
            await client.get(
                "https://apis.data.go.kr/B551011/KorService2/detailIntro2"
            )

    entry = api_usage.get_usage_snapshot()["entries"][0]
    assert (entry["count"], entry["ok"], entry["error"]) == (1, 0, 1)
    assert entry["last_status"] == "ConnectTimeout"


def test_daily_bucket_splits_on_kst_midnight() -> None:
    registry = ApiUsageRegistry()
    before = datetime(2026, 8, 9, 23, 59, 59, tzinfo=_KST)
    after = before + timedelta(seconds=2)

    registry.record("tour_api", "detailIntro2", ok=True, latency_ms=10, status="200", at=before)
    registry.record("tour_api", "detailIntro2", ok=True, latency_ms=10, status="200", at=after)

    assert registry.total_count("tour_api", "detailIntro2") == 2
    assert registry.daily_count("tour_api", "detailIntro2", before.date()) == 1
    assert registry.daily_count("tour_api", "detailIntro2", after.date()) == 1


def test_snapshot_reports_daily_limit_for_quota_bound_providers() -> None:
    api_usage.record_call("tour_api", "detailIntro2", ok=True, latency_ms=5, status="200")
    api_usage.record_call("gemini", "gemini-2.5-flash", ok=True, latency_ms=5, status="ok")

    limits = {
        entry["operation"]: entry["daily_limit"]
        for entry in api_usage.get_usage_snapshot()["entries"]
    }
    assert limits["detailIntro2"] == settings.tour_api_daily_call_limit
    # Gemini는 data.go.kr 오퍼레이션 한도 대상이 아니라 게이지를 그리지 않는다.
    assert limits["gemini-2.5-flash"] is None


def test_snapshot_always_carries_provider_modes() -> None:
    """Fake provider는 외부 HTTP를 아예 보내지 않아 집계표가 비어 있는 게 정상이다.

    그 빈 표를 "트래픽이 없었다"로 오독하면 fake로 뜬 서버를 real로 착각한다
    (D-042). 모드를 스냅샷에 항상 실어 화면이 경고를 띄울 수 있게 한다.
    """
    snapshot = api_usage.get_usage_snapshot()
    assert snapshot["entries"] == []
    assert snapshot["provider_modes"] == provider_modes()
    assert set(snapshot["provider_modes"]) == {
        "llm",
        "place",
        "geocoding",
        "local_search",
        "weather",
        "concentration",
        "holiday",
        "travel_route",
    }
    assert all(mode in {"fake", "real"} for mode in snapshot["provider_modes"].values())


def test_reset_clears_counters_and_restarts_window() -> None:
    api_usage.record_call("tour_api", "detailIntro2", ok=True, latency_ms=5, status="200")
    assert api_usage.get_usage_snapshot()["totals"]["count"] == 1

    api_usage.reset_usage()
    snapshot = api_usage.get_usage_snapshot()
    assert snapshot["totals"]["count"] == 0
    assert snapshot["entries"] == []
