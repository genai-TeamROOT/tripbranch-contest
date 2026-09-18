"""사용자 입력 해석 API 회귀 테스트.

역할: /api/interpret이 fake LLMProvider 경로에서 LLMOutput 계약(intent/status/payload)을
지키는지, 요청 검증 오류 포맷이 유지되는지 확인한다.
입력: TestClient가 보내는 POST /api/interpret JSON payload.
출력: LLMOutput JSON과 공통 오류 JSON에 대한 pytest assertion.
호출 시점: 로컬 테스트와 CI에서 pytest 실행 시 호출된다.
"""

from fastapi.testclient import TestClient

from app.main import app


def test_interpret_tc01_recommend() -> None:
    client = TestClient(app)

    response = client.post(
        "/api/interpret", json={"user_input": "경복궁 근처 카페 추천해줘"}
    )

    assert response.status_code == 200
    body = response.json()["output"]
    assert body["intent"] == "RECOMMEND"
    assert body["status"] == "complete"
    assert body["recommend"]["conditions"]["search_center"] == "경복궁"
    assert body["recommend"]["conditions"]["place_tags"] == ["카페"]


def test_interpret_tc02_weather_recommend() -> None:
    client = TestClient(app)

    response = client.post(
        "/api/interpret", json={"user_input": "비 오는데 갈 만한 곳 추천"}
    )

    conditions = response.json()["output"]["recommend"]["conditions"]
    assert conditions["weather"] == "rain"
    assert conditions["weather_intent"] == "AVOID"
    assert conditions["environment"] == "indoor"


def test_interpret_tc07_modify_reject_all_requires_history() -> None:
    client = TestClient(app)

    response = client.post(
        "/api/interpret",
        json={
            "user_input": "다른 곳 보여줘",
            "has_previous_recommendation": True,
            "shown_place_count": 3,
            "current_conditions": {"search_center": "경복궁"},
        },
    )

    body = response.json()["output"]
    assert body["intent"] == "MODIFY"
    assert body["modify"]["modify_type"] == "REJECT_ALL"


def test_interpret_tc07_without_history_falls_back_to_recommend() -> None:
    """TC-14: 이전 추천 이력 없이 MODIFY 패턴 발화 -> RECOMMEND로 처리."""
    client = TestClient(app)

    response = client.post(
        "/api/interpret",
        json={"user_input": "다른 곳 보여줘", "has_previous_recommendation": False},
    )

    assert response.json()["output"]["intent"] == "RECOMMEND"


def test_interpret_tc08_modify_change_condition() -> None:
    client = TestClient(app)

    response = client.post(
        "/api/interpret",
        json={
            "user_input": "무료인 곳으로",
            "has_previous_recommendation": True,
            "shown_place_count": 2,
            "current_conditions": {
                "search_center": "경복궁",
                "place_types": ["restaurant"],
            },
        },
    )

    body = response.json()["output"]
    assert body["modify"]["modify_type"] == "CHANGE_CONDITION"
    assert body["modify"]["condition_changes"]["budget"] == "free"
    assert body["modify"]["changed_fields"] == ["budget"]


def test_interpret_explicit_new_place_search_overrides_modify_history() -> None:
    """이전 추천이 있어도 새 장소 유형을 명시한 검색은 새 추천으로 처리한다."""
    client = TestClient(app)

    response = client.post(
        "/api/interpret",
        json={
            "user_input": "사진 이쁘게 나오는 안국역 카페 알려줘",
            "has_previous_recommendation": True,
            "shown_place_count": 3,
            "current_conditions": {"search_center": "경복궁", "place_tags": ["박물관"]},
        },
    )

    body = response.json()["output"]
    assert body["intent"] == "RECOMMEND"
    assert body["recommend"]["conditions"]["place_tags"] == ["카페"]


def test_interpret_modify_without_current_conditions_needs_clarification() -> None:
    """current_conditions 없이 MODIFY로 판정될 상황이면 LLM 호출 없이 단락 처리."""
    client = TestClient(app)

    response = client.post(
        "/api/interpret",
        json={
            "user_input": "무료인 곳으로",
            "has_previous_recommendation": True,
            "shown_place_count": 2,
        },
    )

    body = response.json()["output"]
    assert body["intent"] == "MODIFY"
    assert body["status"] == "needs_clarification"


def test_interpret_tc11_general() -> None:
    client = TestClient(app)

    response = client.post(
        "/api/interpret", json={"user_input": "경복궁은 언제 지어졌어?"}
    )

    body = response.json()["output"]
    assert body["intent"] == "GENERAL"
    assert body["general"]["topic"] == "place_knowledge"


def test_interpret_service_identity_question_is_general() -> None:
    client = TestClient(app)

    response = client.post("/api/interpret", json={"user_input": "넌 누구야?"})

    body = response.json()["output"]
    assert body["intent"] == "GENERAL"
    assert body["general"]["topic"] == "service_identity"
    assert body["out_of_scope"] is None


def test_interpret_tc13_out_of_scope() -> None:
    client = TestClient(app)

    response = client.post("/api/interpret", json={"user_input": "주식 추천해줘"})

    body = response.json()["output"]
    assert body["intent"] == "OUT_OF_SCOPE"
    assert body["out_of_scope"]["category"] == "unrelated"


def test_interpret_rejects_empty_input() -> None:
    client = TestClient(app)

    response = client.post("/api/interpret", json={"user_input": ""})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"
