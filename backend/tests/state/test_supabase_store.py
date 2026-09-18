from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest

from app.state.errors import StateStoreError
from app.state.schema import (
    AgentState,
    ConditionChangeLog,
    PendingInfoContext,
    RecommendationHistory,
    SavedPlaceItem,
    SavedPlaceList,
    SavedSchedule,
    UserConditions,
    UserPreference,
    UserPreferenceList,
)
from app.state.supabase_store import SupabaseStateStore

SESSION_ID = "session-1"
USER_ID = "3f1a9c04-0000-4000-8000-000000000001"


def _store(transport: httpx.MockTransport) -> SupabaseStateStore:
    client = httpx.Client(transport=transport)
    return SupabaseStateStore(
        supabase_url="https://project.supabase.co/",
        secret_key="sb_secret_test",
        client=client,
    )


# ------------------------------------------------------------ AgentState


def test_get_state_returns_none_when_not_found() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=[]))
    assert _store(transport).get_state(SESSION_ID) is None


def test_get_state_parses_row_into_agent_state() -> None:
    row = {
        "session_id": SESSION_ID,
        "user_conditions": {"weather": "sunny"},
        "api_context": {"gps_location": "37.5,127.0"},
        "condition_version": 2,
        "last_run_id": "run-1",
        "last_intent": "RECOMMEND",
        "pending_clarification": "location_required",
        "status": "active",
        "created_at": "2026-07-28T00:00:00+09:00",
        "updated_at": "2026-07-28T00:00:00+09:00",
        "last_active_at": "2026-07-28T00:00:00+09:00",
    }
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=[row]))
    state = _store(transport).get_state(SESSION_ID)
    assert state is not None
    assert state.session_id == SESSION_ID
    assert state.user_conditions.weather == "sunny"
    assert state.condition_version == 2
    assert state.pending_clarification == "location_required"


def test_get_state_defaults_pending_clarification_when_column_absent() -> None:
    """08-03 마이그레이션 이전 행(컬럼 없음)을 읽어도 깨지지 않아야 한다."""
    row = {
        "session_id": SESSION_ID,
        "user_conditions": {},
        "api_context": {},
        "condition_version": 0,
        "status": "active",
        "created_at": "2026-07-28T00:00:00+09:00",
        "updated_at": "2026-07-28T00:00:00+09:00",
        "last_active_at": "2026-07-28T00:00:00+09:00",
    }
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=[row]))
    state = _store(transport).get_state(SESSION_ID)
    assert state is not None
    assert state.pending_clarification is None


def test_get_state_uses_secret_key_and_filter() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["request"] = request
        return httpx.Response(200, json=[])

    transport = httpx.MockTransport(handler)
    _store(transport).get_state(SESSION_ID)

    request = seen["request"]
    assert isinstance(request, httpx.Request)
    assert request.url.path == "/rest/v1/agent_states"
    assert request.headers["apikey"] == "sb_secret_test"
    assert request.url.params["session_id"] == f"eq.{SESSION_ID}"


def test_save_state_upserts_with_on_conflict_session_id() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["request"] = request
        return httpx.Response(201)

    transport = httpx.MockTransport(handler)
    state = AgentState(session_id=SESSION_ID, user_conditions=UserConditions())
    _store(transport).save_state(state)

    request = seen["request"]
    assert isinstance(request, httpx.Request)
    assert request.method == "POST"
    assert request.url.params["on_conflict"] == "session_id"
    assert request.headers["prefer"] == "resolution=merge-duplicates,return=minimal"

def test_save_state_includes_pending_clarification_in_body() -> None:
    """save_state가 model_dump()를 그대로 보내므로, 값이 있으면 body에 실려야 한다."""
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["request"] = request
        return httpx.Response(201)

    transport = httpx.MockTransport(handler)
    state = AgentState(
        session_id=SESSION_ID,
        user_conditions=UserConditions(),
        pending_clarification="location_required",
    )
    _store(transport).save_state(state)

    request = seen["request"]
    assert isinstance(request, httpx.Request)
    body = json.loads(request.content)
    assert body["pending_clarification"] == "location_required"


