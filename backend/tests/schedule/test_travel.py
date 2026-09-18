"""일정 구간 이동시간 확정 경로. (TP-216)"""

from __future__ import annotations

import pytest

from app.config import Settings
from app.domain.schedule_travel import (
    ScheduleTravelCandidate,
    ScheduleTravelEdge,
    ScheduleTravelWarning,
    SegmentWeather,
    TravelConfidence,
)
from app.domain.travel_route import (
    GeoCoordinate,
    RouteDestination,
    RouteSource,
    RouteStatus,
    TravelMode,
    TravelRoute,
    TravelRouteBatch,
)
from app.place_search_policy import (
    NON_WALKING_SPEED_KM_PER_MINUTE,
    WALKING_SPEED_KM_PER_MINUTE,
)
from app.providers.contracts import ProviderSource, ProviderStatus, provider_result
from app.schedule.travel import (
    NON_WALKING_SPEED_MPS,
    WALKING_SPEED_MPS,
    consecutive_pairs,
    is_measured,
    resolve_schedule_travel_edges,
    summarize_schedule_travel,
    travel_minutes_from_edges,
)
from app.schemas import Transport, UserConditions
from app.tools.schedule_travel import SCHEDULE_TRAVEL_MEASURE_BUDGET_EXCEEDED_WARNING
from app.tools.travel_route import TravelRouteProviders, TravelRouteTool


def _candidate(place_id: str, longitude: float) -> ScheduleTravelCandidate:
    return ScheduleTravelCandidate(
        place_id=place_id,
        coordinate=GeoCoordinate(latitude=37.5, longitude=longitude),
    )


def _edge(from_id: str, to_id: str, minutes: int) -> ScheduleTravelEdge:
    return ScheduleTravelEdge(
        from_place_id=from_id,
        to_place_id=to_id,
        mode=TravelMode.WALKING,
        status=RouteStatus.SUCCESS,
        source=RouteSource.KAKAO_WALKING,
        duration_min=minutes,
        distance_m=1000,
        confidence=TravelConfidence.HIGH,
    )


_MEASURED_SOURCE = {
    TravelMode.WALKING: RouteSource.KAKAO_WALKING,
    TravelMode.DRIVING: RouteSource.NAVER_DRIVING,
    TravelMode.TRANSIT: RouteSource.KAKAO_TRANSIT,
}


class _MeasuringRouteProvider:
    """실측이 성공한 상황. 구간마다 고정 소요시간을 돌려준다."""

    def __init__(self, duration_seconds: int = 600) -> None:
        self._duration_seconds = duration_seconds

    async def get_routes(
        self,
        origin: GeoCoordinate,
        destinations: tuple[RouteDestination, ...],
        *,
        mode: TravelMode = TravelMode.WALKING,
        radius_m: int | None = None,
    ):
        routes = tuple(
            TravelRoute(
                place_id=item.place_id,
                mode=mode,
                status=RouteStatus.SUCCESS,
                source=_MEASURED_SOURCE[mode],
                distance_m=1_200,
                duration_seconds=self._duration_seconds,
            )
            for item in destinations
        )
        return provider_result(
            TravelRouteBatch(routes=routes),
            source=ProviderSource.KAKAO_WALKING_ROUTE,
            status=ProviderStatus.SUCCESS,
        )


def _tool(
    provider: object | None = None,
    modes=(TravelMode.WALKING, TravelMode.TRANSIT),
) -> TravelRouteTool:
    primary = provider or _MeasuringRouteProvider()
    return TravelRouteTool(
        {mode: TravelRouteProviders(primary=primary) for mode in modes}
    )


class Test속도_상수:
    def test_반경_산정과_같은_가정을_환산한다(self) -> None:
        """Settings 값(1.2 / 5.5 mps)이 아니라 place_search_policy를 쓴다.

        차이가 3% 남짓이라 화면으로도 다른 테스트로도 안 잡힌다 — 여기서 못 박는다.
        """

        assert WALKING_SPEED_MPS == pytest.approx(WALKING_SPEED_KM_PER_MINUTE * 1000 / 60)
        assert NON_WALKING_SPEED_MPS == pytest.approx(
            NON_WALKING_SPEED_KM_PER_MINUTE * 1000 / 60
        )

    def test_Settings_기본값과_다르다(self) -> None:
        settings = Settings()
        assert WALKING_SPEED_MPS != pytest.approx(settings.walking_speed_mps)
        assert NON_WALKING_SPEED_MPS != pytest.approx(settings.driving_speed_mps)


