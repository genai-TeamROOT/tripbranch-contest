"""구간·후보 이동수단을 조건 전체를 보고 정하는 판정. (TP-227)"""

from __future__ import annotations

import pytest

from app.domain.schedule_travel import (
    ModeJudgmentContext,
    SegmentModeInput,
    SegmentWeather,
)
from app.domain.travel_route import TravelMode
from app.providers.gemini_prompts import (
    build_mode_judge_instruction,
    format_mode_judge_context,
)
from app.schemas import Transport, UserConditions
from app.services.runtime.recommendation_transform import (
    modes_for_judged_choice,
    to_measured_travel_modes,
)
from app.tools.mode_judge import (
    TRAVEL_RELEVANT_ACCESSIBILITY_NEEDS,
    LlmModeJudge,
    narrow_accessibility_needs,
)


def _segment(order: int, *, distance_m: int, walk_minutes: float) -> SegmentModeInput:
    return SegmentModeInput(
        from_place_id=f"p{order}",
        to_place_id=f"p{order + 1}",
        order=order,
        distance_m=distance_m,
        walk_minutes=walk_minutes,
    )


class Test무장애_좁히기:
    """9개 중 이동에 관련되는 셋만 판정에 넘긴다."""

    def test_장소_조건_여섯_개는_뺀다(self) -> None:
        needs = [
            "wheelchair_access",
            "accessible_restroom",
            "infant_facilities",
            "stroller_access",
            "visual_guide",
            "low_floor_transit",
        ]
        assert narrow_accessibility_needs(needs) == (
            "wheelchair_access",
            "stroller_access",
            "low_floor_transit",
        )

    def test_넘기는_어휘는_셋뿐이다(self) -> None:
        assert len(TRAVEL_RELEVANT_ACCESSIBILITY_NEEDS) == 3

    def test_이동과_무관한_요구만_있으면_비운다(self) -> None:
        assert narrow_accessibility_needs(["accessible_parking", "seating_available"]) == ()


class Test프롬프트_조립:
    _segments = (_segment(1, distance_m=1200, walk_minutes=18.0),)

    def test_조회하지_못한_값은_줄을_넣지_않는다(self) -> None:
        """'날씨: 없음'처럼 적으면 모델이 그것을 사실로 읽는다."""
        text = format_mode_judge_context(
            self._segments, ModeJudgmentContext(transport=None)
        )
        assert "날씨" not in text
        assert "동행" not in text
        assert "무장애" not in text
        assert "거리만 보고 정한다" in text

    def test_조회한_사실만_싣는다(self) -> None:
        text = format_mode_judge_context(
            self._segments,
            ModeJudgmentContext(
                transport=None,
                companion="parent",
                accessibility_needs=("stroller_access",),
                weather=SegmentWeather(precipitation="rain", sky="overcast"),
            ),
        )
        assert "강수 rain" in text
        assert "하늘 overcast" in text
        # 기온은 없었으므로 적히지 않는다.
        assert "기온" not in text

    def test_도보_시간이_직선_기준임을_밝힌다(self) -> None:
        """보정 없이 넘기므로 프롬프트가 그 사실을 들고 있어야 한다."""
        text = format_mode_judge_context(
            self._segments, ModeJudgmentContext(transport=None)
        )
        assert "직선 기준 도보 18분" in text
        assert "1.6배" in build_mode_judge_instruction()

    def test_순서대로인지_아닌지를_구분해_적는다(self) -> None:
        sequential = format_mode_judge_context(
            self._segments, ModeJudgmentContext(transport=None, sequential=True)
        )
        independent = format_mode_judge_context(
            self._segments, ModeJudgmentContext(transport=None, sequential=False)
        )
        assert "순서대로 이어진다" in sequential
        assert "서로 대안이다" in independent
        # 독립일 때 앞 줄을 근거로 삼지 말라는 지시가 규칙에 있어야 한다.
        assert "앞 줄을 근거로 삼지 않는다" in build_mode_judge_instruction()


