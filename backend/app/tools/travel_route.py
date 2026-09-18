"""이동수단별 실제 경로를 조회하고 누락 결과를 직선거리 추정으로 보완하는 Tool."""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass

from app.domain.travel_route import (
    GeoCoordinate,
    RouteDestination,
    RouteSource,
    RouteStatus,
    TravelMode,
    TravelRoute,
)
from app.errors import AppError
from app.observability.langfuse_tracing import observe_step, record_score
from app.providers.contracts import ProviderMetadata
from app.providers.protocols import TravelRouteProvider
from app.providers.walking_route import MAX_TRAVEL_ROUTE_DESTINATIONS
from app.tools.contracts import ToolError, ToolStatus

logger = logging.getLogger(__name__)

TRAVEL_ROUTE_FALLBACK_WARNING = "travel_route_straight_line_fallback"
TRAVEL_ROUTE_FALLBACK_FAILED_WARNING = "travel_route_fallback_failed"
TRAVEL_ROUTE_MODE_UNSUPPORTED_WARNING = "travel_route_mode_unsupported"


@dataclass(frozen=True)
class TravelRouteQuery:
    """조회 요청. `mode`는 기본값을 두지 않는다.

    기본값을 두면 mode를 넘기지 않은 호출부가 조용히 도보를 조회한다 — 이 카드가
    고친 문제가 정확히 그것이었다(자동차 요청에도 도보를 20건씩 조회하고 D가
    결과를 버렸다).
    """

    origin: GeoCoordinate
    destinations: tuple[RouteDestination, ...]
    mode: TravelMode
    radius_m: int | None = None

    def __post_init__(self) -> None:
        if len(self.destinations) > MAX_TRAVEL_ROUTE_DESTINATIONS:
            raise ValueError(
                f"destinations는 최대 {MAX_TRAVEL_ROUTE_DESTINATIONS}개까지 허용됩니다."
            )
        if self.radius_m is not None and self.radius_m <= 0:
            raise ValueError("radius_m는 0보다 커야 합니다.")
        place_ids = tuple(destination.place_id for destination in self.destinations)
        if len(place_ids) != len(set(place_ids)):
            raise ValueError("destinations의 place_id는 중복될 수 없습니다.")


@dataclass(frozen=True)
class TravelRouteToolResult:
    """`fallback_causes`는 **추정으로 대체된 원인**을 코드별로 센 것이다.

    `error`와 다르다 — `error`는 "Tool이 실패했다"이고, 이쪽은 "채우기는 했는데
    실측이 아니다"의 이유다. 목적지별 실패는 예외를 안 내고 오기 때문에 예전에는
    `logger.warning`으로만 남았고, 서버 로그를 뒤지지 않으면 알 수 없었다.
    """

    status: ToolStatus
    routes: tuple[TravelRoute, ...]
    error: ToolError | None = None
    warnings: tuple[str, ...] = ()
    provider_metadata: tuple[ProviderMetadata, ...] = ()
    fallback_causes: tuple[tuple[str, int], ...] = ()


@dataclass(frozen=True)
class TravelRouteProviders:
    """한 이동수단의 실측 Provider와 그 실패를 메울 추정 Provider."""

    primary: TravelRouteProvider
    fallback: TravelRouteProvider | None = None


