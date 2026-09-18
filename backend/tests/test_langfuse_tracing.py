"""Langfuse 관측 층이 **켜기 전까지 아무 일도 안 하고, 켜도 응답을 못 막는지** 검증한다.

이 스위트가 지키는 성질 세 가지.

1. **기본값이 꺼짐이다.** 지금은 실사용자가 없어(로컬 개발만) 나가는 게 팀원 자기
   발화뿐이지만, 그 조건에서 정한 기본값이 배포 이후까지 살아남으면 남의 발화가
   외부로 나간다. 문서에 "나중에 다시 보자"고 적는 건 안 지켜지므로 여기서 잠근다
   (package_D/[계획] Langfuse 도입 §6.3).
2. **관측 실패가 사용자 응답을 막지 않는다.** 반대로 **호출부의 예외는 삼키지
   않는다** — 관측이 먹어도 되는 건 자기 실패뿐이다.
3. **원문 마스킹이 한 곳에서 걸린다.** 2단계에서 붙일 LangChain CallbackHandler는
   노드 입출력을 자동 수집하므로, 호출부마다 가리는 방식으로는 새 나간다.
"""

from __future__ import annotations

import builtins
import inspect
import pathlib
from typing import Any

import pytest

from app.config import Settings, settings
from app.observability import langfuse_tracing
from app.observability.langfuse_tracing import (
    REDACTED,
    captures_content,
    is_enabled,
    observe_generation,
    observe_step,
    trace_attributes,
    validate_langfuse_config,
)


@pytest.fixture(autouse=True)
def _clear_client_cache() -> Any:
    """클라이언트 캐시는 프로세스 단위라 테스트 간에 새지 않도록 매번 비운다."""
    langfuse_tracing.shutdown()
    yield
    langfuse_tracing.shutdown()


def _enable(monkeypatch: pytest.MonkeyPatch, *, capture_content: bool = False) -> None:
    monkeypatch.setattr(settings, "langfuse_enabled", True)
    monkeypatch.setattr(settings, "langfuse_capture_content", capture_content)


class _FakeSpan:
    def __init__(self) -> None:
        self.updates: list[dict[str, Any]] = []

    def update(self, **fields: Any) -> None:
        self.updates.append(fields)


class _FakeObservation:
    """`start_as_current_observation()`이 돌려주는 context manager 흉내."""

    def __init__(self, span: _FakeSpan, *, fail_on_exit: bool = False) -> None:
        self._span = span
        self._fail_on_exit = fail_on_exit
        self.exited_with: type[BaseException] | None = None

    def __enter__(self) -> _FakeSpan:
        return self._span

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        self.exited_with = exc_type
        if self._fail_on_exit:
            raise RuntimeError("전송 실패")
        return False


class _FakeClient:
    def __init__(self, span: _FakeSpan | None = None, **kwargs: Any) -> None:
        self.span = span or _FakeSpan()
        self.kwargs = kwargs
        self.observations: list[_FakeObservation] = []

    def start_as_current_observation(self, **_: Any) -> _FakeObservation:
        observation = _FakeObservation(self.span, **self.kwargs)
        self.observations.append(observation)
        return observation


def _install(monkeypatch: pytest.MonkeyPatch, client: Any) -> None:
    monkeypatch.setattr(langfuse_tracing, "_client", client)
    monkeypatch.setattr(langfuse_tracing, "_client_failed", False)


# --- 1. 기본값이 꺼짐이다 (설계 잠금) ------------------------------------------


def test_both_switches_default_to_off() -> None:
    """`.env` 없이 만든 Settings에서 둘 다 꺼져 있어야 한다.

    **이 테스트가 실패하면 기본값을 바꾼 것이다.** 배포 환경에서 남의 발화가
    외부로 나가기 시작하므로, 통과시키기 전에 §6.3을 다시 읽어야 한다.
    """
    fresh = Settings(_env_file=None)

    assert fresh.langfuse_enabled is False
    assert fresh.langfuse_capture_content is False


def test_content_capture_stays_off_even_if_only_it_is_turned_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """전송이 꺼져 있으면 원문 수집도 꺼진 것으로 본다 — 두 값이 어긋나지 않게."""
    monkeypatch.setattr(settings, "langfuse_enabled", False)
    monkeypatch.setattr(settings, "langfuse_capture_content", True)

    assert is_enabled() is False
    assert captures_content() is False


