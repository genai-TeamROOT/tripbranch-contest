"""Package B - 익명 세션 관리 테스트.

계약 문서: docs/package-b/agent-state-contract-v1.md (4절, 5절)

session.py 전체를 커버하는 첫 테스트 파일이다. get_or_create_session()이
state.status == "expired"를 확인하지 않아, RESET_FULL로 만료 처리된 세션이
TTL 내 재요청되면 그대로 재사용되던 버그(계약 5.2절 3번 위반)의 회귀 테스트를
포함한다.
"""

from datetime import timedelta

import pytest

from app.auth.principal import Principal
from app.state.errors import SessionOwnershipError
from app.state.history import record_recommended, record_rejected
from app.state.schema import AgentState, RecommendedItemInput, now_kst
from app.state.session import (
    RESET_FULL,
    RESET_HISTORY,
    RESET_SOFT,
    apply_reset,
    attach_user_id,
    create_session,
    get_or_create_session,
    is_gps_expired,
    is_session_expired,
    is_weather_expired,
    new_run_id,
    new_session_id,
    new_trace_id,
    peek_session,
    touch,
    verify_ownership,
)
from app.state.store import InMemoryStateStore


@pytest.fixture
def store() -> InMemoryStateStore:
    return InMemoryStateStore()


# ================================================================ 식별자

class TestIdGeneration:
    def test_세션_ID는_sess_접두어를_가진다(self):
        assert new_session_id().startswith("sess_")

    def test_실행_ID는_run_접두어를_가진다(self):
        assert new_run_id().startswith("run_")

    def test_trace_ID는_trace_접두어를_가진다(self):
        assert new_trace_id().startswith("trace_")

    def test_연속_생성해도_서로_다르다(self):
        ids = {new_session_id() for _ in range(20)}
        assert len(ids) == 20

    def test_생성_순서대로_타임스탬프_부분이_정렬된다(self):
        """접두어 뒤 타임스탬프로 정렬 가능해야 한다. (계약 4.4절)

        _generate_id()는 "접두어_13자리밀리초타임스탬프12자리랜덤"
        형태라, 문자열 전체가 아니라 타임스탬프 부분(접두어 뒤 13자리)만
        시간순 정렬이 보장된다. 같은 밀리초에 여러 번 호출되면 뒤에 붙는
        랜덤 부분끼리는 순서가 뒤바뀔 수 있어 전체 문자열 비교로는 실패할
        수 있다(실제로 빠른 머신에서 재현됨) — 그래서 타임스탬프 부분만
        떼어 비교한다.
        """
        prefix_len = len("sess_")
        ts_len = 13

        earlier = new_session_id()
        later = new_session_id()

        earlier_ts = earlier[prefix_len : prefix_len + ts_len]
        later_ts = later[prefix_len : prefix_len + ts_len]

        assert earlier_ts <= later_ts


# ================================================================ 만료 판정

class TestExpiryChecks:
    def test_방금_활동한_세션은_만료가_아니다(self):
        state = AgentState(session_id="sess_A")
        assert is_session_expired(state) is False

    def test_30분_초과하면_세션이_만료된다(self):
        state = AgentState(session_id="sess_A")
        state.last_active_at = now_kst() - timedelta(minutes=31)
        assert is_session_expired(state) is True

    def test_30분_경계_직전은_만료가_아니다(self):
        state = AgentState(session_id="sess_A")
        state.last_active_at = now_kst() - timedelta(minutes=29)
        assert is_session_expired(state) is False

    def test_GPS를_한번도_안받았으면_만료로_간주한다(self):
        state = AgentState(session_id="sess_A")
        assert is_gps_expired(state) is True

    def test_GPS가_1시간_이내면_만료가_아니다(self):
        state = AgentState(session_id="sess_A")
        state.api_context.gps_location_updated_at = now_kst() - timedelta(minutes=30)
        assert is_gps_expired(state) is False

    def test_GPS가_1시간_초과면_만료된다(self):
        state = AgentState(session_id="sess_A")
        state.api_context.gps_location_updated_at = now_kst() - timedelta(hours=2)
        assert is_gps_expired(state) is True

    def test_날씨를_한번도_안받았으면_만료로_간주한다(self):
        state = AgentState(session_id="sess_A")
        assert is_weather_expired(state) is True

    def test_날씨가_1시간_초과면_만료된다(self):
        state = AgentState(session_id="sess_A")
        state.api_context.api_weather_updated_at = now_kst() - timedelta(hours=2)
        assert is_weather_expired(state) is True


# ================================================================ 생성·조회