class Test판정_결과_옮기기:
    """추천에서 판정이 답하는 질문은 '무엇으로 갈까'가 아니라 '대중교통도 재볼까'다."""

    def test_대중교통_판정은_양쪽을_모두_잰다(self) -> None:
        assert modes_for_judged_choice(None, TravelMode.TRANSIT) == (
            TravelMode.WALKING,
            TravelMode.TRANSIT,
        )

    def test_도보_판정은_도보만_잰다(self) -> None:
        assert modes_for_judged_choice(None, TravelMode.WALKING) == (TravelMode.WALKING,)

    def test_기존_거리_규칙과_같은_모양을_돌려준다(self) -> None:
        """양쪽 조회라는 D-118 설계를 판정이 바꾸지 않는다."""
        by_rule = to_measured_travel_modes(
            UserConditions(), straight_line_km=2.0, switch_threshold_km=0.85
        )
        assert modes_for_judged_choice(None, TravelMode.TRANSIT) == by_rule


class Test판정_구현:
    class _Provider:
        def __init__(self, modes: tuple[str, ...]) -> None:
            self.modes = modes
            self.calls = 0

        async def judge_travel_modes(self, segments, context):
            from app.providers.contracts import ProviderSource, provider_result

            self.calls += 1
            del segments, context
            return provider_result(self.modes, source=ProviderSource.FAKE_LLM)

    @pytest.mark.asyncio
    async def test_문자열을_이동수단으로_옮긴다(self) -> None:
        judge = LlmModeJudge(self._Provider(("walking", "transit")))
        decided = await judge.judge(
            (
                _segment(1, distance_m=100, walk_minutes=1.4),
                _segment(2, distance_m=200, walk_minutes=2.9),
            ),
            ModeJudgmentContext(transport=None),
        )
        assert list(decided) == [TravelMode.WALKING, TravelMode.TRANSIT]

    @pytest.mark.asyncio
    async def test_모르는_값은_버리지_않고_그대로_흘린다(self) -> None:
        """조용히 버리면 개수가 줄어 '몇 번째 구간이 이상했나'가 사라진다."""
        judge = LlmModeJudge(self._Provider(("walking", "자전거")))
        decided = await judge.judge(
            (
                _segment(1, distance_m=100, walk_minutes=1.4),
                _segment(2, distance_m=200, walk_minutes=2.9),
            ),
            ModeJudgmentContext(transport=None),
        )
        assert len(decided) == 2
        assert decided[1] == "자전거"


