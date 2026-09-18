import logging
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.providers.factory import validate_provider_config
from app.recommendation_limits import MAX_RECOMMENDATION_CANDIDATE_LIMIT


def test_provider_mode_applies_to_all_providers() -> None:
    settings = Settings(_env_file=None, provider_mode="real")

    assert settings.resolved_geocoding_provider == "real"
    assert settings.resolved_weather_provider == "real"
    assert settings.resolved_place_provider == "real"
    assert settings.resolved_concentration_provider == "real"
    assert settings.resolved_holiday_provider == "real"
    assert settings.resolved_llm_provider == "real"


def test_individual_provider_overrides_common_mode() -> None:
    settings = Settings(
        _env_file=None,
        provider_mode="real",
        place_provider="fake",
        concentration_provider="fake",
        holiday_provider="fake",
        llm_provider="fake",
    )

    assert settings.resolved_geocoding_provider == "real"
    assert settings.resolved_weather_provider == "real"
    assert settings.resolved_place_provider == "fake"
    assert settings.resolved_concentration_provider == "fake"
    assert settings.resolved_holiday_provider == "fake"
    assert settings.resolved_llm_provider == "fake"


def test_travel_route_provider_stays_fake_until_explicitly_enabled() -> None:
    settings = Settings(_env_file=None, provider_mode="real")

    assert settings.travel_route_provider == "fake"

    enabled = Settings(
        _env_file=None,
        travel_route_provider="real",
        kakao_map_rest_api_key="test-rest-api-key",
    )

    assert enabled.travel_route_provider == "real"
    assert enabled.kakao_map_rest_api_key == "test-rest-api-key"


def test_walking_speed_is_configurable_and_must_be_positive() -> None:
    settings = Settings(_env_file=None, walking_speed_mps=1.0)

    assert settings.walking_speed_mps == 1.0

    with pytest.raises(ValidationError):
        Settings(_env_file=None, walking_speed_mps=0)


def test_schedule_walk_transfer_threshold_is_bounded() -> None:
    settings = Settings(_env_file=None, schedule_walk_transfer_threshold_min=25)

    assert settings.schedule_walk_transfer_threshold_min == 25

    with pytest.raises(ValidationError):
        Settings(_env_file=None, schedule_walk_transfer_threshold_min=0)

    with pytest.raises(ValidationError):
        Settings(_env_file=None, schedule_walk_transfer_threshold_min=121)


def test_schedule_max_measured_segments_is_bounded() -> None:
    settings = Settings(_env_file=None, schedule_max_measured_segments=4)

    assert settings.schedule_max_measured_segments == 4

    with pytest.raises(ValidationError):
        Settings(_env_file=None, schedule_max_measured_segments=0)

    with pytest.raises(ValidationError):
        Settings(_env_file=None, schedule_max_measured_segments=51)


def test_validate_provider_config_requires_kakao_key_for_real_walking_route() -> None:
    with pytest.raises(ValueError, match="KAKAO_MAP_REST_API_KEY"):
        validate_provider_config(Settings(_env_file=None, travel_route_provider="real"))

    validate_provider_config(
        Settings(
            _env_file=None,
            travel_route_provider="real",
            kakao_map_rest_api_key="present",
        )
    )


def test_resolved_llm_timeout_falls_back_to_external_api_timeout() -> None:
    """LLM_API_TIMEOUT_SECONDS 미설정 시 EXTERNAL_API_TIMEOUT_SECONDS를 그대로 쓴다
    (하위 호환 — 기존에 EXTERNAL_API_TIMEOUT_SECONDS만 설정해 쓰던 환경도 그대로
    동작해야 한다)."""
    settings = Settings(_env_file=None, external_api_timeout_seconds=25.0)

    assert settings.llm_api_timeout_seconds is None
    assert settings.resolved_llm_timeout_seconds == 25.0


def test_resolved_llm_timeout_uses_dedicated_value_when_set() -> None:
    """LLM_API_TIMEOUT_SECONDS를 설정하면 EXTERNAL_API_TIMEOUT_SECONDS와 분리된다
    (2026-08-11 — Gemini 지연 대응으로 EXTERNAL_API_TIMEOUT_SECONDS를 올리면
    TourAPI/Naver/Supabase까지 같은 값을 물려받는 문제로 분리)."""
    settings = Settings(
        _env_file=None,
        external_api_timeout_seconds=10.0,
        llm_api_timeout_seconds=25.0,
    )

    assert settings.resolved_llm_timeout_seconds == 25.0
    assert settings.external_api_timeout_seconds == 10.0


def test_resolved_role_models_default_to_single_primary_model() -> None:
    """폴백 미설정 시 1순위 모델 하나짜리 리스트."""
    settings = Settings(
        _env_file=None,
        llm_fast_model_name="gemini-3.5-flash-lite",
        llm_fast_fallback_model_names="",
    )

    assert settings.resolved_llm_fast_models == ["gemini-3.5-flash-lite"]


