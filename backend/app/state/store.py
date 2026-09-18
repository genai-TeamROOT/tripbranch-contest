"""Package B - State 저장소.

계약 문서: docs/package-b/agent-state-contract-v1.md (Phase 1 전제)
설계 문서: docs/package-b/db-store-design-v2.md (Phase 2 Supabase 전환)

Phase 1은 인메모리 구현을 사용한다. 프로토콜을 분리해 두어
저장소를 교체할 때 상위 계층을 수정하지 않도록 한다.
STATE_STORE_BACKEND 설정(memory/supabase)으로 get_store()가 반환하는
구현체를 고른다 — 호출부(service.py 등)는 이 전환을 알 필요가 없다.
"""

from datetime import datetime
from typing import Protocol

import httpx

from app.config import settings
from app.state.schema import (
    AgentState,
    ConditionChangeLog,
    FeedbackRecord,
    RecommendationHistory,
    SavedPlaceList,
    SavedSchedule,
    SessionMessage,
    TraceRecord,
    UserFavoriteList,
    UserPreferenceList,
    now_kst,
)


def for_persistence(state: AgentState) -> AgentState:
    """저장할 사본을 만든다. **기기 좌표는 빼고 남긴다.**

    개인정보 때문이다. 서버가 사용자의 좌표를 들고 있을 이유가 없는데, 로그아웃해도
    남고 세션마다 한 벌씩 쌓였다(2026-09-07 기준 1,800건 이상).

    **뺄 수 있는 근거는 화면이 매 턴 좌표를 실어 보낸다는 점이다.** 서버에 둔 사본은
    "요청에 없을 때를 위한 여벌"이었는데 실제로는 매번 온다. 그 여벌을 읽던 자리는
    둘뿐이고(runtime의 도구 조회 GPS, INFO 도보시간), 둘 다 없으면 이번 턴 좌표만
    쓰고 넘어간다. `gps_location_confirmed_at`은 채우는 코드도 읽는 코드도 없다 —
    30분 재확인은 화면이 sessionStorage의 시각으로 판정한다(utils/locationRefresh.ts).

    **장소 이름(current_location·search_center)은 남긴다.** 함께 빼려다 되돌렸다 —
    되묻기 버튼("검색 범위를 넓혀서 다시")은 세션에 저장된 조건을 베껴 재실행하는데,
    이름이 사라지면 위치가 빈 채로 돌아 또 되묻기로 끝난다(2026-09-08 확인, 테스트
    47건). 화면이 위치를 되돌려 보내주는 경로에서는 괜찮지만 그 고리가 끊기는 자리가
    실제로 있다. 이름까지 빼려면 그 재실행 경로를 먼저 요청값 기준으로 고쳐야 한다
    (TP-256).

    **필드를 없애는 것이 아니라 DB에 안 적는 것이다.** 한 요청을 처리하는 동안에는
    그대로 쓴다. 그래서 원본을 건드리지 않고 사본을 만들어 돌려준다 — 저장 뒤에도
    진행 중인 턴은 값을 들고 있어야 한다.

    **저장소 두 구현이 모두 이 함수를 거친다.** 인메모리 쪽까지 거치게 한 이유는
    테스트가 현실과 다른 모양을 보지 않게 하려는 것이다 — 인메모리만 값을 계속
    들고 있으면, 저장이 사라져 깨지는 경로를 테스트가 통과시킨다.

    좌표를 서버에 심던 세 자리(runtime의 최초 턴, session_orchestrator의 매 턴 갱신,
    interpret 라우트)는 이 변경과 함께 없앴다. 여기서 어차피 안 남으므로 남겨두면
    턴마다 아무것도 저장하지 않는 쓰기가 한 번씩 더 나간다.
    """

    persisted = state.model_copy(deep=True)
    persisted.api_context.gps_location = None
    persisted.api_context.gps_location_updated_at = None
    persisted.api_context.gps_location_confirmed_at = None
    return persisted


