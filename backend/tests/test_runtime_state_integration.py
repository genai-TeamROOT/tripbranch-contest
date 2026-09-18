"""라우터를 거치는 Runtime–State 연결 통합 테스트.

역할: /api/interpret 과 /api/recommendations 가 Package B의 세션 State를
      실제로 이어주는지 HTTP 계층에서 확인한다.
입력: TestClient 가 보내는 POST 요청.
출력: 세션 유지, 조건 병합, 이력 누적에 대한 pytest assertion.

tests/test_state_integration.py 는 transform() + apply() 를 직접 호출하고,
이 파일은 라우터 배선(컨텍스트 주입, run_id 왕복, 이력 기록)을 검증한다.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.state.store import get_store

GPS = "37.5796,126.9770"


@pytest.fixture(autouse=True)
def clean_state_store():
    """라우터는 전역 저장소를 쓰므로 테스트마다 비운다."""
    store = get_store()
    store.clear()
    yield
    store.clear()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def interpret(client: TestClient, user_input: str, session_id: str | None = None) -> dict:
    payload: dict = {"user_input": user_input, "device_location": GPS}
    if session_id:
        payload["session_id"] = session_id
    response = client.post("/api/interpret", json=payload)
    assert response.status_code == 200
    return response.json()


def record(client: TestClient, session_id: str, run_id: str) -> dict:
    """추천 실행. fake 파이프라인이 빈 결과를 내도 요청 자체는 성공해야 한다."""
    response = client.post(
        "/api/recommendations",
        json={
            "location_query": "경복궁",
            "preferred_categories": ["cafe"],
            "weather_condition": None,
            "search_radius_km": 1.0,
            "session_id": session_id,
            "run_id": run_id,
        },
    )
    assert response.status_code == 200
    return response.json()

def seed_history(session_id: str, run_id: str, place_ids: list[str]) -> None:
    """추천 이력을 직접 주입한다.

    fake 추천 파이프라인이 빈 결과를 반환하므로(C 영역),
    이력이 있어야 성립하는 검증은 State에 직접 기록한다.
    """
    from app.state import service as state_service

    state_service.record_recommendation(
        state_service.RecordRecommendationRequest(
            session_id=session_id,
            run_id=run_id,
            recommended=[
                state_service.RecommendedPlace(place_id=pid, rank=i)
                for i, pid in enumerate(place_ids, start=1)
            ],
        )
    )

# ================================================================ 세션

class TestSessionLifecycle:
    def test_세션_없이_요청하면_발급된다(self, client):
        body = interpret(client, "경복궁 근처 카페 추천해줘")
        state = body["state"]

        assert state["session_created"] is True
        assert state["session_id"].startswith("sess_")
        assert state["run_id"].startswith("run_")

    def test_세션을_넘기면_유지된다(self, client):
        first = interpret(client, "경복궁 근처 카페 추천해줘")
        sid = first["state"]["session_id"]

        second = interpret(client, "무료인 곳으로", session_id=sid)

        assert second["state"]["session_id"] == sid
        assert second["state"]["session_created"] is False

    def test_매_요청마다_run_id가_새로_발급된다(self, client):
        first = interpret(client, "경복궁 근처 카페 추천해줘")
        sid = first["state"]["session_id"]

        second = interpret(client, "무료인 곳으로", session_id=sid)

        assert second["state"]["run_id"] != first["state"]["run_id"]

    def test_없는_세션은_오류가_아니라_신규_발급이다(self, client):
        """익명 세션에서 만료는 오류가 아니라 정상 생애주기다. (계약 5.2절)"""
        body = interpret(client, "경복궁 근처 카페", session_id="sess_없는값")

        assert body["state"]["session_created"] is True
        assert body["state"]["session_id"] != "sess_없는값"


# ================================================================ GPS

class TestApiContext:
    def test_좌표는_세션에_남지_않는다(self, client):
        """예전에는 최초 턴 직후 세션에 GPS를 심어 다음 턴이 재사용했다.

        서버가 사용자 좌표를 저장하지 않게 되면서(state/store.py::for_persistence)
        심는 자리를 없앴다 — 매 턴 요청에 실려 오므로 세션에 둘 이유가 없다.
        """
        first = interpret(client, "경복궁 근처 카페 추천해줘")
        sid = first["state"]["session_id"]

        second = interpret(client, "무료인 곳으로", session_id=sid)

        assert second["state"]["gps_expired"] is True


# ================================================================ 조건

class TestConditionMerge:
    def test_초기_요청의_조건이_State에_저장된다(self, client):
        """완료 기준 1번."""
        body = interpret(client, "경복궁 근처 카페 추천해줘")
        conditions = body["state"]["user_conditions"]

        assert conditions["search_center"] == "경복궁"
        assert conditions["place_tags"] == ["카페"]
        assert body["state"]["condition_version"] == 1
        assert body["state"]["condition_changed"] is True

    def test_후속_요청의_조건이_기존_조건에_병합된다(self, client):
        """완료 기준 2번. 이전 조건이 유지된 채 변경분만 반영된다."""
        first = interpret(client, "경복궁 근처 카페 추천해줘")
        sid = first["state"]["session_id"]
        seed_history(sid, first["state"]["run_id"], ["A", "B", "C"])

        second = interpret(client, "무료인 곳으로", session_id=sid)
        conditions = second["state"]["user_conditions"]

        assert conditions["budget"] == "free"
        assert conditions["search_center"] == "경복궁"   # 유지
        assert second["state"]["condition_version"] == 2


# ================================================================ 컨텍스트 주입

class TestContextInjection:
    def test_세션이_있으면_호출자_컨텍스트를_무시한다(self, client):
        """조건과 이력의 단일 기준은 B다. (계약 6.2절)

        호출자가 거짓 컨텍스트를 보내도 B가 가진 값으로 덮어써야 한다.
        """
        first = interpret(client, "경복궁 근처 카페 추천해줘")
        sid = first["state"]["session_id"]

        response = client.post(
            "/api/interpret",
            json={
                "user_input": "다른 곳 보여줘",
                "session_id": sid,
                "device_location": GPS,
                "has_previous_recommendation": True,   # 거짓말
                "shown_place_count": 99,
            },
        )
        body = response.json()

        # 추천 이력이 없으므로 MODIFY 로 잡히지 않는다
        assert body["output"]["intent"] == "RECOMMEND"

    def test_세션이_없으면_호출자_컨텍스트를_신뢰한다(self, client):
        """세션 도입 전 하위 호환 경로. tests/test_interpret.py 가 이 경로를 쓴다."""
        response = client.post(
            "/api/interpret",
            json={
                "user_input": "다른 곳 보여줘",
                "has_previous_recommendation": True,
                "shown_place_count": 3,
                "current_conditions": {"search_center": "경복궁"},
            },
        )
        body = response.json()

        assert body["output"]["intent"] == "MODIFY"


# ================================================================ 이력

class TestRecommendationHistory:
    def test_추천_실행이_세션과_함께_성공한다(self, client):
        first = interpret(client, "경복궁 근처 카페 추천해줘")
        state = first["state"]

        body = record(client, state["session_id"], state["run_id"])

        assert "recommendations" in body
        assert "elapsed_ms" in body

    def test_run_id_없이도_추천이_동작한다(self, client):
        """세션 없이 호출하는 기존 경로가 깨지지 않아야 한다."""
        response = client.post(
            "/api/recommendations",
            json={
                "location_query": "경복궁",
                "preferred_categories": ["cafe"],
                "weather_condition": None,
                "search_radius_km": 1.0,
            },
        )

        assert response.status_code == 200


# ================================================================ 다중 턴

class TestMultiTurn:
    def test_세_턴_동안_세션과_조건이_이어진다(self, client):
        """완료 기준 5번. 핵심 다중 턴 시나리오."""
        # 1턴
        t1 = interpret(client, "경복궁 근처 카페 추천해줘")
        sid = t1["state"]["session_id"]
        seed_history(sid, t1["state"]["run_id"], ["A", "B", "C"])

        assert t1["state"]["user_conditions"]["search_center"] == "경복궁"

        # 2턴 — 조건 추가
        t2 = interpret(client, "무료인 곳으로", session_id=sid)
        seed_history(sid, t2["state"]["run_id"], ["D", "E"])

        assert t2["state"]["session_id"] == sid
        assert t2["state"]["user_conditions"]["budget"] == "free"
        assert t2["state"]["user_conditions"]["search_center"] == "경복궁"

        # 3턴 — 세션이 계속 이어진다
        t3 = interpret(client, "실내로 해줘", session_id=sid)

        assert t3["state"]["session_id"] == sid
        assert t3["state"]["run_id"] != t2["state"]["run_id"]
        assert t3["state"]["user_conditions"]["search_center"] == "경복궁"

# ================================================================ KEEP

class TestKeepBehavior:
    """완료 기준 3번: KEEP에서 기존 조건이 변경되지 않는다.

    A는 Keep 연산을 전송하지 않는다. changed_fields에 없는 필드는
    operations에 아예 담기지 않으며, B가 자동으로 유지한다. (계약 2.1절)
    """

    def test_언급하지_않은_조건은_턴이_바뀌어도_유지된다(self, client):
        t1 = interpret(client, "경복궁 근처 카페 추천해줘")
        sid = t1["state"]["session_id"]
        seed_history(sid, t1["state"]["run_id"], ["A", "B", "C"])

        before = t1["state"]["user_conditions"]

        # budget 만 바꾼다
        t2 = interpret(client, "무료인 곳으로", session_id=sid)
        after = t2["state"]["user_conditions"]

        assert after["budget"] == "free"
        # 나머지는 그대로
        assert after["search_center"] == before["search_center"]
        assert after["place_types"] == before["place_types"]
        assert after["place_tags"] == before["place_tags"]

    def test_조건_변경이_없으면_version이_오르지_않는다(self, client):
        """REJECT_ALL은 조건을 바꾸지 않는다."""
        t1 = interpret(client, "경복궁 근처 카페 추천해줘")
        sid = t1["state"]["session_id"]
        seed_history(sid, t1["state"]["run_id"], ["A", "B", "C"])

        t2 = interpret(client, "다른 곳 보여줘", session_id=sid)

        assert t2["output"]["intent"] == "MODIFY"
        assert t2["state"]["condition_changed"] is False
        assert t2["state"]["condition_version"] == t1["state"]["condition_version"]
        assert t2["state"]["user_conditions"]["search_center"] == "경복궁"

    def test_명시적_Keep_연산도_조건을_바꾸지_않는다(self, client):
        """A는 현재 Keep을 보내지 않으나, 수신 시 무동작이어야 한다.

        연산이 안 온 것과 Keep이 온 것을 구분할 필요가 생기면 A가 보낼 수 있다.
        """
        from app.state import service as state_service

        t1 = interpret(client, "경복궁 근처 카페 추천해줘")
        sid = t1["state"]["session_id"]
        version_before = t1["state"]["condition_version"]

        result = state_service.apply(
            state_service.StateApplyRequest(
                session_id=sid,
                intent="MODIFY",
                confirmed=True,
                operations=[
                    {"op": "Keep", "field": "search_center"},
                    {"op": "Keep", "field": "place_tags"},
                ],
            )
        )

        assert result.user_conditions.search_center == "경복궁"
        assert result.user_conditions.place_tags == ["카페"]
        assert result.condition_changed is False
        assert result.condition_version == version_before


# ================================================================ 오류 처리

class TestErrorHandling:
    """업무 상세: 세션 미존재·초기화·잘못된 State 입력에 대한 기본 오류 처리."""

    def test_빈_입력은_422를_반환한다(self, client):
        response = client.post("/api/interpret", json={"user_input": ""})

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "invalid_request"

    def test_잘못된_형식의_session_id도_오류가_아니다(self, client):
        """세션 미존재는 오류가 아니라 정상 생애주기다. (계약 5.2절)"""
        for bad in ["", "   ", "sess_" * 100, "!@#$%"]:
            response = client.post(
                "/api/interpret",
                json={"user_input": "카페 추천해줘", "session_id": bad},
            )
            assert response.status_code == 200
            assert response.json()["state"]["session_created"] is True

    def test_잘못된_형식의_GPS는_해당_턴만_건너뛴다(self, client):
        """device_location 파싱 실패가 대화를 중단시키지 않아야 한다."""
        response = client.post(
            "/api/interpret",
            json={"user_input": "카페 추천해줘", "device_location": "이상한값"},
        )

        assert response.status_code == 200
        assert response.json()["state"]["session_id"].startswith("sess_")

    def test_알_수_없는_연산은_무시되고_기록된다(self, client):
        """B는 무효한 연산을 거부하되 예외를 던지지 않는다. (계약 2.5절)"""
        from app.state import service as state_service

        t1 = interpret(client, "경복궁 근처 카페 추천해줘")
        sid = t1["state"]["session_id"]

        result = state_service.apply(
            state_service.StateApplyRequest(
                session_id=sid,
                intent="MODIFY",
                confirmed=True,
                operations=[
                    {"op": "Update", "field": "budget", "value": "free"},
                    {"op": "Update", "field": "없는필드", "value": 1},
                    {"op": "Delete", "field": "budget"},
                ],
            )
        )

        assert result.user_conditions.budget == "free"
        assert len(result.ignored_operations) == 2
        reasons = {i.reason for i in result.ignored_operations}
        assert reasons == {"unknown_field", "unknown_op"}

    def test_recommendations에_없는_session_id를_보내도_동작한다(self, client):
        response = client.post(
            "/api/recommendations",
            json={
                "location_query": "경복궁",
                "preferred_categories": ["cafe"],
                "weather_condition": None,
                "search_radius_km": 1.0,
                "session_id": "sess_없는값",
                "run_id": "run_없는값",
            },
        )

        assert response.status_code == 200