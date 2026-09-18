"""혼잡도(concentration) 관련 발화의 Intent/question_type 판별을 실제 Gemini로
검증하는 배치 테스트.

역할: RECOMMEND의 concentration_intent(AVOID/SEEK/IGNORE), INFO의
question_type=concentration + visit_time 판별이 다양한 표현·장소에서 정확한지
확인한다. scripts/test_intent_classification.py와 같은 결(하드코딩 CASES → 실제
LLMProvider 호출 → CSV 저장)이지만, HTTP 라우터 대신
app.services.interpret.orchestrator.interpret_user_input()을 직접 호출한다 —
서버를 띄우지 않고도 동일한 2단계(Intent 분류 → 조건 추출) 흐름을 그대로 탄다.
입력: 없음 (하드코딩된 CASES 목록). .env의 LLM_PROVIDER=real이어야 한다.
출력: backend/test_results/concentration_classification_results.csv,
      backend/test_results/concentration_classification_summary.csv
호출 시점: `python -m scripts.test_concentration_classification`로 수동 실행한다
(1회성 검증 도구, pytest 스위트에는 포함하지 않는다 — 실제 API 호출 비용 때문).
"""

from __future__ import annotations

import asyncio
import csv
import sys
import time
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.schemas import InterpretRequest
from app.services.interpret.orchestrator import interpret_user_input

REQUEST_INTERVAL_SECONDS = 2.5
RESULTS_DIR = Path(__file__).resolve().parent.parent / "test_results"
RESULTS_CSV = RESULTS_DIR / "concentration_classification_results.csv"
SUMMARY_CSV = RESULTS_DIR / "concentration_classification_summary.csv"


@dataclass(frozen=True)
class TestCase:
    number: int
    user_input: str
    expected_intent: str
    expected_question_type: str | None = None  # INFO일 때만, concentration 여부 확인용
    note: str = ""


CASES: list[TestCase] = [
    # --- RECOMMEND: concentration_intent (5) ---
    TestCase(1, "핫한 관광지 어디야", "RECOMMEND", note="SEEK 기대"),
    TestCase(2, "인기 많은 공원 추천해줘", "RECOMMEND", note="SEEK 기대"),
    TestCase(3, "조용한 곳 가고 싶어", "RECOMMEND", note="AVOID 기대"),
    TestCase(4, "사람 없는 한적한 데 추천해줘", "RECOMMEND", note="AVOID 기대"),
    TestCase(
        5,
        "경복궁 근처 카페 추천해줘",
        "RECOMMEND",
        note="대조군: IGNORE 기대(혼잡도 무언급)",
    ),
    # --- INFO: question_type=concentration, 사용자 제시 예시 4개 포함 (11) ---
    TestCase(
        6,
        "추석때 을왕리 사람 많을까?",
        "INFO",
        "concentration",
        note="사용자 예시 — 연휴+지명, visit_time 특수 날짜 파싱",
    ),
    TestCase(
        7,
        "이번 주말 공항에 사람 많을까?",
        "INFO",
        "concentration",
        note="사용자 예시 — 장소명이 모호(공항), visit_time=이번 주말",
    ),
    TestCase(
        8,
        "오늘 용리단길 식당들 대기 많을까?",
        "INFO",
        "concentration",
        note="사용자 예시 — 식당(비관광지) 대기, 근접치 fallback 대상",
    ),
    TestCase(
        9,
        "지금 올림픽대공원 사람 많아?",
        "INFO",
        "concentration",
        note="사용자 예시 — '지금'(실시간) 표현이 그래도 concentration으로 가는지",
    ),
    TestCase(10, "해운대 해수욕장 붐빌까?", "INFO", "concentration", note="종로구 밖 관광지"),
    TestCase(11, "이번 주 남산타워 혼잡해?", "INFO", "concentration", note="visit_time=이번 주"),
    TestCase(12, "명동 거리 사람 많이 몰릴까?", "INFO", "concentration", note="거리/지역명"),
    TestCase(
        13, "다음주 화요일 롯데월드 사람 많아?", "INFO", "concentration", note="특정 요일 지정"
    ),
    TestCase(
        14,
        "경복궁 붐빌까?",
        "INFO",
        "concentration",
        note="종로구 내 관광지, visit_time=오늘",
    ),
    TestCase(
        15,
        "경복궁 오늘 열어?",
        "INFO",
        "operating_hours",
        note="대조군: 혼잡도 아님 — concentration으로 오분류되면 안 됨",
    ),
    TestCase(
        16,
        "경복궁 역사 알려줘",
        "GENERAL",
        note="대조군: 배경지식 — INFO/concentration 아님",
    ),
]


@dataclass
class CaseResult:
    case: TestCase
    actual_intent: str
    actual_question_type: str = ""
    concentration_intent: str = ""
    visit_time: str = ""
    place_name: str = ""
    status: str = ""
    intent_matched: bool = False
    qtype_matched: bool | None = None  # None이면 해당 없음(기대_question_type 없음)
    error: str = ""