class StateStore(Protocol):
    """State 저장소 인터페이스.

    구현체는 조회 시 복사본을 반환하고 저장 시 복사본을 보관해야 한다.
    호출 측이 save를 호출하지 않으면 변경이 반영되지 않아야,
    저장소 교체 시 동작이 달라지지 않는다.
    """

    # --- AgentState
    def get_state(self, session_id: str) -> AgentState | None: ...
    def save_state(self, state: AgentState) -> None: ...
    def delete_state(self, session_id: str) -> None: ...

    # --- RecommendationHistory
    def get_history(self, session_id: str) -> RecommendationHistory | None: ...
    def save_history(self, history: RecommendationHistory) -> None: ...
    def delete_history(self, session_id: str) -> None: ...

    # --- SavedPlaceList (가변 — 담기/빼기, SCHEDULE-12)
    # RecommendationHistory와 같은 read-modify-write 패턴이지만 별도
    # 엔티티다(SavedPlaceList docstring 참고).
    def get_saved_places(self, session_id: str) -> SavedPlaceList | None: ...
    def save_saved_places(self, saved: SavedPlaceList) -> None: ...
    def delete_saved_places(self, session_id: str) -> None: ...

    # -- UserPreferenceList (계정 단위) --
    # 이 둘만 session_id가 아니라 user_id로 조회한다. **"취향을 비운다"는 여전히
    # 빈 목록 저장이다** — 행을 지우면 다음 조회에서 "아직 고른 적 없음"과
    # "다 지웠음"이 구분되지 않기 때문이다.
    #
    # delete_preferences는 그 예외다. **탈퇴에는 "다음 조회"가 없다.** 구분할
    # 주체인 계정이 사라지므로 빈 목록으로 덮으면 user_id만 행에 남는다.
    # 그래서 화면이 아니라 탈퇴 경로에서만 쓴다(routes/account.py).
    def get_preferences(self, user_id: str) -> UserPreferenceList | None: ...
    def save_preferences(self, preferences: UserPreferenceList) -> None: ...
    def delete_preferences(self, user_id: str) -> None: ...

    # -- UserFavoriteList (계정 단위) --
    # 취향과 같은 자리의 값이라 같은 모양으로 둔다. 비우기와 삭제를 가르는 이유도
    # 같다 — "즐겨찾기를 비운다"는 빈 목록 저장이고, delete_favorites는 탈퇴 전용이다.
    def get_favorites(self, user_id: str) -> UserFavoriteList | None: ...
    def save_favorites(self, favorites: UserFavoriteList) -> None: ...
    def delete_favorites(self, user_id: str) -> None: ...

    # -- 사용자의 대화 목록 --
    # 사이드바 채팅 히스토리가 쓴다. 제목이 없는 세션(대화를 시작하지 않고
    # 만들어지기만 한 세션)은 목록에 넣지 않는다 — 사용자가 보기에 그건
    # 대화가 아니다.
    #
    # **만료(status='expired') 여부로는 거르지 않는다.** 만료는 "낡은 조건을
    # 버렸다"는 뜻이지 "그 대화가 없었다"는 뜻이 아니고, 지난 대화를 열어
    # 이어가는 것이 이 목록의 목적이다. 거르면 오래된 대화일수록 안 보인다.
    def list_sessions_for_user(self, user_id: str, limit: int) -> list[AgentState]: ...

    # -- 탈퇴용 세션 id 조회 --
    # 위 목록과 달리 **제목이 없는 세션도 담는다.** 저쪽은 "사용자가 보기에 대화인
    # 것"을 고르지만, 탈퇴는 남는 행이 없어야 하므로 기준이 다르다. 제목 없는 세션도
    # user_conditions·recent_turns를 들고 있어 지우지 않으면 개인정보가 남는다.
    # (2026-09-15 실측: user_id가 붙은 451건 중 200건이 제목이 없다.)
    def list_session_ids_for_user(self, user_id: str) -> list[str]: ...

    # --- ConditionChangeLog (append-only)
    def append_change_logs(self, logs: list[ConditionChangeLog]) -> None: ...
    def get_change_logs(self, session_id: str) -> list[ConditionChangeLog]: ...

    # --- TraceRecord (append-only)
    def append_traces(self, records: list[TraceRecord]) -> None: ...
    def get_traces(self, session_id: str) -> list[TraceRecord]: ...
    # 집계(TP-157)용. get_traces와 달리 세션 하나로 좁히지 않고 전체
    # 테이블을 대상으로 한다 — "최근 에러가 뭐였는지", "step별 평균
    # 지연시간"은 세션 단위로 물을 수 있는 질문이 아니다. since/until은
    # recorded_at 기준 반열린구간이며 둘 다 선택이다.
    def list_traces_for_stats(
        self, since: datetime | None = None, until: datetime | None = None
    ) -> list[TraceRecord]: ...

    # --- SessionMessage (append-only, TP-222 후속 — 화면 기록)
    # recent_turns(모델 맥락)·추천 이력(제외 목록)과 달리 자르지도 비우지도
    # 않는다. 지난 대화를 화면에 그대로 되돌리는 유일한 근거다.
    def append_session_messages(self, messages: list[SessionMessage]) -> None: ...
    def get_session_messages(self, session_id: str) -> list[SessionMessage]: ...
    def delete_session_messages(self, session_id: str) -> None: ...

    # --- SavedSchedule (계정 단위, SCHEDULE 카드 2)
    # UserPreferenceList와 같이 session_id가 아니라 user_id로 조회한다. 세션
    # TTL·30일 정리와 무관하게 남는다 — 사용자가 이름 붙여 저장한 것이 조용히
    # 사라지면 그것은 저장이 아니다. 그래서 만료 세션 정리(_delete_one)에도
    # 넣지 않는다. 계정이 지워질 때 DB의 FK cascade가 걷어간다.
    def save_schedule(self, schedule: SavedSchedule) -> SavedSchedule: ...
    def list_schedules_for_user(self, user_id: str, limit: int) -> list[SavedSchedule]: ...
    def get_schedule(self, schedule_id: str) -> SavedSchedule | None: ...
    def rename_schedule(self, schedule_id: str, title: str) -> SavedSchedule | None: ...
    def delete_schedule(self, schedule_id: str) -> bool: ...

    # --- FeedbackRecord (append-only)
    def append_feedback(self, records: list[FeedbackRecord]) -> None: ...
    def get_feedback(self, session_id: str) -> list[FeedbackRecord]: ...
    # 다른 메서드와 달리 세션 범위가 아니라 테이블 전체를 대상으로 한다 —
    # "나쁜 답변 찾기"는 특정 세션이 아니라 전체 응답 중에서 찾는 분석
    # 작업이라, session_id로 좁힐 수 없다.
    def list_dislike_feedback(self, limit: int) -> list[FeedbackRecord]: ...
    # 집계(TP-146)용. list_dislike_feedback과 달리 rating을 가리지 않고
    # (like까지 포함) limit도 없이 전량을 반환한다 — 통계는 상위 N건이
    # 아니라 전체 합이어야 의미가 있다. since/until은 recorded_at 기준
    # 반열린구간([since, until))이며 둘 다 선택이다.
    def list_feedback_for_stats(
        self, since: datetime | None = None, until: datetime | None = None
    ) -> list[FeedbackRecord]: ...

    # --- 정리(만료 세션 삭제, TP-134)
    # response_feedback은 세션 생애주기와 무관한 별도 분석 데이터라 대상에서
    # 제외한다 — 이 네 메서드는 scripts/cleanup_expired_sessions.py 전용이다.
    def list_stale_session_ids(self, cutoff: datetime) -> list[str]: ...
    def delete_change_logs(self, session_id: str) -> None: ...
    def delete_traces(self, session_id: str) -> None: ...
    # delete_session_messages는 위 SessionMessage 섹션에 있다.
    # delete_saved_places는 위 SavedPlaceList 섹션에 있다 — 만료 세션 정리
    # (scripts/cleanup_expired_sessions.py)도 같은 메서드를 쓴다.


