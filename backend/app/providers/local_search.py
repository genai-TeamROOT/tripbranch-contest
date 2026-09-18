"""Naver API Hub 지역 검색 Provider 구현."""

from __future__ import annotations

import html
import logging
import re

import httpx

from app.domain.models import LocalSearchPlace
from app.errors import AppError, ProviderTimeoutError, ProviderUnavailableError
from app.providers.contracts import ProviderResult, ProviderSource, ProviderStatus, provider_result
from app.providers.upstream_errors import upstream_error_detail

logger = logging.getLogger(__name__)

_LOCAL_SEARCH_URL = "https://naverapihub.apigw.ntruss.com/search/v1/local"
_HTML_TAG_PATTERN = re.compile(r"<[^>]+>")


def _clean_text(value: object | None) -> str | None:
    if value is None:
        return None
    cleaned = _HTML_TAG_PATTERN.sub("", html.unescape(str(value))).strip()
    return cleaned or None


# Local Search의 mapx/mapy는 WGS84 좌표에 10^7을 곱한 정수다(예: 안국역 위도
# 375765389 → 37.5765389). Geocoding은 실수를 그대로 주므로 이 Provider에서만
# 되돌린다 — 나누지 않으면 좌표계 검증(위도 ±90)에서 걸리거나 거리 계산이 깨진다.
_LOCAL_SEARCH_COORDINATE_SCALE = 10**7


def _coordinate(value: object | None) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value)) / _LOCAL_SEARCH_COORDINATE_SCALE
    except (TypeError, ValueError):
        return None


def map_local_search_item(item: object) -> LocalSearchPlace | None:
    """Naver 지역 검색 원본 한 건을 공통 장소 후보로 정규화한다."""
    if not isinstance(item, dict):
        return None
    name = _clean_text(item.get("title"))
    if name is None:
        return None
    return LocalSearchPlace(
        name=name,
        address=_clean_text(item.get("address")),
        road_address=_clean_text(item.get("roadAddress")),
        category=_clean_text(item.get("category")),
        longitude=_coordinate(item.get("mapx")),
        latitude=_coordinate(item.get("mapy")),
    )


class FakeLocalSearchProvider:
    """일반 테스트에서 외부 호출 없이 빈 검색 결과를 반환한다."""

    async def search_places_by_name(
        self, query: str, *, display: int = 5
    ) -> ProviderResult[tuple[LocalSearchPlace, ...]]:
        return provider_result(
            (),
            source=ProviderSource.FAKE_LOCAL_SEARCH,
            status=ProviderStatus.NO_DATA,
        )


class RealLocalSearchProvider:
    """Naver API Hub Local Search를 사용하는 실제 장소명 검색 Provider."""

    def __init__(
        self,
        api_key_id: str,
        api_key: str,
        client: httpx.AsyncClient,
        timeout_seconds: float = 10.0,
    ) -> None:
        self._api_key_id = api_key_id
        self._api_key = api_key
        self._client = client
        self._timeout_seconds = timeout_seconds

    async def search_places_by_name(
        self, query: str, *, display: int = 5
    ) -> ProviderResult[tuple[LocalSearchPlace, ...]]:
        normalized_query = query.strip()
        if not normalized_query:
            raise AppError(code="invalid_request", message="장소명을 입력해주세요.")
        if not 1 <= display <= 5:
            raise ValueError("display는 1 이상 5 이하여야 합니다.")
        headers = {
            "Accept": "application/json",
            "x-ncp-apigw-api-key-id": self._api_key_id,
            "x-ncp-apigw-api-key": self._api_key,
        }
        try:
            response = await self._client.get(
                _LOCAL_SEARCH_URL,
                params={
                    "query": normalized_query,
                    "display": str(display),
                    "sort": "random",
                    "format": "json",
                },
                headers=headers,
                timeout=self._timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.TimeoutException:
            headers.clear()
            logger.error("Naver Local Search 호출 타임아웃")
            raise ProviderTimeoutError("Naver Local Search") from None
        except httpx.HTTPStatusError as exc:
            detail = f"HTTP {exc.response.status_code}, {upstream_error_detail(exc.response)}"
            headers.clear()
            logger.error("Naver Local Search 호출 실패 (%s)", detail)
            raise ProviderUnavailableError(
                "Naver Local Search", detail=detail
            ) from None
        except (httpx.HTTPError, ValueError) as exc:
            headers.clear()
            logger.error("Naver Local Search 호출 실패 (%s)", type(exc).__name__)
            raise ProviderUnavailableError("Naver Local Search") from None

        raw_items = payload.get("items", []) if isinstance(payload, dict) else []
        places = (
            tuple(
                place
                for raw_item in raw_items
                if (place := map_local_search_item(raw_item)) is not None
            )
            if isinstance(raw_items, list)
            else ()
        )
        return provider_result(
            places,
            source=ProviderSource.NAVER_LOCAL_SEARCH,
            status=ProviderStatus.SUCCESS if places else ProviderStatus.NO_DATA,
        )


__all__ = [
    "FakeLocalSearchProvider",
    "RealLocalSearchProvider",
    "map_local_search_item",
]
