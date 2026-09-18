"""to_candidate_enrichment_request() 단위 테스트.

concentration-conditions.md §2.2.3 안 B의 A→C 변환 함수 — 1차 추천 결과
(위경도 없음)를 원본 후보 목록(위경도 있음)과 place_id로 재조인한다.
"""

from __future__ import annotations

from app.agent_context.schemas import Coordinates, PlaceCandidate
from app.schemas import RecommendationItem, RecommendationResponse
from app.services.runtime.enrichment_transform import to_candidate_enrichment_request


def _item(place_id: str) -> RecommendationItem:
    return RecommendationItem(
        place_id=place_id,
        name=f"장소-{place_id}",
        category="cafe",
        distance_km=0.3,
        remaining_minutes=60,
        environment_type="indoor",
        recommendation_reason="테스트용",
        explanations=[],
        warnings=[],
        score=0.5,
        feature_scores={},
        weights_used={},
    )


def _place(place_id: str, *, latitude: float = 37.5, longitude: float = 127.0) -> PlaceCandidate:
    return PlaceCandidate(
        place_id=place_id,
        name=f"장소-{place_id}",
        category="cafe",
        location=Coordinates(latitude=latitude, longitude=longitude),
    )


class TestToCandidateEnrichmentRequest:
    def test_joins_recommendations_and_unverified_with_places(self) -> None:
        recommendations = RecommendationResponse(
            recommendations=[_item("a")],
            unverified_recommendations=[_item("b")],
            elapsed_ms=0,
        )
        places = [_place("a", latitude=37.1, longitude=127.1), _place("b", latitude=37.2)]

        request = to_candidate_enrichment_request("req-1", recommendations, places)

        assert request is not None
        assert request.request_id == "req-1"
        assert request.features == ["concentration"]
        assert {target.place_id for target in request.candidates} == {"a", "b"}
        a_target = next(t for t in request.candidates if t.place_id == "a")
        assert a_target.latitude == 37.1
        assert a_target.longitude == 127.1

    def test_excludes_candidates_missing_from_places(self) -> None:
        recommendations = RecommendationResponse(
            recommendations=[_item("a"), _item("unknown")],
            unverified_recommendations=[],
            elapsed_ms=0,
        )
        places = [_place("a")]

        request = to_candidate_enrichment_request("req-2", recommendations, places)

        assert request is not None
        assert [target.place_id for target in request.candidates] == ["a"]

    def test_returns_none_when_no_candidates_match(self) -> None:
        recommendations = RecommendationResponse(
            recommendations=[_item("a")],
            unverified_recommendations=[],
            elapsed_ms=0,
        )
        assert to_candidate_enrichment_request("req-3", recommendations, []) is None

    def test_returns_none_when_recommendations_empty(self) -> None:
        recommendations = RecommendationResponse(
            recommendations=[], unverified_recommendations=[], elapsed_ms=0
        )
        places = [_place("a")]
        assert to_candidate_enrichment_request("req-4", recommendations, places) is None