class Test구간_뽑기:
    def test_방문_순서의_인접_쌍만_만든다(self) -> None:
        pairs = consecutive_pairs(["a", "b", "c"])
        assert [(p.from_place_id, p.to_place_id) for p in pairs] == [("a", "b"), ("b", "c")]

    def test_자기_자신으로_가는_구간은_버린다(self) -> None:
        assert consecutive_pairs(["a", "a", "b"]) == (
            *consecutive_pairs(["a", "b"]),
        )

    def test_한_곳이면_구간이_없다(self) -> None:
        assert consecutive_pairs(["a"]) == ()


class Test구간표_읽기:
    def test_방향을_지킨다(self) -> None:
        resolve = travel_minutes_from_edges([_edge("a", "b", 12)])
        assert resolve("a", "b") == 12
        # 실측은 왕복이 다를 수 있어 반대 방향으로 대신 답하지 않는다.
        assert resolve("b", "a") is None

    def test_표에_없으면_None이다(self) -> None:
        resolve = travel_minutes_from_edges([_edge("a", "b", 12)])
        assert resolve("a", "c") is None

    def test_0분_구간도_최소_1분으로_올린다(self) -> None:
        resolve = travel_minutes_from_edges([_edge("a", "b", 0)])
        assert resolve("a", "b") == 1


class Test구간_이동정보_확정:
    _conditions = UserConditions(transport=Transport.WALK)

    @pytest.mark.asyncio
    async def test_좌표가_없으면_빈_결과다(self) -> None:
        edges = await resolve_schedule_travel_edges(
            candidates=[],
            place_ids=["a", "b"],
            conditions=self._conditions,
            settings=Settings(),
            travel_route_tool=None,
        )
        assert edges == ()

    @pytest.mark.asyncio
    async def test_한_곳뿐이면_구간을_만들지_않는다(self) -> None:
        edges = await resolve_schedule_travel_edges(
            candidates=[_candidate("a", 127.0)],
            place_ids=["a"],
            conditions=self._conditions,
            settings=Settings(),
            travel_route_tool=None,
        )
        assert edges == ()

    @pytest.mark.asyncio
    async def test_경로_Tool이_없으면_추정만_돌려준다(self) -> None:
        edges = await resolve_schedule_travel_edges(
            candidates=[_candidate("a", 127.0), _candidate("b", 127.01)],
            place_ids=["a", "b"],
            conditions=self._conditions,
            settings=Settings(),
            travel_route_tool=None,
        )
        assert len(edges) == 1
        assert edges[0].source is RouteSource.STRAIGHT_LINE_ESTIMATE
        assert edges[0].confidence is TravelConfidence.LOW

    @pytest.mark.asyncio
    async def test_경로_Tool이_있으면_실측이_추정을_덮는다(self) -> None:
        edges = await resolve_schedule_travel_edges(
            candidates=[_candidate("a", 127.0), _candidate("b", 127.01)],
            place_ids=["a", "b"],
            conditions=self._conditions,
            settings=Settings(),
            travel_route_tool=_tool(),
        )
        assert len(edges) == 1
        assert edges[0].source is RouteSource.KAKAO_WALKING
        assert edges[0].confidence is TravelConfidence.HIGH
        assert edges[0].duration_min == 10

    @pytest.mark.asyncio
    async def test_후보에_없는_place_id가_섞여도_편성을_막지_않는다(self) -> None:
        edges = await resolve_schedule_travel_edges(
            candidates=[_candidate("a", 127.0)],
            place_ids=["a", "unknown"],
            conditions=self._conditions,
            settings=Settings(),
            travel_route_tool=None,
        )
        # 그 구간만 빠지고 예외가 오르지 않는다.
        assert edges == ()

    @pytest.mark.asyncio
    async def test_실측이_통째로_실패해도_추정으로_낸다(self) -> None:
        class _Exploding:
            async def get_routes(self, *args, **kwargs):
                raise RuntimeError("boom")

        tool = _tool(_Exploding())
        edges = await resolve_schedule_travel_edges(
            candidates=[_candidate("a", 127.0), _candidate("b", 127.01)],
            place_ids=["a", "b"],
            conditions=self._conditions,
            settings=Settings(),
            travel_route_tool=tool,
        )
        assert len(edges) == 1
        assert edges[0].duration_min > 0


def _estimated_edge(from_id: str, to_id: str, minutes: int = 12) -> ScheduleTravelEdge:
    return ScheduleTravelEdge(
        from_place_id=from_id,
        to_place_id=to_id,
        mode=TravelMode.TRANSIT,
        status=RouteStatus.SUCCESS,
        source=RouteSource.STRAIGHT_LINE_ESTIMATE,
        duration_min=minutes,
        distance_m=4_300,
        confidence=TravelConfidence.LOW,
    )


class Test실측_판정:
    def test_confidence로_판정한다(self) -> None:
        assert is_measured(_edge("a", "b", 10)) is True
        assert is_measured(_estimated_edge("a", "b")) is False


