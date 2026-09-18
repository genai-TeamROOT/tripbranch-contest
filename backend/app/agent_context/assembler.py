"""개별 Tool 결과를 A–C 최상위 Context 응답으로 조립한다."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.agent_context.mappers import (
    map_holidays_context,
    map_location_context,
    map_places_context,
    map_weather_context,
)
from app.agent_context.metadata import build_response_metadata
from app.agent_context.schemas import (
    AgentContextRequest,
    AgentContextResponse,
    Clarification,
    ContextError,
    ContextWarning,
    DistrictScope,
    RecommendationContext,
    parse_candidate_names,
)
from app.tools.contracts import ToolError, ToolStatus
from app.tools.holiday import HolidayToolResult
from app.tools.nearby_place_details import NearbyPlaceDetailsResult
from app.tools.resolve_location import ResolveLocationResult
from app.tools.weather_forecast import WeatherForecastToolResult


@dataclass(frozen=True)
class ContextAssemblyInput:
    """한 번의 A–C 요청과 그 요청에서 실행된 Tool 결과 묶음."""

    request: AgentContextRequest
    location_result: ResolveLocationResult | None
    # 사용자가 있는 곳. 기준점(location_result)과 같은 타입이지만 다른 질의 결과일 수
    # 있다 — 발화 위치와 검색 기준점이 다르면 따로 해석한다(service.py).
    user_location_result: ResolveLocationResult | None = None
    weather_result: WeatherForecastToolResult | None = None
    places_result: NearbyPlaceDetailsResult | None = None
    holidays_result: HolidayToolResult | None = None
    weather_requested: bool = True
    holidays_requested: bool = True
    # 구 단위로 후보를 모은 요청이면 그 사실. 반경 검색이면 None이다(D-119).
    district_scope: DistrictScope | None = None


def assemble_agent_context_response(
    assembly_input: ContextAssemblyInput,
    *,
    rule_versions: dict[str, str] | None = None,
) -> AgentContextResponse:
    """Tool 결과를 조립하고 추천 진행 가능 여부에 따라 최상위 상태를 정한다."""

    request = assembly_input.request
    if assembly_input.location_result is None:
        return _clarification_response(
            request,
            code="location_required",
            missing_fields=["current_location"],
            rule_versions=rule_versions,
        )

    location_result = assembly_input.location_result
    location = map_location_context(location_result)
    # 기준점이 무엇으로 정해졌든 사용자 위치는 그대로 보존한다 — 검색 기준점이
    # 따로 잡히면 GPS가 버려지던 게 근거 문장이 "현재 위치"라고 거짓말한 원인이다.
    # 좌표만 싣던 것을 기준점과 같은 타입으로 올렸다(TP-112): 발화로 말한 위치도
    # 여기 들어오므로 부를 이름(requested_query)과 출처(source)가 필요하다.
    user_location = (
        map_location_context(assembly_input.user_location_result)
        if assembly_input.user_location_result is not None
        else None
    )
    location_only = RecommendationContext(location=location, user_location=user_location)

    if location_result.status is ToolStatus.NO_DATA:
        cause = location_result.error.cause if location_result.error else None
        candidate_names = (
            location_result.error.details.get("candidate_names", "")
            if location_result.error and cause == "ambiguous_location"
            else ""
        )
        return _clarification_response(
            request,
            code=("location_ambiguous" if cause == "ambiguous_location" else "location_required"),
            missing_fields=[] if cause == "ambiguous_location" else ["current_location"],
            candidates=parse_candidate_names(candidate_names),
            metadata_context=location_only,
            rule_versions=rule_versions,
        )

    if location_result.status is ToolStatus.UNSUPPORTED:
        return _blocked_response(
            request,
            status="unsupported",
            error=_context_error(
                location_result.error,
                fallback_code="unsupported",
                fallback_message="현재 지원하지 않는 위치입니다.",
                retryable=False,
            ),
            output_context=None,
            metadata_context=location_only,
            rule_versions=rule_versions,
        )

    if location_result.status is ToolStatus.UNAVAILABLE:
        return _blocked_response(
            request,
            status="unavailable",
            error=_context_error(
                location_result.error,
                fallback_code="location_unavailable",
                fallback_message="위치 정보를 가져오지 못했습니다.",
                retryable=True,
            ),
            output_context=location_only,
            metadata_context=location_only,
            rule_versions=rule_versions,
        )

    weather = (
        map_weather_context(assembly_input.weather_result)
        if assembly_input.weather_result is not None
        else None
    )
    places = (
        map_places_context(assembly_input.places_result)
        if assembly_input.places_result is not None
        else None
    )
    holidays = (
        map_holidays_context(assembly_input.holidays_result)
        if assembly_input.holidays_result is not None
        else None
    )
    context = RecommendationContext(
        location=location,
        user_location=user_location,
        weather=weather,
        places=places,
        holidays=holidays,
        district_scope=assembly_input.district_scope,
    )

    if assembly_input.places_result is None:
        return _blocked_response(
            request,
            status="unavailable",
            error=ContextError(
                code="places_not_collected",
                message="장소 정보를 수집하지 못했습니다.",
                retryable=True,
            ),
            output_context=context,
            metadata_context=context,
            warnings=_collect_warnings(
                context,
                weather_missing=assembly_input.weather_requested and weather is None,
                holiday_missing=assembly_input.holidays_requested and holidays is None,
            ),
            rule_versions=rule_versions,
        )

    places_status = assembly_input.places_result.status
    if places_status is ToolStatus.UNSUPPORTED:
        return _blocked_response(
            request,
            status="unsupported",
            error=_context_error(
                assembly_input.places_result.error,
                fallback_code="places_unsupported",
                fallback_message="현재 지원하지 않는 장소 검색 조건입니다.",
                retryable=False,
            ),
            output_context=None,
            metadata_context=context,
            rule_versions=rule_versions,
        )

    if places_status is ToolStatus.UNAVAILABLE:
        return _blocked_response(
            request,
            status="unavailable",
            error=_context_error(
                assembly_input.places_result.error,
                fallback_code="places_unavailable",
                fallback_message="장소 정보를 가져오지 못했습니다.",
                retryable=True,
            ),
            output_context=context,
            metadata_context=context,
            warnings=_collect_warnings(context),
            rule_versions=rule_versions,
        )

    if places_status is ToolStatus.NO_DATA:
        return AgentContextResponse(
            request_id=request.request_id,
            intent=request.intent,
            status="no_data",
            context=context,
            warnings=_collect_warnings(context),
            error=None,
            metadata=build_response_metadata(
                context,
                rule_versions=rule_versions,
            ),
        )

    partial = _requires_partial_status(assembly_input)
    return AgentContextResponse(
        request_id=request.request_id,
        intent=request.intent,
        status="partial" if partial else "success",
        context=context,
        warnings=_collect_warnings(
            context,
            weather_missing=_is_missing_or_failed_weather(assembly_input),
            holiday_missing=_is_missing_or_failed_holiday(assembly_input),
        ),
        error=None,
        metadata=build_response_metadata(
            context,
            rule_versions=rule_versions,
        ),
    )


def _requires_partial_status(assembly_input: ContextAssemblyInput) -> bool:
    if (
        assembly_input.places_result is not None
        and assembly_input.places_result.status is ToolStatus.PARTIAL
    ):
        return True
    if _is_missing_or_failed_weather(assembly_input):
        return True
    return _is_missing_or_failed_holiday(assembly_input)


def _is_missing_or_failed_weather(
    assembly_input: ContextAssemblyInput,
) -> bool:
    if not assembly_input.weather_requested:
        return False
    result = assembly_input.weather_result
    return result is None or result.status is not ToolStatus.SUCCESS


def _is_missing_or_failed_holiday(
    assembly_input: ContextAssemblyInput,
) -> bool:
    if not assembly_input.holidays_requested:
        return False
    result = assembly_input.holidays_result
    if result is None:
        return True
    return result.status not in {ToolStatus.SUCCESS, ToolStatus.NO_DATA}


def _collect_warnings(
    context: RecommendationContext,
    *,
    weather_missing: bool = False,
    holiday_missing: bool = False,
) -> list[ContextWarning]:
    """Context 순서대로 경고를 모으고 같은 경고는 한 번만 반환한다."""

    warnings: list[ContextWarning] = []
    for value in (
        context.location,
        context.weather,
        context.places,
        context.holidays,
    ):
        if value is not None:
            warnings.extend(value.warnings)
    if weather_missing:
        warnings.append(
            ContextWarning(
                code="weather_missing",
                message="날씨 정보 없이 추천 Context를 반환합니다.",
            )
        )
    if holiday_missing:
        warnings.append(
            ContextWarning(
                code="holiday_missing",
                message="공휴일 정보 없이 추천 Context를 반환합니다.",
            )
        )

    unique: list[ContextWarning] = []
    seen: set[tuple[str, str]] = set()
    for warning in warnings:
        key = (warning.code, warning.message)
        if key not in seen:
            seen.add(key)
            unique.append(warning)
    return unique


def _clarification_response(
    request: AgentContextRequest,
    *,
    code: Literal[
        "location_required",
        "location_ambiguous",
        "place_required",
        "place_ambiguous",
    ],
    missing_fields: list[str],
    candidates: list[str] | None = None,
    metadata_context: RecommendationContext | None = None,
    rule_versions: dict[str, str] | None = None,
) -> AgentContextResponse:
    return AgentContextResponse(
        request_id=request.request_id,
        intent=request.intent,
        status="needs_clarification",
        context=None,
        clarification=Clarification(
            code=code,
            missing_fields=missing_fields,
            candidates=candidates or [],
        ),
        error=None,
        metadata=build_response_metadata(
            metadata_context,
            rule_versions=rule_versions,
        ),
    )


def _blocked_response(
    request: AgentContextRequest,
    *,
    status: Literal["unsupported", "unavailable"],
    error: ContextError,
    output_context: RecommendationContext | None,
    metadata_context: RecommendationContext,
    warnings: list[ContextWarning] | None = None,
    rule_versions: dict[str, str] | None = None,
) -> AgentContextResponse:
    return AgentContextResponse(
        request_id=request.request_id,
        intent=request.intent,
        status=status,
        context=output_context,
        warnings=warnings or [],
        error=error,
        metadata=build_response_metadata(
            metadata_context,
            rule_versions=rule_versions,
        ),
    )


def _context_error(
    error: ToolError | None,
    *,
    fallback_code: str,
    fallback_message: str,
    retryable: bool,
) -> ContextError:
    if error is None:
        return ContextError(
            code=fallback_code,
            message=fallback_message,
            retryable=retryable,
        )
    return ContextError(
        code=error.code,
        message=error.message,
        retryable=error.retryable,
    )
