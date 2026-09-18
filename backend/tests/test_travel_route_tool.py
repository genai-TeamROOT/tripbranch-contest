from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from datetime import UTC, datetime

import pytest

from app.domain.travel_route import (
    GeoCoordinate,
    RouteDestination,
    RouteSource,
    RouteStatus,
    TravelMode,
    TravelRoute,
    TravelRouteBatch,
)
from app.errors import ProviderTimeoutError
from app.providers.contracts import (
    ProviderSource,
    ProviderStatus,
    provider_result,
)
from app.providers.walking_route import FakeWalkingRouteProvider
from app.tools import travel_route as travel_route_tool
from app.tools.contracts import ToolStatus
from app.tools.travel_route import (
    TRAVEL_ROUTE_FALLBACK_WARNING,
    TRAVEL_ROUTE_MODE_UNSUPPORTED_WARNING,
    TravelRouteProviders,
    TravelRouteQuery,
    TravelRouteTool,
    TravelRouteToolResult,
    summarize_fanout,
)

_NOW = datetime(2026, 8, 18, tzinfo=UTC)


def _tool(primary, fallback=None) -> TravelRouteTool:
    """도보만 등록한 Tool. 실제 factory 구성과 같은 모양이다."""
    return TravelRouteTool(
        {TravelMode.WALKING: TravelRouteProviders(primary=primary, fallback=fallback)}
    )


def _query() -> TravelRouteQuery:
    return TravelRouteQuery(
        origin=GeoCoordinate(37.57, 126.98),
        destinations=(
            RouteDestination("first", GeoCoordinate(37.571, 126.981)),
            RouteDestination("second", GeoCoordinate(37.572, 126.982)),
        ),
        mode=TravelMode.WALKING,
    )


class _PartialProvider:
    async def get_routes(self, origin, destinations, *, mode=TravelMode.WALKING, radius_m=None):
        return provider_result(
            TravelRouteBatch(
                routes=(
                    TravelRoute(
                        place_id="first",
                        mode=TravelMode.WALKING,
                        status=RouteStatus.SUCCESS,
                        source=RouteSource.KAKAO_WALKING,
                        distance_m=100,
                        duration_seconds=90,
                    ),
                    TravelRoute(
                        place_id="second",
                        mode=TravelMode.WALKING,
                        status=RouteStatus.NO_DATA,
                        source=RouteSource.KAKAO_WALKING,
                        error_code="kakao_result_104",
                    ),
                )
            ),
            source=ProviderSource.KAKAO_WALKING_ROUTE,
            status=ProviderStatus.PARTIAL,
            clock=lambda: _NOW,
        )


class _FailingProvider:
    async def get_routes(self, origin, destinations, *, mode=TravelMode.WALKING, radius_m=None):
        raise ProviderTimeoutError("Kakao Walking Route")


@pytest.mark.asyncio
async def test_travel_route_tool_fills_only_failed_destination() -> None:
    result = await _tool(
        _PartialProvider(),
        FakeWalkingRouteProvider(walking_speed_mps=1.2),
    ).execute(_query())

    assert result.status is ToolStatus.PARTIAL
    assert [route.place_id for route in result.routes] == ["first", "second"]
    assert result.routes[0].source is RouteSource.KAKAO_WALKING
    assert result.routes[0].distance_m == 100
    assert result.routes[1].source is RouteSource.STRAIGHT_LINE_ESTIMATE
    assert result.warnings == (TRAVEL_ROUTE_FALLBACK_WARNING,)
    assert [metadata.source for metadata in result.provider_metadata] == [
        ProviderSource.KAKAO_WALKING_ROUTE,
        ProviderSource.FAKE_WALKING_ROUTE,
    ]


@pytest.mark.asyncio
async def test_travel_route_tool_logs_when_estimate_replaces_real_route(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """폴백 대체는 예외 없이 일어나므로, 로그가 유일한 노출 경로다(D-042)."""
    with caplog.at_level(logging.WARNING, logger="app.tools.travel_route"):
        await _tool(
            _PartialProvider(),
            FakeWalkingRouteProvider(walking_speed_mps=1.2),
        ).execute(_query())

    messages = [record.getMessage() for record in caplog.records]
    assert any("1/2건을 직선거리 추정으로 대체" in message for message in messages)
    assert any("kakao_result_104" in message for message in messages)


@pytest.mark.asyncio
async def test_travel_route_tool_does_not_log_fallback_when_all_routes_succeed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="app.tools.travel_route"):
        await _tool(
            FakeWalkingRouteProvider(walking_speed_mps=1.2),
            FakeWalkingRouteProvider(walking_speed_mps=1.2),
        ).execute(_query())

    assert caplog.records == []