class Test지표_요약:
    def test_실측과_추정_구간을_센다(self) -> None:
        summary = summarize_schedule_travel(
            edges=[_edge("a", "b", 10), _estimated_edge("b", "c")],
            warnings=[],
            measure_attempted=True,
        )
        assert summary["segments"] == 2
        assert summary["measured"] == 1
        assert summary["estimated"] == 1
        assert summary["measured_ratio"] == 0.5
        assert summary["by_mode"] == {"transit": 1, "walking": 1}
        # 추정이 섞인 턴은 열어볼 이유가 있다.
        assert summary["level"] == "WARNING"

    def test_전부_실측이면_경고_수준이_아니다(self) -> None:
        summary = summarize_schedule_travel(
            edges=[_edge("a", "b", 10)], warnings=[], measure_attempted=True
        )
        assert summary["measured_ratio"] == 1.0
        assert summary["level"] == "DEFAULT"

    def test_실측을_시도하지_않은_턴은_성공률이_None이다(self) -> None:
        """0.0으로 적으면 "전부 실패한 턴"과 구분되지 않아 추세가 왜곡된다."""

        summary = summarize_schedule_travel(
            edges=[_estimated_edge("a", "b")], warnings=[], measure_attempted=False
        )
        assert summary["measured_ratio"] is None
        assert summary["measure_attempted"] is False
        # 실측을 안 한 것은 이상 상황이 아니다.
        assert summary["level"] == "DEFAULT"
        assert "실측 미시도" in summary["headline"]

    def test_구간이_없으면_성공률이_None이다(self) -> None:
        summary = summarize_schedule_travel(
            edges=[], warnings=[], measure_attempted=True
        )
        assert summary["segments"] == 0
        assert summary["measured_ratio"] is None

    def test_상한_초과_구간을_센다(self) -> None:
        summary = summarize_schedule_travel(
            edges=[_edge("a", "b", 10), _estimated_edge("b", "c")],
            warnings=[
                ScheduleTravelWarning(
                    code=SCHEDULE_TRAVEL_MEASURE_BUDGET_EXCEEDED_WARNING,
                    from_place_id="b",
                    to_place_id="c",
                )
            ],
            measure_attempted=True,
        )
        assert summary["budget_exceeded"] == 1
        assert summary["warning_codes"] == {
            SCHEDULE_TRAVEL_MEASURE_BUDGET_EXCEEDED_WARNING: 1
        }
        assert "상한초과 1" in summary["headline"]

    def test_place_id와_좌표를_싣지_않는다(self) -> None:
        """어디를 갔느냐는 이 요약의 관심사가 아니다(원문 수집 스위치 우회 방지)."""

        summary = summarize_schedule_travel(
            edges=[_edge("place-a", "place-b", 10)],
            warnings=[
                ScheduleTravelWarning(
                    code=SCHEDULE_TRAVEL_MEASURE_BUDGET_EXCEEDED_WARNING,
                    from_place_id="place-a",
                    to_place_id="place-b",
                )
            ],
            measure_attempted=True,
        )
        assert "place-a" not in repr(summary)
        assert "place-b" not in repr(summary)


class Test지표_방출:
    """완료 조건 "실측 성공률과 예산 초과 빈도 지표가 나간다"의 배선 가드.

    Score는 꺼져 있으면 no-op이라 값만 봐서는 호출 자체가 사라진 것을 알 수 없다.
    """

    _conditions = UserConditions(transport=Transport.WALK)

    @pytest.mark.asyncio
    async def test_실측을_시도한_턴에_두_지표가_나간다(self, monkeypatch) -> None:
        recorded: list[tuple[str, float | bool]] = []
        monkeypatch.setattr(
            "app.schedule.travel.record_score",
            lambda name, value: recorded.append((name, value)),
        )

        await resolve_schedule_travel_edges(
            candidates=[_candidate("a", 127.0), _candidate("b", 127.01)],
            place_ids=["a", "b"],
            conditions=self._conditions,
            settings=Settings(),
            travel_route_tool=_tool(),
        )

        assert ("schedule_travel_measured_ratio", 1.0) in recorded
        assert ("schedule_travel_budget_exceeded", False) in recorded

    @pytest.mark.asyncio
    async def test_경로_Tool이_없으면_성공률을_적지_않는다(self, monkeypatch) -> None:
        recorded: list[tuple[str, float | bool]] = []
        monkeypatch.setattr(
            "app.schedule.travel.record_score",
            lambda name, value: recorded.append((name, value)),
        )

        await resolve_schedule_travel_edges(
            candidates=[_candidate("a", 127.0), _candidate("b", 127.01)],
            place_ids=["a", "b"],
            conditions=self._conditions,
            settings=Settings(),
            travel_route_tool=None,
        )

        assert recorded == []

    @pytest.mark.asyncio
    async def test_상한을_넘기면_초과_지표가_참으로_나간다(self, monkeypatch) -> None:
        recorded: list[tuple[str, float | bool]] = []
        monkeypatch.setattr(
            "app.schedule.travel.record_score",
            lambda name, value: recorded.append((name, value)),
        )

        await resolve_schedule_travel_edges(
            candidates=[
                _candidate("a", 127.0),
                _candidate("b", 127.01),
                _candidate("c", 127.02),
            ],
            place_ids=["a", "b", "c"],
            conditions=self._conditions,
            settings=Settings(schedule_max_measured_segments=1),
            travel_route_tool=_tool(),
        )

        assert ("schedule_travel_budget_exceeded", True) in recorded
        assert ("schedule_travel_measured_ratio", 0.5) in recorded

    @pytest.mark.asyncio
    async def test_관측이_실패해도_편성은_계속된다(self, monkeypatch) -> None:
        def _explode(name: str, value: float | bool) -> None:
            raise RuntimeError("langfuse down")

        monkeypatch.setattr("app.schedule.travel.record_score", _explode)

        edges = await resolve_schedule_travel_edges(
            candidates=[_candidate("a", 127.0), _candidate("b", 127.01)],
            place_ids=["a", "b"],
            conditions=self._conditions,
            settings=Settings(),
            travel_route_tool=_tool(),
        )

        assert len(edges) == 1
        assert edges[0].confidence is TravelConfidence.HIGH


