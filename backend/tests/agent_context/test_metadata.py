from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.agent_context.metadata import (
    build_response_metadata,
    collect_provider_metadata,
)
from app.agent_context.schemas import (
    ContextValue,
    Coordinates,
    HolidayInfo,
    PlaceCandidate,
    ProviderMetadata,
    RecommendationContext,
    ResolvedLocation,
    WeatherForecast,
)

RETRIEVED_AT = datetime(2026, 8, 14, 0, tzinfo=UTC)


def _metadata(
    source: str,
    *,
    status: str = "success",
    retrieved_at: datetime = RETRIEVED_AT,
) -> ProviderMetadata:
    return ProviderMetadata(
        source=source,
        status=status,
        retrieved_at=retrieved_at,
    )


def _recommendation_context(
    *,
    location_metadata: list[ProviderMetadata] | None = None,
    weather_metadata: list[ProviderMetadata] | None = None,
    places_metadata: list[ProviderMetadata] | None = None,
    holidays_metadata: list[ProviderMetadata] | None = None,
) -> RecommendationContext:
    coordinates = Coordinates(latitude=37.579617, longitude=126.977041)
    return RecommendationContext(
        location=ContextValue[ResolvedLocation](
            status="success",
            data=ResolvedLocation(
                requested_query="경복궁",
                resolved_name="경복궁",
                source="query",
                location=coordinates,
            ),
            provider_metadata=location_metadata or [],
        ),
        weather=ContextValue[WeatherForecast](
            status="success",
            data=WeatherForecast(
                forecast_for=datetime(2026, 8, 15, 2, tzinfo=UTC),
            ),
            provider_metadata=weather_metadata or [],
        ),
        places=ContextValue[list[PlaceCandidate]](
            status="success",
            data=[
                PlaceCandidate(
                    place_id="126508",
                    name="경복궁",
                    category="attraction",
                    location=coordinates,
                )
            ],
            provider_metadata=places_metadata or [],
        ),
        holidays=ContextValue[list[HolidayInfo]](
            status="success",
            data=[HolidayInfo(date="2026-08-15", name="광복절")],
            provider_metadata=holidays_metadata or [],
        ),
    )


def test_collects_metadata_in_context_order() -> None:
    context = _recommendation_context(
        location_metadata=[_metadata("location")],
        weather_metadata=[_metadata("weather")],
        places_metadata=[_metadata("places")],
        holidays_metadata=[_metadata("holidays")],
    )

    collected = collect_provider_metadata(context)

    assert [item.source for item in collected] == [
        "location",
        "weather",
        "places",
        "holidays",
    ]


def test_deduplicates_identical_metadata_preserving_first_occurrence() -> None:
    duplicate = _metadata("shared")
    context = _recommendation_context(
        location_metadata=[duplicate],
        weather_metadata=[duplicate.model_copy()],
        places_metadata=[_metadata("places")],
        holidays_metadata=[duplicate.model_copy()],
    )

    collected = collect_provider_metadata(context)

    assert [item.source for item in collected] == ["shared", "places"]
    assert collected[0] is duplicate


def test_keeps_same_provider_when_status_or_retrieved_at_differs() -> None:
    context = _recommendation_context(
        location_metadata=[_metadata("shared")],
        weather_metadata=[_metadata("shared", status="partial")],
        places_metadata=[
            _metadata("shared", retrieved_at=RETRIEVED_AT + timedelta(minutes=1))
        ],
    )

    collected = collect_provider_metadata(context)

    assert len(collected) == 3
    assert [item.status for item in collected] == ["success", "partial", "success"]
    assert collected[0].retrieved_at != collected[2].retrieved_at


def test_none_context_returns_empty_metadata() -> None:
    assert collect_provider_metadata(None) == []
    assert build_response_metadata(None).provider_metadata == []


def test_skips_none_context_values_and_empty_metadata() -> None:
    holidays_metadata = _metadata("holidays")
    context = RecommendationContext(
        location=None,
        weather=None,
        places=ContextValue[list[PlaceCandidate]](
            status="no_data",
            data=[],
            provider_metadata=[],
        ),
        holidays=ContextValue[list[HolidayInfo]](
            status="no_data",
            data=[],
            provider_metadata=[holidays_metadata],
        ),
    )

    assert collect_provider_metadata(context) == [holidays_metadata]


def test_build_response_metadata_preserves_rule_versions() -> None:
    rule_versions = {
        "category_mapping": "v1",
        "operating_hours_normalization": "operating-hours-1.0.0",
    }

    metadata = build_response_metadata(None, rule_versions=rule_versions)

    assert metadata.rule_versions == rule_versions


def test_inputs_are_not_modified() -> None:
    context = _recommendation_context(
        location_metadata=[_metadata("location")],
        weather_metadata=[_metadata("weather")],
    )
    rule_versions = {"category_mapping": "v1"}
    context_before = context.model_dump()
    rule_versions_before = rule_versions.copy()

    metadata = build_response_metadata(context, rule_versions=rule_versions)
    metadata.rule_versions["new_rule"] = "v2"

    assert context.model_dump() == context_before
    assert rule_versions == rule_versions_before