class TravelRouteTool:
    """등록된 이동수단만 조회한다.

    미등록 이동수단은 호출 없이 NO_DATA로 답한다. 추정으로 메우지 않는 이유는
    도보 속도 추정값을 자동차 실측인 척 내보내는 것이 D-042("Real 실패 시 Fake로
    자동 전환하지 않는다")가 막으려던 그 상황이기 때문이다. 소비 측은 값이
    없으면 직선거리로 돌아가므로, 없는 것이 틀린 것보다 안전하다.
    """

    def __init__(self, providers: Mapping[TravelMode, TravelRouteProviders]) -> None:
        if not providers:
            raise ValueError("이동수단이 하나도 등록되지 않았습니다.")
        self._providers = dict(providers)

    async def execute(self, query: TravelRouteQuery) -> TravelRouteToolResult:
        """팬아웃 한 번을 span 하나로 남기고 본체(`_execute`)에 넘긴다.

        **HTTP 호출 하나당 span 하나로 하지 않는다.** Provider가 목적지마다 따로
        요청을 쏘기 때문에(`asyncio.gather`) 후보 20곳이면 호출도 20번이다. 그걸
        전부 span으로 만들면 한 턴 observation이 5개에서 25개로 뛰어 무료 티어
        유닛을 그대로 잡아먹는다. 그래서 **팬아웃 하나를 묶어 집계만 싣는다** —
        observation은 +1인데 "몇 건 쐈고 몇 건이 추정으로 샜나"가 다 보인다.

        `output`은 `capture_content`가 꺼지면 마스킹된다. 그래서 가장 중요한 신호인
        실측/추정 비율은 `level`·`status_message`에도 싣는다 — 그 둘은 mask를 타지
        않는다(`langfuse_tracing` 모듈 docstring).

        좌표는 `query.origin`·`destinations`에만 있고 요약에는 넣지 않는다.
        """
        with observe_step("travel_route") as step:
            result = await self._execute(query)
            try:
                summary = summarize_fanout(query, result)
                step.record(
                    output=summary,
                    level=summary["level"],
                    status_message=summary["headline"],
                )
                # 이 턴의 거리 축이 실측인지 직선거리인지. **추세로 봐야 하는 값이라
                # span의 output만으로는 부족하다** — 2026-08-25에 "실측 0%"를 한 번
                # 보고 알았지만, 그게 언제부터였는지도 카카오가 고쳐지면 언제
                # 올라가는지도 열어보기 전에는 모른다.
                if summary["measured_ratio"] is not None:
                    record_score("route_measured_ratio", float(summary["measured_ratio"]))
            except Exception:
                logger.warning("경로 관측 요약 실패(응답 흐름에는 영향 없음)", exc_info=True)
            return result

    async def _execute(self, query: TravelRouteQuery) -> TravelRouteToolResult:
        if not query.destinations:
            return TravelRouteToolResult(status=ToolStatus.NO_DATA, routes=())

        providers = self._providers.get(query.mode)
        if providers is None:
            logger.info(
                "경로 조회를 지원하지 않는 이동수단 (mode=%s, 등록=%s)",
                query.mode,
                sorted(self._providers),
            )
            return TravelRouteToolResult(
                status=ToolStatus.NO_DATA,
                routes=(),
                warnings=(TRAVEL_ROUTE_MODE_UNSUPPORTED_WARNING,),
            )

        try:
            primary_result = await providers.primary.get_routes(
                query.origin,
                query.destinations,
                mode=query.mode,
                radius_m=query.radius_m,
            )
        except AppError as exc:
            logger.warning(
                "경로 Provider 실패 (mode=%s, code=%s, provider=%s)",
                query.mode,
                exc.code,
                exc.provider,
            )
            return await self._fallback_all(query, providers.fallback, exc)

        failed_ids = frozenset(
            route.place_id
            for route in primary_result.data.routes
            if route.status is not RouteStatus.SUCCESS
        )
        if not failed_ids or providers.fallback is None:
            return TravelRouteToolResult(
                status=_tool_status(primary_result.data.routes),
                routes=primary_result.data.routes,
                provider_metadata=(primary_result.metadata,),
            )

        fallback_destinations = tuple(
            destination for destination in query.destinations if destination.place_id in failed_ids
        )
        try:
            fallback_result = await providers.fallback.get_routes(
                query.origin,
                fallback_destinations,
                mode=query.mode,
                radius_m=query.radius_m,
            )
        except AppError as exc:
            logger.warning(
                "경로 부분 fallback 실패 (mode=%s, code=%s, provider=%s)",
                query.mode,
                exc.code,
                exc.provider,
            )
            return TravelRouteToolResult(
                status=_tool_status(primary_result.data.routes),
                routes=primary_result.data.routes,
                error=_tool_error(exc),
                warnings=(TRAVEL_ROUTE_FALLBACK_FAILED_WARNING,),
                provider_metadata=(primary_result.metadata,),
            )

        fallback_by_id = {route.place_id: route for route in fallback_result.data.routes}
        routes = tuple(
            fallback_by_id.get(route.place_id, route)
            if route.status is not RouteStatus.SUCCESS
            else route
            for route in primary_result.data.routes
        )
        # D-042의 "조용한 폴백 금지". 예외가 난 경로는 위에서 로그를 남기지만,
        # 목적지별 실패는 예외 없이 여기로 오기 때문에 이 분기만 무로그였다 —
        # 카카오가 한 건도 못 풀어도 추정값이 조용히 D까지 흘러갔다.
        replaced_ids = frozenset(
            route.place_id
            for route in primary_result.data.routes
            if route.status is not RouteStatus.SUCCESS and route.place_id in fallback_by_id
        )
        causes: Counter[str] = Counter()
        if replaced_ids:
            causes = Counter(
                route.error_code or "unknown"
                for route in primary_result.data.routes
                if route.place_id in replaced_ids
            )
            logger.warning(
                "%s 경로 %d/%d건을 직선거리 추정으로 대체 (원인=%s)",
                query.mode,
                len(replaced_ids),
                len(primary_result.data.routes),
                dict(sorted(causes.items())),
            )
        return TravelRouteToolResult(
            # 실제값과 추정값이 섞였으므로 모두 채워졌어도 degraded 결과다.
            status=ToolStatus.PARTIAL,
            routes=routes,
            warnings=(TRAVEL_ROUTE_FALLBACK_WARNING,),
            provider_metadata=(primary_result.metadata, fallback_result.metadata),
            # 관측이 "왜 추정인가"에 답할 수 있게 원인을 결과에 싣는다. 로그에만
            # 남기면 대시보드에서 보고도 서버 로그를 따로 뒤져야 한다.
            fallback_causes=tuple(sorted(causes.items())),
        )

    async def _fallback_all(
        self,
        query: TravelRouteQuery,
        fallback_provider: TravelRouteProvider | None,
        primary_error: AppError,
    ) -> TravelRouteToolResult:
        if fallback_provider is None:
            return TravelRouteToolResult(
                status=ToolStatus.UNAVAILABLE,
                routes=(),
                error=_tool_error(primary_error),
            )
        try:
            fallback_result = await fallback_provider.get_routes(
                query.origin,
                query.destinations,
                mode=query.mode,
                radius_m=query.radius_m,
            )
        except AppError as fallback_error:
            logger.warning(
                "경로 전체 fallback 실패 (mode=%s, code=%s, provider=%s)",
                query.mode,
                fallback_error.code,
                fallback_error.provider,
            )
            return TravelRouteToolResult(
                status=ToolStatus.UNAVAILABLE,
                routes=(),
                error=_tool_error(fallback_error),
                warnings=(TRAVEL_ROUTE_FALLBACK_FAILED_WARNING,),
            )
        return TravelRouteToolResult(
            status=ToolStatus.PARTIAL,
            routes=fallback_result.data.routes,
            # **primary가 왜 죽었는지를 결과에 담는다.** 예전에는 logger.warning으로만
            # 남겨서, 관측 span이 "전부 추정으로 대체됨"까지만 말하고 원인은 못 말했다.
            # 2026-08-25에 도보 실측이 0건인 걸 보고도 쿼터 초과인지 키 문제인지
            # 서버 로그를 따로 뒤져야 했다 — 그 왕복이 관측의 존재 이유를 깎는다.
            error=_tool_error(primary_error),
            warnings=(TRAVEL_ROUTE_FALLBACK_WARNING,),
            provider_metadata=(fallback_result.metadata,),
            fallback_causes=((primary_error.code, len(query.destinations)),),
        )


