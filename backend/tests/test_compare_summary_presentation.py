"""COMPARE 요약의 사용자 표시 규칙(도보·시간 단위·추천 말투) 회귀 테스트."""

from __future__ import annotations

import pytest

from app.providers.stub import FakeLLMProvider
from app.schemas import CompareCriteria, ComparisonItem, ComparisonResult


@pytest.mark.asyncio
async def test_fake_compare_summary_uses_walking_and_rounded_hour_labels() -> None:
    summary = await FakeLLMProvider().generate_compare_summary(
        ComparisonResult(
            criteria=CompareCriteria.OVERALL,
            items=[
                ComparisonItem(
                    place_id="p1",
                    place_name="테스트 카페",
                    rank=1,
                    distance_km=0.65,
                    remaining_minutes=256,
                    environment_type="indoor",
                ),
                ComparisonItem(
                    place_id="p2",
                    place_name="테스트 박물관",
                    rank=2,
                    distance_km=1.43,
                    remaining_minutes=311,
                    environment_type="indoor",
                ),
            ],
        )
    )

    assert "테스트 카페를 추천드려요." in summary.data
    assert "도보 약 11분" in summary.data
    assert "도보 약 24분" in summary.data
    assert "약 4시간 남음" in summary.data
    assert "약 5시간 남음" in summary.data
    assert "0.65km" not in summary.data
    assert "256분" not in summary.data