def test_resolved_role_models_append_fallbacks_in_order() -> None:
    settings = Settings(
        _env_file=None,
        llm_generation_model_name="gemini-3.5-flash",
        llm_generation_fallback_model_names="gemini-3.5-flash-lite, gemini-3.1-flash-lite",
    )

    assert settings.resolved_llm_generation_models == [
        "gemini-3.5-flash",
        "gemini-3.5-flash-lite",
        "gemini-3.1-flash-lite",
    ]


def test_resolved_role_models_drop_blank_entries() -> None:
    """트레일링 콤마·빈 항목은 방어적으로 무시한다."""
    settings = Settings(
        _env_file=None,
        llm_fast_model_name="gemini-3.5-flash-lite",
        llm_fast_fallback_model_names="gemini-3.5-flash,,  ,",
    )

    assert settings.resolved_llm_fast_models == ["gemini-3.5-flash-lite", "gemini-3.5-flash"]


def test_resolved_role_based_llm_models_use_independent_routes() -> None:
    settings = Settings(
        _env_file=None,
        llm_fast_model_name="gemini-3.5-flash-lite",
        llm_fast_fallback_model_names="gemini-3.5-flash",
        llm_generation_model_name="gemini-3.5-flash",
        llm_generation_fallback_model_names="gemini-3.5-flash-lite",
    )

    assert settings.resolved_llm_fast_models == [
        "gemini-3.5-flash-lite",
        "gemini-3.5-flash",
    ]
    assert settings.resolved_llm_generation_models == [
        "gemini-3.5-flash",
        "gemini-3.5-flash-lite",
    ]
    assert settings.resolved_gemini_audio_model_name == "gemini-3.5-flash-lite"


def test_mode_judge_default_is_paired_with_prompt_1_1_0() -> None:
    """기본값은 gemini-3.1-flash-lite다. 프롬프트 mode_judge.select 1.1.0과 짝이다.

    1.1.0은 이 모델로 잰 문장이라, 기본값이 generation(3.5-flash)으로 돌아가면 레포
    프롬프트를 읽는 환경에서 추천 판정의 흔들림이 늘어난다(config.py 주석). 기본값을
    바꿀 때는 이 테스트와 함께 프롬프트 HISTORY.md의 측정을 다시 본다.
    """
    settings = Settings(_env_file=None)

    assert settings.resolved_mode_judge_models[0] == "gemini-3.1-flash-lite"


def test_mode_judge_models_follow_generation_when_unset() -> None:
    """MODE_JUDGE_MODEL_NAME을 비우면 판정은 generation 묶음을 그대로 쓴다.

    폴백만 적어 둔 채 주 모델을 비우면 폴백도 무시한다. 주 모델 없이 폴백만 바뀌는 상태는
    뜻이 없다.
    """
    for name in (None, ""):
        settings = Settings(
            _env_file=None,
            llm_generation_model_name="gemini-3.5-flash",
            llm_generation_fallback_model_names="gemini-3.5-flash-lite",
            mode_judge_model_name=name,
            mode_judge_fallback_model_names="gemini-3.1-flash-lite",
        )

        assert settings.resolved_mode_judge_models == [
            "gemini-3.5-flash",
            "gemini-3.5-flash-lite",
        ]


def test_mode_judge_models_fall_back_to_generation_chain_without_duplicates() -> None:
    """폴백을 안 정하면 generation 묶음 전체가 폴백이 되고, 주 모델과 겹치는 이름은 빠진다."""
    settings = Settings(
        _env_file=None,
        llm_generation_model_name="gemini-3.5-flash",
        llm_generation_fallback_model_names="gemini-3.1-flash-lite",
        mode_judge_model_name="gemini-3.1-flash-lite",
    )

    assert settings.resolved_mode_judge_models == ["gemini-3.1-flash-lite", "gemini-3.5-flash"]


def test_mode_judge_explicit_fallbacks_replace_generation_chain() -> None:
    settings = Settings(
        _env_file=None,
        mode_judge_model_name="gemini-3.1-flash-lite",
        mode_judge_fallback_model_names="gemini-3.5-flash-lite",
    )

    assert settings.resolved_mode_judge_models == ["gemini-3.1-flash-lite", "gemini-3.5-flash-lite"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"provider_mode": "Real"},
        {"provider_mode": "stub"},
        {"provider_mode": "ral"},
        {"place_provider": "reall"},
    ],
)
def test_invalid_provider_settings_fail_at_construction(overrides: dict[str, str]) -> None:
    """오타/옛 이름은 Settings 생성 시점(=프로세스 시작)에 즉시 실패해야 한다."""
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **overrides)


