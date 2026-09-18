"""일정 편성 품질 지표. (TP-242)

**왜 필요한가.** TP-238이 시간 준수 판정을, TP-239가 개수 상한을 만들었지만 둘 다
화면에만 쓰이고 어디에도 기록되지 않는다. TP-236이 보관함 누락 사유를 넷으로 갈랐지만
어느 쪽이 얼마나 흔한지 세는 곳이 없다 — 참조하는 파일이 `schemas.py`·
`response_composer.py`·`agent_runtime.py` 셋뿐이다. **고치기 전후를 숫자로 볼 방법이
없다는 것이 이 모듈이 있는 이유다.**

**사용자 텍스트를 담지 않는다.** 장소 이름은 길이만 취한다. `trace_records`를 대화
삭제 때 안 지우는 근거가 "사용자 텍스트가 없다"는 것이라, 이름을 실으면 그 근거가
무너지고 보관 규칙까지 다시 봐야 한다.

**비율이 아니라 원자값을 남긴다.** "누락률"을 여기서 계산하면 분모를 나중에 바꿀 수
없다. 건수와 분모(`saved_place_count`)를 따로 남기고 나누는 것은 집계 쪽에 맡긴다.
"""

from __future__ import annotations

from typing import Any

from app.schemas import ScheduleResult

# Trace의 step 이름. 기존 단계(llm_interpret·tool·scoring)에 얹지 않는다 —
# 단계별 지연시간을 보는 화면이 도메인 지표에 오염된다.
SCHEDULE_QUALITY_STEP = "schedule_quality"

# 근접 묶기(TP-243)의 가치를 재는 기준 거리(도보 분). 사용자 문의 원문이 "5분 거리
# 이내"였다. 이 값을 바꾸면 지표의 의미가 바뀌므로 지표 이름에 값을 박지 않고
# `walkable_within_min`으로 함께 남긴다 — 나중에 기준을 바꿔도 옛 기록을 잘못
# 비교하지 않는다.
WALKABLE_THRESHOLD_MIN = 5


def schedule_quality_metrics(
    result: ScheduleResult,
    *,
    time_available_min: int | None,
    saved_place_count: int,
    walkable_cluster_size: int,
) -> dict[str, Any]:
    """이번 SCHEDULE 턴의 품질 지표.

    `time_budget_delta_min`은 **부호를 유지한다** — 양수가 초과, 음수가 부족이다.
    절댓값만 남기면 두 방향이 섞여 평균이 0에 가까워지고, 정작 보려던 "얼마나
    넘겼나"가 사라진다.

    시간을 말하지 않은 턴은 판정과 차이가 모두 None이다. 0으로 뭉개면 집계에서
    "정확히 맞춘 턴"으로 읽힌다.
    """

    if time_available_min is None:
        delta_min: int | None = None
    else:
        delta_min = result.total_duration_min - time_available_min

    return {
        # 시간 준수 (TP-238)
        "time_budget_status": (
            result.time_budget_status.value
            if result.time_budget_status is not None
            else None
        ),
        "time_budget_delta_min": delta_min,
        "time_available_min": time_available_min,
        "total_duration_min": result.total_duration_min,
        # 개수 상한 (TP-239)
        "item_capacity": result.item_capacity,
        "item_count": len(result.items),
        # 보관함 누락 사유별 건수 (TP-236, TP-223)
        "saved_place_count": saved_place_count,
        "closed_saved_count": len(result.closed_saved_place_names),
        "absent_saved_count": len(result.absent_saved_place_names),
        "over_capacity_count": len(result.over_capacity_place_names),
        "omitted_saved_count": len(result.omitted_saved_place_names),
        "added_place_count": len(result.added_place_names),
        # 근접 묶기의 가치 (TP-243 착수 판단용)
        "walkable_cluster_size": walkable_cluster_size,
        "walkable_within_min": WALKABLE_THRESHOLD_MIN,
    }


__all__ = [
    "SCHEDULE_QUALITY_STEP",
    "WALKABLE_THRESHOLD_MIN",
    "schedule_quality_metrics",
]
