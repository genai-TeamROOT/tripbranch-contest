"""Langfuse(LLMOps 관측)로 나가는 전송을 한 곳에서 감싼다.

역할: SDK 클라이언트의 생성·수명·마스킹을 여기서만 다룬다. 호출부는 이 모듈의
헬퍼만 쓰고 `langfuse` 패키지를 직접 import 하지 않는다 — 켜고 끄는 규칙과
마스킹이 한 군데를 못 벗어나게 하려는 것이다.
입력: `app.config.settings`의 `langfuse_*` 값.
출력: 켜져 있으면 Langfuse로 span/generation 전송, 꺼져 있으면 아무 동작 없음.
호출 시점: 부팅(검증), 요청 처리 중(계측), 종료(flush).

관측 전용이다 — 여기 값이 없거나 틀려도 추천 판정은 달라지지 않는다.
`api_usage`가 "몇 번 불렀나", `api_exchanges`가 "무엇을 주고받았나"라면 이쪽은
"한 턴이 어느 단계를 어떤 버전으로 얼마나 걸려 지나갔나"다.

**스위치가 두 개인 이유.** `langfuse_enabled`는 "전송을 하느냐",
`langfuse_capture_content`는 "발화·응답 원문을 실어 보내느냐"다. 하나로 묶으면
배포 환경에서 지연·토큰만 보고 원문은 빼는 선택을 할 수 없다. 둘 다 기본 off라
켜는 쪽이 명시적 선택이다(`taste_evidence_enabled`와 같은 이유).

보안 — **모르면 막는 쪽으로 둔다.** `capture_content`가 꺼져 있으면 mask 훅이
**input·output·metadata를 전부** 치환한다. "입력만 가리고 메타데이터는 남긴다"가
불가능하기 때문이다: SDK가 mask 함수에 `data`만 넘기고 어느 필드인지는 안 알려준다
(`_client/span.py::_mask_attribute`). 그래서 **가려지면 안 되는 값(프롬프트·Scoring
버전)은 `metadata`가 아니라 `trace_attributes()`의 `version`·`tags`로 싣는다** —
그 둘은 mask를 타지 않는다. `api_exchanges.py`가 "분류되지 않은 host는 값 전부를
마스킹한다"로 택한 것과 같은 원칙이다.

**Tool 인자·응답을 계측하는 헬퍼는 의도적으로 두지 않는다.** 사용자 좌표(장소
검색·경로 조회)와 외부 API 자격증명이 그 경로로만 흐른다. 헬퍼가 없으면 실수로도
못 쓴다. Tool 단계가 남기는 것은 **집계뿐이다** — 몇 건 요청해 몇 건이 성공했나,
Provider별 상태가 무엇인가. 그 요약은 호출부가 직접 고르며(`_summarize_tool_fetch`,
`tools/travel_route.py::summarize_fanout`), 인자와 응답 원문은 거기서 걸러낸다.

**마스킹을 타는 자리와 안 타는 자리를 나눠 쓴다.** `input`·`output`·`metadata`만
mask를 거치고(`_client/span.py::_process_media_and_apply_mask`), `level`·
`status_message`·`version`은 그대로 나간다. 그래서 `capture_content`가 꺼져도 남아야
하는 운영 신호(예: 경로 실측/추정 비율)는 `status_message`에도 한 줄로 싣는다.
**Score도 mask를 타지 않는다** — `create_score`에 마스킹 자체가 없다. 그래서
`record_score()`는 수치만 받고 자유 텍스트를 안 받는다.

실패는 전부 흡수한다 — 관측이 사용자 응답을 막으면 안 된다
(`runtime/agent_runtime.py::_record_trace_safely()`가 쓰는 것과 같은 원칙). 다만
**흡수하는 건 관측 자신의 실패지 호출부의 실패가 아니다**(`_guard()` 참고).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import Any, Final

from app.config import Settings, settings

logger = logging.getLogger(__name__)

# 마스킹된 자리에 남길 값. Langfuse 화면에서 "값이 없다"와 "일부러 가렸다"가
# 구분돼야 설정을 의심할 수 있다.
REDACTED = "<redacted by tripbranch>"

# 켜져 있을 때만 만든다. 생성 자체가 OpenTelemetry provider를 세팅하므로
# 꺼져 있을 때는 그 비용도 부작용도 없어야 한다.
_client: Any | None = None
# 초기화가 한 번 실패하면 매 요청마다 다시 시도하지 않는다.
_client_failed = False


def is_enabled() -> bool:
    """전송이 켜져 있는가."""
    return settings.langfuse_enabled


def captures_content() -> bool:
    """발화·응답 원문을 실어 보내는가. 전송이 꺼져 있으면 당연히 아니다."""
    return settings.langfuse_enabled and settings.langfuse_capture_content


def _mask(*, data: Any, **_: Any) -> Any:
    """`capture_content`가 꺼져 있으면 실려 나갈 값을 전부 치환한다.

    호출 시점에 설정을 다시 읽는다 — 클라이언트를 다시 만들지 않고도 켜고 끌 수
    있어야 하고, 테스트가 설정만 바꿔 검증할 수 있어야 한다.
    """
    return data if captures_content() else REDACTED


def validate_langfuse_config(target: Settings | None = None) -> None:
    """켜져 있는데 자격증명이나 패키지가 없으면 부팅에서 막는다.

    실패는 첫 요청이 아니라 부팅에서 드러나야 한다(D-042). 관측은 조용히 안 되면
    "켠 줄 알았는데 아무것도 안 쌓이는" 상태가 며칠씩 간다.
    누락 항목을 하나씩 발견해 재시작하는 왕복을 없애려고 전부 모아서 보고한다
    (`providers/factory.py::validate_provider_config()`와 같은 방식).
    """
    current = target if target is not None else settings
    if not current.langfuse_enabled:
        return
    missing = [
        variable_name
        for variable_name, value in (
            ("LANGFUSE_PUBLIC_KEY", current.langfuse_public_key),
            ("LANGFUSE_SECRET_KEY", current.langfuse_secret_key),
            ("LANGFUSE_BASE_URL", current.langfuse_base_url),
        )
        if not value
    ]
    if missing:
        raise ValueError(
            "LANGFUSE_ENABLED=true인데 필요한 환경변수가 비어 있습니다: " + ", ".join(missing)
        )
    # **패키지 유무까지 여기서 본다.** 설정만 검사하면 켠 사람이 "켰는데 아무것도
    # 안 쌓이는" 상태로 며칠 간다 — 2026-08-25에 LangChain CallbackHandler가 정확히
    # 그렇게 조용히 꺼졌다(langchain 본체 미설치, 앱은 멀쩡, 노드 span만 통째로 없음).
    # 켠 사람만 영향받는다. 꺼져 있으면 위에서 이미 돌아갔다.
    try:
        import langfuse  # noqa: F401
    except ModuleNotFoundError as exc:
        raise ValueError(
            "LANGFUSE_ENABLED=true인데 langfuse 패키지가 설치되지 않았습니다. "
            'backend에서 pip install -e "." 를 실행하거나 LANGFUSE_ENABLED=false로 두세요.'
        ) from exc


def _shared_client() -> Any | None:
    """관측과 프롬프트가 **같은 클라이언트를 쓴다.**

    두 개 만들면 안 된다 — 생성이 프로세스 전역 OpenTelemetry provider를 세팅하므로
    두 번째 인스턴스가 첫 번째 위에 덮어쓴다. 그래서 스위치가 둘이어도 객체는 하나다.

    `tracing_enabled`로 전송만 따로 끈다. 프롬프트만 켜고 관측은 끄는 조합
    (배치 스크립트 등)에서 span이 새 나가지 않게 한다.

    둘 다 꺼져 있으면 `langfuse`를 import조차 하지 않는다.
    """
    global _client, _client_failed
    if not (settings.langfuse_enabled or settings.langfuse_prompts_enabled):
        return None
    if _client is not None or _client_failed:
        return _client
    try:
        from langfuse import Langfuse

        _client = Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            base_url=settings.langfuse_base_url,
            # 로컬과 배포 기록이 한 프로젝트에 섞이면 비교가 무의미해진다.
            environment=settings.app_env,
            mask=_mask,
            tracing_enabled=is_enabled(),
        )
    except Exception:
        _client_failed = True
        logger.warning("Langfuse 초기화 실패 — 관측만 꺼진다", exc_info=True)
    return _client


def get_tracer() -> Any | None:
    """관측 전송이 켜져 있으면 클라이언트를, 아니면 `None`을 돌려준다."""
    if not is_enabled():
        return None
    return _shared_client()


def get_prompt_client() -> Any | None:
    """프롬프트 관리가 켜져 있으면 클라이언트를, 아니면 `None`을 돌려준다.

    **원문 통로가 아니다.** 이 모듈이 Tool 인자 헬퍼를 안 두는 이유(모듈 docstring)와
    충돌하지 않는다 — 프롬프트는 우리가 레포에 두고 관리하는 자산이지 사용자 입력이
    아니다. 다만 클라이언트를 그대로 돌려주므로, 이걸 받아서 span을 만들거나 원문을
    싣는 것은 이 모듈을 우회하는 것이다. 쓰는 곳은 `langfuse_prompts` 하나여야 한다.
    """
    if not settings.langfuse_prompts_enabled:
        return None
    return _shared_client()


def shutdown() -> None:
    """대기 중인 전송을 내보내고 백그라운드 스레드를 정리한다.

    캐시도 함께 비우므로 테스트가 설정을 바꿔가며 재초기화하는 데도 쓴다.
    """
    global _client, _client_failed
    client, _client, _client_failed = _client, None, False
    if client is None:
        return
    try:
        client.shutdown()
    except Exception:
        logger.warning("Langfuse 종료 실패(응답 흐름에는 영향 없음)", exc_info=True)


def _close(manager: Any, exc: BaseException | None) -> None:
    try:
        if exc is None:
            manager.__exit__(None, None, None)
        else:
            manager.__exit__(type(exc), exc, exc.__traceback__)
    except Exception:
        logger.warning("Langfuse 관측 종료 실패(응답 흐름에는 영향 없음)", exc_info=True)


@contextmanager
def _guard(factory: Callable[[], Any]) -> Iterator[Any]:
    """관측용 context manager를 감싸 **진입·종료 실패만** 흡수한다.

    본문에서 난 예외는 그대로 올린다 — 관측이 삼켜도 되는 건 자기 실패지 호출부의
    실패가 아니다. 그래서 `with` 전체를 try로 감싸지 않는다(그러면 호출부 예외까지
    먹는다). 진입에 실패하면 `None`을 넘겨, 호출부는 계측 없이 그대로 진행한다.
    """
    try:
        manager = factory()
        entered = manager.__enter__()
    except Exception:
        logger.warning("Langfuse 관측 시작 실패(응답 흐름에는 영향 없음)", exc_info=True)
        yield None
        return
    try:
        yield entered
    except BaseException as exc:
        _close(manager, exc)
        raise
    _close(manager, None)


class _Recorder:
    """관측 객체에 값을 붙이되 실패를 호출부로 올리지 않는다."""

    __slots__ = ("_span",)

    def __init__(self, span: Any) -> None:
        self._span = span

    def record(self, **fields: Any) -> None:
        """`output`·`usage_details`·`level` 등 SDK가 받는 필드를 그대로 넘긴다.

        `None`은 걸러낸다 — 값이 없는 것과 "None으로 덮어쓴 것"은 다르다.
        """
        payload = {key: value for key, value in fields.items() if value is not None}
        if not payload:
            return
        try:
            self._span.update(**payload)
        except Exception:
            logger.warning("Langfuse 관측 기록 실패(응답 흐름에는 영향 없음)", exc_info=True)


class _NoopRecorder:
    """꺼져 있거나 관측 시작에 실패했을 때 호출부가 분기하지 않게 하는 자리."""

    __slots__ = ()

    def record(self, **fields: Any) -> None:
        return


_NOOP: _NoopRecorder = _NoopRecorder()


# 목록에서 한 줄로 읽히는 길이. 길게 잡으면 화면에서 잘려 오히려 안 읽힌다.
_TRACE_NAME_LIMIT: Final = 60


def _trace_name(user_input: str | None) -> str | None:
    """목록에 보일 trace 이름. 발화를 쓰되 **원문 수집 스위치를 탄다.**

    `None`을 돌려주면 SDK가 루트 span 이름(`agent_turn`)을 쓴다 — 지금까지의 모양이다.

    **`capture_content`가 꺼져 있으면 발화를 쓰지 않는다.** trace 이름은 mask를 타지
    않아서(`level`·`status_message`와 같은 부류), 여기에 발화를 실으면 원문 수집을 꺼도
    사용자 말이 그대로 나간다. 스위치를 우회하는 통로를 만들지 않는다.

    줄바꿈·연속 공백을 하나로 접는다. 목록은 한 줄로 그려지므로 접지 않으면 뒤가 잘려
    보이거나 줄이 깨진다.
    """
    if not user_input or not captures_content():
        return None
    collapsed = " ".join(user_input.split())
    if not collapsed:
        return None
    if len(collapsed) <= _TRACE_NAME_LIMIT:
        return collapsed
    return collapsed[: _TRACE_NAME_LIMIT - 1] + "…"


def honors_incoming_trace_context() -> bool:
    """들어온 `traceparent`를 부모로 받아들일지.

    **로컬에서만 받는다.** 이 헤더를 신뢰하면 아무 클라이언트나 우리 trace 트리에
    자기 span을 붙이거나 trace id를 골라 만들 수 있다. 실제로 필요한 쪽은 로컬에서
    도는 평가 스크립트 하나뿐이라, 배포에서 열어둘 이유가 없다.

    `main.py`가 개발자 Ops 라우터를 `app_env == "local"`로 가르는 것과 같은 판단이다.
    """
    return is_enabled() and settings.app_env == "local"


@contextmanager
def incoming_trace_context(headers: Mapping[str, str]) -> Iterator[None]:
    """`traceparent`가 있으면 이 블록의 span을 그 trace의 자식으로 만든다.

    평가 스크립트가 `run_experiment()`로 trace를 열고 그 안에서 HTTP를 부르면,
    서버는 **다른 프로세스**라 자기 trace를 따로 만든다. 그러면 실험 표는 그려지는데
    행을 눌러도 `classify_intent`·`scoring` 같은 내부 span이 없다. 이 헬퍼가 그
    두 조각을 하나로 잇는다(W3C Trace Context 표준).

    헤더가 없거나 꺼져 있으면 아무 일도 하지 않는다 — 평소 요청은 지금과 똑같이
    자기 trace를 연다.
    """
    if not honors_incoming_trace_context() or "traceparent" not in headers:
        yield
        return
    try:
        from opentelemetry import context as otel_context
        from opentelemetry.propagate import extract

        token = otel_context.attach(extract(dict(headers)))
    except Exception:
        logger.warning("들어온 trace 문맥 해석 실패(응답 흐름에는 영향 없음)", exc_info=True)
        yield
        return
    try:
        yield
    finally:
        try:
            otel_context.detach(token)
        except Exception:
            logger.warning("trace 문맥 해제 실패(응답 흐름에는 영향 없음)", exc_info=True)


def _tags_with_developer(tags: Sequence[str]) -> list[str]:
    """호출부가 준 태그에 `developer:<핸들>`을 더한다.

    **호출부가 붙이지 않고 여기서 붙인다.** 이 태그는 관측 전용 관심사라, 넣는
    자리를 관측 모듈 안으로 모아두면 호출부(런타임)는 이 설정의 존재를 몰라도 된다.

    설정이 비어 있으면 아무것도 더하지 않는다 — 안 적은 사람의 trace가
    `developer:` 로 오염되면 필터가 제 역할을 못 한다.

    **`app_env`(태그 `env:`)로 대신할 수 없다.** 그건 기능 게이트다 — `main.py`가
    정확히 `"local"`과 비교해 개발자 Ops 라우터를 등록할지 정하므로, 사람 이름을
    넣으려고 값을 바꾸면 `/api/dev/*`가 사라진다(2026-08-26 확인).
    """
    developer = settings.langfuse_developer.strip()
    if not developer:
        return list(tags)
    return [*tags, f"developer:{developer}"]


@contextmanager
def trace_attributes(
    *,
    session_id: str | None = None,
    user_id: str | None = None,
    version: str | None = None,
    tags: Sequence[str] = (),
    metadata: Mapping[str, Any] | None = None,
    user_input: str | None = None,
) -> Iterator[None]:
    """이 블록 안에서 생기는 모든 관측에 trace 단위 속성을 붙인다.

    `user_input`은 **목록에 보일 이름을 만드는 데만** 쓴다(`_trace_name`). 원문 수집이
    꺼져 있으면 쓰지 않으므로, 호출부는 스위치를 확인하지 않고 그냥 넘기면 된다.

    `version`·`tags`는 mask를 타지 않는다 — 프롬프트·Scoring 버전처럼 `capture_content`
    가 꺼져 있어도 남아야 하는 값은 `metadata`가 아니라 여기에 싣는다(모듈 docstring).
    """
    if get_tracer() is None:
        yield
        return

    def factory() -> Any:
        from langfuse import propagate_attributes

        return propagate_attributes(
            session_id=session_id,
            user_id=user_id,
            version=version,
            tags=_tags_with_developer(tags) or None,
            metadata=dict(metadata) if metadata else None,
            # None이면 SDK가 루트 span 이름(`agent_turn`)을 쓴다.
            trace_name=_trace_name(user_input),
        )

    with _guard(factory):
        yield


@contextmanager
def observe_step(
    name: str,
    *,
    kind: str = "span",
    metadata: Mapping[str, Any] | None = None,
) -> Iterator[_Recorder | _NoopRecorder]:
    """실행 단계 하나를 span으로 남긴다. 지연은 SDK가 잰다.

    `name`은 B의 Trace `step`과 같은 값을 쓴다(`llm_interpret`·`scoring` 등) —
    두 기록을 나란히 읽으려면 이름이 갈리면 안 된다.

    `kind`는 Langfuse가 화면에서 다르게 취급하는 의미 타입이다(`retriever`,
    `embedding`, `tool` 등). 기본 `span`이면 "그냥 구간"이고, 검색·임베딩처럼
    성격이 분명한 단계는 제 이름을 붙이는 편이 목록에서 골라내기 쉽다.
    **토큰·비용이 붙는 `generation`은 여기서 열지 않는다** — 그건
    `observe_generation()`이고, 섞으면 비용 통계가 어긋난다.
    """
    client = get_tracer()
    if client is None:
        yield _NOOP
        return

    def factory() -> Any:
        return client.start_as_current_observation(
            as_type=kind,
            name=name,
            metadata=dict(metadata) if metadata else None,
        )

    with _guard(factory) as span:
        yield _NOOP if span is None else _Recorder(span)


@contextmanager
def observe_generation(
    name: str,
    *,
    model: str | None = None,
    version: str | None = None,
    input: Any = None,
    prompt: Any = None,
) -> Iterator[_Recorder | _NoopRecorder]:
    """LLM 호출 하나를 generation으로 남긴다.

    토큰은 호출부가 응답을 받은 뒤 넘긴다:
    `recorder.record(output=..., usage_details={"input_tokens": n, "output_tokens": m})`

    `input`·`output`은 `capture_content`가 꺼져 있으면 mask가 치환한다. 호출부는
    가리는 걸 신경 쓰지 않고 있는 그대로 넘긴다 — 판단을 한 곳에 모아 둔다.

    `version`은 **mask를 타지 않는다.** 프롬프트 버전이 여기 들어가는 이유가 그것이다 —
    원문 수집을 꺼도 "어느 버전이 이 지연·토큰을 냈나"는 남아야 배포 환경에서도
    버전 비교가 된다(모듈 docstring).

    `prompt`는 Langfuse 프롬프트 객체다(`langfuse_prompts.prompt_object()`). 넘기면
    서버가 이 호출을 그 프롬프트 버전에 묶어 **버전별 지연·비용·Score를 자동 집계**한다.
    `version` 문자열로는 안 되는 것이고, 그래서 둘 다 싣는다 — 문자열은 프롬프트 관리가
    꺼져 있어도 남고, 링크는 켰을 때 집계를 만든다.
    """
    client = get_tracer()
    if client is None:
        yield _NOOP
        return

    def factory() -> Any:
        return client.start_as_current_observation(
            as_type="generation",
            name=name,
            model=model,
            version=version,
            input=input,
            prompt=prompt,
        )

    with _guard(factory) as generation:
        yield _NOOP if generation is None else _Recorder(generation)


def current_trace_id() -> str | None:
    """지금 열려 있는 trace의 id. 꺼져 있거나 span 밖이면 `None`.

    **원문 통로가 아니다.** 나가는 값은 SDK가 만든 hex 문자열 하나이고 사용자
    입력에서 유도되지 않는다 — Tool 인자 헬퍼를 두지 않는 이유(모듈 docstring)와
    충돌하지 않는다. 방향도 반대다: 이건 우리가 밖으로 보내는 값이 아니라
    Langfuse가 만든 식별자를 **우리 응답으로 되받는** 자리다.

    **session_id로 대신할 수 없어서 필요하다.** 세션은 LLM 단계가 지나간 뒤
    `apply()`에서 발급돼서 **첫 턴 trace에는 session_id가 안 붙는다**
    (`runtime/agent_runtime.py::run_agent_flow` docstring). 골드셋 dev 35건 중
    20건이 1턴짜리라, session_id 역조회로는 그 20건의 trace를 영영 못 찾는다.
    """
    client = get_tracer()
    if client is None:
        return None
    try:
        return client.get_current_trace_id()
    except Exception:
        logger.warning("Langfuse trace id 조회 실패(응답 흐름에는 영향 없음)", exc_info=True)
        return None


def record_score(name: str, value: float | bool) -> None:
    """지금 turn의 trace에 수치 하나를 남긴다. 꺼져 있으면 아무 일도 안 한다.

    **span의 `output`과 목적이 다르다.** `output`은 그 턴을 열어봤을 때 읽는 값이고,
    Score는 **여러 턴에 걸쳐 집계·정렬·알림이 걸리는 값**이다. 같은 수치라도
    `output`에만 있으면 "이 턴이 어땠나"까지고, Score로 올려야 "지난주 대비 떨어졌나"가
    된다. 관측의 발견들(경로 실측 0%, 취향 상한)이 한 번 본 수치에 머문 이유가 이것이다.

    **자유 텍스트를 받지 않는다.** Score는 mask 훅을 타지 않는다 — `create_score`에
    마스킹이 아예 없다(SDK 4.14.5에서 확인). 그래서 `comment`를 열어두면
    `capture_content=false`인 배포에서도 발화가 그대로 나갈 수 있다. 헬퍼가 없으면
    실수로도 못 쓴다 — Tool 인자 계측 헬퍼를 안 둔 것과 같은 판단이다(모듈 docstring).

    **호출 위치는 값이 있는 곳이다.** `score_current_trace()`가 현재 span에서 trace를
    찾아가므로, 루트까지 값을 들고 올라올 필요가 없다. Tool·Provider 계층에서 바로
    부르면 그 turn의 trace에 붙는다.
    """
    client = get_tracer()
    if client is None:
        return
    try:
        # bool은 float의 하위형이라 그대로 넘기면 BOOLEAN이 아니라 NUMERIC이 된다.
        if isinstance(value, bool):
            client.score_current_trace(name=name, value=int(value), data_type="BOOLEAN")
        else:
            client.score_current_trace(name=name, value=float(value), data_type="NUMERIC")
    except Exception:
        logger.warning("Langfuse Score 기록 실패(응답 흐름에는 영향 없음)", exc_info=True)


# LangGraph 노드는 `observe_step()`으로 직접 감싼다(graph/__init__.py). Langfuse가
# 주는 LangChain CallbackHandler를 쓰지 않는 이유는 그게 **`langchain` 본체를
# 요구하기 때문**이다 — 우리는 langgraph가 끌고 온 `langchain-core`만 두고
# 본체·통합 패키지는 의도적으로 안 넣었다(pyproject.toml). 콜백 하나 때문에 그
# 무거운 의존성을 들이는 것보다, 노드를 직접 감싸 이름까지 우리가 정하는 편이 낫다
# (B의 Trace `step`과 같은 이름을 쓸 수 있다).
__all__ = [
    "REDACTED",
    "captures_content",
    "current_trace_id",
    "honors_incoming_trace_context",
    "incoming_trace_context",
    "is_enabled",
    "get_prompt_client",
    "observe_generation",
    "observe_step",
    "record_score",
    "shutdown",
    "trace_attributes",
    "validate_langfuse_config",
]
