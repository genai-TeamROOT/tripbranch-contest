from dataclasses import replace

import pytest

from app.synthetic_reviews.personas import PlacePersonaInput, generate_personas
from app.synthetic_reviews.review_plans import ReviewPlan, generate_review_plans
from app.synthetic_reviews.sentiments import AxisPolarity, Sentiment, assess_sentiment


def _place(**overrides: object) -> PlacePersonaInput:
    values: dict[str, object] = {
        "content_id": "126508",
        "content_type_id": "14",
        "operating_hours_raw": "09:00~18:00",
        "rest_date_raw": "매주 화요일",
        "parking_info_raw": "불가능",
        "parking_fee_raw": "무료",
        "use_fee_raw": "무료",
        "pet_raw": "불가",
        "baby_carriage_raw": "없음",
        "credit_card_raw": "가능",
        "restroom_raw": "있음",
    }
    values.update(overrides)
    return PlacePersonaInput(**values)  # type: ignore[arg-type]


def _plan(place: PlacePersonaInput, axis: str) -> ReviewPlan:
    personas = generate_personas(place, max_count=5)
    base = generate_review_plans(personas)[0]
    field_by_axis = {
        "OPERATING_HOURS": "operating_hours_raw",
        "REST_DATE": "rest_date_raw",
        "PARKING_AVAILABILITY": "parking_info_raw",
        "PARKING_FEE": "parking_fee_raw",
        "USE_FEE": "use_fee_raw",
        "PET_POLICY": "pet_raw",
        "STROLLER_POLICY": "baby_carriage_raw",
        "CARD_PAYMENT": "credit_card_raw",
        "RESTROOM": "restroom_raw",
    }
    return replace(base, focus_axes=(axis,), evidence_fields=(field_by_axis[axis],))


@pytest.mark.parametrize("raw", ["가능", "주차 가능 (무료)"])
def test_주차_가능은_긍정으로_판정한다(raw: str) -> None:
    place = _place(parking_info_raw=raw)
    result = assess_sentiment(place, _plan(place, "PARKING_AVAILABILITY"))

    assert result.sentiment is Sentiment.POSITIVE
    assert result.axes[0].polarity is AxisPolarity.POSITIVE
    assert result.axes[0].source_value == raw


@pytest.mark.parametrize("raw", ["불가능", "불가", "없음"])
def test_주차_불가는_부정으로_판정한다(raw: str) -> None:
    place = _place(parking_info_raw=raw)

    result = assess_sentiment(place, _plan(place, "PARKING_AVAILABILITY"))
    assert result.sentiment is Sentiment.NEGATIVE


@pytest.mark.parametrize(
    ("axis", "field", "raw", "expected"),
    [
        ("PET_POLICY", "pet_raw", "동반 불가", Sentiment.NEGATIVE),
        ("STROLLER_POLICY", "baby_carriage_raw", "가능", Sentiment.POSITIVE),
        ("CARD_PAYMENT", "credit_card_raw", "모든 카드 사용 가능", Sentiment.POSITIVE),
        ("RESTROOM", "restroom_raw", "있음", Sentiment.POSITIVE),
    ],
)
def test_편의_조건의_명시적_가능과_불가를_판정한다(
    axis: str, field: str, raw: str, expected: Sentiment
) -> None:
    place = _place(**{field: raw})

    assert assess_sentiment(place, _plan(place, axis)).sentiment is expected


@pytest.mark.parametrize("free_value", ["무료", "입장료 0원"])
@pytest.mark.parametrize("paid_value", ["어른 3,000원", "입장료 10,000원"])
def test_무료는_긍정이지만_구체_가격은_중립이다(
    free_value: str, paid_value: str
) -> None:
    free_place = _place(use_fee_raw=free_value)
    paid_place = _place(use_fee_raw=paid_value)

    free_result = assess_sentiment(free_place, _plan(free_place, "USE_FEE"))
    paid_result = assess_sentiment(paid_place, _plan(paid_place, "USE_FEE"))
    assert free_result.sentiment is Sentiment.POSITIVE
    assert paid_result.sentiment is Sentiment.NEUTRAL


@pytest.mark.parametrize("axis", ["OPERATING_HOURS", "REST_DATE"])
def test_운영정보는_존재만으로_긍정이나_부정을_단정하지_않는다(axis: str) -> None:
    place = _place()

    assert assess_sentiment(place, _plan(place, axis)).sentiment is Sentiment.NEUTRAL


def test_긍정과_부정_축이_함께_있으면_mixed다() -> None:
    place = _place(parking_info_raw="불가능", parking_fee_raw="무료")
    plan = replace(
        _plan(place, "PARKING_AVAILABILITY"),
        focus_axes=("PARKING_AVAILABILITY", "PARKING_FEE"),
        evidence_fields=("parking_info_raw", "parking_fee_raw"),
    )

    assert assess_sentiment(place, plan).sentiment is Sentiment.MIXED


def test_계획에_없는_근거_필드를_사용하면_거부한다() -> None:
    place = _place()
    plan = replace(_plan(place, "PET_POLICY"), evidence_fields=())

    with pytest.raises(ValueError, match="허용되지 않은 근거"):
        assess_sentiment(place, plan)


def test_공식_정보가_비어_있으면_감정을_발명하지_않고_거부한다() -> None:
    place = _place(pet_raw=None)
    plan = replace(_plan(_place(), "PET_POLICY"), evidence_fields=("pet_raw",))

    with pytest.raises(ValueError, match="공식 정보가 없습니다"):
        assess_sentiment(place, plan)


def test_알_수_없는_평가_축은_거부한다() -> None:
    place = _place()
    plan = replace(_plan(place, "OPERATING_HOURS"), focus_axes=("CROWD_LEVEL",))

    with pytest.raises(ValueError, match="알 수 없는 평가 축"):
        assess_sentiment(place, plan)
