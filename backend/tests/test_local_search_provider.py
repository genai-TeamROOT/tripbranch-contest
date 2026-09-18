from __future__ import annotations

import httpx
import pytest

from app.errors import AppError
from app.providers.contracts import ProviderSource, ProviderStatus
from app.providers.local_search import RealLocalSearchProvider


@pytest.mark.asyncio
async def test_real_local_search_maps_place_name_address_and_wgs84_coordinates() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/search/v1/local"
        assert request.url.params["query"] == "쌈지길"
        assert request.url.params["display"] == "5"
        assert request.headers["x-ncp-apigw-api-key-id"] == "test-id"
        assert request.headers["x-ncp-apigw-api-key"] == "test-secret"
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "title": "<b>쌈지길</b>",
                        "category": "쇼핑,유통>전통시장",
                        "address": "서울특별시 종로구 관훈동 38",
                        "roadAddress": "서울특별시 종로구 인사동길 44",
                        # 실제 응답 형식: WGS84 × 10^7 정수 (2026-08-03 실측)
                        "mapx": "1269848674",
                        "mapy": "375743062",
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = RealLocalSearchProvider("test-id", "test-secret", client)
        result = await provider.search_places_by_name("쌈지길")

    assert result.metadata.source is ProviderSource.NAVER_LOCAL_SEARCH
    assert result.metadata.status is ProviderStatus.SUCCESS
    assert result.data[0].name == "쌈지길"
    assert result.data[0].longitude == pytest.approx(126.9848674)
    assert result.data[0].latitude == pytest.approx(37.5743062)


@pytest.mark.asyncio
async def test_real_local_search_converts_http_failure_to_app_error() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(401, request=request))
    ) as client:
        provider = RealLocalSearchProvider("test-id", "test-secret", client)
        with pytest.raises(AppError, match="연동에 문제가"):
            await provider.search_places_by_name("쌈지길")


@pytest.mark.asyncio
async def test_real_local_search_skips_items_without_coordinates() -> None:
    """좌표가 비어 있으면 스케일 변환을 시도하지 않고 None으로 남긴다."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"items": [{"title": "좌표없음", "mapx": "", "mapy": None}]},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = RealLocalSearchProvider("test-id", "test-secret", client)
        result = await provider.search_places_by_name("좌표없음")

    assert result.data[0].latitude is None
    assert result.data[0].longitude is None
