"""Package B - 익명 세션 관리.

계약 문서: docs/package-b/agent-state-contract-v1.md (4절, 5절)

세션 만료는 오류가 아니라 정상적인 생애주기이므로,
어떤 경우에도 예외를 발생시키지 않고 신규 세션을 발급한다.
"""

import uuid
from datetime import datetime, timedelta

from app.auth.principal import Principal
from app.state import history as history_module
from app.state.errors import SessionOwnershipError
from app.state.schema import AgentState, ApiContext, UserConditions, now_kst
from app.state.store import StateStore

# ---------------------------------------------------------------- 설정

# TODO(P2): 설정 파일로 이동 검토. 팀 합의로 조정 가능한 값이다.
SESSION_TTL = timedelta(minutes=30)
API_CONTEXT_TTL = timedelta(hours=1)

PREFIX_SESSION = "sess"
PREFIX_RUN = "run"
PREFIX_TRACE = "trace"

RESET_SOFT = "soft"
RESET_HISTORY = "history"
RESET_FULL = "full"


# ---------------------------------------------------------------- 식별자

def _generate_id(prefix: str) -> str:
    """접두어 + 정렬 가능한 고유 문자열. (계약 4.4절)

    접두어는 로그 판독성과 오사용 탐지를 위해 사용한다.
    앞부분에 생성 시각을 두어 문자열 정렬만으로 시간순이 되도록 한다.

    TODO(P1-5): ULID 도입 여부 확정 시 이 함수만 교체한다.
    """
    ts = f"{int(now_kst().timestamp() * 1000):013d}"
    rand = uuid.uuid4().hex[:12]
    return f"{prefix}_{ts}{rand}"


def new_session_id() -> str:
    return _generate_id(PREFIX_SESSION)


def new_run_id() -> str:
    return _generate_id(PREFIX_RUN)


def new_trace_id() -> str:
    return _generate_id(PREFIX_TRACE)


# ---------------------------------------------------------------- 만료 판정

def is_session_expired(state: AgentState, *, at: datetime | None = None) -> bool:
    """세션 TTL 초과 여부. (계약 5.4절)"""
    now = at or now_kst()
    return (now - state.last_active_at) > SESSION_TTL


def is_gps_expired(state: AgentState, *, at: datetime | None = None) -> bool:
    """GPS 유효 기간 초과 여부.

    확보된 적이 없으면 만료로 간주해 A가 확보하도록 알린다.
    """
    if state.api_context.gps_location_updated_at is None:
        return True
    now = at or now_kst()
    return (now - state.api_context.gps_location_updated_at) > API_CONTEXT_TTL


def is_weather_expired(state: AgentState, *, at: datetime | None = None) -> bool:
    """날씨 유효 기간 초과 여부.

    확보된 적이 없으면 만료로 간주한다.
    """
    if state.api_context.api_weather_updated_at is None:
        return True
    now = at or now_kst()
    return (now - state.api_context.api_weather_updated_at) > API_CONTEXT_TTL


# ---------------------------------------------------------------- 생성·조회

def create_session(store: StateStore) -> AgentState:
    """새 세션과 빈 State를 만든다. (계약 5.2절)"""
    state = AgentState(session_id=new_session_id())
    store.save_state(state)
    return state


def get_or_create_session(
    store: StateStore,
    session_id: str | None,
) -> tuple[AgentState, bool]:
    """세션을 확보한다. (계약 5.2절)

    다음 세 경우 모두 신규 세션을 발급하며, 오류를 반환하지 않는다.
      1. session_id가 없음
      2. 저장소에 존재하지 않음 (서버 재시작 등)
      3. session_id가 expired 상태임 (TTL 초과로 이미 만료 처리됐거나,
         apply_reset(RESET_FULL)로 명시적으로 만료 처리된 경우 모두 포함)

    status가 아직 active인데 TTL만 초과한 경우는 여기서 expired로 전환하고
    함께 신규 발급한다 — peek_session()과 달리 이 함수는 판정과 동시에
    상태를 확정 짓는 쓰기 경로이기 때문이다.

    Returns:
        (state, created) - created가 True면 신규 발급된 세션이다.
    """
    if not session_id:
        return create_session(store), True

    state = store.get_state(session_id)
    if state is None or state.status == "expired":
        return create_session(store), True

    if is_session_expired(state):
        state.status = "expired"
        store.save_state(state)
        return create_session(store), True

    return state, False


