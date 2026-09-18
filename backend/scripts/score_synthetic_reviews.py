"""Colab에서 받아온 생성 결과를 기존 검증기로 채점한다.

핵심은 `validate_review_batch()`를 그대로 쓰는 것이다. 이 함수는 Gemini에 결합돼 있지
않아서 어떤 모델이 만든 결과든 같은 잣대로 잴 수 있다. 통과율뿐 아니라 **실패 사유별
분포**를 보는 게 목적이다 — sourceValue 불일치인지, 문장 수인지, 금지 표현인지에 따라
다음에 할 일(모델 교체 / 프롬프트 수정 / 재시도 추가)이 갈린다.

실행:
    cd /Users/jinhyoungkim/Dev/TripBranch-synthetic-reviews/backend
    python <이 파일 경로> --sample <경로>/places_sample.json --results <경로>/reviews.jsonl
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path

from pydantic import ValidationError

from app.synthetic_reviews import (
    PlacePersonaInput,
    SyntheticReviewBatch,
    assess_sentiment,
    generate_personas,
    generate_review_plans,
    validate_review_batch,
)
from app.synthetic_reviews.review_generator import (
    _join_review_sentences,
    wire_schema_for,
)

# 실패 메시지를 사람이 읽을 범주로 접는다. validate_review_batch가 던지는 문구 기준이다.
_FAILURE_CATEGORIES: tuple[tuple[str, str], ...] = (
    # guided_json이 걸려 있으면 형식 위반은 여기까지 오지 않는다. 이 범주가 잡힌다는 건
    # 제약 디코딩이 꺼져 있거나 모델이 스키마를 벗어났다는 뜻이다.
    ("reviewSentences", "문장 수 위반 (스키마 단계)"),
    ("스키마 위반", "JSON 스키마 위반"),
    ("조립 실패", "결과 조립 실패"),
    ("공식 출처 값 불일치", "sourceValue 원문 불일치"),
    ("리뷰 계획에 허용되지 않은 출처 필드", "허용되지 않은 sourceField"),
    ("공식 입력에 없는 출처 필드", "존재하지 않는 sourceField"),
    ("reviewText는 4~5문장", "문장 수 위반"),
    ("공식 입력에 없는 수치", "없는 수치 생성"),
    ("내부 필드명 또는 코드가 노출", "내부 코드 노출"),
    ("동행자 유형에서 근거 없이", "동행자 추론 금지 위반"),
    ("공식 사실에서 근거 없는 평가", "평가 확대 금지 위반"),
    ("실제 방문을 가장하는 표현", "방문 가장"),
    ("합성 시나리오 claim에는 구체적인 수치", "시나리오 claim에 수치"),
    ("공식 근거 없는 객관적 사실 claim", "시나리오 claim에 사실 주장"),
    ("공식 부정 정보와 모순", "공식 정보와 모순"),
    ("공식 긍정 정보와 모순", "공식 정보와 모순"),
    ("personaType 불일치", "메타데이터 불일치"),
    ("visitContext 불일치", "메타데이터 불일치"),
    ("sentiment 불일치", "메타데이터 불일치"),
    ("인덱스는 중복 없이", "리뷰 인덱스 오류"),
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Colab 생성 결과를 validate_review_batch로 채점한다"
    )
    parser.add_argument("--sample", required=True, help="places_sample.json 경로")
    parser.add_argument("--results", required=True, help="reviews.jsonl 경로")
    parser.add_argument(
        "--show-text", action="store_true", help="통과한 리뷰 본문도 함께 출력"
    )
    return parser


def categorize(message: str) -> str:
    for needle, label in _FAILURE_CATEGORIES:
        if needle in message:
            return label
    return "기타"


def rebuild_plan_context(facts: Mapping[str, str]):
    """표본 파일의 facts만으로 페르소나·계획·sentiment를 결정적으로 다시 만든다.

    내보낼 때와 같은 함수를 같은 순서로 부르므로 결과가 같다. Colab에 보낸 payload와
    같은 맥락 위에서 채점한다는 뜻이다.
    """
    place = PlacePersonaInput(
        **{
            field: facts.get(field)
            for field in PlacePersonaInput.__dataclass_fields__
        }
    )
    personas = generate_personas(place)
    plans = generate_review_plans(personas)
    sentiments = tuple(assess_sentiment(place, plan) for plan in plans)
    return plans, sentiments


def _strip_reasoning(raw: str) -> str:
    """일부 모델이 JSON 앞뒤에 붙이는 추론 블록과 코드펜스를 걷어낸다."""
    text = raw.strip()
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", text, flags=re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        text = text[start : end + 1]
    return text


def score_one(entry: Mapping[str, object], raw: str) -> tuple[bool, str]:
    facts = dict(entry["facts"])  # type: ignore[arg-type]
    plans, sentiments = rebuild_plan_context(facts)

    try:
        wire = wire_schema_for(len(plans)).model_validate_json(_strip_reasoning(raw))
    except ValidationError as exc:
        first = exc.errors()[0] if exc.errors() else {}
        return False, f"스키마 위반: {first.get('loc')} {first.get('msg')}"

    plans_by_index = {plan.review_index: plan for plan in plans}
    sentiments_by_index = {item.review_index: item for item in sentiments}
    try:
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
                        "reviewText": _join_review_sentences(review.reviewSentences),
                        "claims": [
                            claim.model_dump(mode="json") for claim in review.claims
                        ],
                    }
                    for review in wire.reviews
                ]
            }
        )
    except (ValidationError, KeyError, ValueError) as exc:
        return False, f"조립 실패: {exc}"

    try:
        validate_review_batch(
            batch, facts=facts, plans=plans, sentiments=sentiments
        )
    except ValueError as exc:
        return False, str(exc)
    return True, ""


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    document = json.loads(Path(args.sample).read_text(encoding="utf-8"))
    entries = {str(item["contentId"]): item for item in document["places"]}

    results: list[dict[str, object]] = []
    for line in Path(args.results).read_text(encoding="utf-8").splitlines():
        if line.strip():
            results.append(json.loads(line))

    by_model: dict[str, list[tuple[str, bool, str, Mapping[str, object]]]] = {}
    for result in results:
        content_id = str(result["contentId"])
        model = str(result.get("model", "(모델명 없음)"))
        entry = entries.get(content_id)
        if entry is None:
            by_model.setdefault(model, []).append(
                (content_id, False, "표본 파일에 없는 contentId", result)
            )
            continue
        passed, reason = score_one(entry, str(result.get("raw", "")))
        by_model.setdefault(model, []).append((content_id, passed, reason, result))

    for model, rows in sorted(by_model.items()):
        total = len(rows)
        passed = sum(1 for _, ok, _, _ in rows if ok)
        print("=" * 74)
        print(f"모델: {model}")
        print(f"통과율: {passed}/{total} ({100 * passed / total:.0f}%)")

        elapsed = [
            float(r["elapsedSeconds"])
            for _, _, _, r in rows
            if isinstance(r.get("elapsedSeconds"), (int, float))
        ]
        tokens = [
            int(r["outputTokens"])
            for _, _, _, r in rows
            if isinstance(r.get("outputTokens"), int)
        ]
        if elapsed:
            print(
                f"장소당 소요: 평균 {sum(elapsed) / len(elapsed):.1f}초 "
                f"(합계 {sum(elapsed):.0f}초)"
            )
        if tokens:
            print(f"출력 토큰: 평균 {sum(tokens) // len(tokens):,} (합계 {sum(tokens):,})")
        if elapsed and tokens:
            throughput = sum(tokens) / sum(elapsed)
            print(f"처리량: 약 {throughput:.0f} tok/s")
            # 종로구 841곳을 같은 속도로 돌리면 얼마나 걸리는지.
            hours = 841 * (sum(tokens) / len(tokens)) / throughput / 3600
            print(f"→ 이 속도면 종로구 841곳에 약 {hours:.1f}시간")

        failures = Counter(
            categorize(reason) for _, ok, reason, _ in rows if not ok
        )
        if failures:
            print("\n실패 사유:")
            for label, count in failures.most_common():
                print(f"  {count:>2}건  {label}")

        print("\n장소별:")
        for content_id, ok, reason, _ in rows:
            entry = entries.get(content_id, {})
            title = str(entry.get("title", "?"))
            richness = entry.get("evidenceRichness", "?")
            mark = "통과" if ok else "실패"
            print(f"  [{mark}] {content_id:<9} 근거{richness:<3} {title}")
            if not ok:
                print(f"         └ {reason[:150]}")
        print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
