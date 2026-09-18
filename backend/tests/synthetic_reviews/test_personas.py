import pytest

from app.synthetic_reviews.personas import (
    PERSONA_COUNT_CEILING,
    PERSONA_COUNT_FLOOR,
    CompanionTypeTrait,
    PlacePersonaInput,
    PriorityTrait,
    TravelPartyTrait,
    VisitPurposeTrait,
    VisitStyleTrait,
    generate_personas,
)


def _place(**overrides: object) -> PlacePersonaInput:
    values: dict[str, object] = {
        "content_id": "126508",
        "content_type_id": "14",
    }
    values.update(overrides)
    return PlacePersonaInput(**values)  # type: ignore[arg-type]


def test_네_가지_축을_조합해_복합_페르소나를_생성한다() -> None:
    personas = generate_personas(
        _place(
            operating_hours_raw="09:00~18:00",
            parking_info_raw="불가능",
            use_fee_raw="3,000원",
            pet_raw="불가",
        )
    )

    # 근거가 되는 우선순위가 4개뿐이므로 페르소나도 4명이다. 5명으로 채우면
    # 남는 한 명에게 인용할 공식 정보가 없다.
    assert len(personas) == 4
    assert personas[0].travel_party is TravelPartyTrait.SOLO
    assert personas[0].companion_type is CompanionTypeTrait.NONE
    assert personas[0].visit_purpose is VisitPurposeTrait.CULTURE
    assert personas[0].priority is PriorityTrait.SCHEDULE
    assert personas[0].visit_style is VisitStyleTrait.FIRST_TIME
    assert personas[0].persona_id == "SOLO_NONE_CULTURE_SCHEDULE_FIRST_TIME"
    assert "혼자" in personas[0].description
    assert "운영 일정" in personas[0].description


def test_공식_정보가_있는_중요_조건만_근거와_함께_쓴다() -> None:
    personas = generate_personas(
        _place(parking_info_raw="가능", parking_fee_raw="무료")
    )

    parking = personas[0]
    assert parking.priority is PriorityTrait.PARKING
    assert "주차를 중요하게" in parking.description
    assert parking.evidence_fields == ("parking_info_raw", "parking_fee_raw")
    assert parking.allowed_evaluation_axes == (
        "PARKING_AVAILABILITY",
        "PARKING_FEE",
    )


def test_빈_공식_정보는_중요_조건으로_선정하지_않는다() -> None:
    personas = generate_personas(
        _place(parking_info_raw="  ", pet_raw="", use_fee_raw=None)
    )

    assert all(persona.priority is PriorityTrait.GENERAL for persona in personas)
    assert all(persona.evidence_fields == () for persona in personas)
    assert all(
        persona.allowed_evaluation_axes == ("PERSONAL_PREFERENCE", "ITINERARY_FIT")
        for persona in personas
    )


def test_알_수_없는_장소_유형은_일반_탐색_목적으로_안전하게_대체한다() -> None:
    personas = generate_personas(_place(content_type_id="999"), max_count=5)

    # 이 장소는 공식 정보가 하나도 없어 근거가 0개다. generate_review_plans가
    # 3명 이상을 요구하므로 하한까지만 GENERAL로 채운다.
    assert len(personas) == PERSONA_COUNT_FLOOR
    assert all(
        persona.visit_purpose is VisitPurposeTrait.GENERAL_EXPLORATION
        for persona in personas
    )
    assert all("content_type_id" not in persona.evidence_fields for persona in personas)