def test_get_state_defaults_pending_info_context_when_column_absent() -> None:
    """202608270001 마이그레이션 이전 행(컬럼 없음)을 읽어도 깨지지 않아야 한다."""
    row = {
        "session_id": SESSION_ID,
        "user_conditions": {},
        "api_context": {},
        "condition_version": 0,
        "status": "active",
        "created_at": "2026-07-28T00:00:00+09:00",
        "updated_at": "2026-07-28T00:00:00+09:00",
        "last_active_at": "2026-07-28T00:00:00+09:00",
    }
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=[row]))
    state = _store(transport).get_state(SESSION_ID)
    assert state is not None
    assert state.pending_info_context is None


def test_save_state_includes_pending_info_context_in_body() -> None:
    """INFO의 place_ambiguous 되묻기가 저장한 원래 질문도 model_dump()로 그대로
    실려야 버튼 클릭 시 재분류 없이 이어받을 수 있다(D-088)."""
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["request"] = request
        return httpx.Response(201)

    transport = httpx.MockTransport(handler)
    state = AgentState(
        session_id=SESSION_ID,
        user_conditions=UserConditions(),
        pending_clarification="place_ambiguous",
        pending_info_context=PendingInfoContext(
            question_type="parking", place_context="explicit", specific_question="주차장 정보"
        ),
    )
    _store(transport).save_state(state)

    request = seen["request"]
    assert isinstance(request, httpx.Request)
    body = json.loads(request.content)
    assert body["pending_info_context"]["question_type"] == "parking"


def test_delete_state_filters_by_session_id() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["request"] = request
        return httpx.Response(200)

    transport = httpx.MockTransport(handler)
    _store(transport).delete_state(SESSION_ID)

    request = seen["request"]
    assert isinstance(request, httpx.Request)
    assert request.method == "DELETE"
    assert request.url.params["session_id"] == f"eq.{SESSION_ID}"


# ------------------------------------------------------------ History


def test_get_history_returns_none_when_not_found() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=[]))
    assert _store(transport).get_history(SESSION_ID) is None


def test_save_history_sends_whole_object() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["request"] = request
        return httpx.Response(201)

    transport = httpx.MockTransport(handler)
    history = RecommendationHistory(session_id=SESSION_ID)
    _store(transport).save_history(history)

    request = seen["request"]
    assert isinstance(request, httpx.Request)
    assert request.url.params["on_conflict"] == "session_id"


# ------------------------------------------------------------ SavedPlaces


def test_get_saved_places_returns_none_when_not_found() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=[]))
    assert _store(transport).get_saved_places(SESSION_ID) is None


def test_get_saved_places_parses_row() -> None:
    row = {
        "session_id": SESSION_ID,
        "user_id": None,
        "items": [
            {
                "place_id": "p1",
                "name": "경복궁",
                "saved_from_run_id": "run-1",
                "saved_at": "2026-08-31T00:00:00+09:00",
                "latitude": None,
                "longitude": None,
            }
        ],
        "updated_at": "2026-08-31T00:00:00+09:00",
    }
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=[row]))
    saved = _store(transport).get_saved_places(SESSION_ID)
    assert saved is not None
    assert [item.place_id for item in saved.items] == ["p1"]
    assert saved.items[0].name == "경복궁"


def test_get_saved_places_raises_on_invalid_row() -> None:
    row = {"session_id": SESSION_ID, "items": "배열이_아니다"}
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=[row]))
    with pytest.raises(StateStoreError):
        _store(transport).get_saved_places(SESSION_ID)


def test_save_saved_places_upserts_on_session_id() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["request"] = request
        return httpx.Response(201)

    transport = httpx.MockTransport(handler)
    saved = SavedPlaceList(
        session_id=SESSION_ID,
        items=[
            SavedPlaceItem(place_id="p1", name="경복궁", saved_from_run_id="run-1")
        ],
    )
    _store(transport).save_saved_places(saved)

    request = seen["request"]
    assert isinstance(request, httpx.Request)
    assert request.url.path.endswith("/saved_place_lists")
    assert request.url.params["on_conflict"] == "session_id"
    body = json.loads(request.content)
    assert [item["place_id"] for item in body["items"]] == ["p1"]