class TestCreateSession:
    def test_새_세션은_active_상태다(self, store):
        state = create_session(store)
        assert state.status == "active"

    def test_저장소에_바로_반영된다(self, store):
        state = create_session(store)
        assert store.get_state(state.session_id) is not None


class TestGetOrCreateSession:
    def test_session_id가_없으면_신규_발급한다(self, store):
        state, created = get_or_create_session(store, None)
        assert created is True
        assert state.status == "active"

    def test_저장소에_없는_session_id는_신규_발급한다(self, store):
        state, created = get_or_create_session(store, "sess_없음")
        assert created is True
        assert state.session_id != "sess_없음"

    def test_존재하고_활성인_세션은_그대로_반환한다(self, store):
        original = create_session(store)

        state, created = get_or_create_session(store, original.session_id)

        assert created is False
        assert state.session_id == original.session_id

    def test_TTL_초과_세션은_expired_처리하고_신규_발급한다(self, store):
        original = create_session(store)
        original.last_active_at = now_kst() - timedelta(minutes=31)
        store.save_state(original)

        state, created = get_or_create_session(store, original.session_id)

        assert created is True
        assert state.session_id != original.session_id
        # 옛 세션은 저장소에서 expired로 남아있어야 한다 (계약 5.4절 "만료 처리").
        assert store.get_state(original.session_id).status == "expired"

    def test_이미_expired_상태인_세션은_TTL과_무관하게_신규_발급한다(self, store):
        """회귀 테스트: RESET_FULL 등으로 status만 expired가 되고
        last_active_at은 아직 TTL 이내인 경우에도 재사용하면 안 된다.
        (계약 5.2절 3번 — status가 expired인 session_id는 무조건 신규 발급)
        """
        original = create_session(store)
        original.status = "expired"
        # last_active_at은 방금이라 TTL 기준으로는 아직 살아있다.
        store.save_state(original)
        assert is_session_expired(original) is False  # 전제 확인

        state, created = get_or_create_session(store, original.session_id)

        assert created is True
        assert state.session_id != original.session_id
        assert state.status == "active"


# ================================================================ 신원 연결

class TestAttachUserId:
    """TP-101 3단계, D-063 결정 3의 "채우되 덮어쓰지 않는다" 규칙."""

    def test_principal이_없으면_아무것도_하지_않는다(self):
        state = AgentState(session_id="sess_A")
        attach_user_id(state, None)
        assert state.user_id is None

    def test_빈_세션은_principal의_user_id로_채워진다(self):
        state = AgentState(session_id="sess_A")
        principal = Principal(user_id="user-1", is_anonymous=True)

        attach_user_id(state, principal)

        assert state.user_id == "user-1"

    def test_이미_user_id가_있으면_덮어쓰지_않는다(self):
        state = AgentState(session_id="sess_A", user_id="user-원래주인")
        principal = Principal(user_id="user-다른사람", is_anonymous=True)

        attach_user_id(state, principal)

        assert state.user_id == "user-원래주인"

    def test_정식_로그인으로_전환돼도_user_id는_그대로다(self):
        """is_anonymous만 바뀌고 user_id는 유지되는 경우(D-062 2절)도
        덮어쓰기 금지 규칙이 동일하게 적용되는지 확인한다."""
        state = AgentState(session_id="sess_A", user_id="user-1")
        principal = Principal(user_id="user-1", is_anonymous=False)

        attach_user_id(state, principal)

        assert state.user_id == "user-1"


class TestVerifyOwnership:
    """D-063 결정 2 후속(D-073) — session_id만으로 남의 세션에 접근하지 못하게 막는다."""

    def test_principal이_없으면_통과한다(self):
        state = AgentState(session_id="sess_A", user_id="user-1")
        verify_ownership(state, None)  # 예외 없이 통과해야 한다

    def test_user_id가_비어있으면_통과한다(self):
        state = AgentState(session_id="sess_A")
        principal = Principal(user_id="user-1", is_anonymous=True)
        verify_ownership(state, principal)  # 예외 없이 통과해야 한다

    def test_같은_user_id는_통과한다(self):
        state = AgentState(session_id="sess_A", user_id="user-1")
        principal = Principal(user_id="user-1", is_anonymous=True)
        verify_ownership(state, principal)  # 예외 없이 통과해야 한다

    def test_다른_user_id는_거부된다(self):
        state = AgentState(session_id="sess_A", user_id="user-원래주인")
        principal = Principal(user_id="user-다른사람", is_anonymous=True)

        with pytest.raises(SessionOwnershipError):
            verify_ownership(state, principal)


