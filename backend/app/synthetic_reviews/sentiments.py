"""공식 TourAPI 원문과 리뷰 계획의 평가 축으로 sentiment를 판정한다."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from app.domain.operating_hours import clean_operating_text
from app.domain.parking import ParkingAvailability, normalize_parking
from app.synthetic_reviews.personas import PlacePersonaInput
from app.synthetic_reviews.review_plans import ReviewPlan


class Sentiment(StrEnum):
    POSITIVE = "POSITIVE"
    NEUTRAL = "NEUTRAL"
    MIXED = "MIXED"
    NEGATIVE = "NEGATIVE"


class AxisPolarity(StrEnum):
    POSITIVE = "POSITIVE"
    NEUTRAL = "NEUTRAL"
    NEGATIVE = "NEGATIVE"


@dataclass(frozen=True)
class AxisAssessment:
    axis: str
    polarity: AxisPolarity
    reason: str
    source_field: str | None
    source_value: str | None


@dataclass(frozen=True)
class SentimentAssessment:
    review_index: int
    persona_id: str
    sentiment: Sentiment
    reason: str
    axes: tuple[AxisAssessment, ...]


_AXIS_FIELDS: dict[str, tuple[str, ...]] = {
    "OPERATING_HOURS": ("operating_hours_raw",),
    "REST_DATE": ("rest_date_raw",),
    "PARKING_AVAILABILITY": ("parking_info_raw",),
    "PARKING_FEE": ("parking_fee_raw",),
    "USE_FEE": ("use_fee_raw",),
    "DISCOUNT": ("discount_info_raw",),
    "PET_POLICY": ("pet_raw",),
    "STROLLER_POLICY": ("baby_carriage_raw",),
    "CARD_PAYMENT": ("credit_card_raw",),
    "RESTROOM": ("restroom_raw",),
    "INFORMATION_CONTACT": ("info_center_raw",),
}
_SCENARIO_AXES = frozenset({"PERSONAL_PREFERENCE", "ITINERARY_FIT"})
_NEGATIVE_MARKERS = ("불가능", "불가", "없음", "금지", "안됨", "안 됨")
_POSITIVE_MARKERS = ("가능", "있음", "제공")
_ZERO_WON_PATTERN = re.compile(r"(?<![\d,])0\s*원")


def _source_for_axis(
    place: PlacePersonaInput, plan: ReviewPlan, axis: str
) -> tuple[str | None, str | None]:
    if axis in _SCENARIO_AXES:
        return None, None
    fields = _AXIS_FIELDS.get(axis)
    if fields is None:
        raise ValueError(f"알 수 없는 평가 축입니다: {axis}")
    for field in fields:
        raw = getattr(place, field)
        if raw is not None and raw.strip():
            if field not in plan.evidence_fields:
                raise ValueError(f"계획에 허용되지 않은 근거 필드입니다: {field}")
            return field, raw.strip()
    raise ValueError(f"평가 축 {axis}에 사용할 공식 정보가 없습니다.")


def _availability_polarity(raw: str) -> AxisPolarity:
    cleaned = clean_operating_text(raw) or raw.strip()
    probe = "".join(cleaned.split())
    negative = any(marker.replace(" ", "") in probe for marker in _NEGATIVE_MARKERS)
    positive = any(marker in probe for marker in _POSITIVE_MARKERS)
    if negative and positive:
        return AxisPolarity.NEUTRAL
    if negative:
        return AxisPolarity.NEGATIVE
    if positive:
        return AxisPolarity.POSITIVE
    return AxisPolarity.NEUTRAL


def _assess_axis(
    place: PlacePersonaInput, plan: ReviewPlan, axis: str
) -> AxisAssessment:
    source_field, source_value = _source_for_axis(place, plan, axis)
    if axis in _SCENARIO_AXES:
        return AxisAssessment(axis, AxisPolarity.NEUTRAL, "주관적 시나리오 축", None, None)
    assert source_field is not None and source_value is not None

    if axis == "PARKING_AVAILABILITY":
        availability = normalize_parking(source_value).availability
        polarity = {
            ParkingAvailability.AVAILABLE: AxisPolarity.POSITIVE,
            ParkingAvailability.UNAVAILABLE: AxisPolarity.NEGATIVE,
            ParkingAvailability.UNKNOWN: AxisPolarity.NEUTRAL,
        }[availability]
    elif axis in {"PET_POLICY", "STROLLER_POLICY", "CARD_PAYMENT", "RESTROOM"}:
        polarity = _availability_polarity(source_value)
    elif axis in {"PARKING_FEE", "USE_FEE"}:
        polarity = (
            AxisPolarity.POSITIVE
            if "무료" in source_value or _ZERO_WON_PATTERN.search(source_value)
            else AxisPolarity.NEUTRAL
        )
    elif axis == "DISCOUNT":
        judged = _availability_polarity(source_value)
        polarity = (
            AxisPolarity.POSITIVE
            if judged is AxisPolarity.POSITIVE
            else AxisPolarity.NEUTRAL
        )
    else:
        # 운영시간·휴무일·안내처는 값의 존재만으로 편리함이나 불편함을 단정하지 않는다.
        polarity = AxisPolarity.NEUTRAL

    return AxisAssessment(
        axis=axis,
        polarity=polarity,
        reason=f"공식 {source_field} 값에 따른 판정",
        source_field=source_field,
        source_value=source_value,
    )


def _combine(axes: tuple[AxisAssessment, ...]) -> Sentiment:
    polarities = {axis.polarity for axis in axes}
    if AxisPolarity.POSITIVE in polarities and AxisPolarity.NEGATIVE in polarities:
        return Sentiment.MIXED
    if AxisPolarity.NEGATIVE in polarities:
        return Sentiment.NEGATIVE
    if AxisPolarity.POSITIVE in polarities:
        return Sentiment.POSITIVE
    return Sentiment.NEUTRAL


def assess_sentiment(
    place: PlacePersonaInput, plan: ReviewPlan
) -> SentimentAssessment:
    if not plan.focus_axes:
        raise ValueError("sentiment 판정에는 하나 이상의 focus axis가 필요합니다.")
    axes = tuple(_assess_axis(place, plan, axis) for axis in plan.focus_axes)
    sentiment = _combine(axes)
    return SentimentAssessment(
        review_index=plan.review_index,
        persona_id=plan.persona_id,
        sentiment=sentiment,
        reason=" + ".join(f"{axis.axis}={axis.polarity.value}" for axis in axes),
        axes=axes,
    )


__all__ = [
    "AxisAssessment",
    "AxisPolarity",
    "Sentiment",
    "SentimentAssessment",
    "assess_sentiment",
]