def test_delete_saved_places_filters_by_session_id() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["request"] = request
        return httpx.Response(204)

    transport = httpx.MockTransport(handler)
    _store(transport).delete_saved_places(SESSION_ID)

    request = seen["request"]
    assert isinstance(request, httpx.Request)
    assert request.method == "DELETE"
    assert request.url.params["session_id"] == f"eq.{SESSION_ID}"


# ------------------------------------------------------------ ChangeLog


def test_append_change_logs_skips_empty_list() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(201)

    transport = httpx.MockTransport(handler)
    _store(transport).append_change_logs([])
    assert calls == []


def test_append_change_logs_posts_all_records() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["request"] = request
        return httpx.Response(201)

    transport = httpx.MockTransport(handler)
    logs = [
        ConditionChangeLog(session_id=SESSION_ID, run_id="run-1", seq=1, op="Add"),
        ConditionChangeLog(session_id=SESSION_ID, run_id="run-1", seq=2, op="Update"),
    ]
    _store(transport).append_change_logs(logs)

    request = seen["request"]
    assert isinstance(request, httpx.Request)
    assert request.method == "POST"
    assert request.url.path == "/rest/v1/condition_change_logs"
    assert request.headers["prefer"] == "return=minimal"


def test_get_change_logs_orders_by_id_ascending() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["request"] = request
        return httpx.Response(
            200,
            json=[
                {
                    "id": 1,
                    "session_id": SESSION_ID,
                    "run_id": "run-1",
                    "seq": 1,
                    "op": "Add",
                    "applied_at": "2026-07-28T00:00:00+09:00",
                }
            ],
        )

    transport = httpx.MockTransport(handler)
    logs = _store(transport).get_change_logs(SESSION_ID)

    request = seen["request"]
    assert isinstance(request, httpx.Request)
    assert request.url.params["order"] == "id.asc"
    assert len(logs) == 1
    assert logs[0].op == "Add"


# ------------------------------------------------------------ Trace


def test_append_traces_skips_empty_list() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(201)

    transport = httpx.MockTransport(handler)
    _store(transport).append_traces([])
    assert calls == []


def test_get_traces_parses_rows_into_trace_records() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json=[
                {
                    "id": 1,
                    "session_id": SESSION_ID,
                    "run_id": "run-1",
                    "trace_id": "trace-1",
                    "step": "llm_interpret",
                    "recorded_at": "2026-07-28T00:00:00+09:00",
                }
            ],
        )
    )
    traces = _store(transport).get_traces(SESSION_ID)
    assert len(traces) == 1
    assert traces[0].step == "llm_interpret"


def test_list_traces_for_stats_no_range_omits_recorded_at_filter() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["request"] = request
        return httpx.Response(200, json=[])

    transport = httpx.MockTransport(handler)
    _store(transport).list_traces_for_stats()

    request = seen["request"]
    assert "session_id" not in request.url.params
    assert "recorded_at" not in request.url.params
    assert "and" not in request.url.params
    assert request.url.params["order"] == "recorded_at.asc"


def test_list_traces_for_stats_since_only_uses_gte() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["request"] = request
        return httpx.Response(200, json=[])

    transport = httpx.MockTransport(handler)
    since = datetime(2026, 8, 1, tzinfo=UTC)
    _store(transport).list_traces_for_stats(since=since)

    request = seen["request"]
    assert request.url.params["recorded_at"] == f"gte.{since.isoformat()}"


def test_list_traces_for_stats_since_and_until_uses_and_filter() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["request"] = request
        return httpx.Response(200, json=[])

    transport = httpx.MockTransport(handler)
    since = datetime(2026, 8, 1, tzinfo=UTC)
    until = datetime(2026, 8, 8, tzinfo=UTC)
    _store(transport).list_traces_for_stats(since=since, until=until)

    request = seen["request"]
    assert "recorded_at" not in request.url.params
    assert request.url.params["and"] == (
        f"(recorded_at.gte.{since.isoformat()},recorded_at.lt.{until.isoformat()})"
    )


def test_list_traces_for_stats_includes_rows_across_sessions() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json=[
                {
                    "id": 1,
                    "session_id": SESSION_ID,
                    "run_id": "run-1",
                    "trace_id": "trace-1",
                    "step": "llm_interpret",
                    "recorded_at": "2026-08-21T00:00:00+09:00",
                }
            ],
        )
    )
    records = _store(transport).list_traces_for_stats()
    assert len(records) == 1
    assert records[0].step == "llm_interpret"


