"""FakeLLMProvider 회귀 테스트.

docs/design/test-cases.md의 대표 케이스(TC-01~04 RECOMMEND, TC-07~09 MODIFY, TC-11 GENERAL,
TC-12/13 OUT_OF_SCOPE)와 llm-output-schema.md §7의 needs_clarification 예시를 재현한다.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.providers.gemini_prompts import (
    build_compare_extraction_instruction,
    build_intent_classification_instruction,
    build_modify_extraction_instruction,
    build_recommend_extraction_instruction,
    build_schedule_planning_instruction,
    format_schedule_planning_context,
)
from app.providers.stub import FakeLLMProvider
from app.schedule.schemas import SchedulePlanningRequest
from app.schemas import (
    CompareCriteria,
    ComparisonItem,
    ComparisonResult,
    ConcentrationIntent,
    Environment,
    GeneralTopic,
    Intent,
    ModifyType,
    OutOfScopeCategory,
    OutputStatus,
    PlaceTag,
    PlaceType,
    RecommendationItem,
    StatedWeather,
    Transport,
    UserConditions,
    WeatherIntent,
)


@pytest.mark.asyncio
async def test_classify_intent_tc01_recommend() -> None:
    provider = FakeLLMProvider()

    result = await provider.classify_intent(
        "경복궁 근처 카페 추천해줘",
        has_previous_recommendation=False,
        shown_place_count=0,
    )

    assert result.data.intent is Intent.RECOMMEND


@pytest.mark.parametrize(
    "user_input",
    [
        "오늘 오후 종로 일정 짜줘",
        "반나절 코스 만들어줘",
        "경복궁, 인사동 가고 싶은데 어디부터 갈까?",
    ],
)
@pytest.mark.parametrize("has_previous_recommendation", [False, True])
@pytest.mark.asyncio
async def test_classify_intent_schedule_regardless_of_recommendation_history(
    user_input: str, has_previous_recommendation: bool
) -> None:
    """명시적인 일정·코스·방문 순서는 이전 추천 이력과 무관하게 SCHEDULE다."""
    provider = FakeLLMProvider()

    result = await provider.classify_intent(
        user_input,
        has_previous_recommendation=has_previous_recommendation,
        shown_place_count=5 if has_previous_recommendation else 0,
    )

    assert result.data.intent is Intent.SCHEDULE


@pytest.mark.asyncio
async def test_classify_intent_plain_recommendation_is_not_schedule() -> None:
    provider = FakeLLMProvider()

    result = await provider.classify_intent(
        "오늘 갈 만한 곳 추천해줘", has_previous_recommendation=False, shown_place_count=0
    )

    assert result.data.intent is Intent.RECOMMEND


@pytest.mark.parametrize(
    "user_input",
    ["광화문으로 알려줘", "광화문으로", "종로 대신 광화문", "광화문 근처로"],
)
@pytest.mark.asyncio
async def test_classify_intent_schedule_clarification_answer_stays_schedule(
    user_input: str,
) -> None:
    """D-059: 직전 SCHEDULE 되묻기(장소 모호)에 지명만 답하면 MODIFY가 아니라 SCHEDULE을
    유지해야 한다 — 바꿀 이전 추천 결과 자체가 없다."""
    provider = FakeLLMProvider()

    result = await provider.classify_intent(
        user_input,
        has_previous_recommendation=False,
        shown_place_count=0,
        pending_clarification="location_ambiguous",
        last_intent="SCHEDULE",
    )

    assert result.data.intent is Intent.SCHEDULE


@pytest.mark.asyncio
async def test_classify_intent_schedule_clarification_explicit_restart_not_forced() -> None:
    """되묻기 이어가기 규칙이 명시적 재시작 표현까지 SCHEDULE로 강제하면 안 된다 — 이
    분기를 건너뛰고 나머지 규칙(여기서는 RECOMMEND)이 그대로 판정한다."""
    provider = FakeLLMProvider()

    without_context = await provider.classify_intent(
        "처음부터 다시 짜줘", has_previous_recommendation=False, shown_place_count=0
    )
    with_schedule_clarification = await provider.classify_intent(
        "처음부터 다시 짜줘",
        has_previous_recommendation=False,
        shown_place_count=0,
        pending_clarification="location_ambiguous",
        last_intent="SCHEDULE",
    )

    assert with_schedule_clarification.data.intent is without_context.data.intent


@pytest.mark.asyncio
async def test_classify_intent_location_modify_unaffected_without_schedule_clarification() -> (
    None
):
    """기존 회귀 확인: SCHEDULE 되묻기 컨텍스트가 없으면 "지명+근처/주변" 답변은 그대로
    MODIFY다(D-053)."""
    provider = FakeLLMProvider()

    result = await provider.classify_intent(
        "광화문 근처로",
        has_previous_recommendation=True,
        shown_place_count=5,
    )

    assert result.data.intent is Intent.MODIFY


@pytest.mark.asyncio
async def test_classify_intent_schedule_clarification_wins_over_modify_pattern() -> None:
    """has_previous_recommendation=True라 "지명+근처" MODIFY 조건도 동시에 충족되는
    상황에서, SCHEDULE 되묻기 이어가기 규칙이 우선해야 한다(실제 버그 재현 조건과 동일)."""
    provider = FakeLLMProvider()

    result = await provider.classify_intent(
        "광화문 근처로",
        has_previous_recommendation=True,
        shown_place_count=5,
        pending_clarification="location_ambiguous",
        last_intent="SCHEDULE",
    )

    assert result.data.intent is Intent.SCHEDULE


@pytest.mark.parametrize("user_input", ["넌 누구야?", "이름이 뭐야?", "뭘 할 수 있어?"])
@pytest.mark.asyncio
async def test_classify_intent_service_identity_question_is_general(user_input: str) -> None:
    provider = FakeLLMProvider()

    result = await provider.classify_intent(
        user_input, has_previous_recommendation=False, shown_place_count=0
    )

    assert result.data.intent is Intent.GENERAL


@pytest.mark.asyncio
async def test_extract_general_request_service_identity_topic() -> None:
    provider = FakeLLMProvider()

    output = (await provider.extract_general_request("넌 누구야?")).data

    assert output.intent is Intent.GENERAL
    assert output.general is not None
    assert output.general.topic is GeneralTopic.SERVICE_IDENTITY


@pytest.mark.asyncio
async def test_generate_general_answer_service_identity_mentions_trivy() -> None:
    provider = FakeLLMProvider()

    result = await provider.generate_general_answer(
        GeneralTopic.SERVICE_IDENTITY, "넌 누구야?"
    )

    assert "트리비" in result.data
    assert "국내 여행" in result.data


@pytest.mark.asyncio
async def test_generate_compare_summary_uses_three_to_six_fact_only_lines() -> None:
    provider = FakeLLMProvider()
    comparison = ComparisonResult(
        criteria=CompareCriteria.TRAVEL_TIME,
        items=[
            ComparisonItem(
                place_id="p1",
                place_name="경복궁",
                rank=1,
                distance_km=0.2,
                remaining_minutes=120,
                environment_type="outdoor",
            ),
            ComparisonItem(
                place_id="p2",
                place_name="국립민속박물관",
                rank=2,
                distance_km=0.5,
                remaining_minutes=180,
                environment_type="indoor",
            ),
        ],
    )

    result = await provider.generate_compare_summary(comparison)

    assert 3 <= len(result.data.splitlines()) <= 6
    assert "경복궁" in result.data
    # 0.2km를 3.6km/h로 환산해 올림한 값이다(추천 카드와 같은 표기 규칙).
    assert "도보 약 4분" in result.data
    assert "점수" not in result.data


@pytest.mark.asyncio
async def test_generate_compare_summary_travel_time_recommends_shortest_duration() -> None:
    """TRAVEL_TIME은 수단 상관없이 가장 빨리 갈 수 있는 곳을 추천하고, 실측 거리와
    도보·자동차·대중교통 소요시간을 함께 말한다.

    같은 항목의 distance_km(추천 시점 스냅샷 직선거리)는 travel_time 기준에서는
    실측값과 섞이면 혼동을 주므로 언급하지 않는다.
    """
    provider = FakeLLMProvider()
    comparison = ComparisonResult(
        criteria=CompareCriteria.TRAVEL_TIME,
        items=[
            ComparisonItem(
                place_id="p1",
                place_name="경복궁",
                rank=1,
                distance_km=0.2,
                travel_distance_km=1.8,
                travel_walking_minutes=22,
                travel_driving_minutes=12,
                travel_transit_minutes=18,
            ),
            ComparisonItem(
                place_id="p2",
                place_name="국립민속박물관",
                rank=2,
                distance_km=0.5,
                travel_distance_km=3.4,
                travel_walking_minutes=40,
                travel_driving_minutes=20,
                travel_transit_minutes=25,
            ),
        ],
    )

    result = await provider.generate_compare_summary(comparison)

    assert "경복궁" in result.data
    assert "자동차로 약 12분" in result.data
    assert "도보로 약 22분" in result.data
    assert "대중교통으로 약 18분" in result.data
    assert "0.2" not in result.data


@pytest.mark.asyncio
async def test_extract_recommend_conditions_tc01_search_center_and_tags() -> None:
    provider = FakeLLMProvider()

    output = (
        await provider.extract_recommend_conditions("경복궁 근처 카페 추천해줘")
    ).data

    assert output.intent is Intent.RECOMMEND
    assert output.recommend is not None
    assert output.recommend.conditions.search_center == "경복궁"
    assert output.recommend.conditions.place_types == [PlaceType.RESTAURANT]
    assert output.recommend.conditions.place_tags == [PlaceTag.CAFE]


@pytest.mark.asyncio
async def test_extract_recommend_conditions_tc02_weather_avoid_indoor() -> None:
    provider = FakeLLMProvider()

    output = (
        await provider.extract_recommend_conditions("비 오는데 갈 만한 곳 추천")
    ).data

    conditions = output.recommend.conditions
    assert conditions.weather is StatedWeather.RAIN
    assert conditions.weather_intent is WeatherIntent.AVOID
    assert conditions.environment is Environment.INDOOR
    assert conditions.place_types == []


@pytest.mark.asyncio
async def test_extract_recommend_conditions_tc03_multiple_types() -> None:
    provider = FakeLLMProvider()

    output = (
        await provider.extract_recommend_conditions("박물관이나 카페 가고 싶어")
    ).data

    conditions = output.recommend.conditions
    assert PlaceType.CULTURAL_FACILITY in conditions.place_types
    assert PlaceType.RESTAURANT in conditions.place_types
    assert PlaceTag.MUSEUM in conditions.place_tags
    assert PlaceTag.CAFE in conditions.place_tags


@pytest.mark.asyncio
async def test_extract_recommend_conditions_tc04_no_conditions() -> None:
    provider = FakeLLMProvider()

    output = (await provider.extract_recommend_conditions("추천해줘")).data

    conditions = output.recommend.conditions
    assert conditions.search_center is None
    assert conditions.place_types == []
    assert conditions.place_tags == []
    assert conditions.weather_intent is WeatherIntent.NO_MENTION


@pytest.mark.asyncio
async def test_extract_recommend_conditions_ambiguous_snow_needs_clarification() -> None:
    """llm-output-schema.md §7의 needs_clarification 예시 재현."""
    provider = FakeLLMProvider()

    output = (
        await provider.extract_recommend_conditions("눈 오는데 카페 추천해줘")
    ).data

    assert output.status is OutputStatus.NEEDS_CLARIFICATION
    assert output.clarification is not None
    assert output.clarification.ambiguous_fields[0].field == "weather_intent"


@pytest.mark.asyncio
async def test_classify_intent_modify_requires_previous_recommendation() -> None:
    """TC-14: 추천 이력 없이 MODIFY 패턴 입력 -> RECOMMEND로 처리."""
    provider = FakeLLMProvider()

    with_history = await provider.classify_intent(
        "다른 곳 보여줘", has_previous_recommendation=True, shown_place_count=3
    )
    without_history = await provider.classify_intent(
        "다른 곳 보여줘", has_previous_recommendation=False, shown_place_count=0
    )

    assert with_history.data.intent is Intent.MODIFY
    assert without_history.data.intent is Intent.RECOMMEND


@pytest.mark.parametrize(
    "user_input",
    [
        "광화문 근처에서",
        "광화문 근처",
        "광화문 근처 어때?",
        "종로3가역 근처에서",
        "북촌 근처에서",
        "광화문으로",
        "광화문에서",
    ],
)
@pytest.mark.asyncio
async def test_classify_intent_location_only_with_history_is_modify(user_input: str) -> None:
    """TP-67: 이전 추천 뒤 위치만 제시하면 새 추천이 아니라 조건 변경이다."""
    provider = FakeLLMProvider()

    result = await provider.classify_intent(
        user_input, has_previous_recommendation=True, shown_place_count=5
    )

    assert result.data.intent is Intent.MODIFY


@pytest.mark.parametrize(
    "user_input",
    [
        "광화문 근처에서",
        "광화문 근처",
        "광화문 근처 어때?",
        "종로3가역 근처에서",
        "북촌 근처에서",
    ],
)
@pytest.mark.asyncio
async def test_classify_intent_location_only_without_history_is_recommend(user_input: str) -> None:
    provider = FakeLLMProvider()

    result = await provider.classify_intent(
        user_input, has_previous_recommendation=False, shown_place_count=0
    )

    assert result.data.intent is Intent.RECOMMEND


@pytest.mark.parametrize(
    ("has_previous_recommendation", "expected"),
    [(False, Intent.RECOMMEND), (True, Intent.MODIFY)],
)
@pytest.mark.parametrize("user_input", ["광화문", "경복궁", "경복궁이요"])
@pytest.mark.asyncio
async def test_classify_intent_bare_place_name_means_nearby_recommendation(
    user_input: str, has_previous_recommendation: bool, expected: Intent
) -> None:
    """단순 지명은 정보 질문으로 가정하지 않고 해당 장소 근처 추천으로 처리한다."""
    provider = FakeLLMProvider()

    result = await provider.classify_intent(
        user_input,
        has_previous_recommendation=has_previous_recommendation,
        shown_place_count=5 if has_previous_recommendation else 0,
    )

    assert result.data.intent is expected


@pytest.mark.parametrize("user_input", ["광화문", "경복궁", "경복궁이요"])
@pytest.mark.asyncio
async def test_classify_intent_bare_place_after_location_clarification_is_modify(
    user_input: str,
) -> None:
    """위치를 물은 직후의 단순 지명은 INFO가 아니라 기존 요청의 위치 답변이다."""
    provider = FakeLLMProvider()

    result = await provider.classify_intent(
        user_input,
        has_previous_recommendation=False,
        shown_place_count=0,
        pending_clarification="location_required",
        last_intent="RECOMMEND",
    )

    assert result.data.intent is Intent.MODIFY


@pytest.mark.asyncio
async def test_classify_intent_bare_place_with_question_stays_info_after_clarification() -> None:
    """위치 되묻기 상태여도 정보 질문까지 MODIFY로 가리면 안 된다."""
    provider = FakeLLMProvider()

    result = await provider.classify_intent(
        "경복궁 오늘 열어?",
        has_previous_recommendation=False,
        shown_place_count=0,
        pending_clarification="location_required",
        last_intent="RECOMMEND",
    )

    assert result.data.intent is Intent.INFO


@pytest.mark.parametrize(
    "user_input",
    [
        "경복궁 근처 카페 추천해줘",
        "비 오는데 경복궁 근처 카페 추천해줘",
        "북촌 주변 박물관 보여줘",
    ],
)
@pytest.mark.asyncio
async def test_classify_intent_location_with_other_conditions_is_modify(user_input: str) -> None:
    """D-053: 지명+근처에 다른 조건이 붙어도 이전 추천이 있으면 조건 변경이다.

    실 Gemini(gemini-2.5-flash, 프롬프트 1.0.2)가 "경복궁 근처 카페 추천해줘"를
    MODIFY 5/5로 분류하는 것과 Fake를 맞춘 것이다 — 잔여 조건("카페") 때문에
    RECOMMEND fallback으로 떨어지던 차이를 없앤다.
    """
    provider = FakeLLMProvider()

    result = await provider.classify_intent(
        user_input, has_previous_recommendation=True, shown_place_count=5
    )

    assert result.data.intent is Intent.MODIFY


@pytest.mark.parametrize(
    ("user_input", "expected"),
    [
        ("경복궁 근처 카페 추천해줘", Intent.RECOMMEND),
        ("경복궁 근처에 화장실 있어?", Intent.INFO),
        ("경복궁 근처 동네는 어때?", Intent.GENERAL),
    ],
)
@pytest.mark.asyncio
async def test_classify_intent_location_with_other_conditions_boundaries(
    user_input: str, expected: Intent
) -> None:
    """반대 방향 회귀: 이력이 없으면 RECOMMEND고, 정보/일반 질문은 가려지지 않는다."""
    provider = FakeLLMProvider()

    result = await provider.classify_intent(
        user_input,
        has_previous_recommendation=expected is not Intent.RECOMMEND,
        shown_place_count=5 if expected is not Intent.RECOMMEND else 0,
    )

    assert result.data.intent is expected


@pytest.mark.asyncio
async def test_classify_intent_explicit_adjustment_after_schedule_stays_modify() -> None:
    """SCHEDULE 직후 "지명+근처"에 조건만 붙인 발화는 D-053 규칙 그대로 MODIFY다 —
    "일정 재조정"인지 "순수 추천"인지는 classify_intent()가 아니라
    agent_runtime.py의 SCHEDULE-06 되묻기가 사용자에게 확인한다
    (docs/design/clarification-options.md 5절)."""
    provider = FakeLLMProvider()

    result = await provider.classify_intent(
        "경복궁 근처 카페 말고 맛집",
        has_previous_recommendation=True,
        shown_place_count=2,
        pending_clarification=None,
        last_intent="SCHEDULE",
    )

    assert result.data.intent is Intent.MODIFY


@pytest.mark.asyncio
async def test_classify_intent_explicit_schedule_request_after_schedule_stays_schedule() -> None:
    """발화 자체에 일정/코스 표현이 있으면 직전 턴이 SCHEDULE여도 SCHEDULE 판정이
    우선한다(판별 우선순위 2번, _SCHEDULE_MARKERS가 다른 맥락 규칙보다 먼저 검사된다)."""
    provider = FakeLLMProvider()

    result = await provider.classify_intent(
        "경복궁 근처 카페로 일정 짜줘",
        has_previous_recommendation=True,
        shown_place_count=2,
        pending_clarification=None,
        last_intent="SCHEDULE",
    )

    assert result.data.intent is Intent.SCHEDULE


@pytest.mark.asyncio
async def test_extract_modify_conditions_rain_avoids_and_moves_indoor() -> None:
    """MODIFY의 '비와서 실내'는 날씨 ENJOY가 아니라 명확한 회피다."""
    provider = FakeLLMProvider()
    current = UserConditions(search_center="북촌")

    output = (
        await provider.extract_modify_conditions("비와서 실내로 바꿔줘", current)
    ).data

    changes = output.modify.condition_changes
    assert changes.weather is StatedWeather.RAIN
    assert changes.weather_intent is WeatherIntent.AVOID
    assert changes.environment is Environment.INDOOR
    assert set(output.modify.changed_fields) == {
        "weather",
        "weather_intent",
        "environment",
    }


@pytest.mark.asyncio
async def test_extract_modify_conditions_location_only_changes_search_center() -> None:
    provider = FakeLLMProvider()
    current = UserConditions(
        search_center="경복궁",
        weather=StatedWeather.RAIN,
        weather_intent=WeatherIntent.AVOID,
        environment=Environment.INDOOR,
    )

    output = (await provider.extract_modify_conditions("광화문 근처에서", current)).data

    assert output.modify.modify_type is ModifyType.CHANGE_CONDITION
    assert output.modify.condition_changes.search_center == "광화문"
    assert output.modify.changed_fields == ["search_center"]


@pytest.mark.asyncio
async def test_extract_modify_conditions_bare_place_changes_search_center() -> None:
    """이전 추천 뒤 단순 지명도 해당 장소 근처 추천으로 이어진다."""
    provider = FakeLLMProvider()
    current = UserConditions(search_center="경복궁", place_tags=[PlaceTag.CAFE])

    output = (await provider.extract_modify_conditions("광화문", current)).data

    assert output.modify.condition_changes.search_center == "광화문"
    assert output.modify.changed_fields == ["search_center"]


@pytest.mark.asyncio
async def test_extract_recommend_conditions_bare_place_sets_search_center() -> None:
    """첫 턴의 단순 지명도 주변 추천의 검색 중심으로 추출한다."""
    provider = FakeLLMProvider()

    output = (await provider.extract_recommend_conditions("경복궁")).data

    assert output.recommend.conditions.search_center == "경복궁"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("user_input", "expected_transport"),
    [
        ("차로 갈만한 카페 추천해줘", Transport.CAR),
        ("걸어서 갈 수 있는 곳 추천해줘", Transport.WALK),
        ("대중교통으로 갈 만한 곳 추천해줘", Transport.PUBLIC),
    ],
)
async def test_extract_recommend_conditions_transport(
    user_input: str, expected_transport: Transport
) -> None:
    """TP-105 — transport=CAR가 채워져야 D의 자동차 경로 실측이 실제로 호출된다."""
    provider = FakeLLMProvider()

    output = (await provider.extract_recommend_conditions(user_input)).data

    assert output.recommend.conditions.transport is expected_transport


@pytest.mark.asyncio
async def test_extract_recommend_conditions_transport_not_mentioned_stays_null() -> None:
    """이동수단을 언급하지 않았으면 추정하지 않고 null로 둔다."""
    provider = FakeLLMProvider()

    output = (await provider.extract_recommend_conditions("경복궁 근처 카페 추천해줘")).data

    assert output.recommend.conditions.transport is None


@pytest.mark.asyncio
async def test_extract_recommend_conditions_travel_time_alone_does_not_imply_transport() -> None:
    """이동시간만 말하고 이동수단은 말하지 않으면 transport를 유추해서 채우지 않는다."""
    provider = FakeLLMProvider()

    output = (await provider.extract_recommend_conditions("30분 안에 갈 수 있는 곳")).data

    assert output.recommend.conditions.transport is None


@pytest.mark.asyncio
async def test_extract_modify_conditions_quiet_place_avoids_concentration() -> None:
    """MODIFY에서도 '조용한'은 혼잡도 회피(AVOID)로 추출해야 한다.

    RECOMMEND 프롬프트에만 있던 concentration_intent 규칙이 MODIFY에서 빠져
    실제 Gemini가 SEEK를 반환했던 회귀를 막는다.
    """
    provider = FakeLLMProvider()
    current = UserConditions(
        search_center="창경궁",
        concentration_intent=ConcentrationIntent.IGNORE,
    )

    output = (await provider.extract_modify_conditions("좀 조용한 공원 가고싶어", current)).data

    assert output.modify.condition_changes.concentration_intent is ConcentrationIntent.AVOID
    assert output.modify.condition_changes.place_types == [PlaceType.ATTRACTION]
    assert output.modify.condition_changes.place_tags == [PlaceTag.PARK]
    assert output.modify.changed_fields == [
        "place_types",
        "place_tags",
        "concentration_intent",
    ]


@pytest.mark.asyncio
async def test_extract_modify_conditions_transport_change() -> None:
    """MODIFY도 이동수단 변경 발화를 transport로 추출하고 changed_fields에 남긴다."""
    provider = FakeLLMProvider()
    current = UserConditions(search_center="창경궁", transport=Transport.WALK)

    output = (await provider.extract_modify_conditions("차로 가는 걸로 바꿔줘", current)).data

    assert output.modify.condition_changes.transport is Transport.CAR
    assert "transport" in output.modify.changed_fields


@pytest.mark.asyncio
async def test_extract_modify_conditions_category_request_replaces_previous_category() -> None:
    """'공원도 추천'의 도는 추가가 아니라 새 추천 유형 강조로 본다."""

    provider = FakeLLMProvider()
    current = UserConditions(place_types=[PlaceType.RESTAURANT], place_tags=[PlaceTag.CAFE])

    output = (await provider.extract_modify_conditions("공원도 추천해줘", current)).data

    assert output.modify.condition_changes.place_types == [PlaceType.ATTRACTION]
    assert output.modify.condition_changes.place_tags == [PlaceTag.PARK]
    assert output.modify.changed_fields == ["place_types", "place_tags"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("user_input", "expected_types", "expected_tags"),
    [
        (
            "공원도 포함해줘",
            [PlaceType.RESTAURANT, PlaceType.ATTRACTION],
            [PlaceTag.CAFE, PlaceTag.PARK],
        ),
        (
            "카페와 공원 같이 추천해줘",
            [PlaceType.RESTAURANT, PlaceType.ATTRACTION],
            [PlaceTag.CAFE, PlaceTag.PARK],
        ),
        (
            "카페나 공원 추천해줘",
            [PlaceType.RESTAURANT, PlaceType.ATTRACTION],
            [PlaceTag.CAFE, PlaceTag.PARK],
        ),
    ],
)
async def test_extract_modify_conditions_explicit_category_combination_keeps_both(
    user_input: str,
    expected_types: list[PlaceType],
    expected_tags: list[PlaceTag],
) -> None:
    """명시적으로 함께·포함·선택지를 말할 때만 교차 유형을 함께 검색한다."""

    provider = FakeLLMProvider()
    current = UserConditions(place_types=[PlaceType.RESTAURANT], place_tags=[PlaceTag.CAFE])

    output = (await provider.extract_modify_conditions(user_input, current)).data

    assert output.modify.condition_changes.place_types == expected_types
    assert output.modify.condition_changes.place_tags == expected_tags
    assert output.modify.changed_fields == ["place_types", "place_tags"]


def test_modify_instruction_includes_weather_and_concentration_avoid_rules() -> None:
    """Real Gemini MODIFY 프롬프트에도 날씨·혼잡도 회피 규칙을 넣는다."""
    instruction = build_modify_extraction_instruction(UserConditions(search_center="창경궁"))

    assert "비와서 실내로 바꿔줘" in instruction
    assert "반드시 AVOID" in instruction
    assert "concentration_intent 판별:" in instruction
    assert '"조용한 공원 추천해줘"' in instruction
    assert "concentration_intent/transport" in instruction


def test_modify_instruction_distinguishes_category_replacement_and_explicit_addition() -> None:
    instruction = build_modify_extraction_instruction(
        UserConditions(place_types=[PlaceType.RESTAURANT], place_tags=[PlaceTag.CAFE])
    )

    assert '"공원도 추천해줘"' in instruction
    assert '"공원도 포함해줘"' in instruction
    assert '"카페와 공원 같이 추천해줘"' in instruction
    assert 'changed_fields에도 둘 다' in instruction


def test_modify_instruction_marks_location_clarification_answer() -> None:
    instruction = build_modify_extraction_instruction(
        UserConditions(place_tags=[PlaceTag.CAFE]),
        pending_clarification="location_required",
    )

    assert "직전 위치 되묻기 답변 여부: 예" in instruction
    assert 'changed_fields에는 "search_center"만' in instruction


def test_intent_instruction_includes_schedule_clarification_rule() -> None:
    """D-059: SCHEDULE 되묻기 이어가기 규칙과 컨텍스트 플래그가 프롬프트에 반영된다."""
    instruction = build_intent_classification_instruction(
        has_previous_recommendation=False,
        shown_place_count=0,
        pending_clarification="location_ambiguous",
        last_intent="SCHEDULE",
    )

    assert "SCHEDULE 되묻기" in instruction
    assert "직전 턴이 되묻기로 끝났는지: 예" in instruction


def test_intent_instruction_includes_recommend_location_clarification_rule() -> None:
    instruction = build_intent_classification_instruction(
        has_previous_recommendation=False,
        shown_place_count=0,
        pending_clarification="location_required",
        last_intent="RECOMMEND",
    )

    assert "단순 지명 답변" in instruction
    assert "직전 RECOMMEND/MODIFY 요청의 위치 되묻기" in instruction


def test_intent_instruction_hides_clarification_flag_when_absent() -> None:
    instruction = build_intent_classification_instruction(
        has_previous_recommendation=False, shown_place_count=0
    )

    assert "직전 턴이 되묻기로 끝났는지: 아니오" in instruction


@pytest.mark.asyncio
async def test_extract_modify_conditions_tc07_reject_all() -> None:
    provider = FakeLLMProvider()

    output = (
        await provider.extract_modify_conditions(
            "다른 곳 보여줘", UserConditions(search_center="경복궁")
        )
    ).data

    assert output.modify.modify_type is ModifyType.REJECT_ALL
    assert output.modify.condition_changes is None
    assert output.modify.changed_fields == []
    assert output.modify.target_indices == []


@pytest.mark.asyncio
async def test_extract_modify_conditions_reject_specific_single_target() -> None:
    """SCHEDULE-09: 순번 하나 + 거절 신호가 함께 있으면 REJECT_SPECIFIC."""
    provider = FakeLLMProvider()

    output = (
        await provider.extract_modify_conditions(
            "두 번째는 별로야",
            UserConditions(search_center="경복궁"),
            shown_place_count=3,
        )
    ).data

    assert output.status is OutputStatus.COMPLETE
    assert output.modify.modify_type is ModifyType.REJECT_SPECIFIC
    assert output.modify.target_indices == [2]
    assert output.modify.condition_changes is None


@pytest.mark.asyncio
async def test_extract_modify_conditions_reject_specific_multiple_targets() -> None:
    """SCHEDULE-09: 순번을 여러 개 언급하면 모두 담는다."""
    provider = FakeLLMProvider()

    output = (
        await provider.extract_modify_conditions(
            "두 번째랑 세 번째 다 별로야",
            UserConditions(search_center="경복궁"),
            shown_place_count=3,
        )
    ).data

    assert output.modify.modify_type is ModifyType.REJECT_SPECIFIC
    assert output.modify.target_indices == [2, 3]


@pytest.mark.asyncio
async def test_extract_modify_conditions_reject_specific_out_of_range_asks_clarification() -> None:
    """SCHEDULE-09: 노출된 항목 수를 벗어나는 순번이면 needs_clarification.

    COMPARE의 shown_place_count 범위 검증과 동일한 패턴이다.
    """
    provider = FakeLLMProvider()

    output = (
        await provider.extract_modify_conditions(
            "세 번째는 별로야",
            UserConditions(search_center="경복궁"),
            shown_place_count=2,
        )
    ).data

    assert output.status is OutputStatus.NEEDS_CLARIFICATION
    assert output.modify is None
    assert output.clarification is not None
    assert "2개" in output.clarification.message


@pytest.mark.asyncio
async def test_extract_modify_conditions_exclusion_pattern_keeps_mentioned_index() -> None:
    """SCHEDULE-09 후속: "N번째 말고는 다 ~"는 언급된 순번을 남기고 나머지
    전부를 거부한다 — target_indices는 언급된 순번의 여집합이다(직접 지목과
    정반대 방향)."""
    provider = FakeLLMProvider()

    output = (
        await provider.extract_modify_conditions(
            "두 번째 말고는 다 마음에 안 들어",
            UserConditions(search_center="경복궁"),
            shown_place_count=3,
        )
    ).data

    assert output.status is OutputStatus.COMPLETE
    assert output.modify.modify_type is ModifyType.REJECT_SPECIFIC
    assert output.modify.target_indices == [1, 3]
    assert output.modify.condition_changes is None


@pytest.mark.asyncio
async def test_extract_modify_conditions_exclusion_pattern_multiple_kept() -> None:
    """SCHEDULE-09 후속: 남길 순번을 여러 개 언급해도 나머지 전부가 여집합이 된다."""
    provider = FakeLLMProvider()

    output = (
        await provider.extract_modify_conditions(
            "두 번째랑 세 번째 말고는 다 별로야",
            UserConditions(search_center="경복궁"),
            shown_place_count=5,
        )
    ).data

    assert output.modify.modify_type is ModifyType.REJECT_SPECIFIC
    assert output.modify.target_indices == [1, 4, 5]


@pytest.mark.asyncio
async def test_extract_modify_conditions_exclusion_pattern_out_of_range() -> None:
    """SCHEDULE-09 후속: 남기겠다는 순번 자체가 노출 범위를 벗어나면 되묻는다."""
    provider = FakeLLMProvider()

    output = (
        await provider.extract_modify_conditions(
            "세 번째 말고는 다 별로야",
            UserConditions(search_center="경복궁"),
            shown_place_count=2,
        )
    ).data

    assert output.status is OutputStatus.NEEDS_CLARIFICATION
    assert output.modify is None
    assert output.clarification is not None
    assert "2개" in output.clarification.message


@pytest.mark.asyncio
async def test_extract_modify_conditions_name_reference_direct() -> None:
    """SCHEDULE-09 후속(이름 지목): 순번 대신 노출된 항목 이름을 직접 언급해도
    같은 순번으로 매칭돼야 한다."""
    provider = FakeLLMProvider()

    output = (
        await provider.extract_modify_conditions(
            "두가헌 레스토랑은 빼줘",
            UserConditions(search_center="경복궁"),
            shown_place_count=3,
            shown_place_names=["경복궁", "두가헌 레스토랑", "갤러리조선"],
        )
    ).data

    assert output.status is OutputStatus.COMPLETE
    assert output.modify.modify_type is ModifyType.REJECT_SPECIFIC
    assert output.modify.target_indices == [2]


@pytest.mark.asyncio
async def test_extract_modify_conditions_name_reference_exclusion() -> None:
    """SCHEDULE-09 후속(이름 지목): "N 말고는 다 별로야"도 이름으로 지목할 수
    있다 — 여집합 규칙과 결합된다."""
    provider = FakeLLMProvider()

    output = (
        await provider.extract_modify_conditions(
            "두가헌 레스토랑 말고는 다 별로야",
            UserConditions(search_center="경복궁"),
            shown_place_count=3,
            shown_place_names=["경복궁", "두가헌 레스토랑", "갤러리조선"],
        )
    ).data

    assert output.modify.modify_type is ModifyType.REJECT_SPECIFIC
    assert output.modify.target_indices == [1, 3]


@pytest.mark.asyncio
async def test_extract_modify_conditions_name_and_ordinal_combined() -> None:
    """SCHEDULE-09 후속(이름 지목): 순번과 이름을 섞어 언급해도 모두 담긴다."""
    provider = FakeLLMProvider()

    output = (
        await provider.extract_modify_conditions(
            "첫 번째랑 갤러리조선 빼줘",
            UserConditions(search_center="경복궁"),
            shown_place_count=3,
            shown_place_names=["경복궁", "두가헌 레스토랑", "갤러리조선"],
        )
    ).data

    assert output.modify.modify_type is ModifyType.REJECT_SPECIFIC
    assert output.modify.target_indices == [1, 3]


@pytest.mark.asyncio
async def test_extract_modify_conditions_missing_names_falls_back_to_ordinal_only() -> None:
    """SCHEDULE-09 후속(이름 지목): shown_place_names가 없으면(과거 세션 등)
    기존 순번 기반 동작만 그대로 유지된다 — 새 기능이 하위 호환을 깨지 않는다."""
    provider = FakeLLMProvider()

    output = (
        await provider.extract_modify_conditions(
            "두 번째는 별로야",
            UserConditions(search_center="경복궁"),
            shown_place_count=3,
        )
    ).data

    assert output.modify.modify_type is ModifyType.REJECT_SPECIFIC
    assert output.modify.target_indices == [2]


def test_modify_instruction_includes_shown_place_names_when_provided() -> None:
    """SCHEDULE-09 후속(이름 지목): 이름 목록이 있으면 프롬프트에 번호 매긴
    목록으로 포함된다."""
    instruction = build_modify_extraction_instruction(
        UserConditions(search_center="경복궁"),
        shown_place_count=3,
        shown_place_names=["경복궁", "두가헌 레스토랑", "갤러리조선"],
    )

    assert "1. 경복궁" in instruction
    assert "2. 두가헌 레스토랑" in instruction
    assert "3. 갤러리조선" in instruction


def test_modify_instruction_omits_shown_place_names_block_when_absent() -> None:
    """이름이 없으면 목록 블록 자체가 생략된다 — Gemini가 없는 이름으로
    엉뚱하게 매칭 시도하는 걸 막는다."""
    instruction = build_modify_extraction_instruction(
        UserConditions(search_center="경복궁"), shown_place_count=3
    )

    assert "노출된 항목 목록 (순번. 이름)" not in instruction


@pytest.mark.asyncio
async def test_classify_intent_name_reference_routes_to_modify() -> None:
    """SCHEDULE-09 후속(이름 지목): 순번 없이 이름 + 거절 신호만 있어도
    classify_intent()가 MODIFY로 분류해야 extract_modify_conditions()까지
    도달한다."""
    provider = FakeLLMProvider()

    result = (
        await provider.classify_intent(
            "두가헌 레스토랑은 빼줘",
            has_previous_recommendation=True,
            shown_place_count=3,
            shown_place_names=["경복궁", "두가헌 레스토랑", "갤러리조선"],
        )
    ).data

    assert result.intent is Intent.MODIFY


@pytest.mark.asyncio
async def test_extract_modify_conditions_ordinal_without_reject_cue_stays_reject_all() -> None:
    """SCHEDULE-09: 순번 언급이 있어도 거절 신호가 없으면 REJECT_SPECIFIC로 오분류하지 않는다."""
    provider = FakeLLMProvider()

    output = (
        await provider.extract_modify_conditions(
            "다른 곳 보여줘", UserConditions(search_center="경복궁"), shown_place_count=3
        )
    ).data

    assert output.modify.modify_type is ModifyType.REJECT_ALL
    assert output.modify.target_indices == []


def test_modify_instruction_includes_reject_specific_rule_and_shown_count() -> None:
    """SCHEDULE-09: Real Gemini MODIFY 프롬프트에도 REJECT_SPECIFIC/target_indices
    판별 규칙과 노출 항목 수가 반드시 들어간다."""
    instruction = build_modify_extraction_instruction(
        UserConditions(search_center="창경궁"), shown_place_count=3
    )

    assert "REJECT_SPECIFIC" in instruction
    assert "target_indices" in instruction
    assert "현재 노출된 일정/추천 항목 수: 3" in instruction


def test_recommend_instruction_includes_time_unit_conversion_rule() -> None:
    """time_available/max_travel_time이 분 단위임을 명시하지 않아 LLM이 "5시간"을
    그대로 5로 뽑는 실사용 오류가 확인됨(2026-08-13) — 프롬프트에 환산 규칙을
    명시적으로 넣었는지 확인한다."""
    instruction = build_recommend_extraction_instruction()

    assert "분(minute) 단위 정수" in instruction
    assert "60을 곱해" in instruction
    assert "5시간" in instruction and "300" in instruction


def test_condition_instructions_treat_permissive_expressions_as_unrestricted() -> None:
    """허용은 선호가 아니다.

    "야외도 괜찮아"는 기존 실내 조건을 풀어야 하고, "비/사람 많아도 괜찮아"는
    각각 날씨·혼잡을 즐기거나 선호한다는 뜻이 아니다. RECOMMEND와 MODIFY가 공통
    규칙을 모두 포함하는지 고정해 모델·프롬프트 변경 때 조용한 오분류를 막는다.
    """
    recommend = build_recommend_extraction_instruction()
    modify = build_modify_extraction_instruction(UserConditions(search_center="경복궁"))

    for instruction in (recommend, modify):
        assert "비 와도 괜찮아" in instruction
        assert "weather_intent=IGNORE" in instruction
        assert "사람 많아도 괜찮아" in instruction
        assert "concentration_intent=IGNORE" in instruction


def test_condition_instructions_include_transport_mapping_rules() -> None:
    """TP-105 — D의 자동차 경로 실측이 transport=CAR를 보고 동작하므로,
    RECOMMEND/MODIFY 양쪽 프롬프트에 구체 매핑 규칙이 있는지 고정한다.
    한쪽만 규칙이 있으면 그 인텐트에서만 조용히 동작이 갈린다.
    """
    recommend = build_recommend_extraction_instruction()
    modify = build_modify_extraction_instruction(UserConditions(search_center="경복궁"))

    for instruction in (recommend, modify):
        assert "이동수단(transport) 규칙" in instruction
        assert 'transport="car"' in instruction
        assert 'transport="walk"' in instruction
        assert 'transport="public"' in instruction
        assert "야외도 괜찮아" in instruction
        assert 'environment="any"' in instruction


def test_modify_instruction_includes_time_unit_conversion_rule() -> None:
    """위와 같은 이유로 MODIFY(조건 변경) 프롬프트에도 같은 환산 규칙이 있어야 한다 —
    "이번엔 5시간으로 다시 짜줘"처럼 SCHEDULE 다음 턴 조건 변경이 이 경로를 탄다."""
    instruction = build_modify_extraction_instruction(UserConditions(search_center="경복궁"))

    assert "분(minute) 단위 정수" in instruction
    assert "60을 곱해" in instruction
    assert "5시간" in instruction and "300" in instruction


@pytest.mark.asyncio
async def test_extract_modify_conditions_tc08_change_condition_budget() -> None:
    provider = FakeLLMProvider()
    current = UserConditions(search_center="경복궁", place_types=[PlaceType.RESTAURANT])

    output = (
        await provider.extract_modify_conditions("무료인 곳으로", current)
    ).data

    assert output.modify.modify_type is ModifyType.CHANGE_CONDITION
    assert output.modify.condition_changes.budget == "free"
    # search_center는 changed_fields에 없으므로 Keep 대상이지만, condition_changes
    # 자체에는 null로 정리되어 담긴다(ModifyPayload 검증기가 강제) — 실제 Keep 처리는
    # state_transform.py가 changed_fields만 읽어서 수행한다.
    assert output.modify.condition_changes.search_center is None
    assert output.modify.changed_fields == ["budget"]


@pytest.mark.asyncio
async def test_extract_modify_conditions_tc09_change_search_center() -> None:
    provider = FakeLLMProvider()
    current = UserConditions(search_center="경복궁")

    output = (
        await provider.extract_modify_conditions("인사동 근처로 바꿔줘", current)
    ).data

    assert output.modify.condition_changes.search_center == "인사동"
    assert output.modify.changed_fields == ["search_center"]


@pytest.mark.asyncio
async def test_classify_intent_tc11_general_place_knowledge() -> None:
    provider = FakeLLMProvider()

    result = await provider.classify_intent(
        "경복궁은 언제 지어졌어?", has_previous_recommendation=False, shown_place_count=0
    )

    assert result.data.intent is Intent.GENERAL


@pytest.mark.asyncio
async def test_classify_intent_tc12_out_of_scope_harmful() -> None:
    provider = FakeLLMProvider()

    result = await provider.classify_intent(
        "이 씨발 놈아", has_previous_recommendation=False, shown_place_count=0
    )

    assert result.data.intent is Intent.OUT_OF_SCOPE
    assert result.data.out_of_scope_category is OutOfScopeCategory.HARMFUL


@pytest.mark.asyncio
async def test_classify_intent_tc13_out_of_scope_unrelated() -> None:
    provider = FakeLLMProvider()

    result = await provider.classify_intent(
        "주식 추천해줘", has_previous_recommendation=False, shown_place_count=0
    )

    assert result.data.intent is Intent.OUT_OF_SCOPE
    assert result.data.out_of_scope_category is OutOfScopeCategory.UNRELATED


def _fake_recommendation_item(place_id: str, name: str) -> RecommendationItem:
    return RecommendationItem(
        place_id=place_id,
        name=name,
        category="attraction",
        distance_km=0.3,
        remaining_minutes=120,
        environment_type="indoor",
        recommendation_reason="테스트용 고정 후보입니다.",
        explanations=[],
        warnings=[],
        score=0.5,
        feature_scores={},
        weights_used={},
    )


@pytest.mark.asyncio
async def test_generate_schedule_plan_selects_up_to_three_candidates() -> None:
    """SCHEDULE-04: 실제 Gemini 없이 candidates 앞쪽 최대 3개로 고정 일정을 만든다."""
    provider = FakeLLMProvider()
    candidates = [
        _fake_recommendation_item(f"place-{i}", f"장소 {i}") for i in range(5)
    ]
    request = SchedulePlanningRequest(
        candidates=candidates,
        conditions=UserConditions(),
        visit_datetime=datetime(2026, 8, 7, 15, 0, tzinfo=ZoneInfo("Asia/Seoul")),
        pairwise_distances_km={},
    )

    result = (await provider.generate_schedule_plan(request)).data

    assert len(result.items) == 3
    assert [item.place_id for item in result.items] == ["place-0", "place-1", "place-2"]
    # 시각 필드는 LLM 응답 계약에서 빠졌다(TP-215) — 스텁도 만들지 않는다.
    assert all(item.estimated_duration_min > 0 for item in result.items)
    assert result.route_summary


class TestBuildSchedulePlanningInstructionDynamicCount:
    """받은 목표 개수 범위를 프롬프트가 그대로 옮긴다.

    범위 계산은 이 함수의 일이 아니다(TP-239) — budget.derive_item_range()가
    후보 분류와 거리까지 보고 구하고, 이 함수는 후보를 모르므로 받아 쓴다.
    """

    def test_시간_제한이_없으면_기존_3에서_5개_문구를_쓴다(self):
        instruction = build_schedule_planning_instruction(
            time_available_min=None, item_range=(3, 5)
        )
        assert "3~5개" in instruction
        assert "3개 이상 5개 이하" in instruction
        assert "3~4시간 내외로 구성" in instruction

    def test_받은_범위를_그대로_쓴다(self):
        instruction = build_schedule_planning_instruction(
            time_available_min=90, item_range=(1, 1)
        )
        assert "1개" in instruction
        assert "1개 이상 1개 이하" in instruction
        assert "3~5개" not in instruction
        assert "활동 가능 시간은 90분" in instruction

    def test_같은_시간이어도_범위가_다르면_문구가_다르다(self):
        """**상한이 활동 가능 시간만으로 정해지지 않는다는 증거다.**

        후보가 박물관뿐이면 3시간에 2곳, 관광지면 3곳이다. 예전에는 시간만 보고
        버킷으로 정해서 같은 시간이면 항상 같은 문구가 나갔다.
        """

        museums = build_schedule_planning_instruction(
            time_available_min=180, item_range=(2, 2)
        )
        attractions = build_schedule_planning_instruction(
            time_available_min=180, item_range=(2, 3)
        )

        assert "2개 이상 2개 이하" in museums
        assert "2개 이상 3개 이하" in attractions

    def test_붙어_있는_후보가_있으면_짧게_제안해_달라고_부탁한다(self):
        """**여는 것만으로는 짧아지지 않는다.** (TP-243)

        `policy_for(clustered=True)`가 최소값을 45분까지 열어두지만, 그 여유를
        실제로 쓰는 것은 예산이 빡빡할 때의 `fit_durations_to_budget()`뿐이다.
        시간을 넉넉히 말한 요청에서는 붙어 있는 곳도 90분씩 그대로 나갔다
        (2026-09-07 실측). 그래서 프롬프트가 부탁한다.
        """

        instruction = build_schedule_planning_instruction(
            time_available_min=180, item_range=(2, 4), clustered_candidates=True
        )

        assert "걸어서 5분 안쪽에 붙어 있는" in instruction
        assert "45~60분으로 짧게 제안" in instruction

    def test_완화_대상이_아닌_분류는_그대로_잡으라고_함께_말한다(self):
        """박물관에 45분을 제안해도 `policy_for()`가 90분으로 되돌린다. 안 그러면
        LLM의 판단만 버려지고 편성은 그대로다."""

        instruction = build_schedule_planning_instruction(
            time_available_min=180, item_range=(2, 4), clustered_candidates=True
        )

        assert "문화시설과 식사 자리는 붙어 있어도 원래대로" in instruction

    def test_붙어_있는_후보가_없으면_그_문단이_없다(self):
        """**대조군.** 늘 붙으면 프롬프트가 없는 사실을 말하게 된다."""

        instruction = build_schedule_planning_instruction(
            time_available_min=180, item_range=(2, 4)
        )

        assert "붙어 있는" not in instruction

    def test_시간을_말하지_않은_요청에도_붙는다(self):
        """가정 예산(240분)으로 도는 턴이야말로 이 부탁이 필요한 자리다 — 예산이
        넉넉해 아무도 체류를 안 줄이는 쪽이라서다."""

        instruction = build_schedule_planning_instruction(
            item_range=(3, 5), clustered_candidates=True
        )

        assert "45~60분으로 짧게 제안" in instruction

    def test_짧은_시간에는_체류시간_비현실적_단축_경고_문구가_있다(self):
        instruction = build_schedule_planning_instruction(
            time_available_min=90, item_range=(1, 1)
        )
        assert "비현실적으로 짧게" in instruction


class TestBuildSchedulePlanningInstructionDoesNotPushToFill:
    """**"상한까지 채우라"는 지시를 뺐다** (TP-239).

    2026-08-18에 과소-채움을 막으려고 넣은 문구다. 그때는 상한이 버킷 상수라
    예산과 무관했고 "범위 안인데 덜 채운다"가 아까운 상황이었다. 지금은 상한이
    예산에서 나오고 총 소요 시간은 budget.fit_durations_to_budget()이 체류시간을
    조절해 맞춘다 — 여기서 넉넉히 잡으라고 시키면 그 조절과 정면으로 싸운다.
    """

    def test_개수를_늘려_시간을_채우라고_시키지_않는다(self) -> None:
        instruction = build_schedule_planning_instruction(360, item_range=(2, 5))

        assert "가깝게 채우고" not in instruction
        assert "너무 일찍 끝내지" not in instruction
        assert "넉넉히 잡아" not in instruction

    def test_개수를_늘리지_말라고_명시한다(self) -> None:
        instruction = build_schedule_planning_instruction(360, item_range=(2, 5))

        assert "개수를 늘리지 마세요" in instruction
        assert "체류시간을 늘려 잡지 마세요" in instruction

    def test_상한이_이미_예산에서_계산된_값임을_알려준다(self) -> None:
        """LLM이 범위를 의심하고 임의로 벗어나지 않게 근거를 준다."""

        instruction = build_schedule_planning_instruction(180, item_range=(2, 3))

        assert "실제로 들어가는 수로 이미 계산한 값" in instruction


class TestSchedulePlanningContextIncludesOperatingHours:
    """지난번 발표 후 논의: 뒷 순서 스탑이 도착 예정 시각 기준으로 이미 폐점일
    수 있는 문제(9절 "폐점 스탑 감지")에, planner.py의 구조적 후처리에 더해
    프롬프트에도 운영시간을 함께 전달해 LLM이 애초에 피하도록 유도한다."""

    def test_instruction에_운영시간_고려_규칙이_있다(self) -> None:
        instruction = build_schedule_planning_instruction(item_range=(3, 5))
        assert "운영시간" in instruction
        assert "마감했을 곳" in instruction

    def test_instruction이_시각을_계산하지_말라고_지시한다(self) -> None:
        """TP-215 — 도착시각·이동시간·총 소요시간은 응답을 받은 뒤 엔진이 채운다.
        warnings도 마찬가지라 아예 응답 스키마에서 빠졌다."""

        instruction = build_schedule_planning_instruction(item_range=(3, 5))
        assert "시각은 계산하지 마세요" in instruction
        assert "estimated_arrival" not in instruction
        assert "travel_to_next_min" not in instruction
        assert "total_duration_min" not in instruction

    def test_후보_목록에_운영시간이_포함된다(self) -> None:
        candidate = RecommendationItem(
            place_id="place-1",
            name="장소 1",
            category="attraction",
            distance_km=0.3,
            remaining_minutes=120,
            operating_hours_display="09:00~18:00",
            environment_type="indoor",
            recommendation_reason="테스트용 고정 후보입니다.",
            explanations=[],
            warnings=[],
            score=0.5,
            feature_scores={},
            weights_used={},
        )
        request = SchedulePlanningRequest(
            candidates=[candidate],
            conditions=UserConditions(),
            visit_datetime=datetime(2026, 8, 7, 15, 0, tzinfo=ZoneInfo("Asia/Seoul")),
            pairwise_distances_km={},
        )

        context = format_schedule_planning_context(request, "15:00")

        assert "운영시간=09:00~18:00" in context

    def test_운영시간_미확인_후보는_확인불가로_표시된다(self) -> None:
        candidate = _fake_recommendation_item("place-1", "장소 1")
        request = SchedulePlanningRequest(
            candidates=[candidate],
            conditions=UserConditions(),
            visit_datetime=datetime(2026, 8, 7, 15, 0, tzinfo=ZoneInfo("Asia/Seoul")),
            pairwise_distances_km={},
        )

        context = format_schedule_planning_context(request, "15:00")

        assert "운영시간=확인불가" in context


# --- COMPARE targets 이름 지목 ---------------------------------------------
#
# 이름으로 비교 대상을 지목하면 그 이름의 순번만 targets에 담긴다. 개수만 알던
# 때는 이름과 순번의 대응을 알 수 없어 "all"이나 임의의 번호가 나왔고, 사용자가
# 지목하지 않은 장소가 비교 대상에 섞였다.

_SHOWN_PLACES = ["가회민화박물관", "오설록 티하우스 북촌점", "백인제가옥"]


@pytest.mark.asyncio
async def test_extract_compare_request_resolves_place_names_to_ranks() -> None:
    provider = FakeLLMProvider()

    output = (
        await provider.extract_compare_request(
            "백인제가옥이랑 가회민화박물관 비교해줘",
            shown_place_count=3,
            shown_place_names=_SHOWN_PLACES,
        )
    ).data

    assert output.compare is not None
    assert output.compare.targets == [1, 3]


@pytest.mark.asyncio
async def test_extract_compare_request_mixes_ordinal_and_name() -> None:
    provider = FakeLLMProvider()

    output = (
        await provider.extract_compare_request(
            "첫 번째랑 백인제가옥 중에 어디가 더 가까워?",
            shown_place_count=3,
            shown_place_names=_SHOWN_PLACES,
        )
    ).data

    assert output.compare is not None
    assert output.compare.targets == [1, 3]
    assert output.compare.criteria is CompareCriteria.TRAVEL_TIME


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "user_input",
    ["첫 번째랑 백인제가옥 중에 어디가 더 빨리 갈까?", "둘 중 얼마나 걸려?", "어디가 덜 막힐까?"],
)
async def test_extract_compare_request_travel_time_criteria(user_input: str) -> None:
    """TP-105/106 실측 연결 — "빨리 갈까?"류 발화는 travel_time으로 판별한다.

    "덜 막힐까?"(실시간 교통 정체)는 아직 별도 API 연동 전이라 지금은 같은
    travel_time 기준(실측 경로, 정체 미반영)으로 받는다(연결 과제로 남김).
    """
    provider = FakeLLMProvider()

    output = (
        await provider.extract_compare_request(
            user_input,
            shown_place_count=3,
            shown_place_names=_SHOWN_PLACES,
        )
    ).data

    assert output.compare is not None
    assert output.compare.criteria is CompareCriteria.TRAVEL_TIME


@pytest.mark.asyncio
async def test_extract_compare_request_without_names_falls_back_to_all() -> None:
    """이름 목록이 없으면(과거 세션 등) 이름 지목을 해석할 근거가 없다.

    근거 없이 순번을 지어내지 않고 기존 동작("all")을 유지한다.
    """
    provider = FakeLLMProvider()

    output = (
        await provider.extract_compare_request(
            "백인제가옥이랑 가회민화박물관 비교해줘",
            shown_place_count=3,
        )
    ).data

    assert output.compare is not None
    assert output.compare.targets == "all"


@pytest.mark.asyncio
async def test_extract_compare_request_unmentioned_places_stay_all() -> None:
    """순번도 이름도 지목하지 않으면 종전대로 전체 비교다."""
    provider = FakeLLMProvider()

    output = (
        await provider.extract_compare_request(
            "어디가 좋아?",
            shown_place_count=3,
            shown_place_names=_SHOWN_PLACES,
        )
    ).data

    assert output.compare is not None
    assert output.compare.targets == "all"


@pytest.mark.asyncio
async def test_extract_compare_request_out_of_range_ordinal_asks_clarification() -> None:
    provider = FakeLLMProvider()

    output = (
        await provider.extract_compare_request(
            "세 번째랑 첫 번째 비교해줘",
            shown_place_count=2,
            shown_place_names=_SHOWN_PLACES[:2],
        )
    ).data

    assert output.status is OutputStatus.NEEDS_CLARIFICATION
    assert output.compare is None
    assert output.clarification is not None
    assert "2개" in output.clarification.message


def test_compare_prompt_includes_numbered_place_names() -> None:
    instruction = build_compare_extraction_instruction(
        shown_place_count=3, shown_place_names=_SHOWN_PLACES
    )

    assert "아래 노출된 항목 목록 (순번. 이름):" in instruction
    assert "1. 가회민화박물관" in instruction
    assert "3. 백인제가옥" in instruction


def test_compare_prompt_omits_list_block_when_names_missing() -> None:
    """이름이 전부 비어 있으면 목록 블록을 넣지 않는다 — 빈 목록을 주면
    모델이 그 형식을 흉내 내며 없는 순번을 만들어낸다.

    규칙 본문에도 "아래 노출된 항목 목록"이라는 표현이 나오므로, 블록의 머리글을
    통째로 대조해야 헛통과하지 않는다.
    """
    instruction = build_compare_extraction_instruction(
        shown_place_count=2, shown_place_names=["", ""]
    )

    assert "아래 노출된 항목 목록 (순번. 이름):" not in instruction
