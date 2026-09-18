"""C 응답 → 개발자용 Audit 표시 정보 변환.

역할: C의 AgentContextResponse에서 관측 전용 정보만 뽑아 A의 ToolExecutionDebug로
옮긴다. context_transform이 A→C 변환을 전담하는 것과 같은 원칙으로, 이 모듈은
C→Audit 변환만 전담한다.

이 변환은 판정에 관여하지 않는다 — 여기서 만든 값은 AgentResponse.tool_executions에
실려 /dev-chat 감사 패널에 표시될 뿐이고, 추천 결과나 응답 문장을 바꾸지 않는다.
그래서 실패해도 요청을 중단시키지 않는다(build_tool_execution_debug()의 예외 처리).
"""

from __future__ import annotations

import logging
from collections import Counter
from typing import Literal

from app.agent_context.compare_schemas import CompareContextResponse
from app.agent_context.enrichment_schemas import (
    CandidateEnrichmentResponse,
    CandidateEnrichmentResult,
)
from app.agent_context.info_schemas import (
    InfoContextResponse,
    RealtimeCityInfoResult,
    RealtimeCommercialInfoResult,
    RealtimePopulationInfoResult,
)
from app.agent_context.schemas import (
    AgentContextResponse,
    ContextValue,
    ProviderMetadata,
    RecommendationContext,
    ResolvedLocation,
)
from app.domain.ranking_origin import resolve_ranking_origin
from app.schemas import (
    CandidateConcentrationDebug,
    LocationDebug,
    ToolContextItemDebug,
    ToolExecutionDebug,
    ToolProviderDebug,
    TravelOrigin,
    UserConditions,
)

logger = logging.getLogger(__name__)

# RecommendationContext의 항목을 표시 순서대로 나열한다. C가 항목을 추가하면
# 여기에도 넣어야 패널에 나타난다 — 빠뜨려도 조회 자체는 정상 동작한다.
_CONTEXT_ITEM_KEYS = ("location", "weather", "places", "holidays")


def _to_provider_debug(metadata: ProviderMetadata) -> ToolProviderDebug:
    return ToolProviderDebug(
        source=metadata.source,
        status=metadata.status,
        retrieved_at=metadata.retrieved_at.isoformat(),
    )


def _dedupe_providers(collected: list[ProviderMetadata]) -> list[ToolProviderDebug]:
    """Provider metadata를 표시용으로 옮기고 중복을 제거한다."""

    seen: set[tuple[str, str, str]] = set()
    providers: list[ToolProviderDebug] = []
    for metadata in collected:
        debug = _to_provider_debug(metadata)
        marker = (debug.source, debug.status, debug.retrieved_at or "")
        if marker in seen:
            continue
        seen.add(marker)
        providers.append(debug)
    return providers


def _collect_context_providers(response: AgentContextResponse) -> list[ToolProviderDebug]:
    """최상위 metadata와 항목별 provider_metadata를 합쳐 중복을 제거한다.

    C는 같은 Provider 기록을 두 곳에 모두 담을 수 있어서, 어느 한쪽만 보면
    호출 이력이 빠진다. (source, status, retrieved_at)이 같으면 같은 호출로 본다.
    """

    collected: list[ProviderMetadata] = list(response.metadata.provider_metadata)
    context = response.context
    if context is not None:
        for key in _CONTEXT_ITEM_KEYS:
            value = getattr(context, key, None)
            if value is not None:
                collected.extend(value.provider_metadata)

    return _dedupe_providers(collected)


def _item_count(value: ContextValue[object]) -> int | None:
    """목록형 항목만 개수를 센다. 단건형(location/weather)은 None을 그대로 둔다."""

    data = value.data
    if isinstance(data, list | tuple):
        return len(data)
    return None