def _trace_row(i: int) -> dict[str, object]:
    return {
        "id": i,
        "session_id": f"sess_{i}",
        "run_id": f"run_{i}",
        "trace_id": f"trace_{i}",
        "step": "llm_interpret",
        "recorded_at": "2026-08-21T00:00:00+09:00",
    }


def test_list_traces_for_stats_paginates_beyond_postgrest_default_limit() -> None:
    """PostgREST(Supabase REST)는 명시하지 않으면 응답을 기본 1000행으로
    자른다 — 첫 페이지가 꽉 찬(1000행) 응답이면 offset을 올려 다음 페이지를
    마저 가져와야 한다."""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        offset = int(request.url.params["offset"])
        if offset == 0:
            return httpx.Response(200, json=[_trace_row(i) for i in range(1000)])
        return httpx.Response(200, json=[_trace_row(i) for i in range(1000, 1002)])

    transport = httpx.MockTransport(handler)
    records = _store(transport).list_traces_for_stats()

    assert len(records) == 1002
    assert [r.url.params["offset"] for r in requests] == ["0", "1000"]
    assert [r.url.params["limit"] for r in requests] == ["1000", "1000"]


def test_list_traces_for_stats_stops_when_first_page_is_partial() -> None:
    """대상이 페이지 크기(1000)보다 적으면 두 번째 요청을 보내지 않는다."""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=[_trace_row(1)])

    transport = httpx.MockTransport(handler)
    records = _store(transport).list_traces_for_stats()

    assert len(records) == 1
    assert len(requests) == 1


# ------------------------------------------------------------ Feedback


def test_append_feedback_skips_empty_list() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(201)

    transport = httpx.MockTransport(handler)
    _store(transport).append_feedback([])
    assert calls == []


def test_get_feedback_parses_rows_into_feedback_records() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json=[
                {
                    "id": 1,
                    "session_id": SESSION_ID,
                    "run_id": "run-1",
                    "rating": "like",
                    "user_input": "경복궁 근처 카페 추천해줘",
                    "assistant_message": "이런 곳들을 찾아봤어요.",
                    "recorded_at": "2026-08-21T00:00:00+09:00",
                }
            ],
        )
    )
    feedback = _store(transport).get_feedback(SESSION_ID)
    assert len(feedback) == 1
    assert feedback[0].rating == "like"
    assert feedback[0].user_input == "경복궁 근처 카페 추천해줘"
    assert feedback[0].assistant_message == "이런 곳들을 찾아봤어요."


def test_get_feedback_parses_rows_without_turn_text() -> None:
    """202608210001 마이그레이션 이전 행(컬럼 없음)을 읽어도 깨지지 않아야 한다."""
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json=[
                {
                    "id": 1,
                    "session_id": SESSION_ID,
                    "run_id": "run-1",
                    "rating": "like",
                    "recorded_at": "2026-08-21T00:00:00+09:00",
                }
            ],
        )
    )
    feedback = _store(transport).get_feedback(SESSION_ID)
    assert feedback[0].user_input is None
    assert feedback[0].assistant_message is None


def test_get_feedback_parses_intent_and_comment() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json=[
                {
                    "id": 1,
                    "session_id": SESSION_ID,
                    "run_id": "run-1",
                    "rating": "dislike",
                    "intent": "RECOMMEND",
                    "comment": "추천 장소가 너무 멀어요",
                    "recorded_at": "2026-08-21T00:00:00+09:00",
                }
            ],
        )
    )
    feedback = _store(transport).get_feedback(SESSION_ID)
    assert feedback[0].intent == "RECOMMEND"
    assert feedback[0].comment == "추천 장소가 너무 멀어요"


def test_get_feedback_parses_rows_without_intent_or_comment() -> None:
    """202608210003 마이그레이션 이전 행(컬럼 없음)을 읽어도 깨지지 않아야 한다."""
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json=[
                {
                    "id": 1,
                    "session_id": SESSION_ID,
                    "run_id": "run-1",
                    "rating": "like",
                    "recorded_at": "2026-08-21T00:00:00+09:00",
                }
            ],
        )
    )
    feedback = _store(transport).get_feedback(SESSION_ID)
    assert feedback[0].intent is None
    assert feedback[0].comment is None


