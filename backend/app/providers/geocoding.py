"""GeocodingProvider 계약과 구현체.

계약: 자유 텍스트(주소 또는 장소명)를 좌표로 변환한다. 결과가 없으면
AppError(code="location_not_found")를 던진다. 검색 결과가 여러 건이면 API가
반환한 순서(관련도/거리순) 중 첫 번째 결과를 채택한다 - 모호함을 사용자에게
되묻는 흐름은 이번 범위에 포함하지 않는다.
"""

from __future__ import annotations

import logging

import httpx

from app.domain.models import GeocodeResult
from app.errors import AppError
from app.providers.contracts import ProviderResult, ProviderSource, provider_result
from app.providers.upstream_errors import upstream_error_detail
from app.service_area import (
    SUPPORTED_DISTRICTS,
    ServiceDistrict,
    district_representative_point,
)

logger = logging.getLogger(__name__)

_GEOCODE_URL = "https://maps.apigw.ntruss.com/map-geocode/v2/geocode"

# Naver Geocoding은 도로명/지번 주소와 행정동/법정동 이름은 인식하지만("인사동",
# "익선동"은 그대로 통함) 궁궐·공원·상가 같은 개별 장소명(POI)은 인식하지 못한다
# (실제 호출로 확인, backend/docs/api-samples.md 참고). 그 우회로 잘 알려진
# 장소명을 formal 주소로 치환한다. 각 주소는 실제 Naver Geocoding 호출로 결과가
# 나오는 것을 확인한 값이다. 지원 지역이 종로구뿐이던 시절에 만들어 종로구
# 13곳만 들어 있다.
#
# 다만 이 표는 지금 어느 경로에서도 도달하지 않는다(2026-08-24 실측). 사다리
# 3단계인데 표의 13곳이 저장소(9곳)나 지역 검색(2곳)에서 끝나거나 그 전에
# 되묻기로 끊긴다 — 세 목적(PLACE_IDENTITY·SEARCH_CENTER·REALTIME_CITYDATA)
# 모두에서 그렇다. 저장소에 TourAPI 장소 2,570곳이 채워지고 검색 중심점도 저장소를
# 보게 되면서(TP-127) 이 표가 하던 일을 저장소가 넘겨받았다. 제거하려면
# resolve_location의 사다리 3단계를 함께 걷어내야 해서 별도로 판단한다.
_LANDMARK_ADDRESS_ALIASES: dict[str, str] = {
    "경복궁": "서울특별시 종로구 사직로 161",
    "광화문": "서울특별시 종로구 사직로 161",  # 경복궁 정문
    "창덕궁": "서울특별시 종로구 율곡로 99",
    "종묘": "서울특별시 종로구 종로 157",
    "탑골공원": "서울특별시 종로구 종로 99",
    "북촌한옥마을": "서울특별시 종로구 계동길 37",
    "광장시장": "서울특별시 종로구 창경궁로 88",
    "청계광장": "서울특별시 종로구 청계천로 1",
    "종로구청": "서울특별시 종로구 삼봉로 43",
    "대학로": "서울특별시 종로구 대학로 100",
    "동묘": "서울특별시 종로구 종로 359",
    "낙원악기상가": "서울특별시 종로구 삼일대로 428",
    "낙원상가": "서울특별시 종로구 삼일대로 428",
}


def get_landmark_alias(normalized_query: str) -> str | None:
    for name, address in _LANDMARK_ADDRESS_ALIASES.items():
        if name in normalized_query:
            return address
    return None


def _apply_landmark_alias(normalized_query: str) -> str:
    return get_landmark_alias(normalized_query) or normalized_query


# 로컬 개발/테스트용 고정 지명 테이블. substring 매칭으로 찾는다. 지원 구가 넷으로
# 늘어난 뒤에도 종로구만 들어 있으면 테스트가 종로구 밖을 한 번도 밟지 않으므로,
# 구마다 한 곳씩 둔다.
_KNOWN_LOCATIONS: dict[str, tuple[str, float, float]] = {
    "경복궁": ("경복궁", 37.5788, 126.9770),
    "광화문": ("광화문", 37.5788, 126.9770),
    "창덕궁": ("창덕궁", 37.5826, 126.9919),
    "종묘": ("종묘", 37.5739, 126.9945),
    "인사동": ("인사동", 37.5717, 126.9860),
    # 확장 구(TP-125·TP-126). 좌표는 places의 해당 장소 값이다.
    "명동성당": ("서울 명동성당", 37.5637, 126.9868),
    "노들섬": ("노들섬", 37.5175, 126.9585),
    "서울숲": ("서울숲", 37.5445, 127.0374),
    # 성동구. 실제 Naver Geocoding 호출로 확인한 값이다(2026-08-26) — "성수동"은
    # 지역 검색에 카페·식당 상호명만 잡혀 되묻기로 빠지던 버그의 Geocoding 폴백
    # 경로를 로컬에서도 확인할 수 있게 둔다.
    "성수동": ("성수동", 37.542108, 127.04965),
}


