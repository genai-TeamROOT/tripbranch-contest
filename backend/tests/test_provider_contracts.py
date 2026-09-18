from __future__ import annotations

import pytest

from app.agent_context.mappers import _PRECIPITATION_BY_PTY_CODE, _SKY_BY_CODE
from app.domain.models import GeocodeResult, PlaceCategoryFilter, WeatherCondition
from app.domain.weather_judgment import judge_weather_condition_from_facts
from app.providers.concentration import FakeConcentrationProvider
from app.providers.contracts import ProviderSource, ProviderStatus
from app.providers.geocoding import FakeGeocodingProvider
from app.providers.holiday import FakeHolidayProvider
from app.providers.stub import FakePlaceProvider, FakeWeatherProvider


@pytest.mark.asyncio
async def test_fake_geocoding_provider_uses_common_result() -> None:
    result = await FakeGeocodingProvider().geocode("경복궁")

    assert result.data == GeocodeResult(
        query="경복궁",
        resolved_name="경복궁",
        latitude=37.5788,
        longitude=126.9770,
        administrative_district="종로구",
    )
    assert result.metadata.source is ProviderSource.FAKE_GEOCODING
    assert result.metadata.status is ProviderStatus.SUCCESS


@pytest.mark.asyncio
async def test_fake_weather_provider_uses_common_facts() -> None:
    provider = FakeWeatherProvider("4", "1")
    forecast = await provider.get_forecast_slots(37.5796, 126.9770)

    assert forecast.metadata.source is ProviderSource.FAKE_WEATHER
    assert forecast.data.slots
    assert all(
        slot.sky_code == "4" and slot.precipitation_type == "1"
        for slot in forecast.data.slots
    )


@pytest.mark.parametrize(
    ("sky_code", "precipitation_type", "expected_precipitation", "expected_sky", "expected"),
    [
        ("1", "0", "none", "clear", WeatherCondition.GOOD),
        ("4", "0", "none", "overcast", WeatherCondition.NEUTRAL),
        ("4", "1", "rain", "overcast", WeatherCondition.BAD),
    ],
)
@pytest.mark.asyncio
async def test_fake_weather_provider_emits_facts_d_can_judge(
    sky_code: str,
    precipitation_type: str,
    expected_precipitation: str,
    expected_sky: str,
    expected: WeatherCondition,
) -> None:
    """fake도 판정 재료(강수/하늘/기온)를 내려준다.

    코드를 비워두면 C 매퍼를 통과한 뒤 D 입력이 전부 None이 되어, 무엇을
    설정하든 판정이 NEUTRAL로 굳는다 — fake로는 우천 시나리오를 재현할 수 없었다.
    """
    forecast = await FakeWeatherProvider(
        sky_code, precipitation_type
    ).get_forecast_slots(37.5796, 126.9770)
    slot = forecast.data.slots[0]

    # C가 D에 넘기는 형태(도메인 용어)까지 확인한다 — 코드만 맞고 매핑이 빠지면
    # D 입장에선 여전히 결측이다.
    assert _PRECIPITATION_BY_PTY_CODE[slot.precipitation_type or ""] == (
        expected_precipitation
    )
    assert _SKY_BY_CODE[slot.sky_code or ""] == expected_sky
    assert slot.temperature_celsius is not None

    judged, _reason = judge_weather_condition_from_facts(
        _PRECIPITATION_BY_PTY_CODE[slot.precipitation_type or ""],
        _SKY_BY_CODE[slot.sky_code or ""],
        slot.temperature_celsius,
        None,
    )
    assert judged is expected


@pytest.mark.asyncio
async def test_fake_weather_provider_temperature_drives_heat_judgment() -> None:
    """폭염·한파는 SKY·PTY로 표현할 수 없어 기온으로 직접 만든다."""
    forecast = await FakeWeatherProvider(
        "1", "0", temperature_celsius=36.0
    ).get_forecast_slots(37.5796, 126.9770)
    slot = forecast.data.slots[0]

    judged, reason = judge_weather_condition_from_facts(
        _PRECIPITATION_BY_PTY_CODE[slot.precipitation_type or ""],
        _SKY_BY_CODE[slot.sky_code or ""],
        slot.temperature_celsius,
        None,
    )
    assert judged is WeatherCondition.BAD
    assert reason == "heat"


