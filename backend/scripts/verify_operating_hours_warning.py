"""SCHEDULE 폐점 스탑 구조적 경고 동작 확인 — 실제 Gemini 없이 결정적으로 재현.

역할: 실사용(`/dev-chat`)이나 실제 Gemini 호출로는 "폐점 스탑이 안 나오는" 게
① LLM이 프롬프트 힌트(운영시간)를 보고 알아서 잘 피한 것인지, ② 애초에
planner.py의 구조적 후처리(`_finalize_items`)가 안 도는 것인지 구분하기 어렵다
— 둘 다 결과적으로 "경고 없음"으로 보이기 때문이다.

이 스크립트는 `app.schedule.planner.plan_schedule()`을 실제 프로덕션 코드
그대로 호출하되, LLM 자리에는 항상 정해진 응답만 돌려주는 최소 stub
(`_FixedLLM`)을 넣는다 — 즉 "LLM이 마감 이후 시각을 응답으로 준 경우 planner.py가
정말로 경고를 붙이는가"만 네트워크 없이 100% 재현 가능하게 검증한다. 프롬프트
문구 자체(LLM이 실제로 그 힌트를 읽고 반응하는지)는 이 스크립트로 확인할 수
없다 — 그건 `/dev-chat`의 Interpret 디버그 카드에서 실제로 전송된 프롬프트에
"운영시간=" 값이 들어있는지로 별도 확인한다.

입력: 없음(하드코딩된 시나리오 4개 — tests/schedule/test_planner.py의
TestPlanScheduleFlagsClosedStops와 같은 케이스를 스크립트로 옮긴 것).
출력: 표준 출력에 각 시나리오의 items[].warnings를 그대로 출력.
호출 시점: `python -m scripts.verify_operating_hours_warning`로 수동 실행
(1회성 확인 도구, pytest 스위트에는 이미 동일 케이스가 회귀 테스트로 있어
포함하지 않는다 — compare_schedule_thinking_budget.py와 같은 위치의 스크립트).
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

from app.providers.contracts import ProviderSource, provider_result
from app.schedule.planner import plan_schedule
from app.schedule.schemas import ScheduleLLMPlan, SchedulePlanningRequest
from app.schemas import RecommendationItem, ScheduleItem, UserConditions

_KST = ZoneInfo("Asia/Seoul")


class _FixedLLM:
    """항상 정해진 ScheduleLLMPlan만 돌려주는 최소 stub — 실제 Gemini 호출 없음."""

    def __init__(self, plan: ScheduleLLMPlan) -> None:
        self._plan = plan

    async def generate_schedule_plan(self, request: SchedulePlanningRequest):
        return provider_result(self._plan, source=ProviderSource.FAKE_LLM)


def _candidate(
    place_id: str, name: str, operating_hours_display: str | None
) -> RecommendationItem:
    return RecommendationItem(
        place_id=place_id,
        name=name,
        category="attraction",
        distance_km=0.4,
        remaining_minutes=120,
        operating_hours_display=operating_hours_display,
        environment_type="indoor",
        recommendation_reason="검증 스크립트용 고정 후보입니다.",
        explanations=[],
        warnings=[],
        score=0.5,
        feature_scores={},
        weights_used={},
    )


def _item(place_id: str, name: str, order: int, arrival: str) -> ScheduleItem:
    return ScheduleItem(
        order=order,
        place_id=place_id,
        place_name=name,
        estimated_arrival=arrival,
        estimated_duration_min=60,
        travel_to_next_min=15 if order < 3 else None,
        reason="검증 스크립트용 고정 이유입니다.",
    )


SCENARIOS: list[tuple[str, list[RecommendationItem], list[ScheduleItem]]] = [
    (
        "① 마감 이후 도착 (09:00~18:00인데 19:00 도착) — 경고가 붙어야 정상",
        [
            _candidate("p1", "장소1(09~18시)", "09:00~18:00"),
            _candidate("p2", "장소2(운영시간 미확인)", None),
            _candidate("p3", "장소3(운영시간 미확인)", None),
        ],
        [
            _item("p1", "장소1(09~18시)", 1, "19:00"),
            _item("p2", "장소2(운영시간 미확인)", 2, "19:30"),
            _item("p3", "장소3(운영시간 미확인)", 3, "20:00"),
        ],
    ),
    (
        "② 운영시간 내 도착 — 경고 없어야 정상",
        [
            _candidate("p1", "장소1(09~18시)", "09:00~18:00"),
            _candidate("p2", "장소2(운영시간 미확인)", None),
            _candidate("p3", "장소3(운영시간 미확인)", None),
        ],
        [
            _item("p1", "장소1(09~18시)", 1, "10:00"),
            _item("p2", "장소2(운영시간 미확인)", 2, "11:00"),
            _item("p3", "장소3(운영시간 미확인)", 3, "12:00"),
        ],
    ),
    (
        "③ 운영시간 미확인 후보가 늦게 도착 — 폐점 단정 불가라 경고 없어야 정상",
        [
            _candidate("p1", "장소1(운영시간 미확인)", None),
            _candidate("p2", "장소2(운영시간 미확인)", None),
            _candidate("p3", "장소3(운영시간 미확인)", None),
        ],
        [
            _item("p1", "장소1(운영시간 미확인)", 1, "23:00"),
            _item("p2", "장소2(운영시간 미확인)", 2, "23:30"),
            _item("p3", "장소3(운영시간 미확인)", 3, "23:50"),
        ],
    ),
    (
        "④ 24시간 운영 후보가 늦게 도착 — 상시 운영이라 경고 없어야 정상",
        [
            _candidate("p1", "장소1(24시간)", "24시간"),
            _candidate("p2", "장소2(운영시간 미확인)", None),
            _candidate("p3", "장소3(운영시간 미확인)", None),
        ],
        [
            _item("p1", "장소1(24시간)", 1, "23:50"),
            _item("p2", "장소2(운영시간 미확인)", 2, "23:55"),
            _item("p3", "장소3(운영시간 미확인)", 3, "00:00"),
        ],
    ),
]


async def main() -> None:
    for title, candidates, items in SCENARIOS:
        plan = ScheduleLLMPlan(
            items=items, total_duration_min=180, route_summary="검증용 동선 요약입니다."
        )
        request = SchedulePlanningRequest(
            candidates=candidates,
            conditions=UserConditions(),
            visit_datetime=datetime(2026, 8, 18, 15, 0, tzinfo=_KST),
            pairwise_distances_km={},
        )

        result = await plan_schedule(request, _FixedLLM(plan))

        print(f"\n{title}")
        for item in result.items:
            mark = "⚠️ " if item.warnings else "   "
            print(f"{mark}{item.place_name} 도착={item.estimated_arrival} warnings={item.warnings}")


if __name__ == "__main__":
    asyncio.run(main())