# 한 span에 실을 원인 코드 종류 상한. 종류가 이보다 많으면 이미 무슨 일이
# 벌어진 건지 개수만으로 충분하다.
_SUMMARY_CAUSE_LIMIT = 5


def summarize_fanout(query: TravelRouteQuery, result: TravelRouteToolResult) -> dict[str, object]:
    """경로 팬아웃 한 번을 span에 실을 집계로 접는다.

    **실측/추정 비율이 이 요약의 존재 이유다.** 실측 소요시간이 있는 후보는
    `_travel_time_score()`로, 없는 후보는 직선거리 `_distance_score()`로 채점된다
    (`app/domain/scoring.py::_proximity_score`). 즉 추정으로 샌 건수만큼 **같은
    순위표 안에 서로 다른 자가 섞인다.** 지금은 그 비율을 아무도 모른다 — 경로가
    실패해도 fallback이 조용히 메워서 응답만 봐서는 드러나지 않는다.

    좌표(`query.origin`·`destinations[].coordinate`)와 place_id는 싣지 않는다.
    여기서 알고 싶은 건 개수와 분포지 어디를 갔느냐가 아니다.
    """
    routes = result.routes
    requested = len(query.destinations)
    by_source = Counter(route.source.value for route in routes)
    estimated = by_source.get(RouteSource.STRAIGHT_LINE_ESTIMATE.value, 0)
    measured = len(routes) - estimated
    # **대체된 뒤의 routes를 읽으면 안 된다** — 전부 추정 성공이라 error_code가 비어
    # 있다. 2026-08-25에 그렇게 짜서 "추정 11건"은 나오는데 원인은 계속 빈칸이었다.
    causes = dict(result.fallback_causes) or Counter(
        route.error_code or "unknown" for route in routes if route.error_code
    )

    if result.status is ToolStatus.UNAVAILABLE:
        level = "ERROR"
    elif estimated or result.status is not ToolStatus.SUCCESS:
        level = "WARNING"
    else:
        level = "DEFAULT"

    return {
        "mode": query.mode.value,
        "status": result.status.value,
        "requested": requested,
        "returned": len(routes),
        "measured": measured,
        "estimated": estimated,
        # 이 턴의 거리 축이 얼마나 실측으로 채워졌나. 1.0이 아니면 자가 섞였다는 뜻이다.
        "measured_ratio": round(measured / len(routes), 3) if routes else None,
        "by_source": dict(sorted(by_source.items())),
        "by_status": dict(sorted(Counter(route.status.value for route in routes).items())),
        "error_causes": dict(sorted(causes.items())[:_SUMMARY_CAUSE_LIMIT]),
        # 원인이 하나뿐이면 headline에 얹을 값. 대부분 그렇다(쿼터·키·타임아웃).
        "primary_cause": next(iter(sorted(causes)), None),
        "warnings": list(result.warnings),
        # primary 실패 원인. 전부 추정으로 대체된 턴에서 "왜"에 답하는 유일한 값이다.
        "error_code": result.error.code if result.error is not None else None,
        "error_detail": result.error.message if result.error is not None else None,
        "level": level,
        # 마스킹을 타지 않는 자리(status_message)에 실을 한 줄.
        "headline": (
            f"{query.mode.value} {requested}건 요청 · 실측 {measured} · 추정 {estimated}"
            + (f" · {next(iter(sorted(causes)))}" if causes else "")
        ),
    }


def _tool_status(routes: tuple[TravelRoute, ...]) -> ToolStatus:
    successful_count = sum(route.status is RouteStatus.SUCCESS for route in routes)
    if successful_count == len(routes) and routes:
        return ToolStatus.SUCCESS
    if successful_count:
        return ToolStatus.PARTIAL
    if routes and all(route.status is RouteStatus.NO_DATA for route in routes):
        return ToolStatus.NO_DATA
    return ToolStatus.UNAVAILABLE


def _tool_error(error: AppError) -> ToolError:
    return ToolError(
        code="unavailable",
        message="이동 경로 정보를 가져오지 못했습니다.",
        cause="timeout" if error.code == "provider_timeout" else "upstream_error",
        retryable=error.retryable,
    )


__all__ = [
    "TRAVEL_ROUTE_FALLBACK_FAILED_WARNING",
    "TRAVEL_ROUTE_FALLBACK_WARNING",
    "TRAVEL_ROUTE_MODE_UNSUPPORTED_WARNING",
    "TravelRouteProviders",
    "TravelRouteQuery",
    "TravelRouteTool",
    "TravelRouteToolResult",
    "summarize_fanout",
]