def attach_user_id(state: AgentState, principal: Principal | None) -> None:
    """검증된 신원을 세션에 연결한다. (TP-101 3단계, D-063 결정 3)

    비어 있으면 채우고, 이미 값이 있으면 절대 덮어쓰지 않는다 — 빈 칸을
    채우는 것은 소유권 이전이 아니지만, 값이 있는 세션을 덮어쓰는 것은
    소유권 탈취이기 때문이다. principal이 None(신원 토큰 없이 온 요청,
    지금은 Phase 4 전이라 정상 경로다)이면 아무것도 하지 않는다.
    """
    if principal is None:
        return
    if state.user_id is not None:
        return
    state.user_id = principal.user_id


MAX_TITLE_CHARS = 200


def attach_title(state: AgentState, user_input: str) -> None:
    """첫 턴의 사용자 발화를 대화 제목으로 붙인다. (TP-222 후속 — 채팅 히스토리)

    attach_user_id와 같은 규칙이다 — **비어 있으면 채우고, 값이 있으면 절대
    덮어쓰지 않는다.** 사용자가 사이드바에서 이름을 바꾼 뒤 대화를 이어가도
    그 이름이 유지된다.

    recent_turns에서 파생하지 않는 이유는 그 배열이 MAX_RECENT_TURNS개만 남기
    때문이다. 첫 질문이 밀려나면 사이드바의 제목이 저절로 바뀐다.
    """
    if state.title is not None:
        return
    trimmed = user_input.strip()
    if not trimmed:
        return
    state.title = trimmed[:MAX_TITLE_CHARS]


def attach_location(state: AgentState, search_center: str | None) -> None:
    """그 대화의 위치를 붙인다. (TP-222 후속 — 채팅 히스토리)

    attach_title과 같은 규칙이다 — **비어 있으면 채우고, 값이 있으면 절대
    덮어쓰지 않는다.** 제목이 첫 질문인 것과 짝을 맞춰 위치도 처음 잡힌 값을
    쓴다. 대화 도중에 지역을 옮겨도 목록의 한 줄이 저절로 바뀌지는 않는다.

    조건(user_conditions)에서 그때그때 읽지 않고 여기 옮겨 두는 이유는 resume()이
    낡은 조건을 버릴 때 그 값이 함께 사라지기 때문이다.
    """
    if state.location is not None:
        return
    if search_center is None:
        return
    trimmed = search_center.strip()
    if not trimmed:
        return
    state.location = trimmed[:MAX_TITLE_CHARS]


def rename(state: AgentState, title: str) -> bool:
    """사용자가 대화 이름을 바꾼다. 빈 이름은 무시한다.

    attach_title과 달리 기존 값을 덮어쓴다 — 사용자가 명시적으로 요청한
    변경이기 때문이다. 반환값은 실제로 바뀌었는지다.
    """
    trimmed = title.strip()
    if not trimmed:
        return False
    next_title = trimmed[:MAX_TITLE_CHARS]
    if state.title == next_title:
        return False
    state.title = next_title
    return True


def verify_ownership(state: AgentState, principal: Principal | None) -> None:
    """이 세션이 요청을 보낸 신원의 것인지 대조한다. (D-063 결정 2 후속, D-073)

    session_id만 알면 남의 세션에 접근할 수 있던 문제를 닫는다. principal이
    없는 요청(토큰 미전송)은 지금은 정상 경로라 그대로 통과시킨다 — Phase 4
    (인증 필수화) 전면 도입 전까지는 검증할 신원 자체가 없기 때문이다.
    state.user_id가 비어 있는 세션(아직 아무도 신원을 붙이지 않은 경우)도
    통과시킨다 — 이 경우는 attach_user_id()가 채우는 대상이지 거부 대상이
    아니다. 값이 있는데 다른 경우만 거부한다.
    """
    if principal is None:
        return
    if state.user_id is None:
        return
    if state.user_id != principal.user_id:
        raise SessionOwnershipError()


