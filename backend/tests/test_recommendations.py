"""추천 API Fake Provider 파이프라인 회귀 테스트.

역할: /api/recommendations의 Tool·Scoring 결과와 이미 노출된 장소 필터링을 검증한다.
입력: TestClient가 보내는 POST /api/recommendations JSON payload.
출력: 추천/검증 불가 목록과 place_id 필터링에 대한 pytest assertion.
호출 시점: 로컬 테스트와 CI에서 pytest 실행 시 호출된다.
TODO: 실제 provider 도입 후 랭킹, 검증 불가, 빈 결과 케이스를 확장한다.
"""

from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from app.main import app

KST = ZoneInfo("Asia/Seoul")
FIXED_VISIT_AT = datetime(2026, 7, 24, 12, 0, tzinfo=KST)


def _fixed_datetime(visit_at: datetime) -> type[datetime]:
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz: ZoneInfo | None = None) -> datetime:
            return visit_at if tz is None else visit_at.astimezone(tz)

    return FixedDateTime


def _request(
    shown_place_ids: list[str] | None = None,
    *,
    preferred_categories: list[str] | None = None,
    visit_at: datetime = FIXED_VISIT_AT,
) -> dict:
    client = TestClient(app)
    with patch(
        "app.services.recommendations.datetime",
        _fixed_datetime(visit_at),
    ):
        response = client.post(
            "/api/recommendations",
            json={
                "location_query": "경복궁",
                "preferred_categories": preferred_categories or ["cultural_facility", "restaurant"],
                "weather_condition": "bad",
                "search_radius_km": 1.0,
                "shown_place_ids": shown_place_ids or [],
            },
        )
    assert response.status_code == 200
    return response.json()


def test_recommendations_return_fake_pipeline_results() -> None:
    body = _request()

    assert [item["place_id"] for item in body["recommendations"]] == [
        "fake-museum-1",
        "fake-cafe-1",
    ]
    assert body["unverified_recommendations"] == []
    assert len(body["recommendations"]) <= 5
    assert body["elapsed_ms"] >= 0


def test_recommendations_filter_shown_place_ids() -> None:
    body = _request(["fake-museum-1"])

    visible_ids = [item["place_id"] for item in body["recommendations"]]
    unverified_ids = [item["place_id"] for item in body["unverified_recommendations"]]
    assert "fake-museum-1" not in visible_ids
    assert "fake-museum-1" not in unverified_ids


def test_recommendations_apply_category_filter() -> None:
    body = _request(preferred_categories=["restaurant"])

    assert [item["place_id"] for item in body["recommendations"]] == [
        "fake-cafe-1"
    ]


def test_recommendations_keep_open_candidate_on_museum_rest_day() -> None:
    monday_noon = datetime(2026, 7, 27, 12, 0, tzinfo=KST)

    body = _request(visit_at=monday_noon)

    assert [item["place_id"] for item in body["recommendations"]] == [
        "fake-cafe-1"
    ]


def test_recommendations_return_empty_when_requested_category_is_closed() -> None:
    monday_noon = datetime(2026, 7, 27, 12, 0, tzinfo=KST)

    body = _request(
        preferred_categories=["cultural_facility"],
        visit_at=monday_noon,
    )

    assert body["recommendations"] == []
    assert body["unverified_recommendations"] == []
