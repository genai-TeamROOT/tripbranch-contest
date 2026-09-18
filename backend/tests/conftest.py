from __future__ import annotations

import os

import pytest

from app.config import settings


@pytest.fixture(autouse=True)
def isolate_regular_tests_from_real_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    """명시적 Smoke·Inspection 외 일반 테스트의 외부 호출을 차단한다."""
    if (
        os.getenv("RUN_REAL_PROVIDER_TESTS") == "true"
        or os.getenv("RUN_REAL_PROVIDER_INSPECTION") == "true"
    ):
        return

    monkeypatch.setattr(settings, "provider_mode", "fake")
    monkeypatch.setattr(settings, "llm_provider", None)
    monkeypatch.setattr(settings, "geocoding_provider", None)
    monkeypatch.setattr(settings, "local_search_provider", None)
    monkeypatch.setattr(settings, "weather_provider", None)
    monkeypatch.setattr(settings, "place_provider", None)
    monkeypatch.setattr(settings, "concentration_provider", None)
    monkeypatch.setattr(settings, "holiday_provider", None)

    # 경로 Provider도 provider_mode와 별개 축이다 — 비용 때문에 공통값을 상속하지
    # 않기 때문이다(config.py 주석). .env에 TRAVEL_ROUTE_*=real이 남아 있으면 일반
    # 테스트가 실 API를 타거나, 그 벤더 키가 없는 환경에서만 부팅 검증이 깨진다.
    monkeypatch.setattr(settings, "travel_route_provider", "fake")
    monkeypatch.setattr(settings, "travel_route_driving_provider", "fake")

    # Package B의 STATE_STORE_BACKEND는 provider_mode와 별개 축이라 위 patch로는
    # 안 잡힌다. .env에 STATE_STORE_BACKEND=supabase가 남아있으면(실사용 전환 중
    # 흔히 발생) 일반 테스트가 실제 네트워크를 타거나 InMemory 전용 메서드(clear()
    # 등)를 호출하는 테스트가 깨진다 — 여기서도 강제로 memory로 고정한다.
    monkeypatch.setattr(settings, "state_store_backend", "memory")

    # TASTE_EVIDENCE_ENABLED도 provider_mode와 별개 축이다. .env에 true가 남아
    # 있으면(실사용 전환 중 흔히 발생) 앱 lifespan을 켜는 테스트(with TestClient)가
    # 임베딩 모델 예열 스레드를 띄운다 — sentence-transformers import 4.3초 +
    # 500MB 적재가 나머지 테스트와 동시에 돌아 스위트가 느려지고, 스레드 타이밍
    # 때문에 무관한 테스트가 간헐적으로 깨진다(2026-08-20 실측). 취향 검색은
    # 별도 단위 테스트가 인코더를 직접 주입해 커버하므로 여기선 꺼도 무방하다.
    monkeypatch.setattr(settings, "taste_evidence_enabled", False)

    # PLACE_MOOD_ENABLED도 같은 이유로 끈다. 여기서 안 끄면 .env에 true가 남은
    # 개발 환경에서 앱 lifespan을 켜는 테스트가 SigLIP을 적재한다 — 취향 인코더보다
    # 무겁다(모델 적재 실측 44초). 사진 검색은 별도 단위 테스트가 Provider와
    # 인코더를 직접 주입해 커버하므로 여기선 꺼도 무방하다.
    monkeypatch.setattr(settings, "place_mood_enabled", False)
    monkeypatch.setattr(settings, "place_mood_warmup_enabled", False)

    # LANGFUSE_ENABLED도 provider_mode와 별개 축이다. .env에 true가 남아 있으면
    # 일반 테스트가 실제로 Langfuse Cloud에 span을 쏘고, 그 안에 테스트 픽스처
    # 발화가 실린다. 관측은 판정에 관여하지 않으므로 테스트에서는 항상 꺼둔다 —
    # 켜진 경로는 test_langfuse_tracing.py가 가짜 클라이언트로 따로 검증한다.
    monkeypatch.setattr(settings, "langfuse_enabled", False)
    monkeypatch.setattr(settings, "langfuse_capture_content", False)
    monkeypatch.setattr(settings, "langfuse_capture_user_id", False)

    # LANGFUSE_PROMPTS_ENABLED는 위 셋과도 또 다른 축이다 — 전송이 아니라 **프롬프트를
    # 어디서 읽느냐**다. 그래서 langfuse_enabled를 꺼도 이건 안 꺼진다.
    #
    # .env에 true가 남아 있으면 프롬프트를 읽는 모든 테스트가 실제 네트워크를 탄다.
    # 프롬프트는 요청마다 여러 번 읽히므로 스위트 전체가 사실상 멈춘다 —
    # 2026-08-26에 실제로 그렇게 됐다(6.8초 → 2분 넘게 미완료). 캐시가 있어도 첫
    # 조회가 43개를 예열하고, 프로세스가 아니라 테스트 단위로 설정이 오가면서
    # 예열이 반복된다.
    #
    # 켜진 경로는 test_langfuse_prompts.py가 가짜 클라이언트로 따로 검증한다.
    monkeypatch.setattr(settings, "langfuse_prompts_enabled", False)


@pytest.fixture(autouse=True)
def _reset_concentration_mapping_cache():
    """매핑 캐시는 프로세스 단위라 테스트 간 데이터가 새지 않도록 매번 비운다."""
    from app.agent_context.concentration_proxy import clear_concentration_mapping_cache

    clear_concentration_mapping_cache()
    yield
    clear_concentration_mapping_cache()