def _supported_district_in(value: str) -> ServiceDistrict | None:
    """질의에 지원 구 이름이 들어 있으면 그 구. "서울특별시 강남구"도 "강남구"도 받는다."""
    normalized = value.replace(" ", "")
    return next(
        (district for district in SUPPORTED_DISTRICTS if district.name in normalized),
        None,
    )


# 후보가 여럿이라 되묻기로 끝나는 지명. 실제 Naver 응답과 같은 모양을 로컬에서도
# 밟게 하려고 둔다(TP-182).
#
# 이게 없으면 되묻기 후보를 싣는 경로가 Fake에서 한 번도 안 돌면서 테스트는
# 통과한다 — 이 저장소에서 반복된 실패다. 값은 실제 호출로 확인한 것이다
# (2026-09-03: "익선동" 2건, "연희동" 2건).
_FAKE_AMBIGUOUS_LOCATIONS: dict[str, tuple[tuple[str, ...], float, float]] = {
    "익선동": (("종로구 익선동", "창원시 진해구 익선동"), 37.57457, 126.98940),
    "연희동": (("서대문구 연희동", "서해구 연희동"), 37.57391, 126.93518),
}


class FakeGeocodingProvider:
    """정해진 소수의 지명만 좌표로 변환하는 가짜 구현."""

    async def geocode(
        self, location_query: str, *, use_alias: bool = True
    ) -> ProviderResult[GeocodeResult]:
        normalized = location_query.strip()
        if not normalized:
            raise AppError(code="invalid_request", message="위치를 입력해주세요.")

        provider_query = (
            _apply_landmark_alias(normalized) if use_alias else normalized
        )
        alias_name = next(
            (
                name
                for name, address in _LANDMARK_ADDRESS_ALIASES.items()
                if address == provider_query and name in _KNOWN_LOCATIONS
            ),
            None,
        )
        if alias_name:
            resolved_name, lat, lon = _KNOWN_LOCATIONS[alias_name]
            return provider_result(
                GeocodeResult(
                    query=location_query,
                    resolved_name=resolved_name,
                    latitude=lat,
                    longitude=lon,
                    administrative_district="종로구",
                ),
                source=ProviderSource.FAKE_GEOCODING,
            )

        ambiguous = _FAKE_AMBIGUOUS_LOCATIONS.get(normalized)
        if ambiguous is not None:
            labels, latitude, longitude = ambiguous
            return provider_result(
                GeocodeResult(
                    query=location_query,
                    resolved_name=f"서울특별시 {labels[0]}",
                    latitude=latitude,
                    longitude=longitude,
                    candidate_count=len(labels),
                    administrative_district=labels[0].split()[0],
                    candidate_labels=labels,
                ),
                source=ProviderSource.FAKE_GEOCODING,
            )

        # 행정구역 이름은 표에 두지 않고 경계 파일에서 푼다. 실제 Naver Geocoding이
        # "서울특별시 강남구"를 인식하므로(docs/api-samples.md의 행정동/법정동 항목)
        # Fake만 못 풀면 구 단위 경로가 로컬·테스트에서 아예 돌지 않는다.
        district = _supported_district_in(provider_query)
        if district is not None:
            point = district_representative_point(district.district_code)
            if point is not None:
                latitude, longitude = point
                return provider_result(
                    GeocodeResult(
                        query=location_query,
                        resolved_name=f"서울특별시 {district.name}",
                        latitude=latitude,
                        longitude=longitude,
                        administrative_district=district.name,
                    ),
                    source=ProviderSource.FAKE_GEOCODING,
                )

        for name, (resolved_name, lat, lon) in _KNOWN_LOCATIONS.items():
            if name in provider_query:
                return provider_result(
                    GeocodeResult(
                        query=location_query,
                        resolved_name=resolved_name,
                        latitude=lat,
                        longitude=lon,
                        administrative_district="종로구",
                    ),
                    source=ProviderSource.FAKE_GEOCODING,
                )

        raise AppError(
            code="location_not_found",
            message=f"'{location_query}' 위치를 찾을 수 없어요.",
            status_code=404,
        )