@pytest.mark.asyncio
async def test_travel_route_tool_falls_back_all_on_primary_failure() -> None:
    result = await _tool(
        _FailingProvider(),
        FakeWalkingRouteProvider(walking_speed_mps=1.2),
    ).execute(_query())

    assert result.status is ToolStatus.PARTIAL
    assert len(result.routes) == 2
    assert all(route.source is RouteSource.STRAIGHT_LINE_ESTIMATE for route in result.routes)
    assert result.warnings == (TRAVEL_ROUTE_FALLBACK_WARNING,)


@pytest.mark.asyncio
async def test_travel_route_tool_returns_unavailable_without_fallback() -> None:
    result = await _tool(_FailingProvider()).execute(_query())

    assert result.status is ToolStatus.UNAVAILABLE
    assert result.routes == ()
    assert result.error is not None
    assert result.error.cause == "timeout"


@pytest.mark.asyncio
async def test_travel_route_tool_fake_mode_is_normal_success() -> None:
    result = await _tool(FakeWalkingRouteProvider(walking_speed_mps=1.2)).execute(_query())

    assert result.status is ToolStatus.SUCCESS
    assert result.warnings == ()
    assert len(result.provider_metadata) == 1


def test_travel_route_query_rejects_duplicate_place_ids() -> None:
    coordinate = GeoCoordinate(37.57, 126.98)
    with pytest.raises(ValueError, match="중복"):
        TravelRouteQuery(
            origin=coordinate,
            destinations=(
                RouteDestination("same", coordinate),
                RouteDestination("same", coordinate),
            ),
            mode=TravelMode.WALKING,
        )


class _CountingProvider:
    """호출 여부만 세는 Provider — 미등록 이동수단에서 호출이 없어야 한다."""

    def __init__(self) -> None:
        self.calls = 0

    async def get_routes(self, origin, destinations, *, mode=TravelMode.WALKING, radius_m=None):
        self.calls += 1
        raise AssertionError("미등록 이동수단에서는 Provider가 호출되지 않아야 한다.")


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [TravelMode.TRANSIT, TravelMode.DRIVING])
async def test_travel_route_tool_returns_no_data_for_unregistered_mode(mode: TravelMode) -> None:
    """미등록 이동수단은 조회도, 추정 대체도 하지 않는다.

    도보 속도로 추정한 값을 자동차 실측인 척 내보내면 D-042가 막으려던 상황이
    된다. 값이 없으면 소비 측이 직선거리로 돌아가므로 없는 편이 안전하다.
    """
    primary = _CountingProvider()
    fallback = _CountingProvider()
    tool = TravelRouteTool(
        {TravelMode.WALKING: TravelRouteProviders(primary=primary, fallback=fallback)}
    )

    result = await tool.execute(
        TravelRouteQuery(
            origin=GeoCoordinate(37.57, 126.98),
            destinations=(RouteDestination("first", GeoCoordinate(37.571, 126.981)),),
            mode=mode,
        )
    )

    assert result.status is ToolStatus.NO_DATA
    assert result.routes == ()
    assert result.warnings == (TRAVEL_ROUTE_MODE_UNSUPPORTED_WARNING,)
    assert (primary.calls, fallback.calls) == (0, 0)


@pytest.mark.asyncio
async def test_travel_route_tool_passes_requested_mode_to_provider() -> None:
    captured: list[TravelMode] = []

    class _RecordingProvider:
        async def get_routes(self, origin, destinations, *, mode, radius_m=None):
            captured.append(mode)
            return await FakeWalkingRouteProvider(walking_speed_mps=1.2).get_routes(
                origin, destinations, mode=mode, radius_m=radius_m
            )

    await _tool(_RecordingProvider()).execute(_query())

    assert captured == [TravelMode.WALKING]


def test_travel_route_tool_rejects_empty_provider_registry() -> None:
    with pytest.raises(ValueError, match="등록되지 않았습니다"):
        TravelRouteTool({})


# --- 관측: 팬아웃 하나를 span 하나로 접는다 -----------------------------------
#
# **왜 호출 단위가 아니라 팬아웃 단위인가**: Provider가 목적지마다 따로 요청을
# 쏜다(`asyncio.gather`). 후보 20곳이면 HTTP도 20번이라, 호출 하나를 span 하나로
# 만들면 한 턴 observation이 5개에서 25개로 뛴다. 무료 티어 유닛을 그대로 먹는다.