def peek_session(store: StateStore, session_id: str | None) -> AgentState | None:
    """세션을 조회만 한다. 없거나 만료면 None. (계약 6.3절)

    get_or_create_session과 달리 세션을 생성하지 않으며
    last_active_at도 갱신하지 않는다.
    """
    if not session_id:
        return None

    state = store.get_state(session_id)
    if state is None or state.status == "expired":
        return None

    if is_session_expired(state):
        return None

    return state


def touch(state: AgentState) -> None:
    """활동 시각을 갱신한다.

    조건 변경 여부와 무관하게 요청 수신 시마다 호출한다.
    updated_at은 갱신하지 않는다. (계약 5.3절)
    """
    state.last_active_at = now_kst()


def resume(state: AgentState) -> None:
    """만료된 대화를 이어간다. (TP-222 후속 — 채팅 히스토리)

    **만료를 없던 일로 되돌리는 함수가 아니다.** 세션 만료가 하는 일은 두 가지가
    한데 묶여 있었다 — "대화를 끊는 것"과 "낡은 조건을 버리는 것". 사이드바에서
    지난 대화를 열어 이어 물을 때 필요한 것은 앞의 하나뿐이라, 여기서 둘을
    나눈다: **대화는 잇고 조건은 버린다.**

    버리는 값은 전부 "그때"에 묶여 있어 오늘 쓰면 틀리는 것들이다.
      - user_conditions: 사흘 전 "비 오는데"가 오늘의 조건으로 남으면 안 된다.
      - api_context: GPS·날씨는 자체 TTL이 1시간이다(API_CONTEXT_TTL).
      - pending_clarification/pending_info_context: 사흘 전에 물은 되묻기의
        답으로 오늘의 발화를 해석하면 엉뚱한 곳에 붙는다.
      - situation_state, ignore_operating_hours_until: 같은 이유다.
      - last_run_id/last_intent: 지난 턴을 가리키는 값이라 함께 비운다.

    남기는 값은 시간이 지나도 틀리지 않는 것들이다 — session_id, user_id,
    title, recent_turns. 이 넷이 남아야 "같은 대화"가 성립한다. 추천 이력은 이
    함수가 아니라 호출 측(service.resume_user_session)이 clear_recommended로
    비운다 — 이력은 별도 저장소에 있고, 거절 이력은 남겨야 하기 때문이다.

    조건을 통째로 갈아치우므로 condition_version을 올린다.
    """
    state.user_conditions = UserConditions()
    state.api_context = ApiContext()
    state.pending_clarification = None
    state.pending_info_context = None
    state.situation_state = None
    state.ignore_operating_hours_until = None
    state.last_run_id = None
    state.last_intent = None
    state.condition_version += 1
    state.status = "active"
    state.last_active_at = now_kst()


# ---------------------------------------------------------------- 초기화

def apply_reset(
    store: StateStore,
    state: AgentState,
    reset_scope: str | None,
) -> tuple[AgentState, bool]:
    """초기화를 적용한다. (계약 5.5절)

    조건 초기화는 merge 단계에서 수행되므로, 이 함수는
    이력 초기화와 세션 재발급만 담당한다.

    Returns:
        (state, session_created)
    """
    if reset_scope is None:
        return state, False

    if reset_scope == RESET_SOFT:
        # 조건만 초기화. 이력은 유지한다. 조건 초기화는 merge가 수행한다.
        return state, False

    if reset_scope == RESET_HISTORY:
        # 추천 이력만 비우고 거절 이력은 유지한다.
        history_module.clear_recommended(store, state.session_id)
        return state, False

    if reset_scope == RESET_FULL:
        # 기존 세션을 만료 처리하고 신규 세션을 발급한다.
        state.status = "expired"
        store.save_state(state)
        history_module.clear_all(store, state.session_id)
        return create_session(store), True

    # 알 수 없는 값은 무시한다. B는 값의 의미를 판단하지 않는다.
    return state, False