class RealGeocodingProvider:
    """Naver Cloud Platform Geocoding API를 사용하는 실제 구현.

    이 API는 도로명/지번 주소 검색에 최적화되어 있어 개별 장소명(POI)은 인식하지
    못한다. 잘 알려진 장소명은 _LANDMARK_ADDRESS_ALIASES로 formal 주소로 치환해서
    우회하지만, 그 표는 지금 도달하지 않는다(위 주석 참고) — 저장소 조회가 먼저
    같은 일을 한다.
    """

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

    async def geocode(
        self, location_query: str, *, use_alias: bool = True
    ) -> ProviderResult[GeocodeResult]:
        normalized = location_query.strip()
        if not normalized:
            raise AppError(code="invalid_request", message="위치를 입력해주세요.")

        request_headers = {
            "Accept": "application/json",
            "x-ncp-apigw-api-key-id": self._api_key_id,
            "x-ncp-apigw-api-key": self._api_key,
        }
        provider_query = (
            _apply_landmark_alias(normalized) if use_alias else normalized
        )
        params = {"query": provider_query, "count": "5"}

        try:
            response = await self._client.get(
                _GEOCODE_URL,
                params=params,
                headers=request_headers,
                timeout=self._timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPStatusError as exc:
            # Naver는 인증 실패를 errorCode/errorMessage로 내려준다.
            logger.error(
                "Naver Geocoding 호출 실패 (http_status=%s, %s)",
                exc.response.status_code,
                upstream_error_detail(exc.response),
            )
            request_headers.clear()
            raise AppError(
                code="geocoding_unavailable",
                message="위치 검색 서비스를 사용할 수 없습니다.",
                status_code=502,
                retryable=True,
            ) from None
        except (httpx.HTTPError, ValueError) as exc:
            # httpx 원인 예외에는 인증 헤더가 포함된 요청 정보가 남을 수 있다.
            request_headers.clear()
            response = None
            logger.error("Naver Geocoding 호출 실패 (%s)", type(exc).__name__)
            raise AppError(
                code="geocoding_unavailable",
                message="위치 검색 서비스를 사용할 수 없습니다.",
                status_code=502,
                retryable=True,
            ) from None

        if payload.get("status") != "OK":
            logger.error(
                "Naver Geocoding 응답 오류 (status=%s, errorMessage=%s)",
                payload.get("status"),
                payload.get("errorMessage"),
            )
            raise AppError(
                code="geocoding_unavailable",
                message="위치 검색 서비스를 사용할 수 없습니다.",
                status_code=502,
                retryable=True,
            )

        addresses = payload.get("addresses", [])
        if not addresses:
            raise AppError(
                code="location_not_found",
                message=f"'{location_query}' 위치를 찾을 수 없어요.",
                status_code=404,
            )

        top = addresses[0]
        resolved_name = top.get("roadAddress") or top.get("jibunAddress") or normalized
        meta = payload.get("meta")
        total_count = meta.get("totalCount") if isinstance(meta, dict) else None
        try:
            candidate_count = int(total_count)
        except (TypeError, ValueError):
            candidate_count = len(addresses)
        return provider_result(
            GeocodeResult(
                query=location_query,
                resolved_name=resolved_name,
                latitude=float(top["y"]),
                longitude=float(top["x"]),
                candidate_count=max(candidate_count, len(addresses)),
                administrative_district=_extract_district(top, resolved_name),
                # 후보가 하나면 되묻지 않으므로 채우지 않는다.
                candidate_labels=(
                    tuple(_candidate_label(item) for item in addresses)
                    if len(addresses) > 1
                    else ()
                ),
            ),
            source=ProviderSource.NAVER_GEOCODING,
        )


def _candidate_label(address: dict) -> str:
    """되묻기 버튼에 쓸 짧은 후보 이름. "서울특별시 종로구 익선동" -> "종로구 익선동".

    시도를 떼는 이유는 버튼 글자가 곧 다음 검색어이기 때문이다. 짧으면서도 혼자서
    찾아져야 하는데, 실측하면 시도를 떼도 후보 1건으로 확정된다(2026-09-03).
    반대로 동 이름만 남기면 다시 여럿이 되어 되묻기가 반복된다.

    응답이 주소를 조각으로 나눠 주므로 글자를 자르지 않고 조각을 골라 붙인다 —
    자르면 "서울특별시"와 "세종특별자치시"처럼 길이가 다른 시도에서 어긋난다.
    조각이 없으면 전체 주소를 그대로 쓴다. 길지만 틀리지는 않는다.
    """
    parts: dict[str, str] = {}
    for element in address.get("addressElements") or []:
        if not isinstance(element, dict):
            continue
        name = element.get("longName") or element.get("shortName")
        if not name:
            continue
        for kind in element.get("types") or []:
            parts.setdefault(str(kind), str(name).strip())
    picked = [
        parts[kind] for kind in ("SIGUGUN", "DONGMYUN", "RI") if parts.get(kind)
    ]
    if picked:
        return " ".join(picked)
    full = address.get("roadAddress") or address.get("jibunAddress") or ""
    return str(full).strip()


def _extract_district(address: dict, resolved_name: str) -> str | None:
    elements = address.get("addressElements")
    if isinstance(elements, list):
        for element in elements:
            if not isinstance(element, dict) or "SIGUGUN" not in element.get("types", []):
                continue
            district = element.get("shortName") or element.get("longName")
            if district:
                return str(district).strip()
    # SIGUGUN이 없으면 이름에서 찾는다. 지원 구 목록을 읽어야 구가 늘 때 여기도
    # 따라온다 - 종로구만 알던 탓에 확장 구는 이 경로에서 항상 None이었다.
    for district in SUPPORTED_DISTRICTS:
        if district.name in resolved_name:
            return district.name
    return None