class TestPeekSession:
    def test_session_id가_없으면_None(self, store):
        assert peek_session(store, None) is None

    def test_저장소에_없으면_None(self, store):
        assert peek_session(store, "sess_없음") is None

    def test_expired_상태면_None(self, store):
        state = create_session(store)
        state.status = "expired"
        store.save_state(state)

        assert peek_session(store, state.session_id) is None

    def test_TTL_초과면_None(self, store):
        state = create_session(store)
        state.last_active_at = now_kst() - timedelta(minutes=31)
        store.save_state(state)

        assert peek_session(store, state.session_id) is None

    def test_활성_세션은_그대로_반환한다(self, store):
        state = create_session(store)
        got = peek_session(store, state.session_id)
        assert got is not None
        assert got.session_id == state.session_id

    def test_조회만_하고_세션을_생성하지_않는다(self, store):
        peek_session(store, "sess_없음")
        assert store.session_ids() == []

    def test_TTL_초과_세션을_expired로_바꾸지_않는다(self, store):
        """get_or_create_session과 달리 peek은 조회 전용이라 상태를 쓰지 않는다."""
        state = create_session(store)
        state.last_active_at = now_kst() - timedelta(minutes=31)
        store.save_state(state)

        peek_session(store, state.session_id)

        assert store.get_state(state.session_id).status == "active"


class TestTouch:
    def test_last_active_at만_갱신하고_updated_at은_그대로다(self):
        state = AgentState(session_id="sess_A")
        original_updated_at = state.updated_at
        state.last_active_at = now_kst() - timedelta(minutes=10)

        touch(state)

        assert state.updated_at == original_updated_at
        assert (now_kst() - state.last_active_at) < timedelta(seconds=1)


# ================================================================ 초기화

class TestApplyReset:
    def test_scope가_None이면_아무것도_안한다(self, store):
        state = create_session(store)
        result, created = apply_reset(store, state, None)

        assert created is False
        assert result.session_id == state.session_id
        assert result.status == "active"

    def test_soft는_세션을_유지한다(self, store):
        state = create_session(store)
        result, created = apply_reset(store, state, RESET_SOFT)

        assert created is False
        assert result.session_id == state.session_id
        assert store.get_state(state.session_id).status == "active"

    def test_history는_추천만_비우고_거절은_유지한다(self, store):
        state = create_session(store)
        record_recommended(
            store, state.session_id, "run_1", [RecommendedItemInput(place_id="p1", rank=1)]
        )
        record_rejected(store, state.session_id, "run_1", [("p2", None)])

        result, created = apply_reset(store, state, RESET_HISTORY)

        assert created is False
        history = store.get_history(state.session_id)
        assert history.recommended == []
        assert len(history.rejected) == 1

    def test_history는_세션을_만료시키지_않는다(self, store):
        state = create_session(store)
        apply_reset(store, state, RESET_HISTORY)
        assert store.get_state(state.session_id).status == "active"

    def test_full은_기존_세션을_expired_처리하고_신규_발급한다(self, store):
        state = create_session(store)
        record_recommended(
            store, state.session_id, "run_1", [RecommendedItemInput(place_id="p1", rank=1)]
        )
        record_rejected(store, state.session_id, "run_1", [("p2", None)])

        result, created = apply_reset(store, state, RESET_FULL)

        assert created is True
        assert result.session_id != state.session_id
        assert store.get_state(state.session_id).status == "expired"

    def test_full은_이전_세션의_이력도_모두_지운다(self, store):
        state = create_session(store)
        record_recommended(
            store, state.session_id, "run_1", [RecommendedItemInput(place_id="p1", rank=1)]
        )

        apply_reset(store, state, RESET_FULL)

        assert store.get_history(state.session_id) is None

    def test_full_이후_옛_session_id로_재요청해도_신규_세션이_나온다(self, store):
        """apply_reset(RESET_FULL) → get_or_create_session 연계 회귀 테스트.

        옛 session_id가 TTL 내에 재요청되더라도(예: 클라이언트가 캐시된 ID를
        실수로 재사용) 만료된 세션이 아니라 완전히 새로운 세션이 나와야 한다.
        """
        state = create_session(store)
        apply_reset(store, state, RESET_FULL)

        reused, created = get_or_create_session(store, state.session_id)

        assert created is True
        assert reused.session_id != state.session_id

    def test_알수없는_scope는_무시한다(self, store):
        state = create_session(store)
        result, created = apply_reset(store, state, "이상한값")

        assert created is False
        assert result.session_id == state.session_id
        assert store.get_state(state.session_id).status == "active"