def _to_item_debug(key: str, value: ContextValue[object] | None) -> ToolContextItemDebug:
    if value is None:
        # C가 조회하지 않은 항목. 조회 후 실패(status=unavailable 등)와 구분된다.
        return ToolContextItemDebug(key=key, fetched=False)
    return ToolContextItemDebug(
        key=key,
        fetched=True,
        status=value.status,
        error_code=value.error.code if value.error is not None else None,
        warning_codes=[warning.code for warning in value.warnings],
        item_count=_item_count(value),
    )


def _resolved_location(context: RecommendationContext | None) -> ResolvedLocation | None:
    if context is None or context.location is None:
        return None
    data = context.location.data
    return data if isinstance(data, ResolvedLocation) else None


def _user_location(context: RecommendationContext | None) -> ResolvedLocation | None:
    if context is None or context.user_location is None:
        return None
    data = context.user_location.data
    return data if isinstance(data, ResolvedLocation) else None


def _to_location_debug(
    location: ResolvedLocation | None,
    *,
    source: Literal["query", "device_gps", "search_center", "travel_origin_override"]
    | None = None,
) -> LocationDebug | None:
    """C의 ResolvedLocation을 표시용 위치로 옮긴다.

    source를 넘기면 그 값으로 덮어쓴다 — 경로 시작점이 사용자 위치가 아닌 검색
    위치를 쓴 경우에만 쓴다("search_center"=위치를 몰라 대체,
    "travel_origin_override"=발화가 확정해서 선택, D-071). 그때 실린
    ResolvedLocation은 검색 위치의 것이라 자기 source("query"/"device_gps")를
    그대로 두면 어느 경로로 골랐는지가 사라진다.
    """

    if location is None:
        return None
    return LocationDebug(
        # device_gps는 부를 이름이 없다(requested_query가 "gps_location" 자리표시자).
        name=location.requested_query if location.source != "device_gps" else None,
        source=source or location.source,
        latitude=location.location.latitude,
        longitude=location.location.longitude,
    )


def _to_route_origin_debug(
    context: RecommendationContext | None,
    conditions: UserConditions | None = None,
) -> LocationDebug | None:
    """이번 턴의 거리·실측 경로가 실제로 기준 삼은 지점.

    시작점을 고르는 규칙(user_location을 쓰되 없으면 location으로 내려간다)을 이
    모듈에 옮겨 적지 않고, agent_runtime이 실제 경로 조회에 쓰는 것과 같은
    resolve_ranking_origin()을 그대로 호출한다. 같은 판정이 두 곳에 있으면 D가 규칙을
    바꿨을 때 런타임은 새 규칙으로 경로를 조회하는데 이 패널만 옛 규칙으로 계산한 값을
    보여준다 — 화면에 "시작점 안국역"이라고 떠 있는데 실제로는 경복궁에서 잰 값인
    상태가 된다.
    """

    if context is None:
        return None
    origin = resolve_ranking_origin(context, conditions)
    if origin is None:
        return None
    # 사용자 위치가 그대로 시작점이 됐는지, 못 구해서 검색 위치로 내려갔는지를 가른다.
    # resolve_ranking_origin()은 context.user_location.data와 context.location.data 중
    # 하나를 새로 만들지 않고 그대로 돌려준다. 그래서 위에서 받은 origin이 둘 중 어느
    # 것과 같은 객체인지 보면 어느 쪽을 골랐는지 알 수 있다.
    #
    # 값이 같은지(`==`)로 비교해도 지금은 같은 답이 나온다 — ContextValue가
    # success/partial이 아니면 data를 담지 못하게 막혀 있어(agent_context/schemas.py),
    # 사용자 위치에 값이 있는데 랭킹 판정에서 걸러지는 경우가 생기지 않기 때문이다.
    # 다만 그건 C 계약이 그렇게 막고 있어서 성립하는 결론이라 여기서 다시 기대지 않는다.
    is_user_location = origin is _user_location(context)
    if is_user_location:
        source = None
    elif conditions is not None and conditions.travel_origin is TravelOrigin.SEARCH_CENTER:
        # 사용자 위치를 몰라서가 아니라 발화가 조사로 출발점을 확정해 검색
        # 위치를 골랐다(D-071) — 대체가 아니라 정상 동작이므로 다른 source를
        # 쓴다. 그렇지 않으면 이 정상 케이스까지 "위치를 몰라서 대체됨"으로
        # 잘못 경고하게 된다(TurnLocationBadges.tsx의 warn 판정 근거).
        source = "travel_origin_override"
    else:
        source = "search_center"
    return _to_location_debug(origin, source=source)