class Test판정_배선:
    """TP-226 — 판정 단계가 실제 확정 경로를 지나가는지.

    이 카드는 판정하는 쪽을 주입하지 않으므로 결과가 배선 전과 같아야 한다.
    그래서 "안 바뀐다"와 "그래도 표는 실제로 흐른다"를 함께 본다.
    """

    _far = [_candidate("a", 127.0), _candidate("b", 127.03)]  # 임계를 넘는 거리

    @pytest.mark.asyncio
    async def test_판정을_주입하지_않으면_기존_규칙과_같다(self) -> None:
        edges = await resolve_schedule_travel_edges(
            candidates=self._far,
            place_ids=["a", "b"],
            conditions=UserConditions(),
            settings=Settings(),
            travel_route_tool=None,
        )
        # 이동수단을 말하지 않았고 도보 예상시간이 임계를 넘으므로 대중교통이다.
        assert [edge.mode for edge in edges] == [TravelMode.TRANSIT]

    @pytest.mark.asyncio
    async def test_주입한_판정이_실제_구간_이동수단을_바꾼다(self) -> None:
        """표가 확정 경로까지 실제로 흐르는지 본다.

        값을 날랐는데 아무도 안 읽으면 다음 카드에서 LLM을 붙여도 결과가 안 바뀐다.
        그 실패는 테스트도 로그도 통과하므로 여기서 못 박는다.
        """

        class _AlwaysWalking:
            def __init__(self) -> None:
                self.calls = 0

            async def judge(self, segments, context):
                self.calls += 1
                return [TravelMode.WALKING] * len(segments)

        judge = _AlwaysWalking()
        edges = await resolve_schedule_travel_edges(
            candidates=self._far,
            place_ids=["a", "b"],
            conditions=UserConditions(),
            settings=Settings(),
            travel_route_tool=None,
            mode_judge=judge,
        )

        assert judge.calls == 1
        # 규칙대로면 대중교통인 구간을 판정이 도보로 뒤집었다.
        assert [edge.mode for edge in edges] == [TravelMode.WALKING]

    @pytest.mark.asyncio
    async def test_조회된_날씨와_동행_무장애가_판정_조건까지_닿는다(self) -> None:
        """필드만 뚫고 소비 측에 안 닿는 상태를 막는다."""
        received: list[object] = []

        class _Recording:
            async def judge(self, segments, context):
                received.append(context)
                return [TravelMode.WALKING] * len(segments)

        weather = SegmentWeather(precipitation="rain", sky="overcast", temperature_celsius=8.0)
        await resolve_schedule_travel_edges(
            candidates=self._far,
            place_ids=["a", "b"],
            conditions=UserConditions(
                companion="parent", accessibility_needs=["stroller_access"]
            ),
            weather=weather,
            settings=Settings(),
            travel_route_tool=None,
            mode_judge=_Recording(),
        )

        context = received[0]
        assert context.weather == weather
        assert context.companion == "parent"
        assert context.accessibility_needs == ("stroller_access",)

    @pytest.mark.asyncio
    async def test_날씨가_없어도_편성이_그대로_돈다(self) -> None:
        edges = await resolve_schedule_travel_edges(
            candidates=self._far,
            place_ids=["a", "b"],
            conditions=UserConditions(),
            weather=None,
            settings=Settings(),
            travel_route_tool=None,
        )
        assert len(edges) == 1