@pytest.mark.asyncio
async def test_fake_place_provider_uses_common_candidate() -> None:
    result = await FakePlaceProvider().search_places(
        latitude=37.5796,
        longitude=126.9770,
        preferred_categories=["cultural_facility"],
        search_radius_km=1.0,
    )

    assert result.data[0].raw_source == "fake_place"
    assert result.data[0].latitude == 37.5796
    assert result.metadata.source is ProviderSource.FAKE_PLACE

    cafe_result = await FakePlaceProvider().search_places(
        latitude=37.5796,
        longitude=126.9770,
        preferred_categories=["restaurant"],
        search_radius_km=1.0,
        category_filter=PlaceCategoryFilter(
            content_type_id="39",
            lcls_systm1="FD",
            lcls_systm2="FD05",
            lcls_systm3="FD050100",
        ),
    )
    assert [candidate.content_type_id for candidate in cafe_result.data] == ["39"]
    assert cafe_result.data[0].lcls_systm1 == "FD"
    assert cafe_result.data[0].lcls_systm2 == "FD05"
    assert cafe_result.data[0].lcls_systm3 == "FD050100"

    keyword_result = await FakePlaceProvider().search_by_keyword("박물관")
    assert keyword_result.data[0].content_type_id == "14"

    details = await FakePlaceProvider().get_details("fake-museum-1", "14")
    assert details.data.title == "테스트 박물관"
    assert details.data.rest_date == "매주 월요일"

    named_details = await FakePlaceProvider().find_details_by_name("테스트 박물관")
    assert named_details.data.title == "테스트 박물관"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("preferred_categories", "expected_ids"),
    [
        ([], ["fake-museum-1", "fake-cafe-1"]),
        (["cultural_facility"], ["fake-museum-1"]),
        (["  RESTAURANT  "], ["fake-cafe-1"]),
        (
            ["cultural_facility", "restaurant", "cultural_facility"],
            ["fake-museum-1", "fake-cafe-1"],
        ),
        (["cultural_facility"], ["fake-museum-1"]),
        (["restaurant"], ["fake-cafe-1"]),
        (["unsupported"], []),
    ],
)
async def test_fake_place_provider_filters_preferred_categories(
    preferred_categories: list[str],
    expected_ids: list[str],
) -> None:
    result = await FakePlaceProvider().search_places(
        latitude=37.5796,
        longitude=126.9770,
        preferred_categories=preferred_categories,
        search_radius_km=1.0,
    )

    assert [candidate.place_id for candidate in result.data] == expected_ids
    expected_status = ProviderStatus.SUCCESS if expected_ids else ProviderStatus.NO_DATA
    assert result.metadata.status is expected_status


@pytest.mark.asyncio
async def test_fake_place_provider_category_filter_takes_precedence() -> None:
    result = await FakePlaceProvider().search_places(
        latitude=37.5796,
        longitude=126.9770,
        preferred_categories=["cultural_facility"],
        search_radius_km=1.0,
        category_filter=PlaceCategoryFilter(content_type_id="39"),
    )

    assert [candidate.place_id for candidate in result.data] == ["fake-cafe-1"]


@pytest.mark.asyncio
async def test_fake_place_provider_uses_distinct_rest_dates() -> None:
    provider = FakePlaceProvider()

    museum = await provider.get_details("fake-museum-1", "14")
    cafe = await provider.get_details("fake-cafe-1", "39")

    assert museum.data.rest_date == "매주 월요일"
    assert cafe.data.rest_date == "연중무휴"


@pytest.mark.asyncio
async def test_fake_concentration_provider_uses_common_result() -> None:
    result = await FakeConcentrationProvider().get_forecast("11", "11110", "경복궁")

    assert result.data.provider == "fake_concentration"
    assert result.data.forecasts
    assert result.metadata.source is ProviderSource.FAKE_CONCENTRATION


@pytest.mark.asyncio
async def test_fake_holiday_provider_uses_common_result() -> None:
    result = await FakeHolidayProvider().get_holidays(2026)

    assert result.data.provider == "fake_holiday"
    assert result.data.entries
    assert result.data.holidays
    assert result.metadata.source is ProviderSource.FAKE_HOLIDAY