def test_fake_weather_settings_hold_kma_codes() -> None:
    """D-051: 설정도 판정(good/neutral/bad)이 아니라 기상청 코드를 받는다.

    이 값이 실제로 fake의 사실을 움직이는지는
    `test_provider_contracts.py::test_fake_weather_provider_emits_facts_d_can_judge`가
    확인한다.
    """
    settings = Settings(
        _env_file=None,
        fake_weather_sky_code="4",
        fake_weather_precipitation_type="1",
    )

    assert settings.fake_weather_sky_code == "4"
    assert settings.fake_weather_precipitation_type == "1"


@pytest.mark.parametrize(
    "overrides",
    [
        {"recommendation_result_limit": 0},
        # 상한 초과. 상한 값을 바꿔도 따라오도록 상수로 쓴다 — 21을 박아두었더니
        # 상한을 20에서 30으로 올릴 때 이 케이스가 유효값이 되어 깨졌다.
        {"recommendation_result_limit": MAX_RECOMMENDATION_CANDIDATE_LIMIT + 1},
        {"recommendation_candidate_limit": 0},
        {"recommendation_candidate_limit": MAX_RECOMMENDATION_CANDIDATE_LIMIT + 1},
        {
            "recommendation_result_limit": 6,
            "recommendation_candidate_limit": 5,
        },
    ],
)
def test_invalid_recommendation_limits_fail_at_construction(
    overrides: dict[str, int],
) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **overrides)


def test_validate_provider_config_allows_fake_mode_without_keys() -> None:
    validate_provider_config(Settings(_env_file=None, provider_mode="fake"))


def _real_settings(**overrides: object) -> Settings:
    """자격증명 검사를 통과하는 real 설정을 만든다."""
    keys: dict[str, object] = {
        "llm_api_key": "present",
        "weather_api_key": "present",
        "tour_api_service_key": "present",
        "seoul_open_data_api_key": "present",
        "kakao_map_rest_api_key": "present",
        "naver_map_client_id": "present",
        "naver_map_client_secret": "present",
        "naver_local_search_client_id": "present",
        "naver_local_search_client_secret": "present",
        "supabase_url": "https://example.supabase.co",
        "supabase_secret_key": "present",
    }
    keys.update(overrides)
    return Settings(_env_file=None, provider_mode="real", **keys)


def test_tour_api_details_rejects_high_candidate_limit_at_boot() -> None:
    """상세를 TourAPI로 받으면서 후보 한도를 높게 잡으면 부팅에서 막는다.

    이 조합은 오류를 내지 않고 일일 한도만 태운다 — 추천 1회에 후보 30곳이면
    detailCommon2 + detailIntro2가 60회 나가서 33요청 만에 소진된다. 조용히
    도는 대신 부팅에서 끊는다(D-042와 같은 이유).
    """
    settings = _real_settings(
        place_details_source="tour_api", recommendation_candidate_limit=30
    )

    with pytest.raises(ValueError) as error:
        validate_provider_config(settings)

    message = str(error.value)
    assert "PLACE_DETAILS_SOURCE" in message
    assert "RECOMMENDATION_CANDIDATE_LIMIT" in message


def test_tour_api_details_allows_low_candidate_limit() -> None:
    """후보 한도가 낮으면 tour_api 상세도 그대로 허용한다."""
    validate_provider_config(
        _real_settings(
            place_details_source="tour_api", recommendation_candidate_limit=10
        )
    )


def test_supabase_details_allows_high_candidate_limit() -> None:
    """supabase 상세는 후보 수와 무관하게 배치 1회라 한도를 막지 않는다."""
    validate_provider_config(
        _real_settings(
            place_details_source="supabase", recommendation_candidate_limit=30
        )
    )


def test_fake_place_mode_skips_details_source_check() -> None:
    """fake 장소 모드는 상세도 Fake가 담당하므로 이 검사 대상이 아니다."""
    validate_provider_config(
        Settings(
            _env_file=None,
            provider_mode="fake",
            place_details_source="tour_api",
            recommendation_candidate_limit=30,
        )
    )


def test_validate_provider_config_reports_every_missing_key_at_once() -> None:
    settings = Settings(_env_file=None, provider_mode="real")

    with pytest.raises(ValueError) as error:
        validate_provider_config(settings)

    message = str(error.value)
    for variable_name in (
        "LLM_API_KEY",
        "WEATHER_API_KEY",
        "TOUR_API_SERVICE_KEY",
        "NAVER_MAP_CLIENT_ID",
        "NAVER_MAP_CLIENT_SECRET",
    ):
        assert variable_name in message
    # 세 provider가 공유하는 키는 한 번만 나열한다.
    assert message.count("TOUR_API_SERVICE_KEY") == 1


