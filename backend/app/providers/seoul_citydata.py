"""서울시 실시간 상권현황(``citydata_cmrcl``) Provider."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any
from urllib.parse import quote

import httpx

from app.domain.models import (
    PopulationAgeShare,
    PopulationForecastSlot,
    RealtimeBusStop,
    RealtimeCityDataResult,
    RealtimeCityEvent,
    RealtimeCommercialCategory,
    RealtimeCommercialResult,
    RealtimeParkingLot,
    RealtimePopulationResult,
    RealtimeSubwayArrival,
    RoadIncidentCategoryCount,
    RoadTrafficStatus,
)
from app.errors import ProviderTimeoutError, ProviderUnavailableError
from app.providers.contracts import (
    ProviderResult,
    ProviderSource,
    ProviderStatus,
    provider_result,
)
from app.providers.upstream_errors import upstream_error_detail

logger = logging.getLogger(__name__)

_BASE_URL = "http://openapi.seoul.go.kr:8088"
_SERVICE = "citydata_cmrcl"
_CITYDATA_SERVICE = "citydata"


def _mappings(value: object) -> tuple[Mapping[str, object], ...]:
    if isinstance(value, Mapping):
        return (value,)
    if isinstance(value, list):
        return tuple(item for item in value if isinstance(item, Mapping))
    return ()


def _text(value: object | None) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _int(value: object | None) -> int | None:
    try:
        return int(str(value)) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _float(value: object | None) -> float | None:
    try:
        return float(str(value)) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def map_realtime_commercial_response(
    payload: Mapping[str, object], *, requested_area: str
) -> RealtimeCommercialResult:
    """서울 열린데이터광장의 JSON 응답 차이를 내부 모델로 흡수한다."""

    # 서울 열린데이터광장은 문서 예시처럼 서비스명/row로 감싼 응답과,
    # 실제 citydata_cmrcl처럼 최상위에 AREA_NM/LIVE_CMRCL_STTS를 바로 두는
    # 응답을 모두 반환한다.
    service = payload.get(_SERVICE)
    response = service if isinstance(service, Mapping) else payload
    rows = _mappings(response.get("row"))
    row = rows[0] if rows else response
    statuses = _mappings(row.get("LIVE_CMRCL_STTS"))
    status = statuses[0] if statuses else row
    category_rows = _mappings(status.get("CMRCL_RSB"))
    # 일부 응답은 상권 하위행을 LIVE_CMRCL_STTS와 같은 배열에 평탄화해 내려준다.
    if not category_rows:
        category_rows = tuple(item for item in statuses if "RSB_PAYMENT_LVL" in item)

    categories = tuple(
        RealtimeCommercialCategory(
            large_category=_text(item.get("RSB_LRG_CTGR")),
            middle_category=_text(item.get("RSB_MID_CTGR")),
            activity_level=_text(item.get("RSB_PAYMENT_LVL")),
            payment_count=_int(item.get("RSB_SH_PAYMENT_CNT")),
            payment_amount_min=_int(item.get("RSB_SH_PAYMENT_AMT_MIN")),
            payment_amount_max=_int(item.get("RSB_SH_PAYMENT_AMT_MAX")),
        )
        for item in category_rows
    )
    return RealtimeCommercialResult(
        area_name=_text(row.get("AREA_NM")) or requested_area,
        area_code=_text(row.get("AREA_CD")),
        area_activity_level=_text(status.get("AREA_CMRCL_LVL")),
        observed_at=_text(status.get("CMRCL_TIME")),
        categories=categories,
        provider="seoul_citydata_commercial",
        payment_count=_int(status.get("AREA_SH_PAYMENT_CNT")),
        payment_amount_min=_int(status.get("AREA_SH_PAYMENT_AMT_MIN")),
        payment_amount_max=_int(status.get("AREA_SH_PAYMENT_AMT_MAX")),
    )


def map_realtime_population_response(
    payload: Mapping[str, object], *, requested_area: str
) -> RealtimePopulationResult:
    """``citydata``의 LIVE_PPLTN_STTS와 12시간 예측 배열을 정규화한다."""

    citydata = payload.get("CITYDATA")
    response = citydata if isinstance(citydata, Mapping) else payload
    rows = _mappings(response.get("row"))
    row = rows[0] if rows else response
    statuses = _mappings(row.get("LIVE_PPLTN_STTS"))
    status = statuses[0] if statuses else {}
    forecasts = tuple(
        PopulationForecastSlot(
            forecast_at=_text(item.get("FCST_TIME")) or "",
            congestion_level=_text(item.get("FCST_CONGEST_LVL")),
            population_min=_int(item.get("FCST_PPLTN_MIN")),
            population_max=_int(item.get("FCST_PPLTN_MAX")),
        )
        for item in _mappings(status.get("FCST_PPLTN"))
        if _text(item.get("FCST_TIME")) is not None
    )
    return RealtimePopulationResult(
        area_name=_text(status.get("AREA_NM")) or _text(row.get("AREA_NM")) or requested_area,
        area_code=_text(status.get("AREA_CD")) or _text(row.get("AREA_CD")),
        current_congestion_level=_text(status.get("AREA_CONGEST_LVL")),
        current_congestion_message=_text(status.get("AREA_CONGEST_MSG")),
        observed_at=_text(status.get("PPLTN_TIME")),
        forecast_available=_text(status.get("FCST_YN")) == "Y",
        forecasts=forecasts,
        provider="seoul_citydata_population",
        current_population_min=_int(status.get("AREA_PPLTN_MIN")),
        current_population_max=_int(status.get("AREA_PPLTN_MAX")),
        age_shares=_population_age_shares(status),
    )


# PPLTN_RATE_* 접미사 → 사람이 읽는 연령대 이름. 서울시가 0/10/.../70 여덟 구간으로
# 고정해 내려주며(매뉴얼 V8.5), 0은 "0대"가 아니라 10세 미만이고 70은 70대 이상이다.
_POPULATION_AGE_LABELS: tuple[tuple[str, str], ...] = (
    ("0", "10세 미만"),
    ("10", "10대"),
    ("20", "20대"),
    ("30", "30대"),
    ("40", "40대"),
    ("50", "50대"),
    ("60", "60대"),
    ("70", "70대 이상"),
)


def _population_age_shares(status: Mapping[str, object]) -> tuple[PopulationAgeShare, ...]:
    """``PPLTN_RATE_0``~``_70``을 응답 순서(어린 연령대부터) 그대로 정규화한다."""

    shares = []
    for suffix, label in _POPULATION_AGE_LABELS:
        rate = _float(status.get(f"PPLTN_RATE_{suffix}"))
        if rate is None:
            continue
        shares.append(PopulationAgeShare(label=label, rate=rate))
    return tuple(shares)


def _citydata_row(payload: Mapping[str, object]) -> Mapping[str, object]:
    citydata = payload.get("CITYDATA")
    response = citydata if isinstance(citydata, Mapping) else payload
    rows = _mappings(response.get("row"))
    return rows[0] if rows else response


# PRK_TYPE 코드 → 공영/민영. 서울시 실측(교대역·강남역·홍대 관광특구, 2026-08-26)으로
# 확인한 값 — 문서화된 코드표가 따로 없어 실제 응답과 이름(예: "OO 공영주차장",
# "OO 주차장(민영)")을 대조해서 확정했다.
_PARKING_LOT_TYPE_LABELS: dict[str, str] = {
    "NW": "공영",  # 노외주차장
    "NS": "공영",  # 노상주차장
    "BS": "민영",  # 부설주차장
    "NP": "민영",  # 개별 민영 주차장
}


def map_realtime_parking_response(
    payload: Mapping[str, object],
) -> tuple[RealtimeParkingLot, ...]:
    """PRK_STTS를 정규화한다.

    같은 주차장(PRK_CD)이 실측에서 두 번 들어오는 걸 확인했다(이촌한강공원의
    "이촌3, 4주차장" — 한쪽은 빈 값, 다른 쪽은 실시간 대수가 채워짐). 코드가 없는
    항목은 이름·좌표로 대체 키를 만들어 중복 제거하고, 실시간 정보가 있는 쪽을 남긴다.
    """

    row = _citydata_row(payload)
    merged: dict[str, RealtimeParkingLot] = {}
    order: list[str] = []
    for item in _mappings(row.get("PRK_STTS")):
        capacity = _int(item.get("CPCTY"))
        current_parked_count = _int(item.get("CUR_PRK_CNT"))
        current_available = _text(item.get("CUR_PRK_YN")) == "Y"
        lot = RealtimeParkingLot(
            name=_text(item.get("PRK_NM")) or "주차장",
            latitude=_float(item.get("LAT")),
            longitude=_float(item.get("LNG")),
            capacity=capacity,
            current_parked_count=current_parked_count,
            current_available=current_available,
            paid=(
                True
                if _text(item.get("PAY_YN")) == "Y"
                else False if _text(item.get("PAY_YN")) == "N" else None
            ),
            observed_at=_text(item.get("CUR_PRK_TIME")),
            address=_text(item.get("ADDR")) or _text(item.get("ADDRESS")),
            code=_text(item.get("PRK_CD")),
            lot_type=_PARKING_LOT_TYPE_LABELS.get(_text(item.get("PRK_TYPE")) or ""),
            available_spaces=(
                max(0, capacity - current_parked_count)
                if capacity is not None and current_parked_count is not None and current_available
                else None
            ),
        )
        key = lot.code or f"{lot.name}|{lot.latitude}|{lot.longitude}"
        existing = merged.get(key)
        if existing is None:
            merged[key] = lot
            order.append(key)
        elif lot.current_available and not existing.current_available:
            merged[key] = lot
    return tuple(merged[key] for key in order)


def map_realtime_subway_response(
    payload: Mapping[str, object],
) -> tuple[RealtimeSubwayArrival, ...]:
    row = _citydata_row(payload)
    arrivals: list[RealtimeSubwayArrival] = []
    for station in _mappings(row.get("SUB_STTS")):
        station_name = _text(station.get("SUB_STN_NM")) or "지하철역"
        for detail in _mappings(station.get("SUB_DETAIL")):
            arrivals.append(
                RealtimeSubwayArrival(
                    station_name=station_name,
                    line=_text(detail.get("SUB_LINE")) or _text(station.get("SUB_STN_LINE")),
                    direction=_text(detail.get("SUB_DIR")),
                    destination=_text(detail.get("SUB_TERMINAL")),
                    arrival_seconds=_int(detail.get("SUB_ARVTIME")),
                    arrival_message=_text(detail.get("SUB_ARMG1")),
                )
            )
    return tuple(arrivals)


def map_realtime_bus_response(payload: Mapping[str, object]) -> tuple[RealtimeBusStop, ...]:
    row = _citydata_row(payload)
    return tuple(
        RealtimeBusStop(
            name=_text(item.get("BUS_STN_NM")) or "버스정류장",
            ars_id=_text(item.get("BUS_ARS_ID")),
            latitude=_float(item.get("BUS_STN_Y")),
            longitude=_float(item.get("BUS_STN_X")),
        )
        for item in _mappings(row.get("BUS_STN_STTS"))
    )


def map_realtime_event_response(payload: Mapping[str, object]) -> tuple[RealtimeCityEvent, ...]:
    row = _citydata_row(payload)
    return tuple(
        RealtimeCityEvent(
            name=_text(item.get("EVENT_NM")) or "행사",
            period=_text(item.get("EVENT_PERIOD")),
            place=_text(item.get("EVENT_PLACE")),
            thumbnail_url=_text(item.get("THUMBNAIL")),
            url=_text(item.get("URL")),
        )
        for item in _mappings(row.get("EVENT_STTS"))
    )


# ACDNT_TYPE 원문 → TOPIS 지도가 쓰는 표준 4분류. 코드표 API(OA-13312, "서울시 돌발
# 유형 코드 정보")가 서비스 종료라 직접 조회할 수 없어, 원문 유형명에 든 키워드로
# 분류한다. 실측(2026-09-06, 121곳 전수)에서는 "공사"·"집회및행사"·"기타" 세 값만
# 나왔지만, 사고·고장·기상·화재는 원래도 드문 사건이라 그 시점에 없었을 뿐 스키마에
# 없는 값이 아니다 — ITS 국가교통정보센터의 표준 돌발유형 체계(사고/고장차량/공사/
# 행사/기상통제/기타)를 그대로 참고했다.
_ROAD_INCIDENT_CATEGORY_ORDER: tuple[str, ...] = (
    "사고/고장",
    "공사/집회",
    "기상/화재",
    "기타",
)
_ROAD_INCIDENT_CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "사고/고장": ("사고", "고장"),
    "공사/집회": ("공사", "집회", "행사"),
    "기상/화재": ("기상", "화재", "재해"),
}


def _road_incident_category(item: Mapping[str, object]) -> str:
    text = " ".join(
        value
        for value in (_text(item.get("ACDNT_TYPE")), _text(item.get("ACDNT_DTYPE")))
        if value
    )
    for label, keywords in _ROAD_INCIDENT_CATEGORY_KEYWORDS.items():
        if any(keyword in text for keyword in keywords):
            return label
    return "기타"


def _road_incident_counts(row: Mapping[str, object]) -> tuple[RoadIncidentCategoryCount, ...]:
    """진행 중인 도로 위 돌발상황을 4분류 건수로 집계한다. 0건도 그대로 낸다."""

    counts = dict.fromkeys(_ROAD_INCIDENT_CATEGORY_ORDER, 0)
    for item in _mappings(row.get("ACDNT_CNTRL_STTS")):
        counts[_road_incident_category(item)] += 1
    return tuple(
        RoadIncidentCategoryCount(label=label, count=counts[label])
        for label in _ROAD_INCIDENT_CATEGORY_ORDER
    )


def map_realtime_traffic_response(payload: Mapping[str, object]) -> RoadTrafficStatus | None:
    """ROAD_TRAFFIC_STTS.AVG_ROAD_DATA와 ACDNT_CNTRL_STTS를 함께 정규화한다.

    개별 도로 링크 배열(``ROAD_TRAFFIC_STTS.ROAD_TRAFFIC_STTS``, 좌표 폴리라인 포함)은
    이번 스코프에서 쓰지 않는다 — 지역 평균 스냅샷(단계·속도·안내문구)만 다룬다.
    24시간 추이는 이 응답에 없다(실측 확인, D-091).
    """

    row = _citydata_row(payload)
    section = row.get("ROAD_TRAFFIC_STTS")
    section_map = section if isinstance(section, Mapping) else None
    if section_map is None:
        return None
    avg = section_map.get("AVG_ROAD_DATA")
    avg_map = avg if isinstance(avg, Mapping) else None
    if avg_map is None:
        return None
    return RoadTrafficStatus(
        level=_text(avg_map.get("ROAD_TRAFFIC_IDX")),
        average_speed_kmh=_float(avg_map.get("ROAD_TRAFFIC_SPD")),
        message=_text(avg_map.get("ROAD_MSG")),
        observed_at=_text(avg_map.get("ROAD_TRAFFIC_TIME")),
        incident_counts=_road_incident_counts(row),
    )


class FakeRealtimeCommercialProvider:
    """테스트용 고정 서울시 상권 데이터."""

    async def get_area_commercial_status(
        self, area_name_or_code: str
    ) -> ProviderResult[RealtimeCommercialResult]:
        area_name = "용리단길" if area_name_or_code == "POI076" else area_name_or_code
        result = RealtimeCommercialResult(
            area_name=area_name,
            area_code="POI076" if area_name_or_code == "용리단길" else None,
            area_activity_level="보통 시간대",
            observed_at="2026-08-20 14:00",
            categories=(
                RealtimeCommercialCategory(
                    large_category="음식·음료",
                    middle_category="커피·음료",
                    activity_level="바쁜 시간대",
                    payment_count=38,
                    payment_amount_min=1_600_000,
                    payment_amount_max=1_700_000,
                ),
            ),
            provider="fake_seoul_citydata",
            payment_count=59,
            payment_amount_min=2_000_000,
            payment_amount_max=2_150_000,
        )
        return provider_result(result, source=ProviderSource.FAKE_SEOUL_CITYDATA)


class FakeRealtimeCityDataProvider:
    async def get_area_citydata(
        self, area_name_or_code: str
    ) -> ProviderResult[RealtimeCityDataResult]:
        commercial = (
            await FakeRealtimeCommercialProvider().get_area_commercial_status(area_name_or_code)
        ).data
        area_name = commercial.area_name
        population = RealtimePopulationResult(
            area_name=area_name,
            area_code="POI076" if area_name == "용리단길" else None,
            current_congestion_level="보통",
            current_congestion_message="사람이 몰려있을 수 있지만 크게 붐비지는 않아요.",
            observed_at="2026-08-20 14:00",
            forecast_available=True,
            forecasts=(
                PopulationForecastSlot("2026-08-20 15:00", "보통", 3000, 3500),
                PopulationForecastSlot("2026-08-20 16:00", "약간 붐빔", 4000, 4500),
                PopulationForecastSlot("2026-08-20 17:00", "붐빔", 5000, 5500),
            ),
            provider="fake_seoul_citydata",
            current_population_min=2500,
            current_population_max=3000,
            age_shares=(
                PopulationAgeShare("10세 미만", 1.2),
                PopulationAgeShare("10대", 5.4),
                PopulationAgeShare("20대", 32.2),
                PopulationAgeShare("30대", 24.8),
                PopulationAgeShare("40대", 16.1),
                PopulationAgeShare("50대", 11.5),
                PopulationAgeShare("60대", 5.6),
                PopulationAgeShare("70대 이상", 3.2),
            ),
        )
        return provider_result(
            RealtimeCityDataResult(
                commercial=commercial,
                population=population,
                parking_lots=(
                    RealtimeParkingLot(
                        name="테스트 공영주차장",
                        latitude=37.5311,
                        longitude=126.9714,
                        capacity=50,
                        current_parked_count=20,
                        current_available=True,
                        paid=True,
                        observed_at="2026-08-20 14:00",
                        code="TEST-PUB-1",
                        lot_type="공영",
                    ),
                    RealtimeParkingLot(
                        name="테스트 민영주차장",
                        latitude=37.5313,
                        longitude=126.9718,
                        capacity=30,
                        current_parked_count=None,
                        current_available=False,
                        paid=True,
                        observed_at=None,
                        code="TEST-PRV-1",
                        lot_type="민영",
                    ),
                ),
                subway_arrivals=(
                    RealtimeSubwayArrival(
                        station_name="삼각지역",
                        line="4호선",
                        direction="상행",
                        destination="당고개",
                        arrival_seconds=180,
                        arrival_message="3분 후",
                    ),
                    RealtimeSubwayArrival(
                        station_name="삼각지역",
                        line="4호선",
                        direction="하행",
                        destination="오이도",
                        arrival_seconds=300,
                        arrival_message="5분 후",
                    ),
                ),
                bus_stops=(
                    RealtimeBusStop("용산구청", "03123", 37.5312, 126.9715),
                ),
                events=(
                    RealtimeCityEvent(
                        name="테스트 지역 행사",
                        period="2026-08-20~2026-08-21",
                        place="용리단길 일대",
                        thumbnail_url=None,
                        url=None,
                    ),
                ),
                road_traffic=RoadTrafficStatus(
                    level="원활",
                    average_speed_kmh=32.0,
                    message="해당 장소로 이동·진입하는 도로가 크게 막히지 않아요.",
                    observed_at="2026-08-20 14:00",
                    incident_counts=(
                        RoadIncidentCategoryCount("사고/고장", 0),
                        RoadIncidentCategoryCount("공사/집회", 1),
                        RoadIncidentCategoryCount("기상/화재", 0),
                        RoadIncidentCategoryCount("기타", 0),
                    ),
                ),
            ),
            source=ProviderSource.FAKE_SEOUL_CITYDATA,
        )


class RealRealtimeCityDataProvider:
    """한 번의 ``citydata`` 호출로 상권 활동과 인구 예측을 함께 조회한다."""

    def __init__(
        self, api_key: str, client: httpx.AsyncClient, timeout_seconds: float = 10.0
    ) -> None:
        self._api_key = api_key
        self._client = client
        self._timeout_seconds = timeout_seconds

    async def get_area_citydata(
        self, area_name_or_code: str
    ) -> ProviderResult[RealtimeCityDataResult]:
        query = area_name_or_code.strip()
        url = f"{_BASE_URL}/{self._api_key}/json/{_CITYDATA_SERVICE}/1/1/{quote(query, safe='')}"
        try:
            response = await self._client.get(url, timeout=self._timeout_seconds)
            response.raise_for_status()
            payload: Any = response.json()
        except httpx.TimeoutException:
            raise ProviderTimeoutError("서울시 실시간 도시데이터") from None
        except (httpx.HTTPError, ValueError) as exc:
            logger.error(
                "서울시 실시간 도시데이터 호출 실패 (%s, area=%s)", type(exc).__name__, query
            )
            raise ProviderUnavailableError("서울시 실시간 도시데이터") from None
        if not isinstance(payload, Mapping) or not isinstance(payload.get("CITYDATA"), Mapping):
            raise ProviderUnavailableError(
                "서울시 실시간 도시데이터", detail="응답 도시데이터 항목 없음"
            )
        return provider_result(
            RealtimeCityDataResult(
                commercial=map_realtime_commercial_response(
                    payload["CITYDATA"], requested_area=query
                ),
                population=map_realtime_population_response(payload, requested_area=query),
                parking_lots=map_realtime_parking_response(payload),
                subway_arrivals=map_realtime_subway_response(payload),
                bus_stops=map_realtime_bus_response(payload),
                events=map_realtime_event_response(payload),
                road_traffic=map_realtime_traffic_response(payload),
            ),
            source=ProviderSource.SEOUL_CITYDATA_POPULATION,
        )


class RealRealtimeCommercialProvider:
    """서울시 ``citydata_cmrcl`` API를 호출하는 실제 Provider."""

    def __init__(
        self, api_key: str, client: httpx.AsyncClient, timeout_seconds: float = 10.0
    ) -> None:
        self._api_key = api_key
        self._client = client
        self._timeout_seconds = timeout_seconds

    async def get_area_commercial_status(
        self, area_name_or_code: str
    ) -> ProviderResult[RealtimeCommercialResult]:
        query = area_name_or_code.strip()
        if not query:
            raise ValueError("서울시 상권 조회에는 지역명이 필요합니다.")
        # 인증키가 경로에 포함되는 서울시 API 형식이다. 로그에는 URL을 남기지 않는다.
        url = f"{_BASE_URL}/{self._api_key}/json/{_SERVICE}/1/1/{quote(query, safe='')}"
        try:
            response = await self._client.get(url, timeout=self._timeout_seconds)
            response.raise_for_status()
            payload: Any = response.json()
        except httpx.TimeoutException:
            logger.error("서울시 실시간 상권 호출 타임아웃 (area=%s)", query)
            raise ProviderTimeoutError("서울시 실시간 상권") from None
        except httpx.HTTPStatusError as exc:
            detail = f"HTTP {exc.response.status_code}, {upstream_error_detail(exc.response)}"
            logger.error("서울시 실시간 상권 호출 실패 (%s, area=%s)", detail, query)
            raise ProviderUnavailableError("서울시 실시간 상권", detail=detail) from None
        except (httpx.HTTPError, ValueError) as exc:
            logger.error("서울시 실시간 상권 호출 실패 (%s, area=%s)", type(exc).__name__, query)
            raise ProviderUnavailableError("서울시 실시간 상권") from None

        if not isinstance(payload, Mapping):
            raise ProviderUnavailableError("서울시 실시간 상권", detail="잘못된 응답 형식")
        service = payload.get(_SERVICE)
        response = service if isinstance(service, Mapping) else payload
        result_node = response.get("RESULT")
        code = (
            _text(result_node.get("CODE") or result_node.get("resultCode"))
            if isinstance(result_node, Mapping)
            else None
        )
        if code not in {None, "INFO-000"}:
            if code == "INFO-200":
                result = map_realtime_commercial_response(payload, requested_area=query)
                return provider_result(
                    result,
                    source=ProviderSource.SEOUL_CITYDATA_COMMERCIAL,
                    status=ProviderStatus.NO_DATA,
                )
            message = (
                _text(result_node.get("MESSAGE") or result_node.get("resultMsg"))
                if isinstance(result_node, Mapping)
                else None
            )
            logger.error("서울시 실시간 상권 응답 오류 (code=%s, area=%s)", code, query)
            raise ProviderUnavailableError("서울시 실시간 상권", detail=f"{code}: {message or ''}")

        result = map_realtime_commercial_response(payload, requested_area=query)
        return provider_result(
            result,
            source=ProviderSource.SEOUL_CITYDATA_COMMERCIAL,
            status=ProviderStatus.SUCCESS if result.categories else ProviderStatus.NO_DATA,
        )


__all__ = [
    "FakeRealtimeCommercialProvider",
    "FakeRealtimeCityDataProvider",
    "RealRealtimeCityDataProvider",
    "RealRealtimeCommercialProvider",
    "map_realtime_commercial_response",
    "map_realtime_bus_response",
    "map_realtime_event_response",
    "map_realtime_parking_response",
    "map_realtime_population_response",
    "map_realtime_subway_response",
    "map_realtime_traffic_response",
]
