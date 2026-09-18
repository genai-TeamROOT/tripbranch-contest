"""app.providers.factory의 Provider 생성 로직 회귀 테스트.

get_llm_provider()가 LLM 전용 타임아웃(resolved_llm_timeout_seconds)을 Tool/DB
쪽 EXTERNAL_API_TIMEOUT_SECONDS와 분리해서 RealGeminiProvider에 전달하는지 검증한다
(2026-08-11 — EXTERNAL_API_TIMEOUT_SECONDS를 Gemini 지연 대응으로 올리면 TourAPI/
Naver/Supabase까지 같은 값을 물려받는 문제로 분리).
"""

from __future__ import annotations

import logging

import pytest

from app.config import Settings
from app.domain.travel_route import TravelMode
from app.errors import AppError
from app.providers import factory
from app.providers.kakao_transit_route import FakeTransitRouteProvider
from app.providers.walking_route import FakeWalkingRouteProvider


def test_get_llm_provider_uses_dedicated_llm_timeout_when_set(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class _RecordingRealGeminiProvider:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(factory, "RealGeminiProvider", _RecordingRealGeminiProvider)
    monkeypatch.setattr(
        factory,
        "settings",
        Settings(
            _env_file=None,
            provider_mode="real",
            llm_api_key="present",
            external_api_timeout_seconds=10.0,
            llm_api_timeout_seconds=25.0,
        ),
    )

    factory.get_llm_provider()

    assert captured["timeout_seconds"] == 25.0
    assert captured["fast_model_names"] == ["gemini-3.5-flash-lite", "gemini-3.5-flash"]
    assert captured["generation_model_names"] == [
        "gemini-3.5-flash",
        "gemini-3.5-flash-lite",
    ]


def test_get_llm_provider_falls_back_to_external_api_timeout_when_unset(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class _RecordingRealGeminiProvider:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(factory, "RealGeminiProvider", _RecordingRealGeminiProvider)
    monkeypatch.setattr(
        factory,
        "settings",
        Settings(
            _env_file=None,
            provider_mode="real",
            llm_api_key="present",
            external_api_timeout_seconds=10.0,
        ),
    )

    factory.get_llm_provider()

    assert captured["timeout_seconds"] == 10.0


def test_get_gemini_audio_transcriber_uses_dedicated_default_model(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class _RecordingTranscriber:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(factory, "GeminiAudioTranscriber", _RecordingTranscriber)
    monkeypatch.setattr(
        factory,
        "settings",
        Settings(_env_file=None, provider_mode="real", llm_api_key="present"),
    )

    factory.get_gemini_audio_transcriber()

    assert captured["model_name"] == "gemini-3.5-flash-lite"


def test_get_gemini_audio_transcriber_requires_real_llm(monkeypatch, caplog) -> None:
    monkeypatch.setattr(factory, "settings", Settings(_env_file=None, provider_mode="fake"))

    with caplog.at_level(logging.WARNING), pytest.raises(AppError) as raised:
        factory.get_gemini_audio_transcriber()

    assert raised.value.code == "voice_input_unavailable"
    # 사용자에게는 지금 할 수 있는 일만 말한다. "Gemini 실연동 환경"은 사용자가
    # 무엇인지도 어떻게 바꾸는지도 알 수 없는 서버 설정이라, 원인은 로그로만 남긴다.
    assert "Gemini" not in raised.value.message
    assert "텍스트로 입력" in raised.value.message
    assert "real이 아닙니다" in caplog.text


def test_get_walking_route_provider_defaults_to_fake(monkeypatch) -> None:
    captured: dict[str, float] = {}

    class _RecordingFakeWalkingRouteProvider:
        def __init__(self, *, walking_speed_mps: float) -> None:
            captured["walking_speed_mps"] = walking_speed_mps

    monkeypatch.setattr(factory, "FakeWalkingRouteProvider", _RecordingFakeWalkingRouteProvider)
    monkeypatch.setattr(
        factory,
        "settings",
        Settings(_env_file=None, walking_speed_mps=1.1),
    )

    factory.get_walking_route_provider(object())  # type: ignore[arg-type]

    assert captured["walking_speed_mps"] == 1.1


def test_get_walking_route_provider_builds_real_provider(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class _RecordingRealKakaoWalkingRouteProvider:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    client = object()
    monkeypatch.setattr(
        factory,
        "RealKakaoWalkingRouteProvider",
        _RecordingRealKakaoWalkingRouteProvider,
    )
    monkeypatch.setattr(
        factory,
        "settings",
        Settings(
            _env_file=None,
            travel_route_provider="real",
            kakao_map_rest_api_key="test-key",
            external_api_timeout_seconds=7.0,
            travel_route_max_concurrency=4,
        ),
    )

    factory.get_walking_route_provider(client)  # type: ignore[arg-type]

    assert captured == {
        "api_key": "test-key",
        "client": client,
        "timeout_seconds": 7.0,
        "max_concurrency": 4,
        # 단독 호출은 공유 세마포어가 없다 — 그때는 Provider가 자기 몫만 제한한다.
        "semaphore": None,
    }


def _capture_travel_route_tool(monkeypatch, settings_override: Settings) -> dict[object, object]:
    walking_primary = object()
    driving_primary = object()
    transit_primary = object()
    captured: dict[object, object] = {}
    semaphores: dict[str, object] = {}

    class _RecordingTravelRouteTool:
        def __init__(self, providers: dict[object, object]) -> None:
            captured.update(providers)

    monkeypatch.setattr(factory, "TravelRouteTool", _RecordingTravelRouteTool)
    monkeypatch.setattr(
        factory,
        "get_walking_route_provider",
        lambda client, semaphore=None: (
            semaphores.setdefault("walking", semaphore),
            walking_primary,
        )[1],
    )
    monkeypatch.setattr(factory, "get_driving_route_provider", lambda client: driving_primary)
    monkeypatch.setattr(
        factory,
        "get_transit_route_provider",
        lambda client, semaphore=None: (
            semaphores.setdefault("transit", semaphore),
            transit_primary,
        )[1],
    )
    monkeypatch.setattr(factory, "settings", settings_override)

    factory.get_travel_route_tool(object())  # type: ignore[arg-type]
    captured["_semaphores"] = semaphores
    return captured


def test_get_travel_route_tool_adds_fallback_only_in_real_mode(monkeypatch) -> None:
    captured = _capture_travel_route_tool(
        monkeypatch,
        Settings(
            _env_file=None,
            travel_route_provider="real",
            walking_speed_mps=1.1,
        ),
    )

    # 도보·자동차·대중교통 셋을 등록한다. 미등록 이동수단은 Tool이 호출 없이
    # NO_DATA로 답하므로, 등록 누락은 조용한 오답이 아니라 값 없음으로 드러난다.
    assert [key for key in captured if isinstance(key, TravelMode)] == [
        TravelMode.WALKING,
        TravelMode.DRIVING,
        TravelMode.TRANSIT,
    ]
    walking = captured[TravelMode.WALKING]
    assert isinstance(walking.fallback, FakeWalkingRouteProvider)
    assert walking.fallback._walking_speed_mps == 1.1


def test_get_travel_route_tool_shares_one_semaphore_between_kakao_providers(
    monkeypatch,
) -> None:
    """도보와 대중교통은 같은 카카오 키를 쓰므로 동시 요청 한도를 함께 나눈다.

    따로 만들면 한 후보를 두 수단으로 조회할 때(D-118) 동시 요청이 5+5로 합산돼
    카카오가 `API limit has been exceeded.`를 낸다 — 2026-09-02 실측에서 40건 중
    대부분이 그렇게 거절됐다.
    """
    captured = _capture_travel_route_tool(
        monkeypatch,
        Settings(_env_file=None, travel_route_max_concurrency=3),
    )

    semaphores = captured["_semaphores"]
    assert semaphores["walking"] is not None
    assert semaphores["walking"] is semaphores["transit"]


def test_get_travel_route_tool_gives_driving_no_fallback(monkeypatch) -> None:
    """자동차에는 직선거리 fallback을 두지 않는다.

    fallback이 내는 STRAIGHT_LINE_ESTIMATE는 채점에서 걸러지므로
    (scoring._applied_travel_route) 만들어도 쓰이지 않는다.
    """
    captured = _capture_travel_route_tool(
        monkeypatch,
        Settings(
            _env_file=None,
            travel_route_provider="real",
            travel_route_driving_provider="real",
        ),
    )

    assert captured[TravelMode.DRIVING].fallback is None


def test_get_travel_route_tool_gives_transit_no_fallback(monkeypatch) -> None:
    """대중교통도 자동차와 같은 이유로 fallback을 두지 않는다."""
    captured = _capture_travel_route_tool(
        monkeypatch,
        Settings(
            _env_file=None,
            travel_route_provider="real",
            travel_route_transit_provider="real",
        ),
    )

    assert captured[TravelMode.TRANSIT].fallback is None


def test_get_travel_route_tool_keeps_transit_fake_unless_explicitly_enabled(monkeypatch) -> None:
    """TRAVEL_ROUTE_PROVIDER=real만으로는 대중교통이 켜지지 않는다.

    도보와 같은 카카오 키를 쓰지만 엔드포인트가 달라 호출량도 따로 늘어난다.
    벤더가 같다고 묶으면 도보만 쓰려던 설정이 대중교통까지 호출하게 된다.
    """
    settings_override = Settings(_env_file=None, travel_route_provider="real")

    assert settings_override.travel_route_provider == "real"
    assert settings_override.travel_route_transit_provider == "fake"

    monkeypatch.setattr(factory, "settings", settings_override)
    assert isinstance(
        factory.get_transit_route_provider(object()),  # type: ignore[arg-type]
        FakeTransitRouteProvider,
    )


def test_get_travel_route_tool_keeps_driving_fake_unless_explicitly_enabled(monkeypatch) -> None:
    """TRAVEL_ROUTE_PROVIDER=real만으로는 자동차(네이버)가 켜지지 않는다.

    이동수단마다 벤더가 달라서, 한 값이 여러 벤더를 켜면 카카오 키만 가진 설정이
    쓰지도 않는 네이버 키를 요구하며 부팅에 실패한다.
    """
    settings_override = Settings(_env_file=None, travel_route_provider="real")

    assert settings_override.travel_route_provider == "real"
    assert settings_override.travel_route_driving_provider == "fake"


class Test휴무_추출기_생성:
    """적재 배치에 LLM 휴무 추출기를 넘길지 정하는 규칙. (TP-231)

    None을 돌려주는 것은 기능이 하나 빠질 뿐 적재는 정상이라는 뜻이다. 그래서
    어느 경우에도 예외를 던져 부팅을 막지 않는다.
    """

    @staticmethod
    def _settings(**overrides: object) -> Settings:
        base: dict[str, object] = {
            "_env_file": None,
            "provider_mode": "real",
            "llm_api_key": "present",
        }
        base.update(overrides)
        return Settings(**base)

    def test_스위치를_끄면_안_만든다(self, monkeypatch) -> None:
        monkeypatch.setattr(
            factory, "settings", self._settings(closure_extract_enabled=False)
        )
        assert factory.get_closure_extractor() is None

    def test_fake_LLM이면_안_만든다(self, monkeypatch, caplog) -> None:
        """가짜 휴무가 DB에 저장되면 끄고 다시 켜도 남는다 (D-042).

        왜 껐는지를 로그로 확인한다 — 켠 줄 알았는데 안 도는 상황에서
        `.env`를 볼지 키를 볼지가 이 한 줄로 갈린다.
        """
        monkeypatch.setattr(
            factory, "settings", self._settings(provider_mode="fake", llm_api_key="")
        )
        with caplog.at_level(logging.WARNING, logger=factory.logger.name):
            assert factory.get_closure_extractor() is None
        assert "fake 모드" in caplog.text

    def test_키가_없으면_안_만든다(self, monkeypatch) -> None:
        monkeypatch.setattr(factory, "settings", self._settings(llm_api_key=""))
        assert factory.get_closure_extractor() is None

    def test_켜져_있으면_실제_Gemini를_만든다(self, monkeypatch) -> None:
        monkeypatch.setattr(factory, "settings", self._settings())
        extractor = factory.get_closure_extractor()
        assert isinstance(extractor, factory.RealGeminiProvider)
        assert hasattr(extractor, "extract_closure_rules")

    def test_기본값은_켜짐이다(self) -> None:
        """끄면 주차가 섞인 휴무 399곳이 지금처럼 안 읽힌 채 남는다."""
        assert Settings(_env_file=None).closure_extract_enabled is True

    def test_휴무_추출을_모르는_구현이면_안_넘긴다(self, monkeypatch) -> None:
        """LLMProvider 규약에는 휴무 추출이 없다 — 넘기기 전에 확인한다."""

        class _NoClosure:
            pass

        monkeypatch.setattr(factory, "settings", self._settings())
        monkeypatch.setattr(factory, "get_llm_provider", lambda: _NoClosure())
        assert factory.get_closure_extractor() is None