class InMemoryStateStore:
    """프로세스 메모리 기반 구현. (Phase 1)

    서버 재시작 시 모든 데이터가 소멸한다. 이는 의도된 제약이며
    저장소 교체 시 해소된다. (계약 5.4절)
    """

    def __init__(self) -> None:
        self._states: dict[str, AgentState] = {}
        self._histories: dict[str, RecommendationHistory] = {}
        self._saved_places: dict[str, SavedPlaceList] = {}
        self._preferences: dict[str, UserPreferenceList] = {}
        self._favorites: dict[str, UserFavoriteList] = {}
        self._change_logs: dict[str, list[ConditionChangeLog]] = {}
        self._traces: dict[str, list[TraceRecord]] = {}
        self._feedback: dict[str, list[FeedbackRecord]] = {}
        self._session_messages: dict[str, list[SessionMessage]] = {}
        self._saved_schedules: dict[str, SavedSchedule] = {}
        self._saved_schedule_seq = 0

    # ------------------------------------------------------------ State

    def get_state(self, session_id: str) -> AgentState | None:
        state = self._states.get(session_id)
        return state.model_copy(deep=True) if state else None

    def save_state(self, state: AgentState) -> None:
        self._states[state.session_id] = for_persistence(state)

    def delete_state(self, session_id: str) -> None:
        self._states.pop(session_id, None)

    # ------------------------------------------------------------ History

    def get_history(self, session_id: str) -> RecommendationHistory | None:
        history = self._histories.get(session_id)
        return history.model_copy(deep=True) if history else None

    def save_history(self, history: RecommendationHistory) -> None:
        self._histories[history.session_id] = history.model_copy(deep=True)

    def delete_history(self, session_id: str) -> None:
        self._histories.pop(session_id, None)

    # ------------------------------------------------------------ SavedPlaces

    def get_saved_places(self, session_id: str) -> SavedPlaceList | None:
        saved = self._saved_places.get(session_id)
        return saved.model_copy(deep=True) if saved else None

    def save_saved_places(self, saved: SavedPlaceList) -> None:
        self._saved_places[saved.session_id] = saved.model_copy(deep=True)

    def delete_saved_places(self, session_id: str) -> None:
        self._saved_places.pop(session_id, None)

    # ------------------------------------------------------------ Preferences

    def list_sessions_for_user(self, user_id: str, limit: int) -> list[AgentState]:
        owned = [
            state
            for state in self._states.values()
            if state.user_id == user_id and state.title is not None
        ]
        owned.sort(key=lambda state: state.last_active_at, reverse=True)
        return [state.model_copy(deep=True) for state in owned[:limit]]

    def list_session_ids_for_user(self, user_id: str) -> list[str]:
        return [
            session_id
            for session_id, state in self._states.items()
            if state.user_id == user_id
        ]

    def get_preferences(self, user_id: str) -> UserPreferenceList | None:
        preferences = self._preferences.get(user_id)
        return preferences.model_copy(deep=True) if preferences else None

    def save_preferences(self, preferences: UserPreferenceList) -> None:
        self._preferences[preferences.user_id] = preferences.model_copy(deep=True)

    def delete_preferences(self, user_id: str) -> None:
        self._preferences.pop(user_id, None)

    def get_favorites(self, user_id: str) -> UserFavoriteList | None:
        favorites = self._favorites.get(user_id)
        return favorites.model_copy(deep=True) if favorites else None

    def save_favorites(self, favorites: UserFavoriteList) -> None:
        self._favorites[favorites.user_id] = favorites.model_copy(deep=True)

    def delete_favorites(self, user_id: str) -> None:
        self._favorites.pop(user_id, None)

    # ------------------------------------------------------------ ChangeLog

    def append_change_logs(self, logs: list[ConditionChangeLog]) -> None:
        """append-only. 기존 기록을 수정하거나 삭제하지 않는다."""
        for log in logs:
            self._change_logs.setdefault(log.session_id, []).append(
                log.model_copy(deep=True)
            )

    def get_change_logs(self, session_id: str) -> list[ConditionChangeLog]:
        logs = self._change_logs.get(session_id, [])
        return [log.model_copy(deep=True) for log in logs]

    # ------------------------------------------------------------ Trace

    def append_traces(self, records: list[TraceRecord]) -> None:
        """append-only. 기존 기록을 수정하거나 삭제하지 않는다."""
        for record in records:
            self._traces.setdefault(record.session_id, []).append(
                record.model_copy(deep=True)
            )

    def get_traces(self, session_id: str) -> list[TraceRecord]:
        records = self._traces.get(session_id, [])
        return [record.model_copy(deep=True) for record in records]

    def list_traces_for_stats(
        self, since: datetime | None = None, until: datetime | None = None
    ) -> list[TraceRecord]:
        all_records = [
            record for records in self._traces.values() for record in records
        ]
        if since is not None:
            all_records = [r for r in all_records if r.recorded_at >= since]
        if until is not None:
            all_records = [r for r in all_records if r.recorded_at < until]
        return [record.model_copy(deep=True) for record in all_records]

    # ------------------------------------------------------------ 화면 기록

    def append_session_messages(self, messages: list[SessionMessage]) -> None:
        """append-only. 기존 기록을 수정하거나 삭제하지 않는다."""
        for message in messages:
            self._session_messages.setdefault(message.session_id, []).append(
                message.model_copy(deep=True)
            )

    def get_session_messages(self, session_id: str) -> list[SessionMessage]:
        messages = self._session_messages.get(session_id, [])
        return [message.model_copy(deep=True) for message in messages]

    def delete_session_messages(self, session_id: str) -> None:
        self._session_messages.pop(session_id, None)

    # ------------------------------------------------------------ 저장한 일정

    def save_schedule(self, schedule: SavedSchedule) -> SavedSchedule:
        """저장하고 id가 채워진 것을 돌려준다.

        같은 (user_id, run_id)가 이미 있으면 **새로 만들지 않고 그것을 돌려준다.**
        DB의 부분 유니크 인덱스와 같은 판단이다 — 저장 버튼을 두 번 누르거나
        요청이 재시도되면 목록에 같은 일정이 두 줄로 보이는데, 사용자에게 그것은
        그 자체로 버그다(saved_places.add의 멱등 처리와 같은 이유).
        """
        if schedule.run_id is not None:
            for existing in self._saved_schedules.values():
                if existing.user_id == schedule.user_id and existing.run_id == schedule.run_id:
                    return existing.model_copy(deep=True)
        self._saved_schedule_seq += 1
        stored = schedule.model_copy(deep=True)
        stored.id = f"sched_{self._saved_schedule_seq:08d}"
        self._saved_schedules[stored.id] = stored
        return stored.model_copy(deep=True)

    def list_schedules_for_user(self, user_id: str, limit: int) -> list[SavedSchedule]:
        """내 일정을 최근 저장순으로. 남의 것은 애초에 걸리지 않는다."""
        mine = [s for s in self._saved_schedules.values() if s.user_id == user_id]
        mine.sort(key=lambda s: s.created_at, reverse=True)
        return [s.model_copy(deep=True) for s in mine[:limit]]

    def get_schedule(self, schedule_id: str) -> SavedSchedule | None:
        """**소유권을 여기서 보지 않는다.** 저장소는 행을 돌려주고, 남의 것인지는
        service가 principal과 대조한다 — 저장소가 신원을 알면 계층이 섞인다."""
        found = self._saved_schedules.get(schedule_id)
        return found.model_copy(deep=True) if found else None

    def rename_schedule(self, schedule_id: str, title: str) -> SavedSchedule | None:
        found = self._saved_schedules.get(schedule_id)
        if found is None:
            return None
        found.title = title
        found.updated_at = now_kst()
        return found.model_copy(deep=True)

    def delete_schedule(self, schedule_id: str) -> bool:
        return self._saved_schedules.pop(schedule_id, None) is not None

    # ------------------------------------------------------------ Feedback

    def append_feedback(self, records: list[FeedbackRecord]) -> None:
        """append-only. 기존 기록을 수정하거나 삭제하지 않는다."""
        for record in records:
            self._feedback.setdefault(record.session_id, []).append(
                record.model_copy(deep=True)
            )

    def get_feedback(self, session_id: str) -> list[FeedbackRecord]:
        records = self._feedback.get(session_id, [])
        return [record.model_copy(deep=True) for record in records]

    def list_dislike_feedback(self, limit: int) -> list[FeedbackRecord]:
        all_records = [
            record for records in self._feedback.values() for record in records
        ]
        dislikes = [record for record in all_records if record.rating == "dislike"]
        dislikes.sort(key=lambda record: record.recorded_at, reverse=True)
        return [record.model_copy(deep=True) for record in dislikes[:limit]]

    def list_feedback_for_stats(
        self, since: datetime | None = None, until: datetime | None = None
    ) -> list[FeedbackRecord]:
        all_records = [
            record for records in self._feedback.values() for record in records
        ]
        if since is not None:
            all_records = [r for r in all_records if r.recorded_at >= since]
        if until is not None:
            all_records = [r for r in all_records if r.recorded_at < until]
        return [record.model_copy(deep=True) for record in all_records]

    # ------------------------------------------------------------ 정리(TP-134)

    def list_stale_session_ids(self, cutoff: datetime) -> list[str]:
        return [
            session_id
            for session_id, state in self._states.items()
            if state.last_active_at < cutoff
        ]

    def delete_change_logs(self, session_id: str) -> None:
        self._change_logs.pop(session_id, None)

    def delete_traces(self, session_id: str) -> None:
        self._traces.pop(session_id, None)

    # ------------------------------------------------------------ 테스트용

    def clear(self) -> None:
        """전체 초기화. 테스트에서만 사용한다."""
        self._states.clear()
        self._histories.clear()
        self._saved_places.clear()
        self._change_logs.clear()
        self._session_messages.clear()
        self._saved_schedules.clear()
        self._saved_schedule_seq = 0
        self._traces.clear()
        self._feedback.clear()

    def session_ids(self) -> list[str]:
        """보관 중인 세션 목록. 디버깅·테스트용."""
        return list(self._states.keys())


