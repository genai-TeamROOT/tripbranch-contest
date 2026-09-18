"""프롬프트를 Langfuse에서 읽어도 **레포 원문으로 언제든 돌아가는지** 검증한다.

이 스위트가 지키는 성질 넷.

1. **기본값이 꺼짐이다.** 켜기 전까지는 지금까지와 완전히 같은 경로로 읽는다.
2. **못 가져오면 디스크다.** Langfuse가 죽어도, 키가 없어도, 그 이름이 아직 없어도
   답변은 나가야 한다 — 프롬프트를 못 읽으면 서비스가 통째로 멈춘다.
3. **치환 엔진은 하나다.** 원격에서 가져온 원문도 `render_text()`가 치환한다.
   SDK의 `compile()`을 섞으면 폴백 경로와 정상 경로가 조용히 달라진다.
4. **과거 기준선은 디스크만 본다.** `TRIPBRANCH_PROMPT_VARIANT`는 "그 시점 원문으로
   돌려보기"라서 원격 최신본이 섞이면 기능 자체가 무의미해진다.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.config import Settings, settings
from app.observability import langfuse_prompts, langfuse_tracing
from app.prompts import loader

_ASSET = "_shared/rules/budget.md"


def _clear_variant_cache() -> None:
    # monkeypatch가 이 함수를 통째로 갈아끼우는 테스트가 있어 캐시가 없을 수 있다.
    # 정리 순서에 따라 아직 원복 전일 수 있으므로 있으면 비우고 없으면 넘어간다.
    clear = getattr(loader._variant_overrides, "cache_clear", None)
    if clear is not None:
        clear()


@pytest.fixture(autouse=True)
def _clean_state() -> Any:
    langfuse_tracing.shutdown()
    langfuse_prompts.reset_fallback_warnings()
    _clear_variant_cache()
    yield
    langfuse_tracing.shutdown()
    langfuse_prompts.reset_fallback_warnings()
    _clear_variant_cache()


class _FakePrompt:
    def __init__(self, text: str, *, is_fallback: bool = False) -> None:
        self.prompt = text
        self.is_fallback = is_fallback


class _FakeClient:
    """`get_prompt()`만 흉내낸다."""

    def __init__(self, result: Any = None, *, raises: bool = False) -> None:
        self.result = result
        self.raises = raises
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def shutdown(self) -> None:
        """`langfuse_tracing.shutdown()`이 정리하며 부른다."""

    def get_prompt(self, name: str, **kwargs: Any) -> Any:
        self.calls.append((name, kwargs))
        if self.raises:
            raise RuntimeError("전송 실패")
        return self.result


def _enable(monkeypatch: pytest.MonkeyPatch, client: Any) -> _FakeClient:
    monkeypatch.setattr(settings, "langfuse_prompts_enabled", True)
    monkeypatch.setattr(langfuse_tracing, "_client", client)
    monkeypatch.setattr(langfuse_tracing, "_client_failed", False)
    return client


# --- 1. 기본값이 꺼짐이다 -------------------------------------------------------


def test_prompt_management_defaults_to_off() -> None:
    """`.env` 없이 만든 Settings에서 꺼져 있어야 한다.

    이 테스트가 실패하면 기본값을 바꾼 것이다 — 프롬프트 원문을 외부에서 읽는 것이
    기본이 되면, 레포만 보고 "이 지침으로 돌고 있다"고 오판하게 된다.
    """
    assert Settings(_env_file=None).langfuse_prompts_enabled is False


def test_disabled_reads_from_disk_without_touching_langfuse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "langfuse_prompts_enabled", False)
    monkeypatch.setattr(settings, "langfuse_enabled", False)

    text = loader.load_text(_ASSET)

    assert text
    assert langfuse_tracing._client is None


# --- 2. 못 가져오면 디스크다 ----------------------------------------------------


def test_remote_text_replaces_the_disk_copy_when_it_arrives(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _enable(monkeypatch, _FakeClient(_FakePrompt("원격 예산 규칙")))

    assert loader.load_text(_ASSET) == "원격 예산 규칙"

    # 이름은 확장자만 뗀 것이어야 한다 — 폴더 구조가 화면에 그대로 보이도록.
    # calls[0]으로 찾지 않는다 — 첫 조회가 캐시 예열을 트리거해 43개가 먼저 들어온다.
    named = [kwargs for name, kwargs in client.calls if name == "_shared/rules/budget"]
    assert named, [name for name, _ in client.calls][:3]
    # 디스크 원문을 fallback으로 함께 넘겨야 SDK 안에서 실패해도 돌아갈 곳이 있다.
    assert named[-1]["fallback"] == _disk_text()


def test_first_lookup_warms_every_asset_in_one_go(monkeypatch: pytest.MonkeyPatch) -> None:
    """첫 조회가 자산 전체를 병렬로 데운다 — 안 하면 부팅이 +1.5초다(실측 2026-08-26).

    `gemini_prompts.py`가 모듈 수준에서 22개를 읽으므로 그게 전부 import 시점의 순차
    왕복이 된다. 예열을 넣어 1.76초 → 1.11초가 됐다.

    두 번째 조회에서 다시 데우면 안 된다 — 조회마다 43회를 또 도는 셈이 된다.
    """
    client = _enable(monkeypatch, _FakeClient(_FakePrompt("원격")))

    loader.load_text(_ASSET)
    after_first = len(client.calls)
    loader.load_text(_ASSET)

    assert after_first == len(loader.asset_paths()) + 1  # 예열 43 + 본 조회 1
    assert len(client.calls) == after_first + 1  # 두 번째는 본 조회 하나뿐


def test_fetch_failure_falls_back_to_disk(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(monkeypatch, _FakeClient(raises=True))

    assert loader.load_text(_ASSET) == _disk_text()


def test_sdk_fallback_marker_falls_back_to_disk(monkeypatch: pytest.MonkeyPatch) -> None:
    """SDK가 fallback으로 만든 객체를 돌려주면 그건 "켰는데 원격에 없다"는 뜻이다.

    표시를 무시하고 그대로 쓰면 값 자체는 같지만(우리가 준 fallback이므로) **켠 사람이
    원격에서 고쳐도 안 바뀌는 이유를 모른다.** 그래서 한 번은 경고를 남긴다.
    """
    _enable(monkeypatch, _FakeClient(_FakePrompt("무시돼야 한다", is_fallback=True)))

    assert loader.load_text(_ASSET) == _disk_text()


def test_non_text_prompt_falls_back_to_disk(monkeypatch: pytest.MonkeyPatch) -> None:
    """chat 프롬프트로 잘못 올리면 `prompt`가 문자열이 아니라 메시지 목록이다."""

    _enable(monkeypatch, _FakeClient(_FakePrompt([{"role": "system"}])))  # type: ignore[arg-type]

    assert loader.load_text(_ASSET) == _disk_text()


def test_fallback_warning_is_logged_once_per_name(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """프롬프트는 요청마다 여러 번 읽힌다 — 매번 찍으면 로그가 그것만 남는다."""

    _enable(monkeypatch, _FakeClient(raises=True))

    with caplog.at_level("WARNING"):
        for _ in range(5):
            loader.load_text(_ASSET)

    assert sum("디스크에서 읽는다" in record.message for record in caplog.records) == 1


# --- 3. 치환 엔진은 하나다 ------------------------------------------------------


def test_placeholders_are_left_for_render_text(monkeypatch: pytest.MonkeyPatch) -> None:
    """원격 원문의 `{{}}`는 그대로 와야 하고, 치환은 `render_text()`가 한다.

    SDK의 `compile()`을 쓰면 치환 엔진이 둘이 된다 — 폴백으로 디스크를 읽는 순간
    우리 엔진이 돌고, 정상 경로는 SDK가 돈다. 두 결과가 어긋나도 아무도 모른다.
    """
    _enable(monkeypatch, _FakeClient(_FakePrompt("기준일은 {{reference_date}}다.")))

    assert loader.load_text(_ASSET) == "기준일은 {{reference_date}}다."
    assert loader.render_text(_ASSET, reference_date="2026-08-26") == "기준일은 2026-08-26다."


# --- 4. 과거 기준선은 디스크만 본다 ---------------------------------------------


def test_variant_override_never_reads_remote(monkeypatch: pytest.MonkeyPatch) -> None:
    """기준선 비교는 레포가, 현행 운영은 Langfuse가 맡는다.

    원격 최신본이 섞이면 "이 커밋 시점의 원문으로 돌려보기"라는 기능 자체가 사라진다.
    """
    client = _enable(monkeypatch, _FakeClient(_FakePrompt("원격은 안 읽혀야 한다")))
    overridden = "recommend/extract.md"
    replacement = "recommend/archive/extract__legacy-2.3.0.md"
    monkeypatch.setattr(
        loader, "_variant_overrides", lambda _variant: {overridden: replacement}
    )
    monkeypatch.setenv(loader.PROMPT_VARIANT_ENV, "spike-baseline")

    text = loader.load_text(overridden)

    assert text == loader._safe_path(replacement).read_text(encoding="utf-8").strip()
    assert client.calls == []


def _disk_text() -> str:
    return loader._safe_path(_ASSET).read_text(encoding="utf-8").strip()


# --- 5. 테스트가 실수로 네트워크를 타지 않는다 ----------------------------------


def test_every_langfuse_switch_is_forced_off_in_tests() -> None:
    """`.env`가 무엇이든 테스트에서는 Langfuse 스위치가 전부 꺼져 있어야 한다.

    **스위치를 새로 추가하면서 여기를 빠뜨리면 스위트가 통째로 멈춘다.**
    2026-08-26에 `langfuse_prompts_enabled`가 그랬다 — `.env`에 true를 넣는 순간
    프롬프트를 읽는 모든 테스트가 실제 네트워크를 탔고, 6.8초짜리 스위트가 2분을
    넘겨도 안 끝났다. 프롬프트는 요청마다 여러 번 읽히기 때문이다.

    `langfuse_enabled` 하나만 끄면 안 된다는 것이 요점이다. 전송·원문·신원·프롬프트가
    **서로 다른 축**이라 하나를 꺼도 나머지가 안 꺼진다(`config.py`).

    이 테스트는 `conftest.py`의 autouse 픽스처가 적용된 상태에서 돈다 — 즉 여기서
    보이는 값이 곧 다른 모든 테스트가 보는 값이다.
    """
    switches = [
        name
        for name in type(settings).model_fields
        if name.startswith("langfuse_") and isinstance(getattr(settings, name), bool)
    ]

    assert switches, "langfuse_* 불리언 설정을 하나도 못 찾았다 — 이름 규칙이 바뀌었나"
    assert {name: getattr(settings, name) for name in switches} == dict.fromkeys(
        switches, False
    )
