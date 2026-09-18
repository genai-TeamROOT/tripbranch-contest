"""실제 /api/chat 다중 턴 되묻기 흐름을 검증하고 CSV로 남긴다.

역할: 단일 턴 /api/interpret 분류 테스트로는 확인할 수 없는 세션 상태
(`pending_clarification`) 소비와 조건 유지 여부를 실제 Agent Runtime 경로에서 검증한다.
입력: 기본값으로 http://localhost:8000의 /api/chat을 호출한다. 서버는 LLM_PROVIDER=real로
      실행되어 있어야 한다.
출력: backend/test_results/multiturn_clarification_e2e_results.csv
호출: backend/에서 `.venv/bin/python -m scripts.test_multiturn_clarification_e2e`

외부 LLM/API를 실제 호출하므로 pytest에 포함하지 않는다.
"""

from __future__ import annotations

import csv
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

BASE_URL = "http://localhost:8000"
RESULTS_CSV = (
    Path(__file__).resolve().parent.parent
    / "test_results"
    / "multiturn_clarification_e2e_results.csv"
)


@dataclass(frozen=True)
class MultiTurnCase:
    number: int
    name: str
    first_input: str
    second_input: str
    expected_first_intent: str
    expected_second_intent: str
    expected_conditions: dict[str, object]
    expects_schedule: bool = False
    # 2턴이 INFO로 이어질 때 유지되어야 하는 question_type. 인텐트만 맞고 질문
    # 종류가 바뀌면(혼잡도 → 개요) 사용자가 물은 답이 아니다.
    expected_second_question_type: str | None = None
    # 2턴 말풍선에 반드시 들어가야 하는 낱말(누적 조건이 문장에 반영됐는지).
    expected_message_contains: tuple[str, ...] = ()
    # 2턴 말풍선에 나오면 안 되는 낱말(말한 조건과 어긋나는 표현).
    forbidden_message_contains: tuple[str, ...] = ()


CASES: tuple[MultiTurnCase, ...] = (
    MultiTurnCase(
        1,
        "위치 보충 뒤 장소 태그 유지",
        "카페 추천해줘",
        "경복궁",
        "RECOMMEND",
        "MODIFY",
        {"search_center": "경복궁", "place_tags": ["카페"]},
    ),
    MultiTurnCase(
        2,
        "위치 보충 뒤 날씨·실내 조건 유지",
        "비를 피할 실내 카페 추천해줘",
        "경복궁",
        "RECOMMEND",
        "MODIFY",
        {
            "search_center": "경복궁",
            "place_tags": ["카페"],
            "weather_intent": "AVOID",
            "environment": "indoor",
        },
    ),
    MultiTurnCase(
        3,
        "위치 되묻기 중 명시적 정보 질문 유지",
        "카페 추천해줘",
        "경복궁 오늘 열어?",
        "RECOMMEND",
        "INFO",
        {"place_tags": ["카페"]},
    ),
    MultiTurnCase(
        4,
        "일정 위치 되묻기 답변은 일정으로 유지",
        "반나절 일정 짜줘",
        "경복궁",
        "SCHEDULE",
        "SCHEDULE",
        {"search_center": "경복궁"},
        expects_schedule=True,
    ),
    # 아래 2건은 되묻기가 아니라 **완결된 턴 뒤에 이어지는 발화**다(2026-08-31
    # 실사용 재현). 되묻기 상태가 없어 기존 규칙이 전부 비껴갔고, 대화 이력
    # 사용법을 프롬프트에 명시한 뒤에야 이어지기 시작했다.
    MultiTurnCase(
        5,
        "완결된 정보 질문 뒤 지명만 던지면 같은 질문을 이어간다",
        "안국역 혼잡도 알려줘",
        "인사동은?",
        "INFO",
        "INFO",
        {},
        expected_second_question_type="concentration",
    ),
    MultiTurnCase(
        6,
        "동행을 말하면 말풍선 문장에도 반영된다",
        "경복궁 근처 카페 추천해줘",
        "친구들이랑 갈거야",
        "RECOMMEND",
        "MODIFY",
        {"companion": "friend"},
        expected_message_contains=("친구",),
        forbidden_message_contains=("혼자",),
    ),
)