def _route(place_id: str, source: RouteSource, *, status=RouteStatus.SUCCESS, error=None):
    return TravelRoute(
        place_id=place_id,
        mode=TravelMode.WALKING,
        status=status,
        source=source,
        distance_m=500,
        duration_seconds=420,
        error_code=error,
    )


def _result(routes, *, status=ToolStatus.SUCCESS, warnings=()):
    return TravelRouteToolResult(status=status, routes=tuple(routes), warnings=tuple(warnings))


def test_summary_counts_measured_and_estimated_separately() -> None:
    """이 비율이 요약의 존재 이유다.

    실측이 있는 후보는 소요시간으로, 없는 후보는 직선거리로 채점된다
    (`domain/scoring.py::_proximity_score`). 추정으로 샌 건수만큼 **같은 순위표
    안에 서로 다른 자가 섞인다.**
    """
    summary = summarize_fanout(
        _query(),
        _result(
            [
                _route("first", RouteSource.KAKAO_WALKING),
                _route("second", RouteSource.STRAIGHT_LINE_ESTIMATE),
            ],
            status=ToolStatus.PARTIAL,
            warnings=(TRAVEL_ROUTE_FALLBACK_WARNING,),
        ),
    )

    assert summary["requested"] == 2
    assert summary["measured"] == 1
    assert summary["estimated"] == 1
    assert summary["measured_ratio"] == 0.5
    assert summary["by_source"] == {"kakao_walking": 1, "straight_line_estimate": 1}


def test_summary_flags_estimated_fallback_as_a_warning() -> None:
    """전부 채워졌어도 추정이 섞였으면 정상이 아니다 — 화면에서 눈에 띄어야 한다."""
    summary = summarize_fanout(
        _query(),
        _result(
            [
                _route("first", RouteSource.KAKAO_WALKING),
                _route("second", RouteSource.STRAIGHT_LINE_ESTIMATE),
            ],
            status=ToolStatus.PARTIAL,
        ),
    )

    assert summary["level"] == "WARNING"


def test_summary_stays_default_when_everything_was_measured() -> None:
    summary = summarize_fanout(
        _query(),
        _result(
            [
                _route("first", RouteSource.KAKAO_WALKING),
                _route("second", RouteSource.KAKAO_WALKING),
            ]
        ),
    )

    assert summary["level"] == "DEFAULT"
    assert summary["measured_ratio"] == 1.0


def test_summary_headline_survives_masking() -> None:
    """`status_message`는 mask를 안 탄다 — `capture_content`를 꺼도 남는 유일한 자리다."""
    summary = summarize_fanout(
        _query(),
        _result([_route("first", RouteSource.STRAIGHT_LINE_ESTIMATE)], status=ToolStatus.PARTIAL),
    )

    assert summary["headline"] == "walking 2건 요청 · 실측 0 · 추정 1"


def test_summary_carries_no_coordinates_or_place_ids() -> None:
    """여기서 알고 싶은 건 개수와 분포지 어디를 갔느냐가 아니다."""
    summary = summarize_fanout(
        _query(),
        _result([_route("first", RouteSource.KAKAO_WALKING)]),
    )

    blob = json.dumps(summary, ensure_ascii=False)
    assert "126.98" not in blob
    assert "37.57" not in blob
    assert "first" not in blob


def test_summary_groups_failure_causes() -> None:
    summary = summarize_fanout(
        _query(),
        _result(
            [
                _route(
                    "first", RouteSource.KAKAO_WALKING, status=RouteStatus.NO_DATA, error="no_path"
                ),
                _route(
                    "second", RouteSource.KAKAO_WALKING, status=RouteStatus.NO_DATA, error="no_path"
                ),
            ],
            status=ToolStatus.NO_DATA,
        ),
    )

    assert summary["error_causes"] == {"no_path": 2}
    assert summary["by_status"] == {"no_data": 2}


@pytest.mark.asyncio
async def test_execute_records_one_span_for_the_whole_fanout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """목적지가 둘이어도 span은 하나여야 한다."""
    opened: list[str] = []
    recorded: list[dict] = []

    class _Step:
        def record(self, **fields) -> None:
            recorded.append(fields)

    @contextmanager
    def _fake_observe_step(name: str, **_):
        opened.append(name)
        yield _Step()

    monkeypatch.setattr(travel_route_tool, "observe_step", _fake_observe_step)

    await _tool(FakeWalkingRouteProvider(walking_speed_mps=1.2)).execute(_query())

    assert opened == ["travel_route"]
    assert recorded[0]["status_message"].startswith("walking 2건 요청")


