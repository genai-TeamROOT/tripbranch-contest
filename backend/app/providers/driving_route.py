"""네이버 지도 자동차 경로 Provider와 직선거리 fake."""

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

NAVER_DRIVING_ROUTE_URL = "https://maps.apigw.ntruss.com/map-direction/v1/driving"

# 탐색 옵션. traoptimal(실시간 최적)은 실시간 교통을 반영하면서 빠른길·편한길
# 어느 쪽으로도 치우치지 않는다. 응답의 route 객체가 이 이름을 키로 쓰므로
# 요청 옵션과 파싱 키가 반드시 같아야 한다.
NAVER_DRIVING_ROUTE_OPTION = "traoptimal"

# 출발지와 도착지가 같으면 Naver는 HTTP 400 code=1로 거절한다. 그런데 같은
# code=1이 파라미터 오류에도 쓰여서 응답만으로는 둘을 가릴 수 없다. 호출 전에
# 걸러 0m/0초로 답한다 — 카카오 도보(SAME_POINT)와 같은 결과가 되고 호출도 아낀다.
_SAME_POINT_EPSILON = 1e-9

# 경로 탐색 실패 중 "그 지점에 길이 없다"는 사실 응답이라 재시도해도 같다.
# 나머지 400은 요청이 잘못된 것이므로 장애로 다룬다.
_NO_DATA_ERROR_CODE = 2


class FakeDrivingRouteProvider:
    """네이버 미연동 환경에서 쓰는 직선거리 추정기.

    이 값은 채점에 쓰이지 않는다 — source가 STRAIGHT_LINE_ESTIMATE라
    `scoring._applied_travel_route()`가 걸러낸다. 추정을 실측으로 포장하지
    않으려는 것이므로, 여기 속도를 정밀하게 맞출 이유는 없다.
    """

    def __init__(self, driving_speed_mps: float) -> None:
        if driving_speed_mps <= 0:
            raise ValueError("driving_speed_mps는 0보다 커야 합니다.")
        self._driving_speed_mps = driving_speed_mps

    async def get_routes(
        self,
        origin: GeoCoordinate,
        destinations: tuple[RouteDestination, ...],
        *,
        mode: TravelMode = TravelMode.DRIVING,
        radius_m: int | None = None,
    ) -> ProviderResult[TravelRouteBatch]:
        _validate_request(destinations, radius_m, mode)
        routes = tuple(
            self._estimate_route(origin, destination, mode) for destination in destinations
        )
        return provider_result(
            TravelRouteBatch(routes=routes),
            source=ProviderSource.FAKE_DRIVING_ROUTE,
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
            duration_seconds=math.ceil(distance_m / self._driving_speed_mps),
        )


