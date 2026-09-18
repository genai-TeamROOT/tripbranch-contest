"""외부 API 요청·응답 캡처(app.observability.api_exchanges) 테스트."""

from __future__ import annotations

import httpx
import pytest

from app.config import settings
from app.observability import api_exchanges
from app.observability.api_exchanges import (
    MASK,
    MAX_BODY_BYTES,
    get_recorder,
    mask_headers,
    mask_query,
)
from app.observability.api_usage import MeteredTransport


@pytest.fixture(autouse=True)
def _clean_recorder():
    get_recorder().set_enabled(False)
    yield
    get_recorder().set_enabled(False)


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=MeteredTransport(httpx.MockTransport(handler)))


def test_capture_is_off_by_default() -> None:
    """MeteredTransport는 배포 환경에서도 돈다. 기본이 켜져 있으면 운영 메모리를 먹는다."""
    assert get_recorder().enabled is False
    assert get_recorder().snapshot()["items"] == []


@pytest.mark.asyncio
async def test_nothing_recorded_while_capture_is_off() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    async with _client(handler) as client:
        await client.get("https://apis.data.go.kr/B551011/KorService2/detailIntro2")

    assert get_recorder().snapshot()["items"] == []


@pytest.mark.asyncio
async def test_records_request_and_response_when_enabled() -> None:
    get_recorder().set_enabled(True)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"response": {"body": {"totalCount": 3}}})

    async with _client(handler) as client:
        response = await client.get(
            "https://apis.data.go.kr/B551011/KorService2/areaBasedList2",
            params={"serviceKey": "SUPER-SECRET-KEY", "areaCd": "11", "pageNo": "1"},
        )

    # 캡처가 응답을 미리 읽어도 호출자는 그대로 본문을 받아야 한다.
    assert response.json() == {"response": {"body": {"totalCount": 3}}}

    item = get_recorder().snapshot()["items"][0]
    assert (item["provider"], item["operation"]) == ("tour_api", "areaBasedList2")
    assert item["method"] == "GET"
    assert item["status"] == "200"
    assert item["ok"] is True
    assert "totalCount" in item["response_body"]


@pytest.mark.asyncio
async def test_service_key_never_appears_in_snapshot() -> None:
    get_recorder().set_enabled(True)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    async with _client(handler) as client:
        await client.get(
            "https://apis.data.go.kr/B551011/KorService2/areaBasedList2",
            params={"serviceKey": "SUPER-SECRET-KEY", "areaCd": "11"},
        )

    snapshot = get_recorder().snapshot()
    assert "SUPER-SECRET-KEY" not in repr(snapshot)
    item = snapshot["items"][0]
    # 이름은 남기고 값만 가린다 — 무엇을 불렀는지는 읽혀야 디버깅이 된다.
    assert item["query"]["serviceKey"] == MASK
    assert item["query"]["areaCd"] == "11"
    # URL 자체에도 쿼리가 남으면 안 된다.
    assert "?" not in item["url"]


@pytest.mark.asyncio
async def test_supabase_api_key_header_is_masked() -> None:
    get_recorder().set_enabled(True)
    monkey_url = "https://project.supabase.co"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    async with _client(handler) as client:
        await client.get(
            f"{monkey_url}/rest/v1/places",
            headers={"apikey": "sb_secret_value", "Accept": "application/json"},
        )

    snapshot = get_recorder().snapshot()
    assert "sb_secret_value" not in repr(snapshot)
    item = snapshot["items"][0]
    assert item["request_headers"]["apikey"] == MASK
    assert item["request_headers"]["accept"] == "application/json"


def test_naver_auth_headers_are_masked() -> None:
    headers = httpx.Headers(
        {
            "x-ncp-apigw-api-key-id": "id-value",
            "x-ncp-apigw-api-key": "key-value",
            "accept": "application/json",
        }
    )
    masked = mask_headers(headers)
    assert masked["x-ncp-apigw-api-key-id"] == MASK
    assert masked["x-ncp-apigw-api-key"] == MASK
    assert masked["accept"] == "application/json"


def test_unknown_provider_masks_every_query_value() -> None:
    """분류되지 않은 host는 값 전부를 가린다.

    denylist로 알려진 키만 막으면 새 provider가 낯선 이름으로 자격증명을 실어올 때
    조용히 새어나간다. 값이 한 번 화면에 뜨면 스크린샷으로 옮겨가 되돌릴 수 없다.
    """
    params = httpx.QueryParams({"token": "leak-me", "harmless": "1"})
    assert mask_query("unknown", params) == {"token": MASK, "harmless": MASK}


def test_supabase_query_values_are_kept() -> None:
    """Supabase는 헤더로 인증한다 — 쿼리(PostgREST 필터)는 가릴 이유가 없다."""
    params = httpx.QueryParams({"select": "content_id", "content_id": "in.(1,2)"})
    assert mask_query("supabase", params) == {
        "select": "content_id",
        "content_id": "in.(1,2)",
    }


