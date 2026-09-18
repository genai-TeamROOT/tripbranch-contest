"""Fake/Real Provider와 Factory 전환 계약의 동등성을 검증한다."""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Protocol

import httpx
import pytest

from app.config import Settings, settings
from app.providers.concentration import (
    FakeConcentrationProvider,
    RealConcentrationProvider,
)
from app.providers.factory import (
    get_concentration_provider,
    get_geocoding_provider,
    get_holiday_provider,
    get_local_search_provider,
    get_place_provider,
    get_weather_provider,
)
from app.providers.geocoding import FakeGeocodingProvider, RealGeocodingProvider
from app.providers.holiday import FakeHolidayProvider, RealHolidayProvider
from app.providers.local_search import FakeLocalSearchProvider, RealLocalSearchProvider
from app.providers.protocols import (
    ConcentrationProvider,
    GeocodingProvider,
    HolidayProvider,
    LocalSearchProvider,
    PlaceProvider,
    WeatherProvider,
)
from app.providers.real_place import RealPlaceProvider
from app.providers.stub import FakePlaceProvider, FakeWeatherProvider
from app.providers.weather import RealWeatherProvider

_PROVIDER_SETTING_NAMES = (
    "geocoding_provider",
    "local_search_provider",
    "weather_provider",
    "place_provider",
    "concentration_provider",
    "holiday_provider",
)
_SECRET = "parity-test-secret-value"


@dataclass(frozen=True)
class ProviderParityCase:
    """Provider 종류별 Protocol과 Fake/Real 구현의 대응 관계."""

    name: str
    protocol: type[Protocol]
    fake_class: type[object]
    real_class: type[object]
    setting_name: str


_PROVIDER_CASES = (
    ProviderParityCase(
        "geocoding",
        GeocodingProvider,
        FakeGeocodingProvider,
        RealGeocodingProvider,
        "geocoding_provider",
    ),
    ProviderParityCase(
        "local_search",
        LocalSearchProvider,
        FakeLocalSearchProvider,
        RealLocalSearchProvider,
        "local_search_provider",
    ),
    ProviderParityCase(
        "weather",
        WeatherProvider,
        FakeWeatherProvider,
        RealWeatherProvider,
        "weather_provider",
    ),
    ProviderParityCase(
        "place",
        PlaceProvider,
        FakePlaceProvider,
        RealPlaceProvider,
        "place_provider",
    ),
    ProviderParityCase(
        "concentration",
        ConcentrationProvider,
        FakeConcentrationProvider,
        RealConcentrationProvider,
        "concentration_provider",
    ),
    ProviderParityCase(
        "holiday",
        HolidayProvider,
        FakeHolidayProvider,
        RealHolidayProvider,
        "holiday_provider",
    ),
)
_FAKE_CLASSES = {case.name: case.fake_class for case in _PROVIDER_CASES}
_REAL_CLASSES = {case.name: case.real_class for case in _PROVIDER_CASES}


def _public_protocol_methods(protocol: type[Protocol]) -> dict[str, object]:
    """상속된 Protocol까지 포함해 공개 메서드만 반환한다."""

    return {
        name: method
        for name, method in inspect.getmembers(protocol, inspect.isfunction)
        if not name.startswith("_")
    }


def _parameter_names(method: object) -> tuple[str, ...]:
    """인스턴스 메서드의 self를 제외한 파라미터 이름을 선언 순서로 반환한다."""

    return tuple(
        name
        for name in inspect.signature(method).parameters
        if name != "self"
    )


def _configure_global_settings(
    monkeypatch: pytest.MonkeyPatch,
    *,
    common_mode: str,
    overrides: dict[str, str] | None = None,
) -> None:
    """Factory 검증에 필요한 설정을 실제 환경과 분리해 주입한다."""

    monkeypatch.setattr(settings, "provider_mode", common_mode)
    for setting_name in _PROVIDER_SETTING_NAMES:
        monkeypatch.setattr(
            settings,
            setting_name,
            (overrides or {}).get(setting_name),
        )
    monkeypatch.setattr(settings, "naver_map_client_id", _SECRET)
    monkeypatch.setattr(settings, "naver_map_client_secret", _SECRET)
    monkeypatch.setattr(settings, "naver_local_search_client_id", _SECRET)
    monkeypatch.setattr(settings, "naver_local_search_client_secret", _SECRET)
    monkeypatch.setattr(settings, "weather_api_key", _SECRET)
    monkeypatch.setattr(settings, "tour_api_service_key", _SECRET)


def _create_all_providers(client: httpx.AsyncClient) -> dict[str, object]:
    """동일한 HTTP client로 Factory의 Provider를 생성한다."""

    return {
        "geocoding": get_geocoding_provider(client),
        "local_search": get_local_search_provider(client),
        "weather": get_weather_provider(client),
        "place": get_place_provider(client),
        "concentration": get_concentration_provider(client),
        "holiday": get_holiday_provider(client),
    }


