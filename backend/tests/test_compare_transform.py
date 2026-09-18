"""COMPARE의 A Runtime 대상 해석·C 응답 변환 회귀 테스트."""

from __future__ import annotations

from app.agent_context.compare_schemas import CompareContextResponse
from app.schemas import CompareCriteria, ComparePayload, ComparisonItem
from app.services.runtime.compare_transform import (
    to_compare_context_request,
    to_comparison_result,
)
from app.state.schema import RecommendedItem


def _shown(*items: tuple[str, int]) -> list[RecommendedItem]:
    return [
        RecommendedItem(
            place_id=place_id,
            run_id="run-test",
            rank=rank,
            distance_km=rank * 0.3,
            remaining_minutes=120 + rank,
            environment_type="indoor",
        )
        for place_id, rank in items
    ]


def test_all_targets_keep_last_recommendation_feature_snapshots() -> None:
    resolution = to_compare_context_request(
        "req-test",
        ComparePayload(targets="all", criteria=CompareCriteria.TRAVEL_TIME),
        _shown(("place-2", 2), ("place-1", 1)),
    )

    assert resolution.message is None
    assert resolution.request is not None
    assert resolution.request.criteria is CompareCriteria.TRAVEL_TIME
    assert [(item.place_id, item.rank) for item in resolution.request.candidates] == [
        ("place-1", 1),
        ("place-2", 2),
    ]
    assert resolution.request.candidates[0].distance_km == 0.3
    assert resolution.request.candidates[0].remaining_minutes == 121


def test_numbered_targets_dedupe_and_keep_selected_order() -> None:
    resolution = to_compare_context_request(
        "req-test",
        ComparePayload(targets=[3, 1, 3], criteria=CompareCriteria.TIME),
        _shown(("place-1", 1), ("place-2", 2), ("place-3", 3)),
    )

    assert resolution.request is not None
    assert [item.rank for item in resolution.request.candidates] == [3, 1]


def test_missing_or_out_of_range_targets_return_user_guidance() -> None:
    empty = to_compare_context_request(
        "req-test",
        ComparePayload(targets="all", criteria=CompareCriteria.OVERALL),
        [],
    )
    assert empty.request is None
    assert empty.message == "먼저 장소를 추천해드릴까요?"

    out_of_range = to_compare_context_request(
        "req-test",
        ComparePayload(targets=[1, 4], criteria=CompareCriteria.OVERALL),
        _shown(("place-1", 1), ("place-2", 2)),
    )
    assert out_of_range.request is None
    assert out_of_range.message == "추천 결과는 2개까지 있어요. 몇 번을 비교할까요?"


def test_compare_response_requires_two_resolved_items() -> None:
    response = CompareContextResponse(
        request_id="req-test",
        status="partial",
        criteria=CompareCriteria.TRAVEL_TIME,
        items=[
            ComparisonItem(place_id="place-1", place_name="첫 장소", rank=1),
            ComparisonItem(place_id="place-2", place_name="둘째 장소", rank=2),
        ],
        missing_place_ids=["place-3"],
    )
    result = to_comparison_result(response)

    assert result is not None
    assert result.criteria is CompareCriteria.TRAVEL_TIME
    assert [item.place_name for item in result.items] == ["첫 장소", "둘째 장소"]

    no_data = response.model_copy(update={"status": "no_data", "items": []})
    assert to_comparison_result(no_data) is None