# 프로세스 단위 기본 저장소(Phase 1, memory 백엔드).
_default_store = InMemoryStateStore()

# Phase 2(supabase 백엔드) 지연 생성 캐시. STATE_STORE_BACKEND=memory인 환경
# (테스트 등)에서는 한 번도 안 만들어진다 — Supabase 자격증명이 없어도
# 이 모듈을 import할 수 있어야 하기 때문이다.
_supabase_store: StateStore | None = None


def _build_supabase_store() -> StateStore:
    """SupabaseStateStore를 최초 호출 시 한 번만 만들어서 재사용한다.

    httpx.Client는 프로세스 생애주기 동안 재사용한다(연결 재사용 방식은
    설계 문서 db-store-design-v2.md 6절의 미결 사항 중 가장 단순한 선택 —
    실제 부하 확인 후 조정 가능). timeout은 다른 real provider와 동일하게
    EXTERNAL_API_TIMEOUT_SECONDS를 따른다.
    """
    global _supabase_store
    if _supabase_store is None:
        from app.state.supabase_store import SupabaseStateStore

        client = httpx.Client()
        _supabase_store = SupabaseStateStore(
            supabase_url=settings.supabase_url,
            secret_key=settings.supabase_secret_key,
            client=client,
            timeout_seconds=settings.external_api_timeout_seconds,
        )
    return _supabase_store


def get_store() -> StateStore:
    """기본 저장소를 반환한다.

    STATE_STORE_BACKEND 설정에 따라 InMemory(memory, 기본값) 또는
    Supabase(supabase) 구현체를 고정 반환한다. FastAPI 의존성 주입에서
    이 함수를 사용하면, 테스트에서 다른 구현으로 교체하기 쉽다.
    """
    if settings.state_store_backend == "supabase":
        return _build_supabase_store()
    return _default_store


def _reset_supabase_store_for_tests() -> None:
    """지연 생성된 Supabase 저장소 캐시를 초기화한다. 테스트에서만 사용한다."""
    global _supabase_store
    _supabase_store = None