def test_public_surface_has_no_tool_argument_helper() -> None:
    """Tool 인자·응답을 계측하는 헬퍼는 **의도적으로 없다.**

    사용자 좌표(장소 검색·경로 조회)와 외부 API 자격증명이 그 경로로만 흐른다.
    헬퍼가 없으면 실수로도 못 쓴다. 이 목록을 늘리려면 §6.3을 먼저 읽어야 한다.

    `current_trace_id`는 그 규칙의 예외가 아니라 **방향이 반대**라서 들어와 있다.
    나머지 헬퍼는 우리 값을 Langfuse로 내보내지만 이건 Langfuse가 만든 식별자를
    우리 응답으로 되받는다 — 사용자 입력에서 유도되지 않는 hex 문자열 하나다.

    `incoming_trace_context`·`honors_incoming_trace_context`도 내보내는 헬퍼가
    아니다. 들어온 `traceparent` 헤더를 부모 문맥으로 받을지 정하는 자리라, 값이
    나가는 통로가 아니라 **들어오는 문맥을 받는 통로**다.
    """
    assert set(langfuse_tracing.__all__) == {
        "REDACTED",
        "captures_content",
        "current_trace_id",
        "get_prompt_client",
        "honors_incoming_trace_context",
        "incoming_trace_context",
        "is_enabled",
        "observe_generation",
        "observe_step",
        "record_score",
        "shutdown",
        "trace_attributes",
        "validate_langfuse_config",
    }


def test_prompt_client_is_the_only_raw_client_accessor() -> None:
    """`get_prompt_client()`는 클라이언트를 그대로 내준다 — 쓰는 곳을 하나로 묶어 둔다.

    위 목록 가드가 막으려는 건 "원문을 실어 보내는 헬퍼가 늘어나는 것"이다. 클라이언트를
    그대로 돌려주는 접근자는 그 가드를 우회할 수 있으므로, **쓰는 모듈이 하나뿐인지**를
    여기서 잠근다. 늘리려면 왜 langfuse_prompts로 안 되는지 답할 수 있어야 한다.
    """
    root = pathlib.Path(langfuse_tracing.__file__).resolve().parents[2]
    users = sorted(
        path.relative_to(root).as_posix()
        for path in root.joinpath("app").rglob("*.py")
        if "get_prompt_client" in path.read_text(encoding="utf-8")
        and path.name != "langfuse_tracing.py"
    )

    assert users == ["app/observability/langfuse_prompts.py"]


def test_record_score_takes_no_free_text() -> None:
    """Score는 mask 훅을 타지 않으므로 **자유 텍스트 인자를 두지 않는다.**

    `create_score`에는 마스킹이 아예 없다(SDK 4.14.5). `comment`나 `metadata`를
    열어두면 `capture_content=false`인 배포에서도 발화가 그대로 나갈 수 있다.
    위 목록 가드와 짝이다 — 이름을 늘리는 것만 막아서는 부족하고, 이미 있는 헬퍼가
    원문 통로가 되는 것도 막아야 한다.
    """
    parameters = inspect.signature(langfuse_tracing.record_score).parameters
    assert set(parameters) == {"name", "value"}
    assert parameters["value"].annotation == "float | bool"


# --- 2. 꺼져 있으면 아무 일도 안 한다 -------------------------------------------


def test_disabled_never_creates_a_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """클라이언트 생성이 OpenTelemetry provider를 세팅하므로 꺼져 있으면 만들지 않는다."""
    monkeypatch.setattr(settings, "langfuse_enabled", False)

    with observe_step("scoring"), observe_generation("llm_interpret"), trace_attributes():
        pass

    assert langfuse_tracing._client is None


def test_disabled_helpers_hand_back_a_no_op_recorder(monkeypatch: pytest.MonkeyPatch) -> None:
    """호출부가 켜짐/꺼짐으로 분기하지 않아도 되게 항상 같은 모양을 준다."""
    monkeypatch.setattr(settings, "langfuse_enabled", False)

    with observe_step("scoring") as step:
        step.record(output="무시된다", usage_details={"input_tokens": 1})
    with observe_generation("llm_interpret", model="gemini") as generation:
        generation.record(output="무시된다")


# --- 3. 마스킹 -----------------------------------------------------------------


