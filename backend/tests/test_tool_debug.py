"""build_tool_execution_debug()가 C 응답의 관측 정보를 빠짐없이 옮기는지 검증한다.

이 값은 /dev-chat 감사 패널에만 쓰이지만, 소비 측이 실제로 읽는 필드(특히
providers[].source)가 비면 "Fake Provider가 조용히 답했다"를 화면에서 못 잡는다.
그래서 빈 껍데기가 아니라 실제 값이 실린다는 것까지 못 박는다.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.agent_context.enrichment_schemas import (
    CandidateEnrichmentResponse,
    CandidateEnrichmentResult,
    ConcentrationForecastData,
)
from app.agent_context.info_schemas import (
    ConcentrationInfoResult,
    InfoContextResponse,
    RealtimePopulationInfoResult,
)
from app.agent_context.schemas import (
    AgentContextResponse,
    Clarification,
    ContextError,
    ContextValue,
    ContextWarning,
    Coordinates,
    HolidayInfo,
    ProviderMetadata,
    RecommendationContext,
    ResolvedLocation,
    ResponseMetadata,
    WeatherForecast,
)
from app.schemas import TravelOrigin, UserConditions
from app.services.runtime.tool_debug import (
    build_candidate_enrichment_execution_debug,
    build_info_concentration_execution_debug,
    build_tool_execution_debug,
)

RETRIEVED_AT = datetime(2026, 8, 7, 3, 0, tzinfo=UTC)


def _location_value(source: str = "kakao") -> ContextValue[ResolvedLocation]:
    return ContextValue(
        status="success",
        data=ResolvedLocation(
            requested_query="경복궁",
            resolved_name="경복궁",
            source="query",
            location=Coordinates(latitude=37.5788, longitude=126.9770),
            address="서울 종로구 사직로 161",
        ),
        provider_metadata=[
            ProviderMetadata(source=source, status="success", retrieved_at=RETRIEVED_AT)
        ],
    )


def test_성공_응답의_provider와_항목_상태를_그대로_옮긴다() -> None:
    response = AgentContextResponse(
        request_id="req-1",
        intent="RECOMMEND",
        status="success",
        context=RecommendationContext(
            location=_location_value(),
            holidays=ContextValue(
                status="success",
                data=[HolidayInfo(date="2026-08-15", name="광복절")],
            ),
        ),
        metadata=ResponseMetadata(
            rule_versions={"tool_execution": "v1"},
            provider_metadata=[
                ProviderMetadata(source="kma", status="success", retrieved_at=RETRIEVED_AT)
            ],
        ),
    )

    debug = build_tool_execution_debug(response, latency_ms=330)

    assert debug is not None
    assert debug.request_id == "req-1"
    assert debug.status == "success"
    assert debug.latency_ms == 330
    assert debug.rule_versions == {"tool_execution": "v1"}
    assert debug.resolved_location_name == "경복궁"
    assert debug.resolved_location_address == "서울 종로구 사직로 161"
    # 최상위 metadata와 항목별 provider_metadata를 모두 모은다 — 어느 한쪽만 보면
    # 실제 호출한 Provider가 화면에서 누락된다.
    assert {provider.source for provider in debug.providers} == {"kakao", "kma"}


def test_조회하지_않은_항목과_실패한_항목을_구분한다() -> None:
    response = AgentContextResponse(
        request_id="req-2",
        intent="RECOMMEND",
        status="partial",
        context=RecommendationContext(
            location=_location_value(),
            # weather는 아예 조회하지 않았다(발화에 날씨가 이미 있는 경우).
            places=ContextValue(
                status="unavailable",
                error=ContextError(code="upstream_timeout", message="시간 초과", retryable=True),
            ),
        ),
        metadata=ResponseMetadata(),
    )

    debug = build_tool_execution_debug(response)

    assert debug is not None
    items = {item.key: item for item in debug.context_items}
    assert items["weather"].fetched is False
    assert items["weather"].status is None
    assert items["places"].fetched is True
    assert items["places"].status == "unavailable"
    assert items["places"].error_code == "upstream_timeout"
    assert debug.latency_ms is None


def test_목록형_항목의_후보_수를_센다() -> None:
    """D Scoring 탭의 결과 수와 비교해 어디서 후보가 줄었는지 보기 위한 값이다."""

    response = AgentContextResponse(
        request_id="req-3",
        intent="RECOMMEND",
        status="success",
        context=RecommendationContext(
            location=_location_value(),
            weather=ContextValue(
                status="success",
                data=WeatherForecast(
                    forecast_for=RETRIEVED_AT,
                    precipitation="none",
                    sky="clear",
                    temperature_celsius=28.0,
                ),
                warnings=[ContextWarning(code="stale_forecast", message="예보가 오래됨")],
            ),
            holidays=ContextValue(status="no_data", data=[]),
        ),
        metadata=ResponseMetadata(),
    )

    debug = build_tool_execution_debug(response)

    assert debug is not None
    items = {item.key: item for item in debug.context_items}
    assert items["holidays"].item_count == 0
    assert items["weather"].warning_codes == ["stale_forecast"]
    # 단건형 항목은 개수를 세지 않는다.
    assert items["weather"].item_count is None
    assert items["location"].item_count is None


def test_되묻기_응답의_코드를_남긴다() -> None:
    response = AgentContextResponse(
        request_id="req-4",
        intent="RECOMMEND",
        status="needs_clarification",
        clarification=Clarification(code="location_required", missing_fields=["search_center"]),
        metadata=ResponseMetadata(),
    )

    debug = build_tool_execution_debug(response)

    assert debug is not None
    assert debug.clarification_code == "location_required"
    assert debug.error_code is None
    # context가 없어도 항목 목록은 "조회 안 함"으로 채워진다.
    assert all(item.fetched is False for item in debug.context_items)


def test_info_혼잡도_조회도_독립_감사_단계로_변환한다() -> None:
    response = InfoContextResponse(
        request_id="info-1",
        status="success",
        result=ConcentrationInfoResult(
            status="success",
            is_proxy=True,
            requested_place_name="카페",
            resolved_place_name="경복궁",
        ),
        metadata=ResponseMetadata(
            provider_metadata=[
                ProviderMetadata(source="tour_api", status="success", retrieved_at=RETRIEVED_AT)
            ]
        ),
    )

    debug = build_info_concentration_execution_debug(response, latency_ms=120)

    assert debug is not None
    assert debug.operation == "info_concentration"
    assert debug.is_proxy is True
    assert debug.resolved_location_name == "경복궁"
    assert debug.context_items[0].key == "concentration"
    assert {provider.source for provider in debug.providers} == {"tour_api"}


def test_info_실시간_인구_조회는_별도_감사_단계로_변환한다() -> None:
    response = InfoContextResponse(
        request_id="population-1",
        status="success",
        result=RealtimePopulationInfoResult(
            status="success",
            requested_place_name="경복궁",
            resolved_place_name="경복궁",
            area_name="광화문·덕수궁",
            current_congestion_level="보통",
        ),
    )

    debug = build_info_concentration_execution_debug(response, latency_ms=80)

    assert debug is not None
    assert debug.operation == "info_realtime_population"
    assert debug.is_proxy is True
    assert debug.resolved_location_name == "경복궁"


def test_후보_보강_조회는_후보별_상태_집계를_남긴다() -> None:
    response = CandidateEnrichmentResponse(
        request_id="enrich-1",
        status="partial",
        candidates=[
            CandidateEnrichmentResult(
                place_id="1",
                name="경복궁",
                latitude=37.57,
                longitude=126.97,
                status="no_data",
                concentration=[],
                provider_metadata=[
                    ProviderMetadata(source="tour_api", status="success", retrieved_at=RETRIEVED_AT)
                ],
            ),
            CandidateEnrichmentResult(
                place_id="2",
                name="카페",
                latitude=37.58,
                longitude=126.98,
                status="unavailable",
                error=ContextError(code="upstream_timeout", message="시간 초과", retryable=True),
            ),
        ],
    )

    debug = build_candidate_enrichment_execution_debug(response, latency_ms=220)

    assert debug is not None
    assert debug.operation == "candidate_enrichment"
    assert debug.candidate_status_counts == {"no_data": 1, "unavailable": 1}
    assert debug.context_items[0].item_count == 2
    assert debug.error_code == "upstream_timeout"
    # 값이 아예 없는 후보도 목록에는 남아야 어느 후보가 비었는지 보인다.
    assert [(item.name, item.status, item.is_proxy) for item in debug.candidate_concentration] == [
        ("경복궁", "no_data", False),
        ("카페", "unavailable", False),
    ]


def test_후보_보강_조회는_후보별로_값의_출처를_남긴다() -> None:
    """직접 조회한 값과 인근에서 빌려온 값이 화면에서 같아 보이면 안 된다.

    상태 집계만 보면 둘 다 "success"라 구분이 안 된다. 근사치의 타당성은 "어느
    장소에서 얼마나 떨어진 값인가"로 판단하므로 후보별로 출처를 남긴다.
    """
    response = CandidateEnrichmentResponse(
        request_id="enrich-2",
        status="success",
        candidates=[
            CandidateEnrichmentResult(
                place_id="1",
                name="종묘",
                latitude=37.5739,
                longitude=126.9945,
                status="success",
                concentration=[
                    ConcentrationForecastData(
                        place_name="종묘 [유네스코 세계유산]",
                        forecast_date="2026-08-09",
                        concentration_rate=42.0,
                        concentration_level="normal",
                        concentration_label="보통",
                    )
                ],
            ),
            CandidateEnrichmentResult(
                place_id="2",
                name="이름없는 카페",
                latitude=37.5748,
                longitude=126.9955,
                status="success",
                concentration=[
                    ConcentrationForecastData(
                        place_name="종묘 [유네스코 세계유산]",
                        forecast_date="2026-08-09",
                        concentration_rate=42.0,
                        concentration_level="normal",
                        concentration_label="보통",
                        is_proxy=True,
                        proxy_place_name="종묘 [유네스코 세계유산]",
                        proxy_distance_km=0.15,
                    )
                ],
            ),
        ],
    )

    debug = build_candidate_enrichment_execution_debug(response, latency_ms=180)

    assert debug is not None
    assert debug.candidate_status_counts == {"success": 2}
    direct, proxied = debug.candidate_concentration
    assert (direct.name, direct.is_proxy, direct.proxy_place_name) == ("종묘", False, None)
    assert (proxied.name, proxied.is_proxy) == ("이름없는 카페", True)
    assert proxied.proxy_place_name == "종묘 [유네스코 세계유산]"
    assert proxied.proxy_distance_km == 0.15


def _gps_user_location_value() -> ContextValue[ResolvedLocation]:
    """C가 기기 GPS만으로 만든 사용자 위치(agent_context/service.py::_gps_location_result)."""

    return ContextValue(
        status="success",
        data=ResolvedLocation(
            requested_query="gps_location",
            resolved_name="기기 GPS 위치",
            source="device_gps",
            location=Coordinates(latitude=37.5709, longitude=126.9990),
        ),
    )


def _spoken_user_location_value() -> ContextValue[ResolvedLocation]:
    return ContextValue(
        status="success",
        data=ResolvedLocation(
            requested_query="안국역",
            resolved_name="서울특별시 종로구 율곡로 62",
            source="query",
            location=Coordinates(latitude=37.5765, longitude=126.9855),
        ),
    )


def _context_response(context: RecommendationContext) -> AgentContextResponse:
    return AgentContextResponse(
        request_id="req-origin",
        intent="RECOMMEND",
        status="success",
        context=context,
        metadata=ResponseMetadata(),
    )


def test_사용자_위치가_기기_GPS면_이름_없이_출처만_남긴다() -> None:
    """requested_query가 "gps_location" 자리표시자라 그대로 실으면 지명처럼 보인다."""

    debug = build_tool_execution_debug(
        _context_response(
            RecommendationContext(
                location=_location_value(),
                user_location=_gps_user_location_value(),
            )
        )
    )

    assert debug is not None
    assert debug.user_location is not None
    assert debug.user_location.name is None
    assert debug.user_location.source == "device_gps"
    assert (debug.user_location.latitude, debug.user_location.longitude) == (37.5709, 126.9990)
    # 검색 위치는 발화로 왔으므로 부를 이름이 그대로 있다.
    assert debug.search_location is not None
    assert (debug.search_location.name, debug.search_location.source) == ("경복궁", "query")


def test_사용자_위치가_있으면_경로_시작점이_그_위치다() -> None:
    debug = build_tool_execution_debug(
        _context_response(
            RecommendationContext(
                location=_location_value(),
                user_location=_spoken_user_location_value(),
            )
        )
    )

    assert debug is not None
    assert debug.route_origin is not None
    assert (debug.route_origin.name, debug.route_origin.source) == ("안국역", "query")
    assert debug.route_origin.latitude == 37.5765


def test_발화가_출발점을_확정하면_시작점이_대체가_아니라_확정으로_표시된다() -> None:
    """"안국역에서 10분"(D-071)은 사용자 위치를 몰라서 검색 위치로 내려간 게
    아니다 — 사용자 위치(안국역)를 알면서도 발화가 검색 위치(경복궁)를 출발점으로
    확정했다. source가 "search_center"로 뭉뚱그려지면 위 대체 케이스와 똑같이
    "위치를 몰라서 대체됨"으로 잘못 경고하게 된다.
    """

    debug = build_tool_execution_debug(
        _context_response(
            RecommendationContext(
                location=_location_value(),
                user_location=_spoken_user_location_value(),
            )
        ),
        conditions=UserConditions(travel_origin=TravelOrigin.SEARCH_CENTER),
    )

    assert debug is not None
    assert debug.user_location is not None
    assert debug.user_location.name == "안국역"
    assert debug.route_origin is not None
    assert (debug.route_origin.name, debug.route_origin.source) == (
        "경복궁",
        "travel_origin_override",
    )


def test_사용자_위치가_없으면_시작점이_검색_위치로_대체된_것을_드러낸다() -> None:
    """되묻기로 검색 위치만 정해진 턴. 사용자는 거기 있다고 말한 적이 없다.

    이 턴의 거리·실측 경로는 전부 경복궁 기준으로 계산되는데, source가 원래의
    "query"로 남으면 사용자가 경복궁에 있다고 말한 턴과 화면에서 구분되지 않는다.
    """

    debug = build_tool_execution_debug(
        _context_response(RecommendationContext(location=_location_value()))
    )

    assert debug is not None
    assert debug.user_location is None
    assert debug.route_origin is not None
    assert debug.route_origin.name == "경복궁"
    assert debug.route_origin.source == "search_center"


def test_사용자_위치를_못_구한_상태값이면_시작점이_검색_위치로_내려간다() -> None:
    """데이터가 없는 ContextValue는 랭킹 기준점 판정에서 걸러진다(ranking_origin._usable)."""

    debug = build_tool_execution_debug(
        _context_response(
            RecommendationContext(
                location=_location_value(),
                user_location=ContextValue(
                    status="unavailable",
                    data=None,
                    error=ContextError(
                        code="location_unavailable",
                        message="사용자 위치를 해석하지 못했습니다.",
                        retryable=True,
                    ),
                ),
            )
        )
    )

    assert debug is not None
    assert debug.user_location is None
    assert debug.route_origin is not None
    assert debug.route_origin.source == "search_center"
