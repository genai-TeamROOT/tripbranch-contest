"""Fake 추천 API의 요청·응답을 터미널에서 확인하는 수동 Inspection Test."""

from __future__ import annotations

import json
import os
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from app.main import app

pytestmark = [
    pytest.mark.inspection,
    pytest.mark.skipif(
        os.getenv("RUN_RECOMMENDATION_INSPECTION") != "true",
        reason=(
            "RUN_RECOMMENDATION_INSPECTION=true일 때만 "
            "추천 API 요청·응답을 출력합니다."
        ),
    ),
]

KST = ZoneInfo("Asia/Seoul")


def _fixed_datetime(visit_at: datetime) -> type[datetime]:
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz: ZoneInfo | None = None) -> datetime:
            return visit_at if tz is None else visit_at.astimezone(tz)

    return FixedDateTime


def _print_json(title: str, payload: object) -> None:
    print(f"\n=== {title} ===")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


@pytest.mark.parametrize(
    ("scenario", "visit_at", "preferred_categories", "expected_ids"),
    [
        (
            "금요일 카페 추천",
            datetime(2026, 7, 24, 12, 0, tzinfo=KST),
            ["cafe"],
            ["fake-cafe-1"],
        ),
        (
            "월요일 전체 추천",
            datetime(2026, 7, 27, 12, 0, tzinfo=KST),
            ["museum", "cafe"],
            ["fake-cafe-1"],
        ),
        (
            "월요일 휴무 카테고리",
            datetime(2026, 7, 27, 12, 0, tzinfo=KST),
            ["museum"],
            [],
        ),
    ],
)
def test_inspect_fake_recommendation_request_and_response(
    scenario: str,
    visit_at: datetime,
    preferred_categories: list[str],
    expected_ids: list[str],
) -> None:
    """카테고리 필터와 휴무일 적용 전후의 최종 API 응답을 출력한다."""

    request_payload = {
        "location_query": "경복궁",
        "preferred_categories": preferred_categories,
        "weather_condition": "bad",
        "search_radius_km": 1.0,
        "shown_place_ids": [],
    }
    _print_json(
        "Recommendation Inspection",
        {
            "scenario": scenario,
            "visit_at": visit_at.isoformat(),
        },
    )
    _print_json("Recommendation Request", request_payload)

    with patch(
        "app.services.recommendation_pipeline.datetime",
        _fixed_datetime(visit_at),
    ):
        response = TestClient(app).post(
            "/api/recommendations",
            json=request_payload,
        )

    response_payload = response.json()
    _print_json(
        "Recommendation Response",
        {
            "status_code": response.status_code,
            "body": response_payload,
        },
    )

    assert response.status_code == 200
    assert [
        recommendation["place_id"]
        for recommendation in response_payload["recommendations"]
    ] == expected_ids
    assert response_payload["elapsed_ms"] >= 0
