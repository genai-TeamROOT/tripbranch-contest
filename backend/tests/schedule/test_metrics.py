"""일정 편성 품질 지표 단위 테스트. (TP-242)

지켜야 하는 것은 셋이다 — 원자값만 남기고(비율을 여기서 계산하지 않는다), 사용자
텍스트를 담지 않고, 시간을 말하지 않은 턴을 0으로 뭉개지 않는다.
"""

from __future__ import annotations

from app.schedule.metrics import (
    SCHEDULE_QUALITY_STEP,
    WALKABLE_THRESHOLD_MIN,
    schedule_quality_metrics,
)
from app.schemas import ScheduleBudgetStatus, ScheduleItem, ScheduleResult


def _item(place_id: str = "p1") -> ScheduleItem:
    return ScheduleItem(
        order=1,
        place_id=place_id,
        place_name="경복궁",
        estimated_arrival="13:00",
        estimated_duration_min=60,
        travel_to_next_min=None,
        reason="테스트 이유",
    )


def _result(**overrides) -> ScheduleResult:
    base = {
        "items": [_item()],
        "total_duration_min": 210,
        "route_summary": "경복궁 일대를 도는 코스예요.",
        "basis_note": "기준 시각 안내",
        "elapsed_ms": 100.0,
    }
    base.update(overrides)
    return ScheduleResult(**base)


class TestScheduleQualityMetrics:
    def test_초과_분량은_부호를_유지한다(self) -> None:
        """절댓값만 남기면 초과와 부족이 섞여 평균이 0에 가까워지고, 정작 보려던
        "얼마나 넘겼나"가 사라진다."""

        over = schedule_quality_metrics(
            _result(total_duration_min=260, time_budget_status=ScheduleBudgetStatus.OVER),
            time_available_min=180,
            saved_place_count=0,
            walkable_cluster_size=0,
        )
        under = schedule_quality_metrics(
            _result(total_duration_min=100, time_budget_status=ScheduleBudgetStatus.UNDER),
            time_available_min=180,
            saved_place_count=0,
            walkable_cluster_size=0,
        )

        assert over["time_budget_delta_min"] == 80
        assert under["time_budget_delta_min"] == -80

    def test_시간을_말하지_않은_턴은_0이_아니라_None이다(self) -> None:
        """0으로 두면 집계에서 "정확히 맞춘 턴"으로 읽힌다."""

        metrics = schedule_quality_metrics(
            _result(),
            time_available_min=None,
            saved_place_count=0,
            walkable_cluster_size=0,
        )

        assert metrics["time_budget_status"] is None
        assert metrics["time_budget_delta_min"] is None
        assert metrics["time_available_min"] is None

    def test_누락_사유를_섞지_않고_따로_센다(self) -> None:
        """TP-236이 사유를 갈라둔 이유가 사용자가 할 수 있는 일이 다르기
        때문이다. 지표에서 합치면 그 구분이 사라진다."""

        metrics = schedule_quality_metrics(
            _result(
                closed_saved_place_names=["스태픽스"],
                absent_saved_place_names=["아띠인력거", "북촌한옥마을"],
                over_capacity_place_names=["인사동"],
                omitted_saved_place_names=[],
                added_place_names=["국립고궁박물관"],
            ),
            time_available_min=180,
            saved_place_count=4,
            walkable_cluster_size=0,
        )

        assert metrics["closed_saved_count"] == 1
        assert metrics["absent_saved_count"] == 2
        assert metrics["over_capacity_count"] == 1
        assert metrics["omitted_saved_count"] == 0
        assert metrics["added_place_count"] == 1
        assert metrics["saved_place_count"] == 4

    def test_장소_이름을_담지_않는다(self) -> None:
        """**trace_records를 대화 삭제 때 안 지우는 근거가 "사용자 텍스트가
        없다"는 것이다.** 이름을 실으면 그 근거가 무너지고 보관 규칙까지 다시
        봐야 한다.
        """

        metrics = schedule_quality_metrics(
            _result(
                items=[_item("p1")],
                closed_saved_place_names=["스태픽스"],
                absent_saved_place_names=["아띠인력거"],
                over_capacity_place_names=["인사동"],
                omitted_saved_place_names=["북촌한옥마을"],
                added_place_names=["국립고궁박물관"],
                route_summary="경복궁에서 시작하는 코스예요.",
            ),
            time_available_min=180,
            saved_place_count=4,
            walkable_cluster_size=3,
        )

        rendered = repr(metrics)
        for name in (
            "스태픽스",
            "아띠인력거",
            "인사동",
            "북촌한옥마을",
            "국립고궁박물관",
            "경복궁",
            "p1",
        ):
            assert name not in rendered

    def test_비율을_계산하지_않고_분모를_함께_남긴다(self) -> None:
        """누락률을 여기서 계산하면 분모를 나중에 바꿀 수 없다."""

        metrics = schedule_quality_metrics(
            _result(absent_saved_place_names=["a", "b"]),
            time_available_min=180,
            saved_place_count=4,
            walkable_cluster_size=0,
        )

        assert metrics["absent_saved_count"] == 2
        assert metrics["saved_place_count"] == 4
        assert not any(key.endswith("_rate") or key.endswith("_ratio") for key in metrics)

    def test_묶기_기준을_값과_함께_남긴다(self) -> None:
        """기준을 바꿔도 옛 기록을 잘못 비교하지 않게 한다 — 지표 이름에 5를
        박으면 기준이 바뀐 뒤의 기록과 구분할 수 없다."""

        metrics = schedule_quality_metrics(
            _result(),
            time_available_min=180,
            saved_place_count=0,
            walkable_cluster_size=3,
        )

        assert metrics["walkable_cluster_size"] == 3
        assert metrics["walkable_within_min"] == WALKABLE_THRESHOLD_MIN

    def test_step_이름이_기존_단계와_겹치지_않는다(self) -> None:
        """단계별 지연시간을 보는 화면이 도메인 지표에 오염되지 않게 한다."""

        assert SCHEDULE_QUALITY_STEP not in {"llm_interpret", "tool", "scoring"}