def test_mask_redacts_everything_when_content_capture_is_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`capture_content`가 꺼져 있으면 mask에 들어온 값을 전부 치환한다.

    SDK가 mask 함수에 어느 필드인지 안 알려주므로(`_mask_attribute`) "입력만 가리고
    메타데이터는 남긴다"가 불가능하다. 모르면 막는 쪽으로 둔다.
    """
    _enable(monkeypatch, capture_content=False)

    assert langfuse_tracing._mask(data="경복궁 근처 조용한 카페") == REDACTED
    assert langfuse_tracing._mask(data={"gps": [37.5796, 126.977]}) == REDACTED
    assert langfuse_tracing._mask(data=None) == REDACTED


def test_mask_passes_data_through_when_content_capture_is_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable(monkeypatch, capture_content=True)

    assert langfuse_tracing._mask(data="경복궁 근처 조용한 카페") == "경복궁 근처 조용한 카페"


def test_mask_rereads_the_setting_on_every_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """클라이언트를 다시 만들지 않고도 원문 수집만 끌 수 있어야 한다."""
    _enable(monkeypatch, capture_content=True)
    assert langfuse_tracing._mask(data="원문") == "원문"

    monkeypatch.setattr(settings, "langfuse_capture_content", False)
    assert langfuse_tracing._mask(data="원문") == REDACTED


# --- 4. 부팅 검증 ---------------------------------------------------------------


def test_boot_fails_when_enabled_without_credentials() -> None:
    """실패는 첫 요청이 아니라 부팅에서 드러나야 한다(D-042).

    조용히 안 되면 "켠 줄 알았는데 아무것도 안 쌓이는" 상태가 며칠씩 간다.
    """
    target = Settings(_env_file=None, langfuse_enabled=True)

    with pytest.raises(ValueError) as error:
        validate_langfuse_config(target)

    # 누락 항목을 하나씩 발견해 재시작하는 왕복이 없게 전부 모아서 보고한다.
    assert "LANGFUSE_PUBLIC_KEY" in str(error.value)
    assert "LANGFUSE_SECRET_KEY" in str(error.value)


def test_boot_passes_when_disabled_even_without_credentials() -> None:
    validate_langfuse_config(Settings(_env_file=None))


def test_boot_passes_when_enabled_with_credentials() -> None:
    validate_langfuse_config(
        Settings(
            _env_file=None,
            langfuse_enabled=True,
            langfuse_public_key="pk-lf-test",
            langfuse_secret_key="sk-lf-test",
        )
    )


def test_boot_fails_when_enabled_without_the_package(monkeypatch: pytest.MonkeyPatch) -> None:
    """설정만 검사하면 "켰는데 조용히 꺼진" 상태가 남는다.

    2026-08-25에 정확히 그렇게 물렸다 — LangChain CallbackHandler가 `langchain` 본체를
    못 찾아 ModuleNotFoundError를 냈는데, 에러 흡수 설계대로 **앱은 멀쩡하고 노드
    span만 통째로 안 생겼다.** 실 서버로 되읽지 않았으면 몰랐다.

    켠 사람만 영향받는다 — 꺼져 있으면 이 검사에 도달하지 않는다(아래 테스트).
    """
    real_import = builtins.__import__

    def _no_langfuse(name: str, *args: object, **kwargs: object) -> object:
        if name == "langfuse" or name.startswith("langfuse."):
            raise ModuleNotFoundError(f"No module named {name!r}")
        return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(builtins, "__import__", _no_langfuse)

    with pytest.raises(ValueError) as error:
        validate_langfuse_config(
            Settings(
                _env_file=None,
                langfuse_enabled=True,
                langfuse_public_key="pk-lf-test",
                langfuse_secret_key="sk-lf-test",
            )
        )

    assert "설치되지 않았습니다" in str(error.value)
    # 무엇을 하라는 건지 문구에 있어야 한다 — 팀원이 이 메시지만 보고 해결한다.
    assert "pip install" in str(error.value)


def test_boot_passes_without_the_package_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """**팀원은 설치도 설정도 안 해야 한다.**

    브랜치를 받은 사람이 `pip install`을 안 돌려도 동작이 이전과 같아야 한다.
    실제로 `langfuse`를 최상위에서 import하는 코드가 한 곳도 없다 — 전부
    `get_tracer()` 안의 지연 import다.
    """
    real_import = builtins.__import__

    def _no_langfuse(name: str, *args: object, **kwargs: object) -> object:
        if name == "langfuse" or name.startswith("langfuse."):
            raise ModuleNotFoundError(f"No module named {name!r}")
        return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(builtins, "__import__", _no_langfuse)

    validate_langfuse_config(Settings(_env_file=None))

    # 계측 헬퍼도 패키지 없이 그냥 지나가야 한다.
    with trace_attributes(session_id="s", tags=["t"]):
        with observe_step("no_package") as step:
            step.record(output={"a": 1})
        with observe_generation("no_package_gen", model="m") as generation:
            generation.record(output="x", usage_details={"input": 1})


# --- 5. 관측 실패가 응답을 막지 않는다 ------------------------------------------


def test_client_initialization_failure_only_turns_observability_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable(monkeypatch)
    monkeypatch.setattr(langfuse_tracing, "_client", None)
    monkeypatch.setattr(langfuse_tracing, "_client_failed", False)

    def _explode(**_: Any) -> Any:
        raise RuntimeError("초기화 실패")

    monkeypatch.setitem(
        __import__("sys").modules, "langfuse", type("_M", (), {"Langfuse": _explode})
    )

    with observe_step("scoring") as step:
        step.record(output="무시된다")

    assert langfuse_tracing._client_failed is True


def test_observation_start_failure_does_not_break_the_caller(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable(monkeypatch)

    class _Broken:
        def start_as_current_observation(self, **_: Any) -> Any:
            raise RuntimeError("시작 실패")

    _install(monkeypatch, _Broken())

    reached = False
    with observe_step("scoring") as step:
        step.record(output="무시된다")
        reached = True

    assert reached is True


def test_observation_exit_failure_does_not_break_the_caller(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """전송은 종료 시점에 일어난다 — 여기서 터지는 게 가장 위험하다."""
    _enable(monkeypatch)
    _install(monkeypatch, _FakeClient(fail_on_exit=True))

    with observe_generation("llm_interpret", model="gemini") as generation:
        generation.record(output="응답")


def test_recorder_failure_does_not_break_the_caller(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(monkeypatch)

    class _BrokenSpan(_FakeSpan):
        def update(self, **fields: Any) -> None:
            raise RuntimeError("기록 실패")

    _install(monkeypatch, _FakeClient(_BrokenSpan()))

    with observe_step("scoring") as step:
        step.record(output="무시된다")


# --- 6. 호출부의 예외는 삼키지 않는다 -------------------------------------------


def test_caller_exception_is_not_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """관측이 먹어도 되는 건 자기 실패뿐이다.

    `with` 전체를 try로 감싸면 호출부 예외까지 사라진다 — 추천이 실패했는데 성공한
    것처럼 보이는 최악의 형태다.
    """
    _enable(monkeypatch)
    client = _FakeClient()
    _install(monkeypatch, client)

    with pytest.raises(ValueError, match="추천 실패"):
        with observe_step("scoring"):
            raise ValueError("추천 실패")

    # 예외를 관측 객체에도 그대로 전달해야 Langfuse가 실패로 표시한다.
    assert client.observations[0].exited_with is ValueError


def test_caller_exception_survives_a_failing_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    """종료가 터져도 호출부 예외가 그걸로 덮이면 안 된다."""
    _enable(monkeypatch)
    _install(monkeypatch, _FakeClient(fail_on_exit=True))

    with pytest.raises(ValueError, match="추천 실패"):
        with observe_step("scoring"):
            raise ValueError("추천 실패")


# --- 7. 기록 값 -----------------------------------------------------------------


def test_recorder_drops_none_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    """값이 없는 것과 "None으로 덮어쓴 것"은 다르다."""
    _enable(monkeypatch)
    client = _FakeClient()
    _install(monkeypatch, client)

    with observe_generation("llm_interpret", model="gemini") as generation:
        generation.record(output=None, usage_details=None)
        generation.record(output="응답", usage_details={"input_tokens": 12, "output_tokens": 34})

    assert client.span.updates == [
        {"output": "응답", "usage_details": {"input_tokens": 12, "output_tokens": 34}}
    ]


# --- 8. Score -------------------------------------------------------------------


class _ScoringClient:
    """`score_current_trace()`만 받아 적는 클라이언트 흉내."""

    def __init__(self, *, fail: bool = False) -> None:
        self.scores: list[dict[str, Any]] = []
        self._fail = fail

    def score_current_trace(self, **fields: Any) -> None:
        if self._fail:
            raise RuntimeError("전송 실패")
        self.scores.append(fields)


def test_score_does_nothing_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """꺼져 있으면 클라이언트를 만들지도 않는다 — 다른 헬퍼와 같은 성질."""
    monkeypatch.setattr(settings, "langfuse_enabled", False)

    langfuse_tracing.record_score("turn_success", True)

    assert langfuse_tracing._client is None


def test_score_marks_booleans_as_boolean_not_numeric(monkeypatch: pytest.MonkeyPatch) -> None:
    """`bool`은 `float`의 하위형이라 그냥 넘기면 NUMERIC으로 새어 들어간다.

    그러면 Langfuse 화면에서 성공률이 0/1 막대가 아니라 연속값 평균으로 잡혀
    "0.5건 성공" 같은 눈금이 생긴다.
    """
    _enable(monkeypatch)
    client = _ScoringClient()
    _install(monkeypatch, client)

    langfuse_tracing.record_score("turn_success", True)
    langfuse_tracing.record_score("route_measured_ratio", 0.25)

    assert client.scores == [
        {"name": "turn_success", "value": 1, "data_type": "BOOLEAN"},
        {"name": "route_measured_ratio", "value": 0.25, "data_type": "NUMERIC"},
    ]


def test_score_failure_never_reaches_the_caller(monkeypatch: pytest.MonkeyPatch) -> None:
    """관측 실패가 사용자 응답을 막지 않는다 — 이 스위트가 지키는 성질 2."""
    _enable(monkeypatch)
    _install(monkeypatch, _ScoringClient(fail=True))

    langfuse_tracing.record_score("turn_success", True)


def test_every_langfuse_setting_is_documented_in_env_example() -> None:
    """설정을 늘려놓고 `.env.example`에 안 적으면 팀원은 그게 있는 줄 모른다.

    실제로 놓쳤다 — `LANGFUSE_CAPTURE_USER_ID`·`LANGFUSE_PROMPTS_ENABLED`·
    `LANGFUSE_PROMPT_CACHE_TTL_SECONDS` 셋이 코드에만 있고 문서에는 없는 채로
    푸시 직전까지 갔다(2026-08-26 검수에서 발견). 특히 `PROMPTS_ENABLED`는
    프롬프트 이관 기능 전체를 켜는 스위치라, 안 적히면 아무도 못 켠다.

    **`langfuse_*`만 본다.** 다른 패키지의 설정까지 강제하면 남의 영역에 규칙을
    새로 부과하는 셈이라, 우리가 늘린 것만 우리가 지킨다.
    """
    env_example = pathlib.Path(__file__).resolve().parents[1] / ".env.example"
    documented = env_example.read_text(encoding="utf-8")

    undocumented = sorted(
        name.upper()
        for name in Settings.model_fields
        if name.startswith("langfuse_") and name.upper() not in documented
    )

    assert not undocumented, f".env.example에 없는 설정: {', '.join(undocumented)}"


# --- 8. trace id를 응답으로 되받는다 -------------------------------------------


class _TraceIdClient:
    """`get_current_trace_id()`만 흉내 내는 클라이언트."""

    def __init__(self, *, value: str | None = "abc123", fail: bool = False) -> None:
        self._value = value
        self._fail = fail

    def get_current_trace_id(self) -> str | None:
        if self._fail:
            raise RuntimeError("span 밖")
        return self._value


def test_trace_id_is_none_when_observability_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """꺼져 있으면 클라이언트를 만들지도 않는다 — 다른 헬퍼와 같은 성질."""
    monkeypatch.setattr(settings, "langfuse_enabled", False)

    assert langfuse_tracing.current_trace_id() is None
    assert langfuse_tracing._client is None


def test_trace_id_comes_back_from_the_client(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(monkeypatch)
    _install(monkeypatch, _TraceIdClient(value="26108fa6566f330f651a8483054f99c4"))

    assert langfuse_tracing.current_trace_id() == "26108fa6566f330f651a8483054f99c4"


def test_trace_id_failure_never_reaches_the_caller(monkeypatch: pytest.MonkeyPatch) -> None:
    """관측 실패가 사용자 응답을 막지 않는다 — 이 스위트가 지키는 성질 2.

    span 밖에서 부르면 SDK가 던질 수 있는데, 그게 턴을 죽이면 안 된다.
    """
    _enable(monkeypatch)
    _install(monkeypatch, _TraceIdClient(fail=True))

    assert langfuse_tracing.current_trace_id() is None


def test_trace_id_field_is_not_the_state_trace_id() -> None:
    """이름을 `trace_id`로 줄이면 B의 `state.trace_id`와 섞인다.

    그쪽은 run 내부 한 단계(`app/state/trace.py`)를 가리키는 다른 식별자다.
    두 값이 한 응답에 같이 실리므로, 이름이 겹치면 화면에서 구분이 안 된다.
    """
    from app.schemas import AgentResponse

    assert "langfuse_trace_id" in AgentResponse.model_fields
    assert "trace_id" not in AgentResponse.model_fields
    assert AgentResponse.model_fields["langfuse_trace_id"].default is None


# --- 9. 팀원끼리 자기 trace를 가려낸다 ------------------------------------------


def test_no_developer_tag_when_the_setting_is_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """안 적은 사람의 trace가 `developer:` 로 오염되면 필터가 제 역할을 못 한다."""
    monkeypatch.setattr(settings, "langfuse_developer", "")

    assert langfuse_tracing._tags_with_developer(["env:local"]) == ["env:local"]


def test_developer_tag_is_appended_to_the_callers_tags(monkeypatch: pytest.MonkeyPatch) -> None:
    """호출부 태그를 지우지 않고 뒤에 더한다 — scoring·env 필터가 살아 있어야 한다."""
    monkeypatch.setattr(settings, "langfuse_developer", "rayquaza410")

    assert langfuse_tracing._tags_with_developer(["scoring:1.6.0", "env:local"]) == [
        "scoring:1.6.0",
        "env:local",
        "developer:rayquaza410",
    ]


def test_developer_setting_is_trimmed(monkeypatch: pytest.MonkeyPatch) -> None:
    """`.env`에 딸려 들어온 공백이 태그 값이 되면 화면에서 같은 사람이 둘로 갈린다."""
    monkeypatch.setattr(settings, "langfuse_developer", "  rayquaza410  ")

    assert langfuse_tracing._tags_with_developer([]) == ["developer:rayquaza410"]

    monkeypatch.setattr(settings, "langfuse_developer", "   ")
    assert langfuse_tracing._tags_with_developer([]) == []


def test_developer_tag_does_not_ride_on_app_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """`APP_ENV`를 사람마다 다르게 두는 방법을 막는다.

    그건 관측 라벨이 아니라 **기능 게이트**다 — `main.py`가 정확히 `"local"`과
    비교해 개발자 Ops 라우터(`/api/dev/*`)를 등록할지 정한다. 관측에서 이름을
    구분하려고 그 값을 바꾸면 개발자 도구가 통째로 사라진다(2026-08-26 확인).

    그래서 이 테스트는 `app_env`가 사람 이름이 아니어도 `developer:` 태그가
    독립적으로 붙는지를 잠근다.
    """
    monkeypatch.setattr(settings, "app_env", "local")
    monkeypatch.setattr(settings, "langfuse_developer", "mintee")

    tags = langfuse_tracing._tags_with_developer([f"env:{settings.app_env}"])

    assert tags == ["env:local", "developer:mintee"]


# --- 10. 목록 이름은 발화로 쓰되 스위치를 탄다 ----------------------------------


def test_trace_name_stays_the_span_name_when_content_capture_is_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**이 테스트가 실패하면 원문 수집 스위치에 구멍을 낸 것이다.**

    trace 이름은 mask를 타지 않는다(`level`·`status_message`와 같은 부류). 그래서
    발화를 이름으로 쓰면 `capture_content=false`인 배포에서도 사용자 말이 그대로
    나간다. `None`을 돌려주면 SDK가 루트 span 이름(`agent_turn`)을 쓴다.
    """
    _enable(monkeypatch, capture_content=False)

    assert langfuse_tracing._trace_name("경복궁 근처 카페 추천해줘") is None