@pytest.mark.asyncio
async def test_observation_failure_does_not_break_the_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """관측이 삼켜도 되는 건 자기 실패뿐이다 — 결과는 그대로 나가야 한다."""

    def _explode(*_args, **_kwargs):
        raise RuntimeError("요약 실패")

    monkeypatch.setattr(travel_route_tool, "summarize_fanout", _explode)

    result = await _tool(FakeWalkingRouteProvider(walking_speed_mps=1.2)).execute(_query())

    assert result.status is ToolStatus.SUCCESS
    assert len(result.routes) == 2


def test_summary_names_the_cause_when_everything_fell_back() -> None:
    """전부 추정으로 대체된 턴에서 "왜"에 답하는 값이다.

    2026-08-25에 도보 실측이 0건인 걸 span으로 보고도 원인(카카오 쿼터 초과)은
    서버 로그를 따로 뒤져야 알았다. `_fallback_all()`이 primary 에러를 결과에 안
    담고 logger.warning으로만 남겼기 때문이다. 그 왕복을 없앤다.
    """
    result = TravelRouteToolResult(
        status=ToolStatus.PARTIAL,
        # 대체가 끝난 routes다 — 전부 추정 성공이라 error_code가 비어 있다.
        # 여기서 원인을 읽으려 하면 계속 빈칸이 나온다(2026-08-25에 그렇게 짰다).
        routes=(_route("first", RouteSource.STRAIGHT_LINE_ESTIMATE),),
        warnings=(TRAVEL_ROUTE_FALLBACK_WARNING,),
        fallback_causes=(("provider_quota_exceeded", 1),),
    )

    summary = summarize_fanout(_query(), result)

    assert summary["error_causes"] == {"provider_quota_exceeded": 1}
    assert summary["primary_cause"] == "provider_quota_exceeded"
    # status_message는 mask를 안 탄다 — 원문 수집을 꺼도 원인이 남아야 한다.
    assert str(summary["headline"]).endswith("provider_quota_exceeded")


@pytest.mark.asyncio
async def test_whole_fanout_failure_carries_the_primary_error(caplog) -> None:
    """primary가 통째로 죽으면 그 원인이 결과에 실려야 한다."""

    class _Dead:
        async def get_routes(self, origin, destinations, *, mode=TravelMode.WALKING, radius_m=None):
            raise ProviderTimeoutError("Kakao")

    tool = _tool(_Dead(), FakeWalkingRouteProvider(walking_speed_mps=1.2))
    result = await tool.execute(_query())

    assert result.status is ToolStatus.PARTIAL
    assert result.error is not None
    summary = summarize_fanout(_query(), result)
    assert summary["error_code"] == result.error.code
    # primary_cause는 Provider가 낸 원본 코드다 — ToolError.code("unavailable")보다
    # 화면에서 원인을 좁히는 데 낫다.
    assert summary["primary_cause"] == "provider_timeout"
    assert summary["estimated"] == 2


@pytest.mark.asyncio
async def test_per_destination_failures_surface_their_cause() -> None:
    """실제로 타는 경로는 이쪽이다 — primary가 예외 없이 목적지별로 실패한다.

    2026-08-25에 카카오 도보 쿼터가 초과됐을 때 이 분기를 탔다. `_fallback_all`이
    아니라서 primary 에러가 결과에 없었고, span은 "추정 3건"까지만 말하고 원인은
    빈칸이었다. 대시보드를 보고도 서버 로그를 따로 뒤져야 했다.
    """

    class _PerDestinationFailure:
        async def get_routes(self, origin, destinations, *, mode=TravelMode.WALKING, radius_m=None):
            return provider_result(
                TravelRouteBatch(
                    routes=tuple(
                        TravelRoute(
                            place_id=d.place_id,
                            mode=mode,
                            status=RouteStatus.UNAVAILABLE,
                            source=RouteSource.KAKAO_WALKING,
                            error_code="provider_quota_exceeded",
                        )
                        for d in destinations
                    )
                ),
                source=ProviderSource.KAKAO_WALKING_ROUTE,
                status=ProviderStatus.NO_DATA,
            )

    tool = _tool(_PerDestinationFailure(), FakeWalkingRouteProvider(walking_speed_mps=1.2))
    result = await tool.execute(_query())

    assert result.fallback_causes == (("provider_quota_exceeded", 2),)
    summary = summarize_fanout(_query(), result)
    assert summary["measured_ratio"] == 0
    assert summary["error_causes"] == {"provider_quota_exceeded": 2}
    assert str(summary["headline"]).endswith("provider_quota_exceeded")