def build_tool_execution_debug(
    response: AgentContextResponse,
    *,
    latency_ms: int | None = None,
    conditions: UserConditions | None = None,
) -> ToolExecutionDebug | None:
    """C 응답에서 감사용 표시 정보를 뽑는다. 실패하면 None을 반환한다.

    표시 전용이라 어떤 예외도 요청을 중단시켜서는 안 된다 — C가 계약에 없는 모양의
    응답을 주더라도 추천 흐름은 그대로 진행되어야 하므로 여기서 삼키고 로그만 남긴다.
    """

    try:
        context = response.context
        location = _resolved_location(context)
        return ToolExecutionDebug(
            operation="context_fetch",
            request_id=response.request_id,
            status=response.status,
            latency_ms=latency_ms,
            providers=_collect_context_providers(response),
            context_items=[
                _to_item_debug(key, getattr(context, key, None) if context else None)
                for key in _CONTEXT_ITEM_KEYS
            ],
            rule_versions=dict(response.metadata.rule_versions),
            resolved_location_name=location.resolved_name if location else None,
            resolved_location_address=location.address if location else None,
            search_location=_to_location_debug(location),
            user_location=_to_location_debug(_user_location(context)),
            route_origin=_to_route_origin_debug(context, conditions),
            error_code=response.error.code if response.error is not None else None,
            clarification_code=(
                response.clarification.code if response.clarification is not None else None
            ),
        )
    except Exception:  # noqa: BLE001 - 표시 정보 때문에 요청을 실패시키지 않는다.
        logger.warning("C 응답에서 Audit 표시 정보를 만들지 못함", exc_info=True)
        return None


def build_info_concentration_execution_debug(
    response: InfoContextResponse,
    *,
    latency_ms: int | None = None,
) -> ToolExecutionDebug | None:
    """INFO 단일 장소 조회를 감사용 단계 정보로 변환한다.

    이름은 concentration이지만 INFO question_type 전체가 이 함수를 거친다
    (D-054/D-055 A 배선). is_proxy는 ConcentrationInfoResult 전용 필드라
    PlaceInfoResult/EventInfoResult에는 없으므로 getattr로 방어한다 —
    없으면 AttributeError로 감사 기록 전체가 조용히 사라진다.
    """

    try:
        result = response.result
        error = result.error if result is not None and result.error is not None else response.error
        return ToolExecutionDebug(
            operation=(
                "info_realtime_commercial"
                if isinstance(result, RealtimeCommercialInfoResult)
                else "info_realtime_population"
                if isinstance(result, RealtimePopulationInfoResult)
                else "info_realtime_citydata"
                if isinstance(result, RealtimeCityInfoResult)
                else "info_concentration"
            ),
            request_id=response.request_id,
            status=response.status,
            latency_ms=latency_ms,
            providers=_dedupe_providers(list(response.metadata.provider_metadata)),
            context_items=[
                ToolContextItemDebug(
                    key="concentration",
                    fetched=True,
                    status=result.status if result is not None else response.status,
                    error_code=error.code if error is not None else None,
                    item_count=1 if result is not None else None,
                )
            ],
            rule_versions=dict(response.metadata.rule_versions),
            resolved_location_name=result.resolved_place_name if result is not None else None,
            error_code=error.code if error is not None else None,
            clarification_code=(
                response.clarification.code if response.clarification is not None else None
            ),
            is_proxy=getattr(result, "is_proxy", None) if result is not None else None,
            stale_area_detected=getattr(result, "stale_area_detected", None)
            if result is not None
            else None,
        )
    except Exception:  # noqa: BLE001 - 표시 정보 때문에 요청을 실패시키지 않는다.
        logger.warning("INFO 응답에서 Audit 표시 정보를 만들지 못함", exc_info=True)
        return None


