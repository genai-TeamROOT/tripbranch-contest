"""Agent Runtime(run_agent())이 실제로 B/C/D를 전부 통과하는지 눈으로 확인하는 스크립트.

역할: RECOMMEND(날씨 무관/날씨 언급 2종) → MODIFY(REJECT_ALL) → MODIFY(CHANGE_CONDITION)
→ COMPARE → INFO → GENERAL → OUT_OF_SCOPE까지 6개 Intent를 전부 실제 Gemini + 실제
C(Context) + 실제 D(Recommendation)로 실행하고, 각 단계에서 Intent·추출 조건·C 상태·
실제 추천 결과(장소·거리·점수·근거·경고)·걸린 시간을 보기 좋게 출력한다.
입력: 없음(하드코딩된 시나리오). 실제 Gemini/기상청/TourAPI/네이버 지도 API를 호출하므로
.env에 LLM_PROVIDER=real(또는 PROVIDER_MODE=real)과 각 API 키가 설정돼 있어야 한다.
출력: 표준 출력(터미널)에 시나리오별 결과 + 마지막에 전체 요약표.
호출 시점: `python -m scripts.try_agent_runtime`로 수동 실행한다(1회성 확인 도구,
pytest 스위트에는 포함하지 않는다 — 실제 API 호출 비용과 속도 때문).
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

from app.schemas import AgentRequest, AgentResponse
from app.services.runtime.agent_runtime import run_agent
from app.services.runtime.response_composer import compose_recommendation_message

DEVICE_LOCATION = "37.5788,126.9770"


@dataclass
class ScenarioResult:
    label: str
    user_input: str
    elapsed_seconds: float
    response: AgentResponse | None
    error: str | None = None
    notes: list[str] = field(default_factory=list)


def _print_header(title: str) -> None:
    print()
    print("=" * 72)
    print(title)
    print("=" * 72)


def _print_recommendations(response: AgentResponse) -> None:
    print(f"  [chat message] {response.message!r}")

    if response.recommendations is None:
        print("  recommendations: None (Tool/Recommendation 단계 스킵됨)")
        return

    for bucket_name, items in (
        ("recommendations", response.recommendations.recommendations),
        ("unverified_recommendations", response.recommendations.unverified_recommendations),
    ):
        print(f"  {bucket_name} ({len(items)}건):")
        for item in items:
            print(
                f"    - {item.place_id} | {item.name} | distance={item.distance_km}km"
                f" | score={item.score}"
            )
            if item.explanations:
                print(f"        explanations: {item.explanations}")
            if item.warnings:
                print(f"        warnings: {item.warnings}")
            composed = compose_recommendation_message(item)
            print(f"        [compose_recommendation_message] {composed!r}")

    _print_final_answer(response)


def _print_final_answer(response: AgentResponse) -> None:
    """카드별 compose_recommendation_message() 문장을 번호 목록으로 보여준다."""
    if response.recommendations is None:
        return
    items = [
        *response.recommendations.recommendations,
        *response.recommendations.unverified_recommendations,
    ]
    if not items:
        return

    print("  [카드 목록 — 각 장소의 compose_recommendation_message() 결과]")
    for index, item in enumerate(items, start=1):
        message = compose_recommendation_message(item)
        print(f"    {index}. {item.name}: {message}")


async def _run_scenario(
    label: str,
    user_input: str,
    session_id: str | None,
) -> ScenarioResult:
    _print_header(f"[{label}] \"{user_input}\"")
    request = AgentRequest(
        user_input=user_input,
        session_id=session_id,
        device_location=DEVICE_LOCATION,
    )
    started = time.perf_counter()
    try:
        response = await run_agent(request)
    except Exception as exc:  # noqa: BLE001 — 스크립트 특성상 실패도 결과로 보여줘야 한다
        elapsed = time.perf_counter() - started
        print(f"  실패: {type(exc).__name__}: {exc}")
        return ScenarioResult(
            label=label, user_input=user_input, elapsed_seconds=elapsed, response=None,
            error=f"{type(exc).__name__}: {exc}",
        )

    elapsed = time.perf_counter() - started
    print(f"  intent={response.llm_output.intent} status={response.llm_output.status}")
    print(f"  session_id={response.state.session_id} run_id={response.state.run_id}")
    print(f"  user_conditions={response.state.user_conditions.model_dump(exclude_none=True)}")
    print(f"  excluded_place_ids={response.state.excluded_place_ids}")
    _print_recommendations(response)
    print(f"  elapsed={elapsed:.2f}s")

    return ScenarioResult(
        label=label, user_input=user_input, elapsed_seconds=elapsed, response=response
    )


async def main() -> None:
    results: list[ScenarioResult] = []

    # 1) RECOMMEND — 최초 턴, 실제 Gemini + 실제 C + 실제 D 전부 연동
    r1 = await _run_scenario("1. RECOMMEND", "경복궁 근처 카페 추천해줘", session_id=None)
    results.append(r1)
    session_id = r1.response.state.session_id if r1.response else None

    if session_id is None:
        print("\n1번 시나리오가 실패해서 이후 시나리오를 진행할 세션이 없습니다. 종료합니다.")
        _print_summary(results)
        return

    first_shown = (
        {item.place_id for item in r1.response.recommendations.recommendations}
        | {item.place_id for item in r1.response.recommendations.unverified_recommendations}
        if r1.response and r1.response.recommendations
        else set()
    )

    # 2) MODIFY REJECT_ALL — 1번에서 나온 장소가 실제로 제외되는지 확인
    r2 = await _run_scenario("2. MODIFY(REJECT_ALL)", "다른 곳 보여줘", session_id=session_id)
    results.append(r2)
    if r2.response and r2.response.recommendations:
        second_shown = {
            item.place_id
            for item in [
                *r2.response.recommendations.recommendations,
                *r2.response.recommendations.unverified_recommendations,
            ]
        }
        overlap = first_shown & second_shown
        note = (
            f"1번과 겹치는 장소 없음(정상 제외됨): {sorted(first_shown)} 제외됨"
            if not overlap
            else f"경고 — 1번과 겹치는 장소 있음: {sorted(overlap)}"
        )
        print(f"  [확인] {note}")
        r2.notes.append(note)

    # 3) MODIFY CHANGE_CONDITION — 조건 유지 + 예산만 반영되는지 확인
    r3 = await _run_scenario("3. MODIFY(CHANGE_CONDITION)", "무료인 곳으로", session_id=session_id)
    results.append(r3)
    if r3.response:
        conditions = r3.response.state.user_conditions
        note = (
            f"search_center={conditions.search_center!r}(유지), budget={conditions.budget!r}(반영)"
        )
        print(f"  [확인] {note}")
        r3.notes.append(note)

    # 4) INFO — Tool/Recommendation 단계 없이 바로 응답
    r4 = await _run_scenario("4. INFO", "경복궁 오늘 열어?", session_id=session_id)
    results.append(r4)
    if r4.response:
        note = (
            "recommendations=None(정상 — Tool/Recommendation 스킵)"
            if r4.response.recommendations is None
            else "경고 — recommendations가 채워짐(INFO인데 스킵 안 됨)"
        )
        print(f"  [확인] {note}")
        r4.notes.append(note)

    # 5) RECOMMEND(날씨 언급, 위치 없음) — 사용자가 준 문장 그대로. current_location/
    #    search_center가 둘 다 비어서 C 단계 needs_clarification이 나올 것으로 예상됨
    #    (장소명이 없어서 "근처"만으로는 search_center가 안 채워진다).
    r5 = await _run_scenario(
        "5. RECOMMEND(날씨, 위치없음)",
        "비오는데 근처에 10분 이내로 갈만한 카페 추천해줘",
        session_id=None,
    )
    results.append(r5)
    if r5.response:
        note = (
            f"recommendations={'None' if r5.response.recommendations is None else '있음'}"
            f", search_center={r5.response.state.user_conditions.search_center!r}"
            f", weather={r5.response.state.user_conditions.weather!r}"
        )
        print(f"  [확인] {note}")
        r5.notes.append(note)

    # 6) RECOMMEND(날씨 언급 + 위치 명시) — 날씨 API가 실제로 호출·반영되는지 확인.
    r6 = await _run_scenario(
        "6. RECOMMEND(날씨+위치)",
        "경복궁 근처에서 비 오는데 10분 이내로 갈만한 카페 추천해줘",
        session_id=None,
    )
    results.append(r6)
    compare_session_id: str | None = None
    if r6.response:
        compare_session_id = r6.response.state.session_id
        note = _weather_usage_note(r6.response)
        print(f"  [확인] {note}")
        r6.notes.append(note)

    # 7) COMPARE — 6번에서 2건 이상 노출됐어야 성립(shown_place_count>=2 전제).
    if compare_session_id is not None:
        r7 = await _run_scenario(
            "7. COMPARE", "어디가 더 가까워?", session_id=compare_session_id
        )
        results.append(r7)

    # 8) GENERAL — API로 조회 불가능한 배경지식 질문, Tool/Recommendation 없음.
    r8 = await _run_scenario("8. GENERAL", "서울 여행 팁 좀 알려줘", session_id=None)
    results.append(r8)
    if r8.response:
        note = (
            "recommendations=None(정상 — Tool/Recommendation 스킵)"
            if r8.response.recommendations is None
            else "경고 — recommendations가 채워짐(GENERAL인데 스킵 안 됨)"
        )
        print(f"  [확인] {note}")
        r8.notes.append(note)

    # 9) OUT_OF_SCOPE — 서비스 범위 밖 요청, 즉시 차단 대상.
    r9 = await _run_scenario("9. OUT_OF_SCOPE", "주식 추천해줘", session_id=None)
    results.append(r9)
    if r9.response:
        note = (
            "recommendations=None(정상 — Tool/Recommendation 스킵)"
            if r9.response.recommendations is None
            else "경고 — recommendations가 채워짐(OUT_OF_SCOPE인데 스킵 안 됨)"
        )
        print(f"  [확인] {note}")
        r9.notes.append(note)

    _print_summary(results)


_WEATHER_MISSING_WARNING = "현재 날씨 정보를 확인하지 못해 이 조건은 반영되지 않았어요."


def _weather_usage_note(response: AgentResponse) -> str:
    """추천 결과에 날씨 Feature가 실제로 반영됐는지 확인한다.

    C가 weather_intent!=IGNORE일 때 날씨 Tool을 실제로 호출했다면, 최소 한 후보의
    feature_scores["weather"]가 채워지고(None이 아님) 날씨 결측 warning이 없어야 한다.
    """

    if response.recommendations is None:
        return (
            "recommendations=None — 날씨 반영 여부 확인 불가"
            "(C가 needs_clarification 등으로 종료됐을 가능성)"
        )

    items = [
        *response.recommendations.recommendations,
        *response.recommendations.unverified_recommendations,
    ]
    if not items:
        return "추천 결과 없음 — 날씨 반영 여부 확인 불가"

    weather_used = any(item.feature_scores.get("weather") is not None for item in items)
    missing_warning_present = any(_WEATHER_MISSING_WARNING in item.warnings for item in items)
    if weather_used and not missing_warning_present:
        return "날씨 Feature 실제 반영됨(feature_scores['weather'] 존재, 결측 warning 없음)"
    return (
        f"날씨 Feature 미반영 — weather_used={weather_used}, "
        f"결측 warning 존재={missing_warning_present}"
    )


def _print_summary(results: list[ScenarioResult]) -> None:
    _print_header("요약")
    header = f"{'시나리오':<28} {'intent':<10} {'elapsed':>8}  이슈/확인"
    print(header)
    print("-" * len(header))
    for result in results:
        if result.error is not None:
            print(
                f"{result.label:<28} {'ERROR':<10} {result.elapsed_seconds:>7.2f}s"
                f"  {result.error}"
            )
            continue
        intent = result.response.llm_output.intent.value if result.response else "-"
        note = "; ".join(result.notes) if result.notes else "-"
        print(f"{result.label:<28} {intent:<10} {result.elapsed_seconds:>7.2f}s  {note}")


if __name__ == "__main__":
    asyncio.run(main())