class RealNaverDrivingRouteProvider:
    """네이버 지도 Directions 5를 목적지마다 제한 병렬로 호출하는 Provider.

    Directions는 한 번에 목적지 한 곳만 받는다. 카카오 도보와 같은 구조라
    동시 호출 수를 세마포어로 묶는다. 자격증명이 지오코딩과 같은 Application을
    쓰므로 동시 호출 상한이 지오코딩 quota에도 영향을 준다.
    """

    def __init__(
        self,
        api_key_id: str,
        api_key: str,
        client: httpx.AsyncClient,
        timeout_seconds: float = 10.0,
        max_concurrency: int = 5,
    ) -> None:
        if not api_key_id or not api_key:
            raise ValueError("NAVER_MAP_CLIENT_ID와 NAVER_MAP_CLIENT_SECRET이 필요합니다.")
        if not 1 <= max_concurrency <= 10:
            raise ValueError("max_concurrency는 1 이상 10 이하여야 합니다.")
        self._api_key_id = api_key_id
        self._api_key = api_key
        self._client = client
        self._timeout_seconds = timeout_seconds
        self._max_concurrency = max_concurrency

    async def get_routes(
        self,
        origin: GeoCoordinate,
        destinations: tuple[RouteDestination, ...],
        *,
        mode: TravelMode = TravelMode.DRIVING,
        radius_m: int | None = None,
    ) -> ProviderResult[TravelRouteBatch]:
        _validate_request(destinations, radius_m, mode)
        if not destinations:
            return provider_result(
                TravelRouteBatch(routes=()),
                source=ProviderSource.NAVER_DRIVING_ROUTE,
                status=ProviderStatus.NO_DATA,
            )

        semaphore = asyncio.Semaphore(self._max_concurrency)

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
            source=ProviderSource.NAVER_DRIVING_ROUTE,
            status=status,
        )

    async def _get_route(
        self, origin: GeoCoordinate, destination: RouteDestination, mode: TravelMode
    ) -> TravelRoute:
        if _is_same_point(origin, destination.coordinate):
            return TravelRoute(
                place_id=destination.place_id,
                mode=mode,
                status=RouteStatus.SUCCESS,
                source=RouteSource.NAVER_DRIVING,
                distance_m=0,
                duration_seconds=0,
            )
        headers = {
            "Accept": "application/json",
            "x-ncp-apigw-api-key-id": self._api_key_id,
            "x-ncp-apigw-api-key": self._api_key,
        }
        params = {
            "start": f"{origin.longitude},{origin.latitude}",
            "goal": f"{destination.coordinate.longitude},{destination.coordinate.latitude}",
            "option": NAVER_DRIVING_ROUTE_OPTION,
        }
        try:
            response = await self._client.get(
                NAVER_DRIVING_ROUTE_URL,
                headers=headers,
                params=params,
                timeout=self._timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.TimeoutException:
            headers.clear()
            logger.error(
                "Naver Driving Route 호출 타임아웃 (place_id=%s)",
                destination.place_id,
            )
            return _unavailable_route(destination.place_id, mode, "provider_timeout")
        except httpx.HTTPStatusError as exc:
            headers.clear()
            return _map_http_error(destination.place_id, mode, exc)
        except (httpx.HTTPError, ValueError):
            headers.clear()
            logger.error(
                "Naver Driving Route 응답 오류 (place_id=%s)",
                destination.place_id,
            )
            return _unavailable_route(destination.place_id, mode, "invalid_response")
        return _map_naver_driving_route(destination.place_id, mode, payload)


def _is_same_point(origin: GeoCoordinate, destination: GeoCoordinate) -> bool:
    return (
        abs(origin.latitude - destination.latitude) < _SAME_POINT_EPSILON
        and abs(origin.longitude - destination.longitude) < _SAME_POINT_EPSILON
    )


def _validate_request(
    destinations: tuple[RouteDestination, ...], radius_m: int | None, mode: TravelMode
) -> None:
    if mode is not TravelMode.DRIVING:
        # 이 모듈은 자동차 엔드포인트만 구현한다. 다른 이동수단을 받으면 자동차
        # 값을 그 수단의 실측인 척 내보내게 되므로 거부한다.
        raise ValueError(f"이 Provider는 {TravelMode.DRIVING} 외의 이동수단을 지원하지 않습니다.")
    if len(destinations) > MAX_TRAVEL_ROUTE_DESTINATIONS:
        raise ValueError(f"destinations는 최대 {MAX_TRAVEL_ROUTE_DESTINATIONS}개까지 허용됩니다.")
    if radius_m is not None and radius_m <= 0:
        raise ValueError("radius_m는 0보다 커야 합니다.")


def _map_http_error(place_id: str, mode: TravelMode, exc: httpx.HTTPStatusError) -> TravelRoute:
    """거절 응답을 사실(NO_DATA)과 장애(UNAVAILABLE)로 가른다.

    Naver는 "도로 주변이 아님"(code=2)도 HTTP 400으로 준다. 재시도해도 결과가
    같은 사실 응답이라 장애로 세면 실패율이 부풀고 fallback도 헛돈다.
    """
    detail = f"HTTP {exc.response.status_code}, {upstream_error_detail(exc.response)}"
    if exc.response.status_code == httpx.codes.BAD_REQUEST:
        if _naver_error_code(exc.response) == _NO_DATA_ERROR_CODE:
            logger.info(
                "Naver Driving Route 경로 없음 (place_id=%s, %s)",
                place_id,
                detail,
            )
            return TravelRoute(
                place_id=place_id,
                mode=mode,
                status=RouteStatus.NO_DATA,
                source=RouteSource.NAVER_DRIVING,
                error_code=f"naver_code_{_NO_DATA_ERROR_CODE}",
            )
    logger.error(
        "Naver Driving Route 호출 실패 (%s, place_id=%s)",
        detail,
        place_id,
    )
    return _unavailable_route(place_id, mode, f"http_{exc.response.status_code}")


def _naver_error_code(response: httpx.Response) -> int | None:
    try:
        payload = response.json()
    except ValueError:
        return None
    error = payload.get("error") if isinstance(payload, dict) else None
    if not isinstance(error, dict):
        return None
    try:
        return int(error["code"])
    except (KeyError, TypeError, ValueError):
        return None


def _map_naver_driving_route(place_id: str, mode: TravelMode, payload: object) -> TravelRoute:
    if not isinstance(payload, dict):
        return _unavailable_route(place_id, mode, "invalid_response")
    code = payload.get("code")
    if code != 0:
        error_code = str(code).strip().lower() if code is not None else "unknown"
        return TravelRoute(
            place_id=place_id,
            mode=mode,
            status=RouteStatus.NO_DATA,
            source=RouteSource.NAVER_DRIVING,
            error_code=f"naver_code_{error_code}",
        )
    route = payload.get("route")
    options = route.get(NAVER_DRIVING_ROUTE_OPTION) if isinstance(route, dict) else None
    summary = options[0].get("summary") if isinstance(options, list) and options else None
    if not isinstance(summary, dict):
        return _unavailable_route(place_id, mode, "invalid_response")
    try:
        distance_m = int(summary["distance"])
        # duration은 **밀리초**다(도보 카카오는 초). 초로 바꾸지 않으면 소요시간이
        # 1000배가 되어 거리 점수가 통째로 0이 된다.
        duration_seconds = round(int(summary["duration"]) / 1000)
    except (KeyError, TypeError, ValueError):
        return _unavailable_route(place_id, mode, "invalid_response")
    if distance_m < 0 or duration_seconds < 0:
        return _unavailable_route(place_id, mode, "invalid_response")
    return TravelRoute(
        place_id=place_id,
        mode=mode,
        status=RouteStatus.SUCCESS,
        source=RouteSource.NAVER_DRIVING,
        distance_m=distance_m,
        duration_seconds=duration_seconds,
    )


def _unavailable_route(place_id: str, mode: TravelMode, error_code: str) -> TravelRoute:
    return TravelRoute(
        place_id=place_id,
        mode=mode,
        status=RouteStatus.UNAVAILABLE,
        source=RouteSource.NAVER_DRIVING,
        error_code=error_code,
    )


__all__ = [
    "FakeDrivingRouteProvider",
    "NAVER_DRIVING_ROUTE_OPTION",
    "NAVER_DRIVING_ROUTE_URL",
    "RealNaverDrivingRouteProvider",
]