class Test스텁이_조건을_실제로_읽는다:
    """Fake가 조건을 안 읽으면 조건을 나르는 배선이 끊겨도 테스트가 통과한다."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("context", "expected"),
        [
            (ModeJudgmentContext(transport=None), TravelMode.WALKING),
            (
                ModeJudgmentContext(
                    transport=None,
                    weather=SegmentWeather(precipitation="rain"),
                ),
                TravelMode.TRANSIT,
            ),
            (
                ModeJudgmentContext(transport=None, companion="parent"),
                TravelMode.TRANSIT,
            ),
            (
                ModeJudgmentContext(
                    transport=None, accessibility_needs=("stroller_access",)
                ),
                TravelMode.TRANSIT,
            ),
        ],
    )
    async def test_조건이_있으면_먼_구간을_전환한다(
        self, context: ModeJudgmentContext, expected: TravelMode
    ) -> None:
        from app.providers.stub import FakeLLMProvider

        segments = (_segment(1, distance_m=1050, walk_minutes=15.0),)
        result = await FakeLLMProvider().judge_travel_modes(segments, context)
        assert result.data == (expected.value,)


class Test명시_이동수단_보호:
    @pytest.mark.parametrize("transport", [Transport.WALK, Transport.CAR])
    def test_명시하면_판정을_건너뛰는_대상이다(self, transport: Transport) -> None:
        from app.tools.schedule_travel import JUDGE_SKIPPED_TRANSPORTS

        assert transport in JUDGE_SKIPPED_TRANSPORTS


class Test추천_경로에서_판정이_실제로_불린다:
    """일정 쪽에서 판정이 한 번도 안 도는데 테스트가 통과한 적이 있어(TP-227) 못 박는다."""

    @pytest.mark.asyncio
    async def test_후보를_한_번에_넘기고_결과가_실측_수단이_된다(self) -> None:
        from app.domain.travel_route import GeoCoordinate, RouteDestination
        from app.providers.contracts import ProviderSource, provider_result
        from app.services.runtime.agent_runtime import _judge_recommendation_modes

        seen: list[tuple] = []

        class _Provider:
            async def judge_travel_modes(self, segments, context):
                seen.append((tuple(segments), context))
                return provider_result(
                    tuple("transit" for _ in segments), source=ProviderSource.FAKE_LLM
                )

        candidates = [
            (
                RouteDestination(
                    place_id=f"p{index}",
                    coordinate=GeoCoordinate(latitude=37.5, longitude=127.0),
                ),
                distance_km,
            )
            for index, distance_km in enumerate((0.3, 2.0), start=1)
        ]

        plan = await _judge_recommendation_modes(
            candidates,
            conditions=UserConditions(),
            context=_empty_context(),
            switch_threshold_km=0.85,
            llm=_Provider(),
        )

        # 후보 수만큼 부르지 않는다 — 한 번에 넘긴다.
        assert len(seen) == 1
        segments, context = seen[0]
        assert len(segments) == 2
        # 후보는 서로 대안이므로 앞 줄을 근거로 삼지 않게 한다.
        assert context.sequential is False
        # 대중교통 판정은 양쪽을 다 재는 것으로 옮겨진다(D-118의 양쪽 조회 유지).
        assert plan.measure["p1"] == (TravelMode.WALKING, TravelMode.TRANSIT)
        assert plan.measure["p2"] == (TravelMode.WALKING, TravelMode.TRANSIT)

    @pytest.mark.asyncio
    async def test_명시_이동수단이면_판정을_부르지_않는다(self) -> None:
        from app.domain.travel_route import GeoCoordinate, RouteDestination
        from app.services.runtime.agent_runtime import _judge_recommendation_modes

        class _Never:
            async def judge_travel_modes(self, segments, context):
                raise AssertionError("명시 이동수단에서는 판정을 부르면 안 된다")

        candidates = [
            (
                RouteDestination(
                    place_id="p1",
                    coordinate=GeoCoordinate(latitude=37.5, longitude=127.0),
                ),
                2.0,
            )
        ]
        plan = await _judge_recommendation_modes(
            candidates,
            conditions=UserConditions(transport=Transport.WALK),
            context=_empty_context(),
            switch_threshold_km=0.85,
            llm=_Never(),
        )
        assert plan.measure["p1"] == (TravelMode.WALKING,)

    @pytest.mark.asyncio
    async def test_판정이_실패하면_거리_규칙으로_되돌아간다(self) -> None:
        from app.domain.travel_route import GeoCoordinate, RouteDestination
        from app.errors import ProviderUnavailableError
        from app.services.runtime.agent_runtime import _judge_recommendation_modes

        class _Broken:
            async def judge_travel_modes(self, segments, context):
                raise ProviderUnavailableError("판정 실패")

        candidates = [
            (
                RouteDestination(
                    place_id="near",
                    coordinate=GeoCoordinate(latitude=37.5, longitude=127.0),
                ),
                0.3,
            ),
            (
                RouteDestination(
                    place_id="far",
                    coordinate=GeoCoordinate(latitude=37.5, longitude=127.0),
                ),
                2.0,
            ),
        ]
        plan = await _judge_recommendation_modes(
            candidates,
            conditions=UserConditions(),
            context=_empty_context(),
            switch_threshold_km=0.85,
            llm=_Broken(),
        )
        # 임계 아래는 도보만, 위는 양쪽 — 판정 도입 전과 같다.
        assert plan.measure["near"] == (TravelMode.WALKING,)
        assert plan.measure["far"] == (TravelMode.WALKING, TravelMode.TRANSIT)


def _empty_context():
    from app.agent_context.schemas import RecommendationContext

    return RecommendationContext()


class Test자동차는_판정이_고를_수_없다:
    """자동차는 사용자가 말했을 때만 쓴다. 그 경우는 판정을 아예 안 부른다."""

    def test_고를_수_있는_어휘는_도보와_대중교통뿐이다(self) -> None:
        from app.tools.schedule_travel import JUDGEABLE_MODES

        assert JUDGEABLE_MODES == {TravelMode.WALKING, TravelMode.TRANSIT}

    def test_프롬프트도_둘만_제시한다(self) -> None:
        instruction = build_mode_judge_instruction()
        assert "`walking` 또는 `transit` 중 하나다" in instruction
        assert "driving" not in instruction

    @pytest.mark.asyncio
    async def test_판정이_자동차를_고르면_막는다(self) -> None:
        """프롬프트에 적은 것은 부탁이고 검증이 계약이다."""
        from app.tools.schedule_travel import select_modes_for_segments

        class _Driving:
            async def judge(self, segments, context):
                del context
                return [TravelMode.DRIVING] * len(segments)

        with pytest.raises(ValueError, match="고를 수 없는 이동수단"):
            await select_modes_for_segments(
                (_segment(1, distance_m=3000, walk_minutes=43.0),),
                ModeJudgmentContext(transport=None),
                judge=_Driving(),
                walking_speed_mps=1.17,
                walk_transfer_threshold_min=20,
            )


class Test판정이_속도_비교를_이긴다:
    """판정이 대중교통을 골랐으면 도보가 더 빨라도 그 값을 쓴다. (TP-227)

    유모차 사용자에게 "걸어서 10분"은 사실 10분이 아니고, 그 숫자를 보여주면
    일정이 "대중교통 20분"이라고 말하는 것과 어긋난다.
    """

    @staticmethod
    def _route(mode: TravelMode, seconds: int):
        from app.domain.travel_route import RouteSource, RouteStatus, TravelRoute

        source = (
            RouteSource.KAKAO_WALKING
            if mode is TravelMode.WALKING
            else RouteSource.KAKAO_TRANSIT
        )
        return TravelRoute(
            place_id="p1",
            mode=mode,
            status=RouteStatus.SUCCESS,
            source=source,
            distance_m=700,
            duration_seconds=seconds,
        )

    def test_판정이_없으면_빠른_쪽을_고른다(self) -> None:
        """규칙 경로는 D-118 그대로다."""
        from app.services.runtime.agent_runtime import _fastest_routes

        routes = (
            self._route(TravelMode.WALKING, 576),  # 9.6분
            self._route(TravelMode.TRANSIT, 672),  # 11.2분
        )
        assert _fastest_routes(routes)[0].mode is TravelMode.WALKING

    def test_판정이_있으면_느려도_그것을_쓴다(self) -> None:
        from app.services.runtime.agent_runtime import _fastest_routes

        routes = (
            self._route(TravelMode.WALKING, 576),
            self._route(TravelMode.TRANSIT, 672),
        )
        chosen = _fastest_routes(routes, {"p1": TravelMode.TRANSIT})
        assert chosen[0].mode is TravelMode.TRANSIT

    def test_추정은_판정과_맞아도_실측을_이기지_못한다(self) -> None:
        """실측 등급이 판정보다 앞이다 — 추정이 채택되면 회차 전체가 직선거리로 내려간다."""
        from app.domain.travel_route import RouteSource, RouteStatus, TravelRoute
        from app.services.runtime.agent_runtime import _fastest_routes

        estimate = TravelRoute(
            place_id="p1",
            mode=TravelMode.TRANSIT,
            status=RouteStatus.SUCCESS,
            source=RouteSource.STRAIGHT_LINE_ESTIMATE,
            distance_m=700,
            duration_seconds=60,
        )
        measured_walking = self._route(TravelMode.WALKING, 576)
        chosen = _fastest_routes(
            (estimate, measured_walking), {"p1": TravelMode.TRANSIT}
        )
        assert chosen[0].source is RouteSource.KAKAO_WALKING

    @pytest.mark.asyncio
    async def test_규칙으로_되돌아가면_판정_목록이_비어_있다(self) -> None:
        """비어 있어야 뒤 단계가 예전처럼 빠른 쪽을 고른다."""
        from app.domain.travel_route import GeoCoordinate, RouteDestination
        from app.errors import ProviderUnavailableError
        from app.services.runtime.agent_runtime import _judge_recommendation_modes

        class _Broken:
            async def judge_travel_modes(self, segments, context):
                raise ProviderUnavailableError("판정 실패")

        plan = await _judge_recommendation_modes(
            [
                (
                    RouteDestination(
                        place_id="p1",
                        coordinate=GeoCoordinate(latitude=37.5, longitude=127.0),
                    ),
                    2.0,
                )
            ],
            conditions=UserConditions(),
            context=_empty_context(),
            switch_threshold_km=0.85,
            llm=_Broken(),
        )
        assert plan.judged == {}
