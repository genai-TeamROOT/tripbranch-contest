"""카카오맵 대중교통 경로와 직선거리 fallback Provider."""

from __future__ import annotations

import asyncio
import logging
import math

import httpx

from app.domain.travel_route import (
    GeoCoordinate,
    RouteDestination,
    RouteSource,
    RouteStatus,
    TravelMode,
    TravelRoute,
    TravelRouteBatch,
)
from app.geo import haversine_km
from app.providers.contracts import ProviderResult, ProviderSource, ProviderStatus, provider_result
from app.providers.upstream_errors import upstream_error_detail
from app.providers.walking_route import MAX_TRAVEL_ROUTE_DESTINATIONS

logger = logging.getLogger(__name__)

KAKAO_MAP_TRANSIT_ROUTE_URL = "https://dapi.kakao.com/v2/routing/publictraffic"

# 출발 시각 파라미터가 없다. 실측(2026-08-20, 경복궁 기준 7개 구간)에서 동일 요청
# 3회가 완전히 같은 결과를 냈다 — 시간표·배차를 반영하지 않는 결정적 응답이다.
# 그래서 이 Provider는 도보·자동차와 마찬가지로 시각 축을 갖지 않는다(TP-106).


class FakeTransitRouteProvider:
    """카카오 미연동 환경에서 쓰는 직선거리 추정기.

    이 값은 채점에 쓰이지 않는다 — source가 STRAIGHT_LINE_ESTIMATE라
    `scoring._applied_travel_route()`가 걸러낸다. 추정을 실측으로 포장하지
    않으려는 것이므로, 여기 속도를 정밀하게 맞출 이유는 없다.
    """

    def __init__(self, transit_speed_mps: float) -> None:
        if transit_speed_mps <= 0:
            raise ValueError("transit_speed_mps는 0보다 커야 합니다.")
        self._transit_speed_mps = transit_speed_mps

    async def get_routes(
        self,
        origin: GeoCoordinate,
        destinations: tuple[RouteDestination, ...],
        *,
        mode: TravelMode = TravelMode.TRANSIT,
        radius_m: int | None = None,
    ) -> ProviderResult[TravelRouteBatch]:
        _validate_request(destinations, radius_m, mode)
        routes = tuple(
            self._estimate_route(origin, destination, mode) for destination in destinations
        )
        return provider_result(
            TravelRouteBatch(routes=routes),
            source=ProviderSource.FAKE_TRANSIT_ROUTE,
            status=ProviderStatus.SUCCESS if routes else ProviderStatus.NO_DATA,
        )

    def _estimate_route(
        self, origin: GeoCoordinate, destination: RouteDestination, mode: TravelMode
    ) -> TravelRoute:
        distance_m = round(
            haversine_km(
                origin.latitude,
                origin.longitude,
                destination.coordinate.latitude,
                destination.coordinate.longitude,
            )
            * 1000
        )
        return TravelRoute(
            place_id=destination.place_id,
            mode=mode,
            status=RouteStatus.SUCCESS,
            source=RouteSource.STRAIGHT_LINE_ESTIMATE,
            distance_m=distance_m,
            duration_seconds=math.ceil(distance_m / self._transit_speed_mps),
        )


