"""공식 장소 정보와 사전 결정된 계획으로 장소당 합성 리뷰를 한 번에 생성한다.

리뷰 수는 그 장소가 가진 공식 근거 수를 따라 3~5로 달라진다.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from functools import lru_cache

from google import genai
from google.genai import types as genai_types
from pydantic import BaseModel, Field, ValidationError, create_model

from app.prompts.loader import load_text
from app.prompts.registry import slot_versions
from app.synthetic_reviews.review_models import (
    ClaimGrounding,
    SyntheticReviewBatch,
    to_nfc,
)
from app.synthetic_reviews.review_plans import (
    MAX_REVIEWS_PER_PLACE,
    MIN_REVIEWS_PER_PLACE,
    ReviewPlan,
)
from app.synthetic_reviews.sentiments import SentimentAssessment

GENERATOR_VERSION = "synthetic-review-generator-3.1.0"
PROMPT_VERSION = f"synthetic_review.generate@{slot_versions()['synthetic_review.generate']}"

ALLOWED_OFFICIAL_FIELDS = (
    "content_id",
    "content_type_id",
    "title",
    "address",
    "lcls_systm1",
    "lcls_systm2",
    "lcls_systm3",
    "operating_hours_raw",
    "rest_date_raw",
    "parking_info_raw",
    "parking_fee_raw",
    "use_fee_raw",
    "discount_info_raw",
    "info_center_raw",
    "baby_carriage_raw",
    "pet_raw",
    "credit_card_raw",
    "restroom_raw",
)

_NUMBER_PATTERN = re.compile(r"\d+(?:[.,]\d+)?")
_SENTENCE_END_PATTERN = re.compile(r"[.!?]+(?:\s|$)")
_POSITIVE_MARKERS = ("가능", "있음", "제공", "무료")
_NEGATIVE_MARKERS = ("불가능", "불가", "없음", "금지", "안 됨", "안됨")
_UNSUPPORTED_SCENARIO_FACTS = (
    "직원",
    "친절",
    "불친절",
    "혼잡",
    "붐비",
    "대기",
    "청결",
    "깨끗",
    "더럽",
    "맛있",
    "맛없",
    "넓",
    "좁",
    "도보",
    "역에서",
)
_FALSE_VISIT_SIGNALS = ("직접 방문", "다녀왔", "지난주", "어제 방문", "직원에게 물")
_UNSUPPORTED_COMPANION_INFERENCES = (
    "아이들이 좋아",
    "어린이가 좋아",
    "어린이에게 안전",
    "교육적",
    "걷기 편",
    "계단이 적",
    "휠체어",
    "휴식 공간",
    "데이트 명소",
    "로맨틱",
    "사진이 잘",
)
_UNSUPPORTED_EVALUATIVE_INFERENCES = (
    "주차 공간이 넉넉",
    "주차장이 넉넉",
    "이동하기에 편리",
    "이동이 편리",
    "차량을 대기 수월",
    "전통적인 볼거리",
    "온 가족이 함께 둘러보기 알맞",
    "온 가족에게 알맞",
    "둘러보기 좋은 관광지",
    "방문하기 좋은 관광지",
    "일정 변경에도 유연하게 대처",
    "무리 없이 소화",
    "일정을 구상하기에 적합",
    "의미 있는 선택지",
    "도움이 되는 장소",
    "연계하기에도 무난",
    "동선에 포함하기 좋",
    "차분한 분위기",
)
_INTERNAL_METADATA_PATTERNS = (
    re.compile(r"content[_ ]?type(?:[_ ]?id)?", re.IGNORECASE),
    re.compile(r"lcls(?:_systm)?\d?", re.IGNORECASE),
    re.compile(r"콘텐츠\s*타입", re.IGNORECASE),
    re.compile(r"(?:관광지\s*)?유형\s*(?:코드|id)\b", re.IGNORECASE),
    re.compile(r"관광지\s*타입(?:\s*(?:코드|id|\d))?", re.IGNORECASE),
    re.compile(
        r"(?:operating_hours|rest_date|parking_info|parking_fee|use_fee|"
        r"discount_info|info_center|baby_carriage|credit_card|restroom)_raw",
        re.IGNORECASE,
    ),
)


def build_official_facts(row: Mapping[str, object]) -> dict[str, str]:
    """TourAPI 캐시에서 허용한 필드만 원문 그대로 보존한다.

    표기를 바꾸지 않되 한글만 NFC로 맞춘다. 모델 응답도 같은 규칙을 지나므로
    (review_models.to_nfc) 대조하는 두 값이 늘 같은 형태가 된다. 2026-08-25
    실측으로 종로구 9,534개 값이 전부 이미 NFC라 지금은 아무것도 바꾸지 않지만,
    한쪽만 정규화하면 다시 어긋나므로 양쪽을 같은 규칙 아래 둔다.
    """
    facts = {
        field: to_nfc(str(row[field]).strip())
        for field in ALLOWED_OFFICIAL_FIELDS
        if row.get(field) is not None and str(row[field]).strip()
    }
    if not facts.get("content_id"):
        raise ValueError("content_id가 필요합니다.")
    if not facts.get("content_type_id"):
        raise ValueError("content_type_id가 필요합니다.")
    if not facts.get("title"):
        raise ValueError("title이 필요합니다.")
    return facts


def _polarity_markers(text: str) -> tuple[bool, bool]:
    """`불가능` 안의 `가능`을 긍정으로 중복 판정하지 않는다."""
    probe = "".join(text.split())
    negative_markers = tuple("".join(marker.split()) for marker in _NEGATIVE_MARKERS)
    negative = any(marker in probe for marker in negative_markers)
    positive_probe = probe
    for marker in negative_markers:
        positive_probe = positive_probe.replace(marker, "")
    positive = any(
        "".join(marker.split()) in positive_probe for marker in _POSITIVE_MARKERS
    )
    return positive, negative


def _validate_claim(
    claim: object,
    *,
    plan: ReviewPlan,
    facts: Mapping[str, str],
) -> None:
    from app.synthetic_reviews.review_models import SyntheticReviewClaim

    assert isinstance(claim, SyntheticReviewClaim)
    if claim.grounding is ClaimGrounding.SYNTHETIC_SCENARIO:
        if _NUMBER_PATTERN.search(claim.text):
            raise ValueError("합성 시나리오 claim에는 구체적인 수치를 쓸 수 없습니다.")
        if any(keyword in claim.text for keyword in _UNSUPPORTED_SCENARIO_FACTS):
            raise ValueError(f"공식 근거 없는 객관적 사실 claim입니다: {claim.text}")
        return

    assert claim.source_field is not None and claim.source_value is not None
    if claim.source_field not in plan.evidence_fields:
        raise ValueError(f"리뷰 계획에 허용되지 않은 출처 필드: {claim.source_field}")
    expected = facts.get(claim.source_field)
    if expected is None:
        raise ValueError(f"공식 입력에 없는 출처 필드: {claim.source_field}")
    if claim.source_value != expected:
        raise ValueError(f"공식 출처 값 불일치: {claim.source_field}")

    source_positive, source_negative = _polarity_markers(expected)
    claim_positive, claim_negative = _polarity_markers(claim.text)
    if source_negative and not source_positive and claim_positive and not claim_negative:
        raise ValueError(f"공식 부정 정보와 모순되는 claim: {claim.text}")
    if source_positive and not source_negative and claim_negative and not claim_positive:
        raise ValueError(f"공식 긍정 정보와 모순되는 claim: {claim.text}")


def validate_review_batch(
    batch: SyntheticReviewBatch,
    *,
    facts: Mapping[str, str],
    plans: Sequence[ReviewPlan],
    sentiments: Sequence[SentimentAssessment],
) -> None:
    # 리뷰 수는 장소가 가진 공식 근거 수를 따라 3~5로 달라진다. 고정값이 아니라
    # 그 장소의 리뷰 계획 수를 기준으로 삼는다.
    review_count = len(plans)
    if not MIN_REVIEWS_PER_PLACE <= review_count <= MAX_REVIEWS_PER_PLACE:
        raise ValueError(
            f"리뷰 계획은 {MIN_REVIEWS_PER_PLACE}~{MAX_REVIEWS_PER_PLACE}개여야 합니다: "
            f"{review_count}개"
        )
    if len(sentiments) != review_count:
        raise ValueError(
            f"리뷰 계획 {review_count}개와 sentiment {len(sentiments)}개가 맞지 않습니다."
        )
    plans_by_index = {plan.review_index: plan for plan in plans}
    sentiments_by_index = {item.review_index: item for item in sentiments}
    expected_indices = set(range(review_count))
    last = review_count - 1
    if set(plans_by_index) != expected_indices or set(sentiments_by_index) != expected_indices:
        raise ValueError(f"리뷰 인덱스는 중복 없이 0~{last}여야 합니다.")
    if {review.review_index for review in batch.reviews} != expected_indices:
        raise ValueError(f"생성 리뷰 인덱스는 중복 없이 0~{last}여야 합니다.")

    official_numbers = set(_NUMBER_PATTERN.findall(" ".join(facts.values())))
    for review in batch.reviews:
        plan = plans_by_index[review.review_index]
        sentiment = sentiments_by_index[review.review_index]
        if review.persona_type != plan.persona_id:
            raise ValueError(f"계획과 personaType 불일치: {review.review_index}")
        if review.visit_context != plan.visit_context:
            raise ValueError(f"계획과 visitContext 불일치: {review.review_index}")
        if review.sentiment is not sentiment.sentiment:
            raise ValueError(f"계획과 sentiment 불일치: {review.review_index}")
        sentence_count = len(_SENTENCE_END_PATTERN.findall(review.review_text.strip()))
        if not 4 <= sentence_count <= 5:
            raise ValueError(
                f"reviewText는 4~5문장이어야 합니다: {review.review_index}"
            )
        if any(signal in review.review_text for signal in _FALSE_VISIT_SIGNALS):
            raise ValueError(f"실제 방문을 가장하는 표현: {review.review_index}")
        if any(
            pattern.search(review.review_text)
            for pattern in _INTERNAL_METADATA_PATTERNS
        ):
            raise ValueError(
                f"리뷰 본문에 내부 필드명 또는 코드가 노출됨: {review.review_index}"
            )
        if any(
            phrase in review.review_text
            for phrase in _UNSUPPORTED_COMPANION_INFERENCES
        ):
            raise ValueError(
                f"동행자 유형에서 근거 없이 장소 적합성을 추론함: {review.review_index}"
            )
        if any(
            phrase in review.review_text
            for phrase in _UNSUPPORTED_EVALUATIVE_INFERENCES
        ):
            raise ValueError(
                f"공식 사실에서 근거 없는 평가를 확장함: {review.review_index}"
            )
        generated_numbers = set(_NUMBER_PATTERN.findall(review.review_text))
        if generated_numbers - official_numbers:
            raise ValueError(
                f"공식 입력에 없는 수치가 생성됨: {sorted(generated_numbers - official_numbers)}"
            )
        for claim in review.claims:
            _validate_claim(claim, plan=plan, facts=facts)


def _prompt_payload(
    facts: Mapping[str, str],
    plans: Sequence[ReviewPlan],
    sentiments: Sequence[SentimentAssessment],
) -> str:
    payload = {
        "officialFacts": dict(facts),
        "reviewPlans": [asdict(plan) for plan in plans],
        "sentimentAssessments": [asdict(item) for item in sentiments],
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


class _GeminiClaimSchema(BaseModel):
    text: str
    grounding: ClaimGrounding
    sourceField: str | None = None
    sourceValue: str | None = None


@lru_cache(maxsize=MAX_REVIEWS_PER_PLACE + 1)
def wire_schema_for(review_count: int) -> type[BaseModel]:
    """리뷰 수에 맞춘 전송 스키마를 만든다.

    리뷰 수가 장소마다 3~5로 달라져 모듈 상수로 둘 수 없다. reviewIndex 상한과 배열
    길이를 그 장소의 리뷰 수로 고정해야 모델이 개수를 틀릴 여지가 사라진다. 값이
    세 가지뿐이라 캐시해 재사용한다.
    """
    if not MIN_REVIEWS_PER_PLACE <= review_count <= MAX_REVIEWS_PER_PLACE:
        raise ValueError(
            f"리뷰 수는 {MIN_REVIEWS_PER_PLACE}~{MAX_REVIEWS_PER_PLACE}여야 합니다: "
            f"{review_count}"
        )
    review_schema = create_model(
        f"_GeminiReviewSchema{review_count}",
        reviewIndex=(int, Field(ge=0, le=review_count - 1)),
        reviewSentences=(list[str], Field(min_length=4, max_length=5)),
        claims=(list[_GeminiClaimSchema], ...),
    )
    return create_model(
        f"_GeminiReviewBatchSchema{review_count}",
        reviews=(
            list[review_schema],
            Field(min_length=review_count, max_length=review_count),
        ),
    )


_SYSTEM_INSTRUCTION = load_text("synthetic_review/system_instruction.md")


def _join_review_sentences(sentences: Sequence[str]) -> str:
    normalized: list[str] = []
    for sentence in sentences:
        stripped = sentence.strip()
        if not stripped:
            raise ValueError("reviewSentences에는 빈 문장을 쓸 수 없습니다.")
        if stripped[-1] not in ".!?":
            stripped = f"{stripped}."
        normalized.append(stripped)
    return " ".join(normalized)


class GeminiSyntheticReviewGenerator:
    def __init__(
        self,
        *,
        api_key: str,
        model_name: str,
        client: genai.Client | None = None,
    ) -> None:
        if not api_key.strip() and client is None:
            raise ValueError("LLM_API_KEY가 필요합니다.")
        if not model_name.strip():
            raise ValueError("model_name이 필요합니다.")
        self._client = client or genai.Client(api_key=api_key)
        self._model_name = model_name
        self._usage_metadata: dict[str, int] = {}

    @property
    def usage_metadata(self) -> dict[str, int]:
        """가장 최근 호출의 Gemini 과금 관련 토큰 사용량을 반환한다."""

        return dict(self._usage_metadata)

    async def generate(
        self,
        *,
        facts: Mapping[str, str],
        plans: Sequence[ReviewPlan],
        sentiments: Sequence[SentimentAssessment],
    ) -> SyntheticReviewBatch:
        review_count = len(plans)
        if len(sentiments) != review_count:
            raise ValueError(
                f"리뷰 계획 {review_count}개와 sentiment {len(sentiments)}개가 "
                "맞지 않습니다."
            )
        # 이 장소의 리뷰 수에 맞춘 스키마를 쓴다. 범위 검사는 wire_schema_for가 한다.
        wire_schema = wire_schema_for(review_count)
        response = await self._client.aio.models.generate_content(
            model=self._model_name,
            contents=_prompt_payload(facts, plans, sentiments),
            config=genai_types.GenerateContentConfig(
                system_instruction=_SYSTEM_INSTRUCTION,
                response_mime_type="application/json",
                # 전송 모델은 Google Schema 호환 형태로 단순화한다. 추가 필드와
                # grounding 조건은 수신 후 SyntheticReviewBatch가 엄격하게 검증한다.
                response_schema=wire_schema,
                temperature=0.7,
            ),
        )
        usage = getattr(response, "usage_metadata", None)
        self._usage_metadata = {
            name: value
            for name in (
                "prompt_token_count",
                "candidates_token_count",
                "thoughts_token_count",
                "cached_content_token_count",
                "total_token_count",
            )
            if isinstance((value := getattr(usage, name, None)), int)
        }
        try:
            wire_batch = wire_schema.model_validate_json(response.text or "")
        except ValidationError as exc:
            raise ValueError("Gemini 합성 리뷰가 JSON Schema를 만족하지 않습니다.") from exc
        plans_by_index = {plan.review_index: plan for plan in plans}
        sentiments_by_index = {item.review_index: item for item in sentiments}
        batch = SyntheticReviewBatch.model_validate(
            {
                "reviews": [
                    {
                        "reviewIndex": review.reviewIndex,
                        "personaType": plans_by_index[review.reviewIndex].persona_id,
                        "sentiment": sentiments_by_index[
                            review.reviewIndex
                        ].sentiment.value,
                        "visitContext": plans_by_index[
                            review.reviewIndex
                        ].visit_context,
                        "reviewText": _join_review_sentences(
                            review.reviewSentences
                        ),
                        "claims": [
                            claim.model_dump(mode="json") for claim in review.claims
                        ],
                    }
                    for review in wire_batch.reviews
                ]
            }
        )
        validate_review_batch(
            batch, facts=facts, plans=plans, sentiments=sentiments
        )
        return batch


__all__ = [
    "ALLOWED_OFFICIAL_FIELDS",
    "GENERATOR_VERSION",
    "PROMPT_VERSION",
    "GeminiSyntheticReviewGenerator",
    "build_official_facts",
    "validate_review_batch",
    "wire_schema_for",
]