def test_trace_name_is_the_utterance_when_content_capture_is_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable(monkeypatch, capture_content=True)

    assert langfuse_tracing._trace_name("경복궁 근처 카페 추천해줘") == "경복궁 근처 카페 추천해줘"


def test_trace_name_collapses_whitespace(monkeypatch: pytest.MonkeyPatch) -> None:
    """목록은 한 줄로 그려진다 — 줄바꿈이 들어가면 뒤가 잘리거나 줄이 깨진다."""
    _enable(monkeypatch, capture_content=True)

    assert langfuse_tracing._trace_name("경복궁\n근처   카페") == "경복궁 근처 카페"


def test_trace_name_is_truncated(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(monkeypatch, capture_content=True)

    name = langfuse_tracing._trace_name("가" * 200)

    assert len(name or "") == langfuse_tracing._TRACE_NAME_LIMIT
    assert (name or "").endswith("…")


def test_trace_name_is_none_for_empty_input(monkeypatch: pytest.MonkeyPatch) -> None:
    """빈 이름을 넘기면 목록에서 그 줄이 무엇인지 알 수 없다 — span 이름으로 되돌린다."""
    _enable(monkeypatch, capture_content=True)

    assert langfuse_tracing._trace_name(None) is None
    assert langfuse_tracing._trace_name("") is None
    assert langfuse_tracing._trace_name("   ") is None


# --- 11. 들어온 traceparent를 부모로 받는다 --------------------------------------


def _traceparent_headers() -> tuple[dict[str, str], str]:
    """클라이언트가 span 하나를 열고 만든 헤더와 그 trace id."""
    from opentelemetry import trace as otel_trace
    from opentelemetry.propagate import inject
    from opentelemetry.sdk.trace import TracerProvider

    if not isinstance(otel_trace.get_tracer_provider(), TracerProvider):
        otel_trace.set_tracer_provider(TracerProvider())
    tracer = otel_trace.get_tracer("test")
    with tracer.start_as_current_span("client-item"):
        trace_id = format(otel_trace.get_current_span().get_span_context().trace_id, "032x")
        headers: dict[str, str] = {}
        inject(headers)
    return headers, trace_id


def _trace_id_inside(headers: dict[str, str]) -> str:
    from opentelemetry import trace as otel_trace

    with langfuse_tracing.incoming_trace_context(headers):
        tracer = otel_trace.get_tracer("test")
        with tracer.start_as_current_span("agent_turn") as span:
            return format(span.get_span_context().trace_id, "032x")


def test_incoming_traceparent_joins_the_callers_trace(monkeypatch: pytest.MonkeyPatch) -> None:
    """평가가 연 실험 trace에 서버 span이 붙어야 실험 표의 행에서 내부가 보인다.

    안 이어지면 trace가 둘로 갈린다 — 표는 그려지는데 행을 눌러도
    `classify_intent`·`scoring`이 없는 빈 trace가 열린다.
    """
    _enable(monkeypatch)
    monkeypatch.setattr(settings, "app_env", "local")
    headers, client_trace_id = _traceparent_headers()

    assert _trace_id_inside(headers) == client_trace_id


def test_a_request_without_traceparent_opens_its_own_trace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """평소 요청은 지금과 똑같아야 한다 — 헤더가 없으면 자기 trace를 연다."""
    _enable(monkeypatch)
    monkeypatch.setattr(settings, "app_env", "local")
    _, client_trace_id = _traceparent_headers()

    assert _trace_id_inside({}) != client_trace_id


def test_traceparent_is_ignored_outside_local(monkeypatch: pytest.MonkeyPatch) -> None:
    """**이 테스트가 실패하면 배포에서 남이 우리 trace 트리에 끼어들 수 있다.**

    헤더를 신뢰하면 아무 클라이언트나 trace id를 골라 만들거나 우리 트리에 자기
    span을 붙일 수 있다. 필요한 쪽은 로컬 평가 스크립트 하나뿐이라 배포에서는 막는다.
    """
    _enable(monkeypatch)
    monkeypatch.setattr(settings, "app_env", "production")
    headers, client_trace_id = _traceparent_headers()

    assert langfuse_tracing.honors_incoming_trace_context() is False
    assert _trace_id_inside(headers) != client_trace_id


def test_traceparent_is_ignored_when_observability_is_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "langfuse_enabled", False)
    monkeypatch.setattr(settings, "app_env", "local")

    assert langfuse_tracing.honors_incoming_trace_context() is False


def test_a_broken_traceparent_does_not_break_the_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """관측 실패가 사용자 응답을 막지 않는다 — 이 스위트가 지키는 성질 2."""
    _enable(monkeypatch)
    monkeypatch.setattr(settings, "app_env", "local")

    with langfuse_tracing.incoming_trace_context({"traceparent": "쓰레기"}):
        pass