class RealKakaoTransitRouteProvider:
    """카카오디벨로퍼스 대중교통 단건 API를 제한 병렬로 호출하는 Provider."""

    def __init__(
        self,
        api_key: str,
        client: httpx.AsyncClient,
        timeout_seconds: float = 10.0,
        max_concurrency: int = 5,
        # 여러 Provider가 같은 카카오 키를 쓸 때 넘긴다. 도보와 대중교통을 함께
        # 조회하면 인스턴스마다 세마포어를 만드는 동안 동시 요청이 합산돼(5+5)
        # 카카오가 `API limit has been exceeded.`로 대부분 거절한다(2026-09-02
        # 실측). 넘기지 않으면 예전처럼 자기 몫만 제한한다.
        semaphore: asyncio.Semaphore | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("KAKAO_MAP_REST_API_KEY가 필요합니다.")
        if not 1 <= max_concurrency <= 10:
            raise ValueError("max_concurrency는 1 이상 10 이하여야 합니다.")
        self._api_key = api_key
        self._client = client
        self._timeout_seconds = timeout_seconds
        self._max_concurrency = max_concurrency
        self._semaphore = semaphore

    async def get_routes(
        self,
        origin: GeoCoordinate,
        destinations: tuple[RouteDestination, ...],
        *,
        mode: TravelMode = TravelMode.TRANSIT,
        radius_m: int | None = None,
    ) -> ProviderResult[TravelRouteBatch]:
        _validate_request(destinations, radius_m, mode)
        if not destinations:
            return provider_result(
                TravelRouteBatch(routes=()),
                source=ProviderSource.KAKAO_TRANSIT_ROUTE,
                status=ProviderStatus.NO_DATA,
            )

        semaphore = self._semaphore or asyncio.Semaphore(self._max_concurrency)

        async def fetch(destination: RouteDestination) -> TravelRoute:
            async with semaphore:
                return await self._get_route(origin, destination, mode)

        routes = tuple(await asyncio.gather(*(fetch(item) for item in destinations)))
        successful_count = sum(route.status is RouteStatus.SUCCESS for route in routes)
        has_unavailable = any(route.status is RouteStatus.UNAVAILABLE for route in routes)
        status = (
            ProviderStatus.SUCCESS
            if successful_count == len(routes)
            else ProviderStatus.PARTIAL
            if successful_count or has_unavailable
            else ProviderStatus.NO_DATA
        )
        return provider_result(
            TravelRouteBatch(routes=routes),
            source=ProviderSource.KAKAO_TRANSIT_ROUTE,
            status=status,
        )

    async def _get_route(
        self, origin: GeoCoordinate, destination: RouteDestination, mode: TravelMode
    ) -> TravelRoute:
        headers = {"Authorization": f"KakaoAK {self._api_key}"}
        params = {
            "start_x": str(origin.longitude),
            "start_y": str(origin.latitude),
            "end_x": str(destination.coordinate.longitude),
            "end_y": str(destination.coordinate.latitude),
            "input_coord": "WGS84",
            "output_coord": "WGS84",
        }
        try:
            response = await self._client.get(
                KAKAO_MAP_TRANSIT_ROUTE_URL,
                headers=headers,
                params=params,
                timeout=self._timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.TimeoutException:
            headers.clear()
            logger.error(
                "Kakao Map Transit Route 호출 타임아웃 (place_id=%s)",
                destination.place_id,
            )
            return _unavailable_route(destination.place_id, mode, "provider_timeout")
        except httpx.HTTPStatusError as exc:
            detail = f"HTTP {exc.response.status_code}, {upstream_error_detail(exc.response)}"
            headers.clear()
            logger.error(
                "Kakao Map Transit Route 호출 실패 (%s, place_id=%s)",
                detail,
                destination.place_id,
            )
            return _unavailable_route(
                destination.place_id, mode, f"http_{exc.response.status_code}"
            )
        except (httpx.HTTPError, ValueError):
            headers.clear()
            logger.error(
                "Kakao Map Transit Route 응답 오류 (place_id=%s)",
                destination.place_id,
            )
            return _unavailable_route(destination.place_id, mode, "invalid_response")
        return _map_kakao_transit_route(destination.place_id, mode, payload)


def _validate_request(
    destinations: tuple[RouteDestination, ...], radius_m: int | None, mode: TravelMode
) -> None:
    if mode is not TravelMode.TRANSIT:
        # 이 모듈은 카카오 대중교통 엔드포인트만 구현한다. 다른 이동수단을 받으면
        # 대중교통 값을 그 수단의 실측인 척 내보내게 되므로 거부한다.
        raise ValueError(f"이 Provider는 {TravelMode.TRANSIT} 외의 이동수단을 지원하지 않습니다.")
    if len(destinations) > MAX_TRAVEL_ROUTE_DESTINATIONS:
        raise ValueError(f"destinations는 최대 {MAX_TRAVEL_ROUTE_DESTINATIONS}개까지 허용됩니다.")
    if radius_m is not None and radius_m <= 0:
        raise ValueError("radius_m는 0보다 커야 합니다.")


def _map_kakao_transit_route(place_id: str, mode: TravelMode, payload: object) -> TravelRoute:
    if not isinstance(payload, dict):
        return _unavailable_route(place_id, mode, "invalid_response")
    status = payload.get("status")
    if status == "EQUAL_POINTS":
        # 카카오 도보의 SAME_POINT와 같은 상황이다. routes가 빈 배열로 오므로
        # 아래 최소 경로 선택에 맡기면 no_data가 되어버린다.
        return TravelRoute(
            place_id=place_id,
            mode=mode,
            status=RouteStatus.SUCCESS,
            source=RouteSource.KAKAO_TRANSIT,
            distance_m=0,
            duration_seconds=0,
        )
    if status != "OK":
        error_code = str(status).strip().lower() if status is not None else "unknown"
        return TravelRoute(
            place_id=place_id,
            mode=mode,
            status=RouteStatus.NO_DATA,
            source=RouteSource.KAKAO_TRANSIT,
            error_code=f"kakao_status_{error_code}",
        )

    fastest = _fastest_route_properties(payload.get("routes"))
    if fastest is None:
        return TravelRoute(
            place_id=place_id,
            mode=mode,
            status=RouteStatus.NO_DATA,
            source=RouteSource.KAKAO_TRANSIT,
            error_code="kakao_no_transit_route",
        )
    duration_seconds, distance_m = fastest
    return TravelRoute(
        place_id=place_id,
        mode=mode,
        status=RouteStatus.SUCCESS,
        source=RouteSource.KAKAO_TRANSIT,
        distance_m=distance_m,
        duration_seconds=duration_seconds,
    )


def _fastest_route_properties(routes: object) -> tuple[int, int] | None:
    """`routes[]`에서 소요시간이 가장 짧은 경로의 (초, 미터)를 고른다.

    **응답 배열은 정렬돼 있지 않다.** 실측(2026-08-20, 경복궁→남산서울타워)에서
    totalTime이 `[2401, 2398, 2983, 3991, ...]` 순으로 왔다. 첫 원소를 쓰면 최적이
    아닌 경로를 실측값으로 내보내게 된다.

    같은 구간에 최대 15개까지 오고 최악 경로는 최소의 2배가 넘는다(40분 대 81분).
    상위 N개 평균도 검토했으나 min 대비 +0~5분에 그쳐 실익이 없었고, min은
    카카오맵이 상단에 보여주는 최적 경로와 값이 일치한다(TP-106).

    소요시간에는 출발지→첫 정류장, 마지막 정류장→목적지 도보가 포함되지만 배차
    대기는 빠져 있다. 실제 체감보다 낙관적인 값이다.
    """

    if not isinstance(routes, list):
        return None
    candidates: list[tuple[int, int]] = []
    for route in routes:
        if not isinstance(route, dict):
            continue
        properties = route.get("properties")
        if not isinstance(properties, dict):
            continue
        try:
            duration_seconds = int(properties["totalTime"])
            distance_m = int(properties["totalDistance"])
        except (KeyError, TypeError, ValueError):
            continue
        if duration_seconds < 0 or distance_m < 0:
            continue
        candidates.append((duration_seconds, distance_m))
    return min(candidates) if candidates else None


def _unavailable_route(place_id: str, mode: TravelMode, error_code: str) -> TravelRoute:
    return TravelRoute(
        place_id=place_id,
        mode=mode,
        status=RouteStatus.UNAVAILABLE,
        source=RouteSource.KAKAO_TRANSIT,
        error_code=error_code,
    )


__all__ = [
    "FakeTransitRouteProvider",
    "KAKAO_MAP_TRANSIT_ROUTE_URL",
    "RealKakaoTransitRouteProvider",
]