@dataclass
class CaseResult:
    case: MultiTurnCase
    first: dict[str, Any] | None
    second: dict[str, Any] | None
    first_elapsed_ms: float | None
    second_elapsed_ms: float | None
    matched: bool
    checks: list[str]
    error: str = ""


def _post(client: httpx.Client, payload: dict[str, object]) -> tuple[dict[str, Any], float]:
    started_at = time.perf_counter()
    response = client.post(f"{BASE_URL}/api/chat", json=payload)
    elapsed_ms = (time.perf_counter() - started_at) * 1000
    if response.is_error:
        try:
            detail = response.json()
        except ValueError:
            detail = response.text[:500]
        raise RuntimeError(f"HTTP {response.status_code}: {detail}")
    body = response.json()
    if not isinstance(body, dict):
        raise ValueError("/api/chat 응답이 JSON 객체가 아닙니다.")
    return body, elapsed_ms


def _intent(body: dict[str, Any]) -> str:
    output = body.get("llm_output")
    return str(output.get("intent", "")) if isinstance(output, dict) else ""


def _status(body: dict[str, Any]) -> str:
    output = body.get("llm_output")
    return str(output.get("status", "")) if isinstance(output, dict) else ""


def _question_type(body: dict[str, Any]) -> str | None:
    output = body.get("llm_output")
    if not isinstance(output, dict):
        return None
    info = output.get("info")
    return str(info.get("question_type")) if isinstance(info, dict) else None


def _conditions(body: dict[str, Any]) -> dict[str, Any]:
    state = body.get("state")
    if not isinstance(state, dict):
        return {}
    conditions = state.get("user_conditions")
    return conditions if isinstance(conditions, dict) else {}


def _recommendation_count(body: dict[str, Any]) -> int:
    recommendations = body.get("recommendations")
    if not isinstance(recommendations, dict):
        return 0
    items = recommendations.get("recommendations")
    return len(items) if isinstance(items, list) else 0


def _check(case: MultiTurnCase, first: dict[str, Any], second: dict[str, Any]) -> list[str]:
    checks = [
        f"1턴 Intent={_intent(first)} (기대 {case.expected_first_intent})",
        f"2턴 Intent={_intent(second)} (기대 {case.expected_second_intent})",
    ]
    if _intent(first) != case.expected_first_intent:
        checks.append("FAIL: 1턴 Intent 불일치")
    if _intent(second) != case.expected_second_intent:
        checks.append("FAIL: 2턴 Intent 불일치")

    conditions = _conditions(second)
    for field, expected in case.expected_conditions.items():
        actual = conditions.get(field)
        if actual == expected:
            checks.append(f"{field} 유지/반영")
        else:
            checks.append(f"FAIL: {field}={actual!r} (기대 {expected!r})")

    if case.expects_schedule:
        if second.get("schedule") is not None:
            checks.append("일정 결과 반환")
        else:
            checks.append("FAIL: schedule 결과 없음")

    if case.expected_second_question_type is not None:
        actual_type = _question_type(second)
        if actual_type == case.expected_second_question_type:
            checks.append(f"question_type={actual_type} 유지")
        else:
            checks.append(
                f"FAIL: question_type={actual_type!r} "
                f"(기대 {case.expected_second_question_type!r})"
            )

    message = str(second.get("message") or "")
    for word in case.expected_message_contains:
        if word in message:
            checks.append(f"말풍선에 '{word}' 반영")
        else:
            checks.append(f"FAIL: 말풍선에 '{word}' 없음")
    for word in case.forbidden_message_contains:
        if word in message:
            checks.append(f"FAIL: 말풍선에 '{word}' 등장(말한 조건과 어긋남)")
        else:
            checks.append(f"말풍선에 '{word}' 없음")

    return checks