def test_kakao_map_route_coordinates_are_kept() -> None:
    """카카오맵은 헤더로 인증하므로 경로 좌표 쿼리는 캡처에서 확인할 수 있다."""
    params = httpx.QueryParams({"start_x": "127.1", "start_y": "37.4"})

    assert mask_query("kakao_map", params) == {
        "start_x": "127.1",
        "start_y": "37.4",
    }


def test_naver_driving_route_coordinates_are_kept() -> None:
    """자동차 경로도 x-ncp-* 헤더로 인증하므로 좌표 쿼리는 가리지 않는다.

    provider가 지오코딩에서 갈려 나오면서(TP-131) `_QUERY_SAFE_PROVIDERS`에
    새 이름을 넣지 않으면, 집계만 고치고 원문 패널의 좌표는 전부 마스킹된다.
    """
    params = httpx.QueryParams({"start": "127.1,37.4", "goal": "126.9,37.5"})

    assert mask_query("naver_driving", params) == {
        "start": "127.1,37.4",
        "goal": "126.9,37.5",
    }


@pytest.mark.asyncio
async def test_large_body_is_truncated() -> None:
    get_recorder().set_enabled(True)
    payload = "x" * (MAX_BODY_BYTES + 500)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=payload)

    async with _client(handler) as client:
        response = await client.get(
            "https://apis.data.go.kr/B551011/KorService2/areaBasedList2"
        )

    # 잘라 보관하더라도 호출자에게는 원본이 온다.
    assert len(response.text) == len(payload)
    item = get_recorder().snapshot()["items"][0]
    assert item["response_body_truncated"] is True
    assert len(item["response_body"]) == MAX_BODY_BYTES
    assert item["response_bytes"] == len(payload)


@pytest.mark.asyncio
async def test_timeout_is_recorded_without_url_details() -> None:
    get_recorder().set_enabled(True)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out", request=request)

    async with _client(handler) as client:
        with pytest.raises(httpx.ConnectTimeout):
            await client.get(
                "https://apis.data.go.kr/B551011/KorService2/detailIntro2",
                params={"serviceKey": "SUPER-SECRET-KEY"},
            )

    snapshot = get_recorder().snapshot()
    assert "SUPER-SECRET-KEY" not in repr(snapshot)
    item = snapshot["items"][0]
    assert item["error"] == "ConnectTimeout"
    assert item["ok"] is False
    assert item["response_body"] is None


@pytest.mark.asyncio
async def test_disabling_capture_drops_buffered_bodies() -> None:
    """끄면 남아 있던 본문도 버린다 — 남아 있으면 끈 의미가 없다."""
    get_recorder().set_enabled(True)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"a": 1})

    async with _client(handler) as client:
        await client.get("https://apis.data.go.kr/B551011/KorService2/detailIntro2")

    assert len(get_recorder().snapshot()["items"]) == 1
    get_recorder().set_enabled(False)
    assert get_recorder().snapshot()["items"] == []


@pytest.mark.asyncio
async def test_ring_buffer_keeps_only_recent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api_exchanges, "MAX_EXCHANGES", 3)
    recorder = api_exchanges.ExchangeRecorder()
    monkeypatch.setattr(api_exchanges, "_recorder", recorder)
    recorder.set_enabled(True)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"n": request.url.params.get("pageNo")})

    async with _client(handler) as client:
        for page in range(1, 6):
            await client.get(
                "https://apis.data.go.kr/B551011/KorService2/areaBasedList2",
                params={"pageNo": str(page)},
            )

    items = recorder.snapshot()["items"]
    # 5건을 보냈지만 용량만큼만 남고 오래된 것부터 밀려난다.
    assert len(items) == 3
    # 최신이 위로 온다.
    assert [item["query"]["pageNo"] for item in items] == ["5", "4", "3"]


def test_dev_exchange_routes_toggle_capture() -> None:
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app()) as client:
        assert client.get("/api/dev/exchanges").json()["enabled"] is False
        enabled = client.post(
            "/api/dev/exchanges/capture", json={"enabled": True}
        ).json()
        assert enabled["enabled"] is True
        assert enabled["capacity"] == api_exchanges.MAX_EXCHANGES
        disabled = client.post(
            "/api/dev/exchanges/capture", json={"enabled": False}
        ).json()
        assert disabled["enabled"] is False


def test_dev_exchange_routes_absent_outside_local(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi.testclient import TestClient

    from app.main import create_app

    monkeypatch.setattr(settings, "app_env", "production")
    with TestClient(create_app()) as client:
        # 켤 방법이 없어야 배포 환경에서 본문 버퍼링이 시작될 수 없다.
        assert client.post("/api/dev/exchanges/capture", json={"enabled": True}).status_code == 404