def test_validate_provider_config_rejects_legacy_llm_model_settings() -> None:
    """폐지된 단일 모델 설정이 .env에 남아 있으면 부팅에서 막는다.

    이 값들은 팩토리가 더 이상 읽지 않아 남아 있어도 서버는 뜬다. 그러면 .env에
    적힌 모델과 실제로 호출되는 모델이 다른 채로 돌고, 그 차이는 응답이 이상해진
    뒤에야 드러난다. 조용히 무시하는 대신 부팅에서 실패시킨다(D-042).
    """
    settings = Settings(
        _env_file=None,
        provider_mode="fake",
        legacy_llm_model_name="gemini-2.5-flash",
        legacy_llm_fallback_model_names="gemini-2.5-flash-lite",
    )

    with pytest.raises(ValueError) as error:
        validate_provider_config(settings)

    message = str(error.value)
    assert "LLM_MODEL_NAME" in message
    assert "LLM_FALLBACK_MODEL_NAMES" in message
    # 어디로 옮겨야 하는지까지 알려 준다.
    assert "LLM_FAST_MODEL_NAME" in message
    assert "LLM_GENERATION_MODEL_NAME" in message


def test_validate_provider_config_allows_absent_legacy_llm_model_settings() -> None:
    """역할별 설정만 있는 .env는 그대로 통과한다."""
    settings = Settings(
        _env_file=None,
        provider_mode="fake",
        llm_fast_model_name="gemini-3.5-flash-lite",
        llm_generation_model_name="gemini-3.5-flash",
    )

    validate_provider_config(settings)


def test_validate_provider_config_ignores_keys_for_fake_providers() -> None:
    settings = Settings(
        _env_file=None,
        provider_mode="fake",
        weather_provider="real",
        weather_api_key="present",
    )

    validate_provider_config(settings)


def test_validate_provider_config_flags_only_the_real_provider() -> None:
    settings = Settings(_env_file=None, provider_mode="fake", llm_provider="real")

    with pytest.raises(ValueError) as error:
        validate_provider_config(settings)

    message = str(error.value)
    assert "LLM_API_KEY" in message
    assert "WEATHER_API_KEY" not in message


def test_validate_provider_config_rejects_duplicate_fallback_model() -> None:
    """역할별 폴백에 1순위와 같은 모델이 들어가면 부팅 시 막는다."""
    settings = Settings(
        _env_file=None,
        provider_mode="real",
        llm_api_key="present",
        weather_api_key="present",
        tour_api_service_key="present",
        naver_map_client_id="present",
        naver_map_client_secret="present",
        naver_local_search_client_id="present",
        naver_local_search_client_secret="present",
        llm_fast_model_name="gemini-3.5-flash-lite",
        llm_fast_fallback_model_names="gemini-3.5-flash,gemini-3.5-flash-lite",
    )

    with pytest.raises(ValueError) as error:
        validate_provider_config(settings)

    assert "gemini-3.5-flash-lite" in str(error.value)


def test_validate_provider_config_allows_distinct_fallback_models() -> None:
    # PLACE_DETAILS_SOURCE 기본값이 supabase가 되면서 real 모드는 Supabase 자격증명도
    # 요구한다. 이 테스트는 모델 폴백만 보는 것이라 _real_settings로 그 부분을 채운다.
    settings = _real_settings(
        llm_fast_model_name="gemini-3.5-flash-lite",
        llm_fast_fallback_model_names="gemini-3.5-flash",
        llm_generation_model_name="gemini-3.5-flash",
        llm_generation_fallback_model_names="gemini-3.5-flash-lite",
    )

    validate_provider_config(settings)


def test_env_file_is_resolved_relative_to_backend_package() -> None:
    """실행 위치가 아니라 backend/.env를 절대경로로 가리켜야 한다.

    상대경로면 저장소 루트에서 서버를 띄웠을 때 .env를 읽지 못하고 오류 없이
    전 Provider가 fake로 뜬다(npm run dev가 그렇게 실행되던 회귀).
    """
    import app.config as config_module

    env_file = Path(config_module.Settings.model_config["env_file"])

    assert env_file.is_absolute()
    assert env_file.name == ".env"
    assert env_file.parent == Path(config_module.__file__).resolve().parent.parent


def test_boot_log_includes_travel_route_mode(caplog: pytest.LogCaptureFixture) -> None:
    """부팅 로그가 도보 provider 모드를 빠뜨리면 fake로 뜬 걸 알아챌 수 없다(D-042)."""
    from app.main import _log_provider_modes

    # main.py는 uvicorn 로그와 같은 자리에 찍히도록 "uvicorn.error"를 쓴다.
    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        _log_provider_modes()

    messages = [record.getMessage() for record in caplog.records]
    assert any("travel_route=" in message for message in messages)