def test_list_dislike_feedback_filters_by_rating_and_limit() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["request"] = request
        return httpx.Response(
            200,
            json=[
                {
                    "id": 1,
                    "session_id": SESSION_ID,
                    "run_id": "run-1",
                    "rating": "dislike",
                    "recorded_at": "2026-08-21T00:00:00+09:00",
                }
            ],
        )

    transport = httpx.MockTransport(handler)
    dislikes = _store(transport).list_dislike_feedback(10)

    request = seen["request"]
    assert request.url.params["rating"] == "eq.dislike"
    assert request.url.params["limit"] == "10"
    assert request.url.params["order"] == "recorded_at.desc"
    assert len(dislikes) == 1
    assert dislikes[0].rating == "dislike"


def test_list_feedback_for_stats_no_range_omits_recorded_at_filter() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["request"] = request
        return httpx.Response(200, json=[])

    transport = httpx.MockTransport(handler)
    _store(transport).list_feedback_for_stats()

    request = seen["request"]
    assert "rating" not in request.url.params
    assert "recorded_at" not in request.url.params
    assert "and" not in request.url.params
    assert request.url.params["order"] == "recorded_at.asc"


def test_list_feedback_for_stats_since_only_uses_gte() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["request"] = request
        return httpx.Response(200, json=[])

    transport = httpx.MockTransport(handler)
    since = datetime(2026, 8, 1, tzinfo=UTC)
    _store(transport).list_feedback_for_stats(since=since)

    request = seen["request"]
    assert request.url.params["recorded_at"] == f"gte.{since.isoformat()}"


def test_list_feedback_for_stats_since_and_until_uses_and_filter() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["request"] = request
        return httpx.Response(200, json=[])

    transport = httpx.MockTransport(handler)
    since = datetime(2026, 8, 1, tzinfo=UTC)
    until = datetime(2026, 8, 8, tzinfo=UTC)
    _store(transport).list_feedback_for_stats(since=since, until=until)

    request = seen["request"]
    assert "recorded_at" not in request.url.params
    assert request.url.params["and"] == (
        f"(recorded_at.gte.{since.isoformat()},recorded_at.lt.{until.isoformat()})"
    )


def test_list_feedback_for_stats_includes_like_rows() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json=[
                {
                    "id": 1,
                    "session_id": SESSION_ID,
                    "run_id": "run-1",
                    "rating": "like",
                    "recorded_at": "2026-08-21T00:00:00+09:00",
                }
            ],
        )
    )
    records = _store(transport).list_feedback_for_stats()
    assert len(records) == 1
    assert records[0].rating == "like"


def _feedback_row(i: int) -> dict[str, object]:
    return {
        "id": i,
        "session_id": f"sess_{i}",
        "run_id": f"run_{i}",
        "rating": "like",
        "recorded_at": "2026-08-21T00:00:00+09:00",
    }


def test_list_feedback_for_stats_paginates_beyond_postgrest_default_limit() -> None:
    """list_traces_for_stats와 동일한 이유(PostgREST 기본 1000행 상한)."""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        offset = int(request.url.params["offset"])
        if offset == 0:
            return httpx.Response(200, json=[_feedback_row(i) for i in range(1000)])
        return httpx.Response(200, json=[_feedback_row(i) for i in range(1000, 1001)])

    transport = httpx.MockTransport(handler)
    records = _store(transport).list_feedback_for_stats()

    assert len(records) == 1001
    assert [r.url.params["offset"] for r in requests] == ["0", "1000"]


# ------------------------------------------------------------ 정리(TP-134)


def test_list_stale_session_ids_filters_by_last_active_at() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["request"] = request
        return httpx.Response(200, json=[{"session_id": "sess_old"}])

    transport = httpx.MockTransport(handler)
    cutoff = datetime(2026, 7, 1, tzinfo=UTC)
    ids = _store(transport).list_stale_session_ids(cutoff)

    request = seen["request"]
    assert isinstance(request, httpx.Request)
    assert request.method == "GET"
    assert request.url.path == "/rest/v1/agent_states"
    assert request.url.params["last_active_at"] == f"lt.{cutoff.isoformat()}"
    assert request.url.params["select"] == "session_id"
    assert ids == ["sess_old"]