def _is_matched(checks: list[str]) -> bool:
    return not any(check.startswith("FAIL:") for check in checks)


def _check_server(client: httpx.Client) -> None:
    try:
        response = client.get(f"{BASE_URL}/api/health", timeout=5.0)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        print(f"서버({BASE_URL})에 연결할 수 없습니다: {exc}")
        print("backend에서 npm run dev 또는 uvicorn을 먼저 실행해주세요.")
        sys.exit(1)


def run() -> list[CaseResult]:
    results: list[CaseResult] = []
    with httpx.Client(timeout=90.0) as client:
        _check_server(client)
        for case in CASES:
            try:
                first, first_elapsed_ms = _post(client, {"user_input": case.first_input})
                session_id = (first.get("state") or {}).get("session_id")
                if not isinstance(session_id, str) or not session_id:
                    raise ValueError("1턴 응답에 session_id가 없습니다.")
                second, second_elapsed_ms = _post(
                    client,
                    {"user_input": case.second_input, "session_id": session_id},
                )
                checks = _check(case, first, second)
                result = CaseResult(
                    case=case,
                    first=first,
                    second=second,
                    first_elapsed_ms=first_elapsed_ms,
                    second_elapsed_ms=second_elapsed_ms,
                    matched=_is_matched(checks),
                    checks=checks,
                )
            except (httpx.HTTPError, RuntimeError, ValueError) as exc:
                result = CaseResult(
                    case=case,
                    first=None,
                    second=None,
                    first_elapsed_ms=None,
                    second_elapsed_ms=None,
                    matched=False,
                    checks=["FAIL: 요청 실행 오류"],
                    error=str(exc),
                )

            results.append(result)
            mark = "OK" if result.matched else "FAIL"
            print(f"[{case.number}/{len(CASES)}] {mark:4} {case.name}")
            for check in result.checks:
                print(f"  - {check}")
            if result.error:
                print(f"  - 오류: {result.error}")
    return results


def write_results_csv(results: list[CaseResult]) -> None:
    RESULTS_CSV.parent.mkdir(parents=True, exist_ok=True)
    with RESULTS_CSV.open("w", newline="", encoding="utf-8-sig") as fp:
        writer = csv.writer(fp)
        writer.writerow(
            [
                "번호",
                "시나리오",
                "1턴_입력",
                "2턴_입력",
                "1턴_기대_intent",
                "1턴_실제_intent",
                "1턴_status",
                "1턴_소요_ms",
                "2턴_기대_intent",
                "2턴_실제_intent",
                "2턴_status",
                "2턴_소요_ms",
                "2턴_최종조건",
                "추천_건수",
                "일정_반환",
                "검증결과",
                "확인사항",
                "오류",
            ]
        )
        for result in results:
            first = result.first or {}
            second = result.second or {}
            writer.writerow(
                [
                    result.case.number,
                    result.case.name,
                    result.case.first_input,
                    result.case.second_input,
                    result.case.expected_first_intent,
                    _intent(first),
                    _status(first),
                    f"{result.first_elapsed_ms:.1f}" if result.first_elapsed_ms else "",
                    result.case.expected_second_intent,
                    _intent(second),
                    _status(second),
                    f"{result.second_elapsed_ms:.1f}" if result.second_elapsed_ms else "",
                    _conditions(second),
                    _recommendation_count(second),
                    "예" if second.get("schedule") is not None else "아니오",
                    "통과" if result.matched else "실패",
                    " / ".join(result.checks),
                    result.error,
                ]
            )


def main() -> None:
    results = run()
    write_results_csv(results)
    passed = sum(result.matched for result in results)
    print(f"\n결과: {passed}/{len(results)} 통과")
    print(f"결과 CSV: {RESULTS_CSV}")


if __name__ == "__main__":
    main()