def test_페르소나마다_조합과_id가_중복되지_않는다() -> None:
    personas = generate_personas(
        _place(
            operating_hours_raw="09:00~18:00",
            parking_info_raw="가능",
            use_fee_raw="무료",
            pet_raw="불가",
            baby_carriage_raw="없음",
        ),
        max_count=5,
    )

    assert len({persona.persona_id for persona in personas}) == 5
    assert [persona.priority for persona in personas] == [
        PriorityTrait.SCHEDULE,
        PriorityTrait.PARKING,
        PriorityTrait.BUDGET,
        PriorityTrait.PET,
        PriorityTrait.STROLLER,
    ]
    assert [persona.travel_party for persona in personas] == [
        TravelPartyTrait.SOLO,
        TravelPartyTrait.COMPANION,
        TravelPartyTrait.SOLO,
        TravelPartyTrait.COMPANION,
        TravelPartyTrait.SOLO,
    ]
    assert [persona.companion_type for persona in personas] == [
        CompanionTypeTrait.NONE,
        CompanionTypeTrait.CHILD,
        CompanionTypeTrait.NONE,
        CompanionTypeTrait.OLDER_ADULT,
        CompanionTypeTrait.NONE,
    ]


def test_동행자_유형은_content_id에_따라_결정적으로_다양화한다() -> None:
    first = generate_personas(_place(content_id="1"), max_count=4)
    second = generate_personas(_place(content_id="2"), max_count=4)

    first_companions = [
        persona.companion_type
        for persona in first
        if persona.travel_party is TravelPartyTrait.COMPANION
    ]
    second_companions = [
        persona.companion_type
        for persona in second
        if persona.travel_party is TravelPartyTrait.COMPANION
    ]
    assert first_companions != second_companions
    assert all(item is not CompanionTypeTrait.NONE for item in first_companions)


def test_같은_입력은_항상_같은_페르소나를_만든다() -> None:
    place = _place(operating_hours_raw="09:00~18:00", parking_info_raw="가능")

    assert generate_personas(place) == generate_personas(place)


@pytest.mark.parametrize("max_count", [2, 6])
def test_페르소나_수_상한은_3에서_5만_허용한다(max_count: int) -> None:
    with pytest.raises(ValueError, match="3~5"):
        generate_personas(_place(), max_count=max_count)


@pytest.mark.parametrize("field", ["content_id", "content_type_id"])
def test_장소_식별자는_필수다(field: str) -> None:
    values = {"content_id": "1", "content_type_id": "14", field: " "}
    with pytest.raises(ValueError, match=field):
        PlacePersonaInput(**values)


@pytest.mark.parametrize(
    ("fields", "expected_count"),
    [
        ({}, PERSONA_COUNT_FLOOR),
        ({"parking_info_raw": "가능"}, PERSONA_COUNT_FLOOR),
        ({"parking_info_raw": "가능", "pet_raw": "불가"}, PERSONA_COUNT_FLOOR),
        (
            {
                "parking_info_raw": "가능",
                "pet_raw": "불가",
                "restroom_raw": "있음",
            },
            3,
        ),
        (
            {
                "parking_info_raw": "가능",
                "pet_raw": "불가",
                "restroom_raw": "있음",
                "credit_card_raw": "가능",
            },
            4,
        ),
        (
            {
                "operating_hours_raw": "09:00~18:00",
                "parking_info_raw": "가능",
                "use_fee_raw": "무료",
                "pet_raw": "불가",
                "restroom_raw": "있음",
                "credit_card_raw": "가능",
            },
            PERSONA_COUNT_CEILING,
        ),
    ],
)
def test_페르소나_수는_공식_근거_수를_따른다(
    fields: dict[str, str], expected_count: int
) -> None:
    """근거가 없는 자리를 GENERAL로 메우면 모델이 인용할 것이 없어 근거를 지어낸다."""
    personas = generate_personas(_place(**fields))

    assert len(personas) == expected_count


def test_근거가_하한에_못_미치면_그_수까지만_general로_채운다() -> None:
    personas = generate_personas(_place(parking_info_raw="가능"))

    assert len(personas) == PERSONA_COUNT_FLOOR
    with_evidence = [p for p in personas if p.evidence_fields]
    without_evidence = [p for p in personas if not p.evidence_fields]

    assert len(with_evidence) == 1
    assert with_evidence[0].priority is PriorityTrait.PARKING
    # 남은 자리는 GENERAL이고, 이 페르소나들이 TP-152가 걸린 지점이다.
    assert len(without_evidence) == PERSONA_COUNT_FLOOR - 1
    assert all(p.priority is PriorityTrait.GENERAL for p in without_evidence)