def build_compare_execution_debug(
    response: CompareContextResponse,
    *,
    latency_ms: int | None = None,
) -> ToolExecutionDebug | None:
    """COMPARE 장소명 보강 조회를 개발자 Audit 단계 정보로 변환한다."""

    try:
        return ToolExecutionDebug(
            operation="compare_fetch",
            request_id=response.request_id,
            status=response.status,
            latency_ms=latency_ms,
            # Compare 계약은 장소명·추천 시점 Feature 스냅샷만 반환한다. 일반
            # Context처럼 Provider metadata/rule_versions를 싣지 않으므로, Audit도
            # 빈 값으로 두고 존재하지 않는 필드를 읽지 않는다.
            providers=[],
            context_items=[
                ToolContextItemDebug(
                    key="comparison_candidates",
                    fetched=True,
                    status=response.status,
                    error_code=response.error.code if response.error is not None else None,
                    item_count=len(response.items),
                )
            ],
            error_code=response.error.code if response.error is not None else None,
        )
    except Exception:  # noqa: BLE001 - Audit 때문에 COMPARE 응답을 실패시키지 않는다.
        logger.warning("COMPARE 응답에서 Audit 표시 정보를 만들지 못함", exc_info=True)
        return None


def _candidate_concentration_debug(
    candidate: CandidateEnrichmentResult,
) -> CandidateConcentrationDebug:
    """후보 한 건의 혼잡도 출처를 감사용으로 옮긴다.

    한 후보의 concentration은 오늘 예보 한 건이라 첫 항목만 본다
    (select_concentration_forecast가 날짜·장소로 이미 한 건으로 좁힌다).
    """
    forecast = candidate.concentration[0] if candidate.concentration else None
    return CandidateConcentrationDebug(
        place_id=candidate.place_id,
        name=candidate.name,
        status=candidate.status,
        is_proxy=bool(forecast is not None and forecast.is_proxy),
        proxy_place_name=forecast.proxy_place_name if forecast is not None else None,
        proxy_distance_km=forecast.proxy_distance_km if forecast is not None else None,
    )


def build_candidate_enrichment_execution_debug(
    response: CandidateEnrichmentResponse,
    *,
    latency_ms: int | None = None,
) -> ToolExecutionDebug | None:
    """추천 후보 혼잡도 보강 조회를 감사용 단계 정보로 변환한다."""

    try:
        status_counts = dict(Counter(candidate.status for candidate in response.candidates))
        first_error = next(
            (candidate.error for candidate in response.candidates if candidate.error is not None),
            None,
        )
        metadata = [
            item
            for candidate in response.candidates
            for item in candidate.provider_metadata
        ]
        return ToolExecutionDebug(
            operation="candidate_enrichment",
            request_id=response.request_id,
            status=response.status,
            latency_ms=latency_ms,
            providers=_dedupe_providers(metadata),
            context_items=[
                ToolContextItemDebug(
                    key="concentration_candidates",
                    fetched=True,
                    status=response.status,
                    error_code=first_error.code if first_error is not None else None,
                    item_count=len(response.candidates),
                )
            ],
            error_code=first_error.code if first_error is not None else None,
            candidate_status_counts=status_counts,
            candidate_concentration=[
                _candidate_concentration_debug(candidate)
                for candidate in response.candidates
            ],
        )
    except Exception:  # noqa: BLE001 - 표시 정보 때문에 요청을 실패시키지 않는다.
        logger.warning("후보 혼잡도 보강 응답에서 Audit 표시 정보를 만들지 못함", exc_info=True)
        return None
