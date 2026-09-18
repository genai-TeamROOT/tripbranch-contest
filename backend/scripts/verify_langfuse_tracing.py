"""Langfuse 전송과 **마스킹 스위치**가 실제 서버에서 먹는지 검증한다.

배경: 단위 테스트(`tests/test_langfuse_tracing.py`)는 mask 함수가 값을 치환하는지
프로세스 안에서만 확인한다. 그런데 정작 중요한 질문은 **"발화 원문이 정말 밖으로
안 나갔는가"**이고, 그건 보낸 뒤 서버에서 되읽어야만 답할 수 있다. 마스킹이 조용히
안 먹으면 아무도 모르는 채로 사용자 발화가 계속 쌓인다.

방법: 같은 마커 문자열을 두 번 보낸다 — `LANGFUSE_CAPTURE_CONTENT`를 켠 채로 한 번,
끈 채로 한 번. 전송 후 공개 API로 두 trace를 되읽어 마커가 어디에 남아 있는지 본다.

실패 기준 — 하나라도 어긋나면 불합격이다.
  (a) 인증이 안 되면 불합격 (키 또는 리전 불일치)
  (b) **끈 쪽 trace에서 마커가 발견되면 불합격.** 이게 이 스크립트의 존재 이유다
  (c) 켠 쪽에서 마커가 안 보이면 불합격 — 마스킹이 아니라 전송이 깨진 것이다
  (d) 토큰(usage_details)이 안 실리면 불합격 — 비용 화면이 비게 된다
  (e) **루트 span 아래 두 호출이 같은 trace에 안 묶이면 불합격.** 2026-08-25 첫
      실측에서 실제로 깨져 있었다 — 속성만 전파하고 루트 span을 안 만들면 부모가
      없는 observation이 저마다 자기가 trace 루트가 되어, 한 턴이 LLM 호출 수만큼
      조각난다. 화면에서 "이 턴이 무슨 일을 했나"를 볼 수 없게 된다
  (f) **원문 수집을 꺼도 프롬프트 버전이 남아야 한다.** 버전이 mask를 타면 배포
      환경(CAPTURE_CONTENT=false)에서 버전별 비교가 통째로 불가능해진다 — 3단계의
      목적이 사라진다
  (g) **원문 수집을 꺼도 `status_message`·`level`이 남아야 한다.** Tool 팬아웃
      요약(`tools/travel_route.py::summarize_fanout`)이 실측/추정 비율을 여기 싣는
      근거다. SDK 소스상 mask는 input·output·metadata에만 걸리는데
      (`_client/span.py::_process_media_and_apply_mask`), **읽어서 내린 결론이라
      서버에서 되읽어 확인한다.** 여기가 마스킹되면 배포 환경에서 경로가 추정으로
      새는 것을 볼 방법이 없어진다

참고값(합격/불합격을 가르지 않음): 되읽기까지 걸린 시간.

⚠️ **조회 성공을 "데이터가 다 왔다"로 보면 안 된다.** 수집이 비동기라 trace는 이미
있는데 observation 필드(usage_details 등)는 아직 안 채워진 중간 상태가 있다. 실제로
그 상태를 (d) 실패로 잘못 읽은 적이 있다 — 값은 나중에 확인하니 멀쩡히 들어가 있었다.
그래서 **기다리는 조건을 "조회 성공"이 아니라 "볼 값이 도착함"으로 둔다.**

실행: `cd backend && .venv/bin/python -m scripts.verify_langfuse_tracing`
서버는 필요 없다. `.env`에 LANGFUSE_ENABLED=true와 키가 있어야 한다.
실제 Langfuse 프로젝트에 검증용 trace 2건이 남는다(이름 `verify_langfuse_tracing`).
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable
from typing import Any, Final

from app.config import settings
from app.observability import langfuse_tracing
from app.observability.langfuse_tracing import (
    REDACTED,
    get_tracer,
    observe_generation,
    observe_step,
    shutdown,
    trace_attributes,
)

# 실제 발화처럼 보이지 않으면서 우연히 겹치지 않을 문자열.
MARKER = "TRIPBRANCH-VERIFY-MARKER-8f3a2c"
STEP_NAME = "verify_langfuse_tracing"
# 실제 슬롯 이름과 겹치지 않게 둔다 — 통계에 섞이면 안 된다.
VERSION = "verify.slot@0.0.0"
# status_message에만 싣는 마커. MARKER와 겹치면 (b) 판정이 흐려진다.
STATUS_MARKER = "verify-headline-2건-실측-1-추정-1"

# 수집이 비동기·배치라 flush 직후엔 아직 조회가 안 될 수 있다.
_POLL_ATTEMPTS = 10
_POLL_INTERVAL_SECONDS = 2.0


def _emit(*, capture_content: bool) -> str | None:
    """마커를 담은 generation 하나를 보내고 trace id를 돌려준다."""
    settings.langfuse_capture_content = capture_content
    client = get_tracer()
    if client is None:
        return None

    trace_id: str | None = None
    with trace_attributes(
        session_id=f"verify-{'on' if capture_content else 'off'}",
        tags=[f"capture_content:{str(capture_content).lower()}"],
    ):
        with observe_generation(
            STEP_NAME,
            model="verification-not-a-real-model",
            version=VERSION,
            input={"user_input": MARKER},
        ) as generation:
            trace_id = client.get_current_trace_id()
            generation.record(
                output={"answer": MARKER},
                usage_details={"input": 11, "output": 22, "total": 33},
                # (g) — 마스킹을 타는 자리(output)와 안 타는 자리(status_message)를
                # 같은 observation에 나란히 실어 둔다.
                level="WARNING",
                status_message=STATUS_MARKER,
            )
    return trace_id


def _emit_nested() -> str | None:
    """루트 span 하나 아래에 generation 둘을 넣고 trace id를 돌려준다.

    실제 한 턴의 모양이다(`run_agent_flow` 아래 classify + extract).
    """
    client = get_tracer()
    if client is None:
        return None

    trace_id: str | None = None
    with observe_step("verify_root_span"):
        trace_id = client.get_current_trace_id()
        for name in ("verify_child_one", "verify_child_two"):
            with observe_generation(name, model="verification-not-a-real-model") as child:
                child.record(usage_details={"input": 1, "output": 1, "total": 2})
    return trace_id


# `fields`는 필드명이 아니라 **그룹명**을 받는다(4.14.5 문서). 기본값에는 `usage`가
# 없어서 `usage_details`가 전부 `None`으로 온다 — 그러면 "토큰이 안 실렸다"는 판정이
# 항상 참이 되어 검증이 거짓 합격한다.
#
# **`io`도 반드시 넣는다.** 기본값은 `core`+`basic`뿐이고 `input`/`output`은 `io`
# 그룹에만 들어 있다(SDK 4.15.1 `api/observations/client.py` 필드 그룹 표). 빼면
# 마커가 실린 자리를 아예 안 받아와서, (c)는 항상 실패하고 **(b)는 "마커가 없다"로
# 거짓 합격한다** — 마스킹이 깨져도 통과한다. 이 스크립트의 존재 이유가 (b)이므로
# 그 거짓 합격이 제일 위험하다.
_OBSERVATION_FIELDS: Final = "core,basic,io,usage"


def _fetch(client: Any, trace_id: str, ready: Callable[[Any], bool]) -> Any | None:
    """`ready`가 참이 될 때까지 되읽는다. 끝내 안 되면 마지막으로 받은 것을 돌려준다.

    마지막 것을 돌려주는 이유: `None`을 주면 "수집이 안 됐다"와 "값이 안 실렸다"가
    구분되지 않아, 판정 메시지가 원인을 못 짚는다.
    """
    latest: Any | None = None
    for attempt in range(_POLL_ATTEMPTS):
        try:
            latest = client.api.observations.get_many(
                trace_id=trace_id, fields=_OBSERVATION_FIELDS
            )
            if ready(latest):
                return latest
        except Exception:
            pass
        if attempt < _POLL_ATTEMPTS - 1:
            time.sleep(_POLL_INTERVAL_SECONDS)
    return latest


def _observations(trace: Any) -> list[Any]:
    return list(getattr(trace, "data", None) or [])


def _dump(trace: Any) -> str:
    """trace 전체를 문자열로 눌러 마커 유무를 통째로 검사한다.

    필드 하나씩 보면 우리가 예상 못 한 자리(메타데이터·태그 등)에 남은 원문을
    놓친다. 안전한 쪽으로 판정하려면 전부를 훑어야 한다.
    """
    try:
        return trace.model_dump_json()
    except AttributeError:
        return str(trace)


def _has_usage(trace: Any) -> bool:
    return any(getattr(o, "usage_details", None) for o in _observations(trace))


def main() -> int:
    if not settings.langfuse_enabled:
        print("LANGFUSE_ENABLED가 false다. .env를 켜고 다시 실행한다.")
        return 2

    client = get_tracer()
    if client is None:
        print("[실패] 클라이언트를 만들지 못했다.")
        return 1

    print(f"대상: {settings.langfuse_base_url}")
    try:
        authenticated = client.auth_check()
    except Exception as error:  # noqa: BLE001 - 원인을 그대로 보여주는 게 목적이다
        print(f"[실패] (a) 인증 확인 중 예외: {type(error).__name__}: {error}")
        return 1
    if not authenticated:
        print("[실패] (a) 인증 실패 — 키 또는 리전이 맞지 않는다.")
        return 1
    print("[통과] (a) 인증")

    original = settings.langfuse_capture_content
    try:
        on_id = _emit(capture_content=True)
        off_id = _emit(capture_content=False)
        nested_id = _emit_nested()
    finally:
        settings.langfuse_capture_content = original

    if not on_id or not off_id or not nested_id:
        print("[실패] trace id를 얻지 못했다 — 전송 자체가 안 됐다.")
        return 1

    client.flush()
    print(f"전송 완료. capture on={on_id} / off={off_id}")
    print(f"되읽기 대기 (최대 {int(_POLL_ATTEMPTS * _POLL_INTERVAL_SECONDS)}초)…")

    started = time.perf_counter()
    on_trace = _fetch(client, on_id, lambda t: any(o.usage_details for o in _observations(t)))
    off_trace = _fetch(client, off_id, lambda t: any(o.version for o in _observations(t)))
    nested_trace = _fetch(client, nested_id, lambda t: len(_observations(t)) >= 3)
    elapsed = time.perf_counter() - started
    if on_trace is None or off_trace is None or nested_trace is None:
        print("[실패] 되읽기 실패 — 수집이 안 됐거나 지연이 길다.")
        return 1
    print(f"       되읽기까지 {elapsed:.1f}초 (참고값)")

    failures = 0

    if MARKER in _dump(off_trace):
        print("[실패] (b) **끈 쪽 trace에 마커가 남아 있다 — 마스킹이 안 먹는다.**")
        failures += 1
    else:
        print(f"[통과] (b) 끈 쪽에 마커 없음 (치환값 {REDACTED!r})")

    if MARKER in _dump(on_trace):
        print("[통과] (c) 켠 쪽에 마커 있음 — 전송 경로 정상")
    else:
        print("[실패] (c) 켠 쪽에도 마커가 없다 — 마스킹이 아니라 전송이 깨졌다.")
        failures += 1

    if _has_usage(on_trace):
        print("[통과] (d) 토큰(usage_details) 기록됨")
    else:
        print("[실패] (d) 토큰이 안 실렸다 — 비용 화면이 빈다.")
        failures += 1

    off_versions = {getattr(o, "version", None) for o in _observations(off_trace)}
    if VERSION in off_versions:
        print(f"[통과] (f) 원문을 꺼도 버전이 남음 ({VERSION})")
    else:
        print(f"[실패] (f) 끈 쪽에 버전이 없다 — mask를 타고 있다. 실제: {off_versions}")
        failures += 1

    off_messages = {getattr(o, "status_message", None) for o in _observations(off_trace)}
    off_levels = {getattr(o, "level", None) for o in _observations(off_trace)}
    if STATUS_MARKER in off_messages and "WARNING" in off_levels:
        print("[통과] (g) 원문을 꺼도 status_message·level이 남음")
    else:
        print("[실패] (g) 끈 쪽에 status_message/level이 없다 — mask를 타고 있다.")
        print(f"        status_message={off_messages} level={off_levels}")
        print("        Tool 요약의 운영 신호를 여기 실을 수 없다는 뜻이다.")
        failures += 1

    names = {getattr(o, "name", None) for o in _observations(nested_trace)}
    expected = {"verify_root_span", "verify_child_one", "verify_child_two"}
    if expected <= names:
        print(f"[통과] (e) 한 trace 안에 루트+자식 {len(names)}개 — 턴이 조각나지 않는다")
    else:
        print(f"[실패] (e) 중첩이 깨졌다. trace 안에 있는 것: {sorted(names)}")
        print("        루트 span이 없으면 호출마다 별도 trace가 된다.")
        failures += 1

    print()
    print("불합격" if failures else "합격 — 전송·마스킹·토큰·중첩·버전·상태메시지 모두 확인")
    return 1 if failures else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        shutdown()
        langfuse_tracing.shutdown()
