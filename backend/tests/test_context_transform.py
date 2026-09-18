"""to_agent_context_request() 단위 테스트.

concentration-conditions.md §2.2/§4.2 관련: A의 UserConditions에
concentration_intent가 추가된 뒤에도, C의 ContextUserConditions(StrictModel,
extra="forbid")가 아직 그 필드를 모르는 과도기에 ValidationError 없이 안전하게
넘어가는지 확인한다.
"""

from __future__ import annotations

from app.agent_context.schemas import UserConditions as ContextUserConditions
from app.schemas import (
    Companion,
    ConcentrationIntent,
    Environment,
    PlaceTag,
    PlaceType,
    StatedWeather,
    Transport,
    UserConditions,
    WeatherIntent,
)
from app.services.interpret.state_transform import to_user_conditions
from app.services.runtime.context_transform import to_agent_context_request
from app.state.schema import UserConditions as StateUserConditions

# A와 C가 공통으로 가진 조건 필드. 목록을 하드코딩하지 않고 계산해, 한쪽에 필드가
# 늘면 아래 전수 검증이 자동으로 그 필드까지 확인하도록 한다.
_SHARED_FIELDS = frozenset(UserConditions.model_fields) & frozenset(
    ContextUserConditions.model_fields
)


def _fully_populated_conditions() -> UserConditions:
    """모든 필드에 값이 있는 조건. 어느 하나라도 변환에서 빠지면 드러난다."""
    return UserConditions(
        current_location="홍대",
        search_center="경복궁",
        place_types=[PlaceType.RESTAURANT],
        place_tags=[PlaceTag.CAFE],
        weather=StatedWeather.RAIN,
        weather_intent=WeatherIntent.AVOID,
        concentration_intent=ConcentrationIntent.AVOID,
        transport=Transport.WALK,
        max_travel_time=15,
        time_available=120,
        environment=Environment.INDOOR,
        companion=Companion.COUPLE,
        budget="free",
        exclude_tags=["박물관"],
        special_requirements=["주차"],
    )


class TestToAgentContextRequest:
    def test_round_trips_existing_fields(self) -> None:
        conditions = UserConditions(
            current_location="홍대",
            search_center="경복궁",
            place_types=[PlaceType.RESTAURANT],
            place_tags=[PlaceTag.CAFE],
            weather_intent=WeatherIntent.AVOID,
        )

        request = to_agent_context_request("req-1", conditions)

        assert request.request_id == "req-1"
        assert request.intent == "RECOMMEND"
        assert request.conditions.current_location == "홍대"
        assert request.conditions.search_center == "경복궁"
        assert request.conditions.place_types == ["restaurant"]
        assert request.conditions.place_tags == ["카페"]
        assert request.conditions.weather_intent == "AVOID"

    def test_concentration_intent_does_not_raise_before_c_adds_field(self) -> None:
        """C의 ContextUserConditions가 concentration_intent를 아직 모르는 상태를
        가정한다 — extra="forbid"라 그대로 넘기면 ValidationError가 난다.
        to_agent_context_request()가 C가 모르는 필드를 걸러내는지 확인한다.
        """
        conditions = UserConditions(concentration_intent=ConcentrationIntent.AVOID)

        request = to_agent_context_request("req-2", conditions)

        assert not hasattr(request.conditions, "concentration_intent")

    def test_no_conditions_still_builds_valid_request(self) -> None:
        request = to_agent_context_request("req-3", UserConditions())

        assert request.conditions.current_location is None
        assert request.conditions.place_types == []

    def test_converts_gps_string_to_coordinates(self) -> None:
        request = to_agent_context_request(
            "req-4", UserConditions(), gps_location="37.5796,126.9770"
        )

        assert request.gps_location is not None
        assert request.gps_location.latitude == 37.5796
        assert request.gps_location.longitude == 126.9770

    def test_invalid_gps_is_omitted(self) -> None:
        request = to_agent_context_request(
            "req-5", UserConditions(), gps_location="91.0,126.9770"
        )

        assert request.gps_location is None

    def test_excluded_place_ids_reach_context_request(self) -> None:
        """소진분이 C까지 가야 추가 추천이 성립한다 — D에만 넘기면 후보가 안 바뀐다."""

        request = to_agent_context_request(
            "req-6", UserConditions(), excluded_place_ids=["p1", "p2"]
        )

        assert request.excluded_place_ids == ["p1", "p2"]

    def test_excluded_place_ids_default_to_empty(self) -> None:
        request = to_agent_context_request("req-7", UserConditions())

        assert request.excluded_place_ids == []


class TestConditionFieldCoverage:
    """B에서 병합된 조건이 C 요청까지 누락 없이 전달되는지 확인한다."""

    def test_every_shared_field_reaches_context_request(self) -> None:
        conditions = _fully_populated_conditions()

        request = to_agent_context_request("req-coverage", conditions)

        for field in sorted(_SHARED_FIELDS):
            source = getattr(conditions, field)
            delivered = getattr(request.conditions, field)
            # C는 enum을 문자열로 받으므로 값 기준으로 비교한다.
            expected = (
                [str(item) for item in source] if isinstance(source, list) else source
            )
            assert delivered == expected, f"{field}이(가) C 요청에서 달라졌습니다"

    def test_only_documented_fields_are_dropped(self) -> None:
        """C가 모르는 필드는 조용히 버려진다 — 그 목록을 테스트로 고정한다.

        to_agent_context_request()는 ContextUserConditions가 아는 필드만 넘긴다
        (StrictModel이라 모르는 필드가 들어가면 ValidationError). 덕분에 A가 먼저
        필드를 추가해도 깨지지 않지만, 누락이 조용히 생긴다. 새 필드가 같은 이유로
        사라지면 이 단언이 실패해 알려준다.
        C가 concentration_intent를 받게 되면 기대값에서 그 항목만 제거한다.

        travel_origin(D-071)은 이 둘과 성격이 다르다 — C가 아직 못 받는 게 아니라
        원래부터 C가 몰라도 되는 필드다. C는 위치 문자열을 좌표로 해석하는
        역할만 하고, "이동시간을 어디서부터 잴까"는 D가 랭킹 단계에서만 쓰는
        판정이라 C에 넘길 이유가 없다.
        """
        dropped = frozenset(UserConditions.model_fields) - frozenset(
            ContextUserConditions.model_fields
        )

        assert dropped == {"concentration_intent", "travel_origin"}

    def test_merged_state_conditions_reach_context_request(self) -> None:
        """B(순수 문자열) → A(enum) → C 전 구간을 한 번에 확인한다.

        to_user_conditions() 단계에서 값이 빠져도 드러나도록 B 모델에서 시작한다.
        """
        state_conditions = StateUserConditions(
            current_location="홍대",
            search_center="경복궁",
            place_types=["restaurant"],
            place_tags=["카페"],
            weather="rain",
            weather_intent="AVOID",
            transport="walk",
            max_travel_time=15,
            time_available=120,
            environment="indoor",
            companion="couple",
            budget="free",
            exclude_tags=["박물관"],
            special_requirements=["주차"],
        )

        request = to_agent_context_request(
            "req-merged", to_user_conditions(state_conditions)
        )

        for field in sorted(_SHARED_FIELDS):
            assert getattr(request.conditions, field) == getattr(
                state_conditions, field
            ), f"{field}이(가) B→A→C 구간에서 달라졌습니다"