@pytest.mark.parametrize("case", _PROVIDER_CASES, ids=lambda case: case.name)
def test_fake_and_real_provide_all_protocol_methods(
    case: ProviderParityCase,
) -> None:
    """Fake와 Real이 해당 Protocol의 모든 공개 메서드를 제공해야 한다."""

    required_methods = _public_protocol_methods(case.protocol)
    assert required_methods

    for implementation in (case.fake_class, case.real_class):
        missing = [
            method_name
            for method_name in required_methods
            if not callable(getattr(implementation, method_name, None))
        ]
        assert missing == []


@pytest.mark.parametrize("case", _PROVIDER_CASES, ids=lambda case: case.name)
def test_provider_methods_are_async_and_parameter_names_match(
    case: ProviderParityCase,
) -> None:
    """Protocol·Fake·Real의 async 여부와 핵심 파라미터 이름이 같아야 한다."""

    for method_name, protocol_method in _public_protocol_methods(
        case.protocol
    ).items():
        fake_method = getattr(case.fake_class, method_name)
        real_method = getattr(case.real_class, method_name)

        assert inspect.iscoroutinefunction(protocol_method)
        assert inspect.iscoroutinefunction(fake_method)
        assert inspect.iscoroutinefunction(real_method)
        assert _parameter_names(fake_method) == _parameter_names(protocol_method)
        assert _parameter_names(real_method) == _parameter_names(protocol_method)


@pytest.mark.parametrize("mode", ["fake", "real"])
@pytest.mark.asyncio
async def test_common_provider_mode_switches_all_five_providers_together(
    mode: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """공통 PROVIDER_MODE 하나로 Provider가 함께 전환되어야 한다."""

    network_calls: list[httpx.Request] = []

    def reject_network(request: httpx.Request) -> httpx.Response:
        network_calls.append(request)
        raise AssertionError("Provider 생성 중에는 HTTP 요청을 보내면 안 됩니다.")

    _configure_global_settings(monkeypatch, common_mode=mode)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(reject_network)
    ) as client:
        providers = _create_all_providers(client)

    expected_classes = _FAKE_CLASSES if mode == "fake" else _REAL_CLASSES
    assert {
        name: type(provider) for name, provider in providers.items()
    } == expected_classes
    assert network_calls == []


@pytest.mark.parametrize("override_case", _PROVIDER_CASES, ids=lambda case: case.name)
@pytest.mark.asyncio
async def test_individual_provider_setting_overrides_common_mode(
    override_case: ProviderParityCase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """개별 Provider 설정은 공통 fake 설정보다 우선해야 한다."""

    _configure_global_settings(
        monkeypatch,
        common_mode="fake",
        overrides={override_case.setting_name: "real"},
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: (_ for _ in ()).throw(
                AssertionError(
                    f"Provider 생성 중 HTTP 요청이 발생했습니다: {request.method}"
                )
            )
        )
    ) as client:
        providers = _create_all_providers(client)

    for case in _PROVIDER_CASES:
        expected_class = (
            case.real_class if case.name == override_case.name else case.fake_class
        )
        assert isinstance(providers[case.name], expected_class)


def test_settings_resolve_modes_without_reading_env_file() -> None:
    """Settings 단위에서도 공통값과 개별 덮어쓰기 규칙을 유지해야 한다."""

    configured = Settings(
        _env_file=None,
        provider_mode="real",
        geocoding_provider="fake",
        weather_provider="fake",
        place_provider="fake",
        concentration_provider="fake",
        holiday_provider="fake",
    )

    assert configured.resolved_geocoding_provider == "fake"
    assert configured.resolved_weather_provider == "fake"
    assert configured.resolved_place_provider == "fake"
    assert configured.resolved_concentration_provider == "fake"
    assert configured.resolved_holiday_provider == "fake"


@pytest.mark.asyncio
async def test_real_factory_creation_does_not_expose_secrets_or_send_http(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Real 생성 과정은 네트워크를 사용하거나 비밀값을 출력하지 않아야 한다."""

    network_calls: list[httpx.Request] = []

    def reject_network(request: httpx.Request) -> httpx.Response:
        network_calls.append(request)
        raise AssertionError("Real Provider 생성 중 HTTP 요청이 발생했습니다.")

    _configure_global_settings(monkeypatch, common_mode="real")
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(reject_network)
    ) as client:
        _create_all_providers(client)

    output = capsys.readouterr()
    assert network_calls == []
    assert _SECRET not in output.out
    assert _SECRET not in output.err

    isolated_settings = Settings(
        _env_file=None,
        provider_mode="real",
        weather_api_key=_SECRET,
        tour_api_service_key=_SECRET,
        naver_map_client_id=_SECRET,
        naver_map_client_secret=_SECRET,
    )
    assert _SECRET not in repr(isolated_settings)
