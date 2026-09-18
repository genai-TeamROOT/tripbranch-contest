"""합성 리뷰 Gemini 구조화 출력 계약.

모델이 보내온 한글은 이 계약을 지나면서 NFC로 맞춰진다. 아래 to_nfc의 주석을 본다.
"""

from __future__ import annotations

import unicodedata
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.synthetic_reviews.review_plans import (
    MAX_REVIEWS_PER_PLACE,
    MIN_REVIEWS_PER_PLACE,
)
from app.synthetic_reviews.sentiments import Sentiment


def to_nfc(value: object) -> object:
    """한글 문자열을 완성형(NFC)으로 맞춘다.

    한글은 완성형과 조합형(NFD)이 화면에 똑같이 그려지지만 문자열로는 다르다.
    "상시 개방"이 완성형으로는 5자, 조합형으로는 11자다. 공식 원문은 NFC인데
    모델이 NFD로 돌려주면 validate_review_batch의 sourceValue 대조가 어긋나
    모델이 원문을 정확히 베꼈는데도 실패로 잡힌다. 2026-08-25 종로구 표본에서
    실제로 gemini-3.5-flash-lite가 그렇게 반환했다.

    비교 지점마다 정규화하지 않고 계약을 지나는 길목에서 한 번만 맞춘다.
    검사를 새로 추가할 때 정규화를 잊어버리는 경로가 생기지 않게 하려는 것이다.
    """
    return unicodedata.normalize("NFC", value) if isinstance(value, str) else value


class ClaimGrounding(StrEnum):
    TOUR_API = "TOUR_API"
    SYNTHETIC_SCENARIO = "SYNTHETIC_SCENARIO"


class SyntheticReviewClaim(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    text: str = Field(min_length=1, max_length=300)
    grounding: ClaimGrounding
    source_field: str | None = Field(default=None, alias="sourceField")
    source_value: str | None = Field(default=None, alias="sourceValue")

    # mode="before"라 길이 제약보다 먼저 돈다. NFD는 글자 수가 2~3배로 늘어
    # 정규화를 나중에 하면 max_length에 엉뚱하게 걸린다.
    _normalize = field_validator("text", "source_value", mode="before")(to_nfc)

    @model_validator(mode="after")
    def validate_grounding_fields(self) -> SyntheticReviewClaim:
        if self.grounding is ClaimGrounding.TOUR_API:
            if not self.source_field or not self.source_value:
                raise ValueError("TOUR_API claim에는 sourceField/sourceValue가 필요합니다.")
        elif self.source_field is not None or self.source_value is not None:
            raise ValueError(
                "SYNTHETIC_SCENARIO claim에는 sourceField/sourceValue를 쓸 수 없습니다."
            )
        return self


class SyntheticReview(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    review_index: int = Field(
        alias="reviewIndex", ge=0, le=MAX_REVIEWS_PER_PLACE - 1
    )
    persona_type: str = Field(alias="personaType", min_length=1, max_length=200)
    sentiment: Sentiment
    visit_context: str = Field(alias="visitContext", min_length=1, max_length=300)
    review_text: str = Field(alias="reviewText", min_length=1, max_length=1200)
    claims: list[SyntheticReviewClaim] = Field(min_length=1, max_length=12)

    _normalize = field_validator(
        "persona_type", "visit_context", "review_text", mode="before"
    )(to_nfc)


class SyntheticReviewBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # 리뷰 수는 장소마다 다르다. 여기서는 허용 범위만 막고, 그 장소에 맞는 정확한
    # 개수는 validate_review_batch가 리뷰 계획과 대조해 확인한다.
    reviews: list[SyntheticReview] = Field(
        min_length=MIN_REVIEWS_PER_PLACE,
        max_length=MAX_REVIEWS_PER_PLACE,
    )


__all__ = [
    "ClaimGrounding",
    "SyntheticReview",
    "SyntheticReviewBatch",
    "SyntheticReviewClaim",
    "to_nfc",
]
