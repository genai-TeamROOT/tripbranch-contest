"""카카오맵 도보 경로와 직선거리 fallback Provider."""

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

logger = logging.getLogger(__name__)

KAKAO_MAP_WALKING_ROUTE_URL = "https://dapi.kakao.com/v2/routing/walk"
# 애플리케이션 내부 호출량 방어 상한. 일반 API 자체는 단건 호출만 제공한다.
MAX_TRAVEL_ROUTE_DESTINATIONS = 100


class FakeWalkingRouteProvider:
    """카카오 미연동 환경과 장애 fallback에서 사용할 직선거리 추정기."""

    def __init__(self, walking_speed_mps: float) -> None:
        if walking_speed_mps <= 0:
            raise ValueError("walking_speed_mps는 0보다 커야 합니다.")
        self._walking_speed_mps = walking_speed_mps

    async def get_routes(
        self,
        origin: GeoCoordinate,
        destinations: tuple[RouteDestination, ...],
        *,
        mode: TravelMode = TravelMode.WALKING,
        radius_m: int | None = None,
    ) -> ProviderResult[TravelRouteBatch]:
        _validate_request(destinations, radius_m, mode)
        routes = tuple(
            self._estimate_route(origin, destination, mode) for destination in destinations
        )
        return provider_result(
            TravelRouteBatch(routes=routes),
            source=ProviderSource.FAKE_WALKING_ROUTE,
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
            duration_seconds=math.ceil(distance_m / self._walking_speed_mps),
        )


class RealKakaoWalkingRouteProvider:
    """카카오디벨로퍼스 카카오맵 단건 API를 제한 병렬로 호출하는 Provider."""

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
        mode: TravelMode = TravelMode.WALKING,
        radius_m: int | None = None,
    ) -> ProviderResult[TravelRouteBatch]:
        _validate_request(destinations, radius_m, mode)
        if not destinations:
            return provider_result(
                TravelRouteBatch(routes=()),
                source=ProviderSource.KAKAO_WALKING_ROUTE,
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
            source=ProviderSource.KAKAO_WALKING_ROUTE,
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
            "route_mode": "SHORTEST",
        }
        try:
            response = await self._client.get(
                KAKAO_MAP_WALKING_ROUTE_URL,
                headers=headers,
                params=params,
                timeout=self._timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.TimeoutException:
            headers.clear()
            logger.error(
                "Kakao Map Walking Route 호출 타임아웃 (place_id=%s)",
                destination.place_id,
            )
            return _unavailable_route(destination.place_id, mode, "provider_timeout")
        except httpx.HTTPStatusError as exc:
            detail = f"HTTP {exc.response.status_code}, {upstream_error_detail(exc.response)}"
            headers.clear()
            logger.error(
                "Kakao Map Walking Route 호출 실패 (%s, place_id=%s)",
                detail,
                destination.place_id,
            )
            return _unavailable_route(
                destination.place_id, mode, f"http_{exc.response.status_code}"
            )
        except (httpx.HTTPError, ValueError):
            headers.clear()
            logger.error(
                "Kakao Map Walking Route 응답 오류 (place_id=%s)",
                destination.place_id,
            )
            return _unavailable_route(destination.place_id, mode, "invalid_response")
        return _map_kakao_map_route(destination.place_id, mode, payload)


def _validate_request(
    destinations: tuple[RouteDestination, ...], radius_m: int | None, mode: TravelMode
) -> None:
    if mode is not TravelMode.WALKING:
        # 이 모듈은 카카오 도보 엔드포인트와 도보 속도 추정만 구현한다. 다른
        # 이동수단을 받으면 도보 값을 그 수단의 실측인 척 내보내게 되므로 거부한다.
        raise ValueError(f"이 Provider는 {TravelMode.WALKING} 외의 이동수단을 지원하지 않습니다.")
    if len(destinations) > MAX_TRAVEL_ROUTE_DESTINATIONS:
        raise ValueError(f"destinations는 최대 {MAX_TRAVEL_ROUTE_DESTINATIONS}개까지 허용됩니다.")
    if radius_m is not None and radius_m <= 0:
        raise ValueError("radius_m는 0보다 커야 합니다.")


def _map_kakao_map_route(place_id: str, mode: TravelMode, payload: object) -> TravelRoute:
    if not isinstance(payload, dict):
        return _unavailable_route(place_id, mode, "invalid_response")
    status = payload.get("status")
    if status == "SAME_POINT":
        return TravelRoute(
            place_id=place_id,
            mode=mode,
            status=RouteStatus.SUCCESS,
            source=RouteSource.KAKAO_WALKING,
            distance_m=0,
            duration_seconds=0,
        )
    if status != "OK":
        error_code = str(status).strip().lower() if status is not None else "unknown"
        return TravelRoute(
            place_id=place_id,
            mode=mode,
            status=RouteStatus.NO_DATA,
            source=RouteSource.KAKAO_WALKING,
            error_code=f"kakao_status_{error_code}",
        )

    route = payload.get("route")
    properties = route.get("properties") if isinstance(route, dict) else None
    if not isinstance(properties, dict):
        return _unavailable_route(place_id, mode, "invalid_response")
    try:
        distance_m = int(properties["totalDistance"])
        duration_seconds = int(properties["totalTime"])
    except (KeyError, TypeError, ValueError):
        return _unavailable_route(place_id, mode, "invalid_response")
    if distance_m < 0 or duration_seconds < 0:
        return _unavailable_route(place_id, mode, "invalid_response")
    return TravelRoute(
        place_id=place_id,
        mode=mode,
        status=RouteStatus.SUCCESS,
        source=RouteSource.KAKAO_WALKING,
        distance_m=distance_m,
        duration_seconds=duration_seconds,
    )


def _unavailable_route(place_id: str, mode: TravelMode, error_code: str) -> TravelRoute:
    return TravelRoute(
        place_id=place_id,
        mode=mode,
        status=RouteStatus.UNAVAILABLE,
        source=RouteSource.KAKAO_WALKING,
        error_code=error_code,
    )


__all__ = [
    "FakeWalkingRouteProvider",
    "KAKAO_MAP_WALKING_ROUTE_URL",
    "MAX_TRAVEL_ROUTE_DESTINATIONS",
    "RealKakaoWalkingRouteProvider",
]