def _summarize(llm_output) -> tuple[str, str, str, str, str]:
    """(question_type, concentration_intent, visit_time, place_name, status_extra) 반환."""
    if llm_output.recommend is not None:
        c = llm_output.recommend.conditions
        return ("", str(c.concentration_intent or ""), "", "", "")
    if llm_output.info is not None:
        info = llm_output.info
        return (
            str(info.question_type),
            "",
            str(info.visit_time or ""),
            str(info.place_name or ""),
            "",
        )
    return ("", "", "", "", "")


async def run() -> list[CaseResult]:
    results: list[CaseResult] = []
    for case in CASES:
        try:
            llm_output = await interpret_user_input(
                InterpretRequest(
                    user_input=case.user_input,
                    has_previous_recommendation=False,
                    shown_place_count=0,
                )
            )
            actual_intent = str(llm_output.intent)
            question_type, concentration_intent, visit_time, place_name, _ = _summarize(llm_output)
            intent_matched = actual_intent == case.expected_intent
            qtype_matched = (
                None
                if case.expected_question_type is None
                else question_type == case.expected_question_type
            )
            result = CaseResult(
                case=case,
                actual_intent=actual_intent,
                actual_question_type=question_type,
                concentration_intent=concentration_intent,
                visit_time=visit_time,
                place_name=place_name,
                status=str(llm_output.status),
                intent_matched=intent_matched,
                qtype_matched=qtype_matched,
            )
        except Exception as exc:  # noqa: BLE001 — 배치 테스트는 한 건 실패해도 계속 진행
            result = CaseResult(
                case=case,
                actual_intent="ERROR",
                intent_matched=False,
                error=str(exc),
            )

        results.append(result)

        mark = "OK" if result.intent_matched and result.qtype_matched is not False else "FAIL"
        print(
            f"[{case.number:>2}/{len(CASES)}] {mark:4} "
            f"기대={case.expected_intent:10} 실제={result.actual_intent:10} "
            f"qtype={result.actual_question_type or '-':16} "
            f"'{case.user_input[:35]}'"
        )
        if result.error:
            print(f"        오류: {result.error}")

        time.sleep(REQUEST_INTERVAL_SECONDS)

    return results


def write_results_csv(results: list[CaseResult]) -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    with RESULTS_CSV.open("w", newline="", encoding="utf-8-sig") as fp:
        writer = csv.writer(fp)
        writer.writerow(
            [
                "번호",
                "입력문장",
                "기대_intent",
                "실제_intent",
                "intent_일치여부",
                "기대_question_type",
                "실제_question_type",
                "qtype_일치여부",
                "concentration_intent",
                "visit_time",
                "place_name",
                "status",
                "비고",
                "오류",
            ]
        )
        for result in results:
            case = result.case
            writer.writerow(
                [
                    case.number,
                    case.user_input,
                    case.expected_intent,
                    result.actual_intent,
                    "일치" if result.intent_matched else "불일치",
                    case.expected_question_type or "",
                    result.actual_question_type,
                    (
                        ""
                        if result.qtype_matched is None
                        else ("일치" if result.qtype_matched else "불일치")
                    ),
                    result.concentration_intent,
                    result.visit_time,
                    result.place_name,
                    result.status,
                    case.note,
                    result.error,
                ]
            )


def write_summary_csv(results: list[CaseResult]) -> None:
    total = len(results)
    intent_correct = sum(1 for r in results if r.intent_matched)
    qtype_cases = [r for r in results if r.qtype_matched is not None]
    qtype_correct = sum(1 for r in qtype_cases if r.qtype_matched)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    with SUMMARY_CSV.open("w", newline="", encoding="utf-8-sig") as fp:
        writer = csv.writer(fp)
        writer.writerow(["구분", "전체", "정답", "정확도"])
        writer.writerow(
            [
                "Intent 판별",
                total,
                intent_correct,
                f"{intent_correct / total:.1%}" if total else "-",
            ]
        )
        writer.writerow(
            [
                "question_type=concentration 판별",
                len(qtype_cases),
                qtype_correct,
                f"{qtype_correct / len(qtype_cases):.1%}" if qtype_cases else "-",
            ]
        )

    print()
    print(f"Intent 판별 정확도: {intent_correct}/{total} ({intent_correct / total:.1%})")
    if qtype_cases:
        print(
            f"question_type 판별 정확도: {qtype_correct}/{len(qtype_cases)} "
            f"({qtype_correct / len(qtype_cases):.1%})"
        )

    failed = [r for r in results if not r.intent_matched or r.qtype_matched is False]
    if failed:
        print()
        print(f"불일치/오류 {len(failed)}건:")
        for r in failed:
            print(
                f"  #{r.case.number} '{r.case.user_input}' "
                f"기대intent={r.case.expected_intent} 실제intent={r.actual_intent} "
                f"기대qtype={r.case.expected_question_type} 실제qtype={r.actual_question_type} "
                f"{'(' + r.error + ')' if r.error else ''}"
            )


def main() -> None:
    results = asyncio.run(run())
    write_results_csv(results)
    write_summary_csv(results)
    print()
    print(f"결과 CSV: {RESULTS_CSV}")
    print(f"요약 CSV: {SUMMARY_CSV}")


if __name__ == "__main__":
    main()