def test_list_stale_session_ids_returns_empty_list_when_none_found() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=[]))
    cutoff = datetime.now(UTC)
    assert _store(transport).list_stale_session_ids(cutoff) == []


def test_delete_change_logs_filters_by_session_id() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["request"] = request
        return httpx.Response(200)

    transport = httpx.MockTransport(handler)
    _store(transport).delete_change_logs(SESSION_ID)

    request = seen["request"]
    assert isinstance(request, httpx.Request)
    assert request.method == "DELETE"
    assert request.url.path == "/rest/v1/condition_change_logs"
    assert request.url.params["session_id"] == f"eq.{SESSION_ID}"


def test_delete_traces_filters_by_session_id() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["request"] = request
        return httpx.Response(200)

    transport = httpx.MockTransport(handler)
    _store(transport).delete_traces(SESSION_ID)

    request = seen["request"]
    assert isinstance(request, httpx.Request)
    assert request.method == "DELETE"
    assert request.url.path == "/rest/v1/trace_records"
    assert request.url.params["session_id"] == f"eq.{SESSION_ID}"


# ------------------------------------------------------------ 에러 처리


def test_http_error_raises_state_store_error() -> None:
    """B의 SupabaseStateStore는 B 소유 오류(StateStoreError)로 실패를 알린다.

    이전에는 app.repositories.supabase_places.SupabaseRepositoryError(장소 동기화
    기능 쪽 예외, 메시지가 "장소 데이터 저장 중...")를 빌려 썼는데, B의 세션 상태
    저장 실패에 엉뚱한 메시지가 나가는 문제가 있어 B 소유 StateStoreError로 교체했다.
    """
    transport = httpx.MockTransport(
        lambda request: httpx.Response(500, json={"message": "boom"})
    )
    with pytest.raises(StateStoreError):
        _store(transport).get_state(SESSION_ID)

# ------------------------------------------------------------ UserPreferenceList


def test_get_preferences_returns_none_when_not_found() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=[]))
    assert _store(transport).get_preferences(USER_ID) is None


def test_get_preferences_queries_by_user_id_not_session_id() -> None:
    """이 저장소만 키가 user_id다. session_id로 조회하면 남의 취향에 닿는다."""
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, json=[])

    _store(httpx.MockTransport(handler)).get_preferences(USER_ID)

    assert "/user_preferences" in seen["url"]
    assert f"user_id=eq.{USER_ID}" in seen["url"]
    assert "session_id" not in seen["url"]


def test_get_preferences_parses_row() -> None:
    row = {
        "user_id": USER_ID,
        "items": [{"label": "조용한 곳", "source": "preference", "codes": ["quiet"]}],
        "updated_at": "2026-09-03T00:00:00+09:00",
    }
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=[row]))

    preferences = _store(transport).get_preferences(USER_ID)

    assert preferences is not None
    assert preferences.user_id == USER_ID
    assert preferences.items[0].label == "조용한 곳"
    assert preferences.items[0].codes == ["quiet"]


def test_get_preferences_raises_on_broken_row() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=[{"items": "x"}]))

    with pytest.raises(StateStoreError):
        _store(transport).get_preferences(USER_ID)


def test_save_preferences_upserts_on_user_id() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(201, json=[])

    preferences = UserPreferenceList(
        user_id=USER_ID,
        items=[UserPreference(label="산책하기 좋은", source="place_tag", codes=["walk"])],
    )
    _store(httpx.MockTransport(handler)).save_preferences(preferences)

    assert "on_conflict=user_id" in str(seen["url"])
    body = seen["body"]
    assert isinstance(body, dict)
    assert body["user_id"] == USER_ID
    assert body["items"][0]["codes"] == ["walk"]


# ------------------------------------------------------------ 저장한 일정

SCHEDULE_ROW = {
    "id": "11111111-2222-4333-8444-555555555555",
    "user_id": USER_ID,
    "session_id": "sess_1",
    "run_id": "run_1",
    "title": "종로 반나절",
    "payload": {"items": [], "total_duration_min": 180},
    "created_at": "2026-09-03T10:00:00+09:00",
    "updated_at": "2026-09-03T10:00:00+09:00",
}


def _schedule() -> SavedSchedule:
    return SavedSchedule(
        user_id=USER_ID, session_id="sess_1", run_id="run_1", title="종로 반나절", payload={}
    )


def test_save_schedule_does_not_send_id() -> None:
    """id는 DB 기본값(gen_random_uuid)이 만든다. 클라이언트가 보내면 그 값이 그대로
    키가 되어 남의 id와 충돌시킬 여지가 생긴다."""
    seen: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json=[])
        seen.append({"body": json.loads(request.content), "url": str(request.url)})
        return httpx.Response(201, json=[SCHEDULE_ROW])

    _store(httpx.MockTransport(handler)).save_schedule(_schedule())

    assert "id" not in seen[0]["body"]
    assert "/saved_schedules" in str(seen[0]["url"])


def test_save_schedule_returns_existing_row_for_same_run() -> None:
    """같은 (user_id, run_id)면 새로 만들지 않는다. 부분 유니크 인덱스를
    on_conflict로 못 태워서 조회로 가른다 — 그 조회가 실제로 도는지 고정한다."""
    methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        if request.method == "GET":
            return httpx.Response(200, json=[SCHEDULE_ROW])
        return httpx.Response(201, json=[SCHEDULE_ROW])

    saved = _store(httpx.MockTransport(handler)).save_schedule(_schedule())

    assert methods == ["GET"], "이미 있는데 INSERT까지 나갔다"
    assert saved.id == SCHEDULE_ROW["id"]


def test_save_schedule_skips_lookup_without_run_id() -> None:
    """run_id가 없으면 판정할 근거가 없다. 조회를 건너뛰고 바로 만든다."""
    methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        return httpx.Response(201, json=[SCHEDULE_ROW])

    schedule = _schedule().model_copy(update={"run_id": None})
    _store(httpx.MockTransport(handler)).save_schedule(schedule)

    assert methods == ["POST"]


def test_list_schedules_orders_by_created_at_desc() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, json=[SCHEDULE_ROW])

    schedules = _store(httpx.MockTransport(handler)).list_schedules_for_user(USER_ID, 50)

    assert f"user_id=eq.{USER_ID}" in seen["url"]
    assert "order=created_at.desc" in seen["url"]
    assert "limit=50" in seen["url"]
    assert schedules[0].title == "종로 반나절"


def test_get_schedule_does_not_filter_by_user() -> None:
    """저장소는 소유권을 보지 않는다 — service가 principal과 대조한다.
    여기서 user_id로 좁히면 '없음'과 '남의 것'이 구분되지 않아 404와 403을
    가를 수 없다."""
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, json=[SCHEDULE_ROW])

    _store(httpx.MockTransport(handler)).get_schedule(SCHEDULE_ROW["id"])

    assert "user_id" not in seen["url"]


def test_rename_schedule_patches_title_and_updated_at() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=[{**SCHEDULE_ROW, "title": "엄마랑 가는 날"}])

    renamed = _store(httpx.MockTransport(handler)).rename_schedule(
        SCHEDULE_ROW["id"], "엄마랑 가는 날"
    )

    assert seen["method"] == "PATCH"
    assert seen["body"]["title"] == "엄마랑 가는 날"
    assert "updated_at" in seen["body"]
    assert renamed is not None and renamed.title == "엄마랑 가는 날"


def test_delete_schedule_reports_whether_a_row_was_removed() -> None:
    hit = httpx.MockTransport(lambda request: httpx.Response(200, json=[SCHEDULE_ROW]))
    miss = httpx.MockTransport(lambda request: httpx.Response(200, json=[]))

    assert _store(hit).delete_schedule(SCHEDULE_ROW["id"]) is True
    assert _store(miss).delete_schedule(SCHEDULE_ROW["id"]) is False


def test_schedule_row_raises_on_broken_row() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=[{"title": 1}]))

    with pytest.raises(StateStoreError):
        _store(transport).get_schedule(SCHEDULE_ROW["id"])
