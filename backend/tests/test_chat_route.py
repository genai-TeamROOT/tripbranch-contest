"""통합 Chat API 라우터 테스트.

역할: POST /api/chat이 run_agent()에 요청을 그대로 위임하고 AgentResponse를
      반환하는지 검증한다. Runtime 내부 동작은 test_agent_runtime.py가 담당하므로
      여기서는 라우팅과 요청 전달만 확인한다(실제 Provider를 호출하지 않는다).
입력: TestClient가 보내는 POST /api/chat 요청.
출력: 상태 코드와 응답 payload에 대한 assertion.
호출 시점: 로컬 테스트와 CI에서 pytest 실행 시.
"""

from __future__ import annotations

import asyncio
import contextlib
import json

import pytest
from fastapi.testclient import TestClient

import app.routes.chat as chat_route
from app.agent_context.info_schemas import InfoContextResponse, PlaceCard, PlaceInfoResult
from app.agent_context.schemas import Coordinates
from app.main import app
from app.providers.contracts import ProviderSource, provider_result
from app.schemas import (
    AgentRequest,
    AgentResponse,
    Intent,
    LLMOutput,
    OutputStatus,
)
from app.state.schema import UserConditions as StateUserConditions
from app.state.service import ApiContextView, StateApplyResponse


def _fake_response(session_id: str = "sess_test") -> AgentResponse:
    return AgentResponse(
        llm_output=LLMOutput(intent=Intent.GENERAL, status=OutputStatus.COMPLETE),
        state=StateApplyResponse(
            session_id=session_id,
            run_id="run_test",
            session_created=True,
            user_conditions=StateUserConditions(),
            api_context=ApiContextView(),
            condition_version=1,
            condition_changed=False,
        ),
        recommendations=None,
        message="테스트 응답",
    )


@pytest.fixture
def captured(monkeypatch) -> list[AgentRequest]:
    seen: list[AgentRequest] = []

    async def fake_run_agent(request: AgentRequest, *, principal=None) -> AgentResponse:
        seen.append(request)
        return _fake_response()

    monkeypatch.setattr(chat_route, "run_agent", fake_run_agent)
    return seen


def test_chat_delegates_to_run_agent(captured) -> None:
    client = TestClient(app)

    response = client.post("/api/chat", json={"user_input": "경복궁 근처 카페 추천해줘"})

    assert response.status_code == 200
    body = response.json()
    assert body["message"] == "테스트 응답"
    assert body["state"]["session_id"] == "sess_test"
    assert len(captured) == 1
    assert captured[0].user_input == "경복궁 근처 카페 추천해줘"


def test_chat_passes_session_and_device_location(captured) -> None:
    client = TestClient(app)

    response = client.post(
        "/api/chat",
        json={
            "user_input": "다른 곳 보여줘",
            "session_id": "sess_prev",
            "device_location": "37.5788,126.9770",
        },
    )

    assert response.status_code == 200
    assert captured[0].session_id == "sess_prev"
    assert captured[0].device_location == "37.5788,126.9770"


def test_chat_translates_english_at_route_boundary_without_changing_runtime_contract(
    captured, monkeypatch
) -> None:
    seen_languages: list[str] = []

    async def fake_request_for_runtime(request: AgentRequest) -> AgentRequest:
        seen_languages.append(request.language)
        return request.model_copy(update={"user_input": "경복궁 근처 실내 박물관 추천"})

    async def fake_response_for_user(response: AgentResponse, *, language: str) -> AgentResponse:
        seen_languages.append(language)
        return response.model_copy(update={"message": "Try an indoor museum near Gyeongbokgung."})

    monkeypatch.setattr(chat_route, "_request_for_runtime", fake_request_for_runtime)
    monkeypatch.setattr(chat_route, "_response_for_user", fake_response_for_user)
    client = TestClient(app)

    response = client.post(
        "/api/chat",
        json={"user_input": "Find an indoor museum near Gyeongbokgung", "language": "en"},
    )

    assert response.status_code == 200
    assert captured[0].user_input == "경복궁 근처 실내 박물관 추천"
    assert captured[0].language == "en"
    assert response.json()["message"] == "Try an indoor museum near Gyeongbokgung."
    assert seen_languages == ["en", "en"]


def test_chat_rejects_empty_user_input(captured) -> None:
    client = TestClient(app)

    response = client.post("/api/chat", json={"user_input": ""})

    assert response.status_code == 422
    assert captured == []


def test_recommendation_place_details_returns_matched_c_place_card(monkeypatch) -> None:
    captured_requests = []

    class FakeContextProvider:
        async def fetch_info_context(self, request):
            captured_requests.append(request)
            return InfoContextResponse(
                request_id=request.request_id,
                status="success",
                result=PlaceInfoResult(
                    status="success",
                    question_type="general_info",
                    place_id="126508",
                    fields={"overview": "조선 왕조의 법궁"},
                    destination_coordinates=Coordinates(latitude=37.5796, longitude=126.977),
                    place_card=PlaceCard(
                        place_id="126508",
                        place_name="경복궁",
                        thumbnail_url="https://example.test/gyeongbokgung.jpg",
                        overview="조선 왕조의 법궁",
                        operating_hours="09:00~18:00",
                    ),
                ),
            )

    monkeypatch.setattr(chat_route, "get_context_provider", lambda client: FakeContextProvider())
    # 취향 근거 조회는 conftest가 막지 않는다. 막지 않으면 backend/.env에 Supabase
    # 키가 있는 로컬에서만 실제 DB의 경복궁 후기 근거가 붙어 아래 비교가 깨진다.
    monkeypatch.setattr(chat_route, "get_place_details_repository", lambda client: None)
    client = TestClient(app)

    response = client.post(
        "/api/chat/place-details",
        json={"place_id": "126508", "place_name": "경복궁"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "status": "success",
        "requested_place_id": "126508",
        "place_card": {
            "question_type": "general_info",
            "answer_fields": {"overview": "조선 왕조의 법궁"},
            "place_id": "126508",
            "place_name": "경복궁",
            "latitude": 37.5796,
            "longitude": 126.977,
            "thumbnail_url": "https://example.test/gyeongbokgung.jpg",
            "photos": [],
            "overview": "조선 왕조의 법궁",
            "operating_hours": "09:00~18:00",
            "rest_date": None,
            "parking": None,
            "parking_fee": None,
            "fee": None,
            "baby_carriage": None,
            "pet": None,
            "credit_card": None,
            "restroom": None,
            "homepage": None,
            "accessible_restroom": None,
            "accessible_parking": None,
            "elevator": None,
            "visual_guide": None,
            "wheelchair_rental": None,
            "nursing_room": None,
            "seating": None,
            "stroller_rental": None,
            "guide_dog": None,
            "preference_insights": [],
            "review_sources": [],
            "population_current_level": None,
            "population_current_message": None,
            "population_observed_at": None,
            "population_peak_forecast_summary": None,
            "population_forecasts": [],
            "concentration_forecasts": [],
            "realtime_area_name": None,
            "realtime_observed_at": None,
            "realtime_source_url": None,
            "realtime_map_url": None,
            "realtime_detail_items": [],
            "seoul_realtime_summary": None,
            "road_incident_counts": [],
        },
    }
    assert len(captured_requests) == 1
    assert captured_requests[0].place_context == "from_recommendation"
    assert captured_requests[0].question_type == "general_info"


def test_recommendation_place_details_by_name_only_skips_id_match(monkeypatch) -> None:
    """혼잡도·행사 카드처럼 place_id 없이 이름으로 조회하면 대조를 건너뛰고 성공한다."""

    class FakeContextProvider:
        async def fetch_info_context(self, request):
            return InfoContextResponse(
                request_id=request.request_id,
                status="success",
                result=PlaceInfoResult(
                    status="success",
                    question_type="general_info",
                    place_id="126508",
                    fields={},
                    destination_coordinates=Coordinates(latitude=37.5796, longitude=126.977),
                    place_card=PlaceCard(place_id="126508", place_name="창덕궁"),
                ),
            )

    monkeypatch.setattr(chat_route, "get_context_provider", lambda client: FakeContextProvider())
    # 위 테스트와 같은 이유로 실제 DB를 읽지 않게 막는다.
    monkeypatch.setattr(chat_route, "get_place_details_repository", lambda client: None)
    client = TestClient(app)

    response = client.post("/api/chat/place-details", json={"place_name": "창덕궁"})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    assert body["requested_place_id"] is None
    assert body["place_card"]["place_id"] == "126508"
    assert body["place_card"]["latitude"] == 37.5796
    assert body["place_card"]["longitude"] == 126.977


def test_recommendation_place_details_hides_mismatched_place_card(monkeypatch) -> None:
    class FakeContextProvider:
        async def fetch_info_context(self, request):
            return InfoContextResponse(
                request_id=request.request_id,
                status="success",
                result=PlaceInfoResult(
                    status="success",
                    question_type="general_info",
                    place_id="other-place",
                    fields={},
                    place_card=PlaceCard(place_id="other-place", place_name="동명 장소"),
                ),
            )

    monkeypatch.setattr(chat_route, "get_context_provider", lambda client: FakeContextProvider())
    client = TestClient(app)

    response = client.post(
        "/api/chat/place-details",
        json={"place_id": "126508", "place_name": "경복궁"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "status": "no_data",
        "requested_place_id": "126508",
        "place_card": None,
    }


# --- 상세 카드 "AI가 추천하는 이유" (recommend.place_reason) ------------------
#
# 문장은 상세조회와 **다른 엔드포인트**다(POST /chat/place-details/reason). 한
# 응답에 묶었더니 문장 생성 1~2초가 주소·운영시간이 뜨는 시각을 그대로 밀어서
# 나눴다. 아래 첫 테스트가 그 분리를 잠근다.


class _FakePreferenceRepository:
    """상세 카드 취향 근거 조회만 흉내내는 저장소."""

    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    async def find_preference_insights(self, content_id: str) -> list[dict]:
        return self._rows


_TAG_ROWS = [
    {
        "code": "nature",
        "label": "자연을 즐기기 좋은",
        "mention_count": 14,
        "positive_document_count": 14,
        "negative_document_count": 0,
        "evidence": [
            {"polarity": "positive", "text": "북한산 숲속 한옥에서 차를 마실 수 있어요.",
             "source_type": "naver_post", "source_url": None},
        ],
    },
]


def _place_details_provider(monkeypatch, rows: list[dict]) -> None:
    class FakeContextProvider:
        async def fetch_info_context(self, request):
            return InfoContextResponse(
                request_id=request.request_id,
                status="success",
                result=PlaceInfoResult(
                    status="success",
                    question_type="general_info",
                    place_id="2832918",
                    fields={},
                    place_card=PlaceCard(place_id="2832918", place_name="한옥카페 선운각"),
                ),
            )

    monkeypatch.setattr(chat_route, "get_context_provider", lambda client: FakeContextProvider())
    monkeypatch.setattr(
        chat_route, "get_place_details_repository", lambda client: _FakePreferenceRepository(rows)
    )


def _preference_rows(monkeypatch, rows: list[dict]) -> None:
    monkeypatch.setattr(
        chat_route, "get_place_details_repository", lambda client: _FakePreferenceRepository(rows)
    )


def test_place_details_never_waits_on_the_llm(monkeypatch) -> None:
    """상세조회는 취향 태그가 있어도 LLM을 부르지 않는다.

    이 단정이 이 기능의 요점이다 — 문장을 상세 응답에 실으면 그 생성 시간이
    주소·운영시간·사진이 뜨는 시각 전체를 뒤로 민다. 태그가 있는 장소로 부르는
    것이 중요하다(태그가 없으면 원래 경로에서도 LLM을 안 불러 통과해 버린다).
    """

    _place_details_provider(monkeypatch, _TAG_ROWS)

    def _never_called():
        raise AssertionError("상세조회는 LLM을 부르지 않아야 한다")

    monkeypatch.setattr(chat_route, "get_llm_provider", _never_called)
    client = TestClient(app)

    response = client.post(
        "/api/chat/place-details",
        json={"place_id": "2832918", "place_name": "한옥카페 선운각"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    # 태그 자체는 상세 응답에 그대로 실린다 — 화면이 근거 목록을 그린다.
    assert [tag["label"] for tag in body["place_card"]["preference_insights"]] == [
        "자연을 즐기기 좋은"
    ]
    assert "ai_reason" not in body


def test_place_reason_generates_sentence(monkeypatch) -> None:
    """취향 태그가 있으면 문장을 만들어 내려준다."""

    _preference_rows(monkeypatch, _TAG_ROWS)
    seen: list[dict] = []

    class FakeLLM:
        async def generate_place_reason(
            self, *, place_name, category_label, insights, matched_preference_codes=()
        ):
            seen.append(
                {
                    "place_name": place_name,
                    "category_label": category_label,
                    "labels": [insight.label for insight in insights],
                    "matched": list(matched_preference_codes),
                }
            )
            return provider_result(
                "숲속 한옥의 정취를 느끼며 쉬기 좋아요.", source=ProviderSource.FAKE_LLM
            )

    monkeypatch.setattr(chat_route, "get_llm_provider", FakeLLM)
    client = TestClient(app)

    response = client.post(
        "/api/chat/place-details/reason",
        json={
            "place_id": "2832918",
            "place_name": "한옥카페 선운각",
            "category_label": "카페/전통찻집",
            # 화면이 추천 카드에서 들고 온 일치 표시. 문장이 사용자 취향과 맞은
            # 태그부터 말하도록 고르는 기준으로만 쓰인다.
            "matched_preference_codes": ["nature", "nature", " "],
        },
    )

    assert response.status_code == 200
    assert response.json()["ai_reason"] == "숲속 한옥의 정취를 느끼며 쉬기 좋아요."
    assert seen == [
        {
            "place_name": "한옥카페 선운각",
            "category_label": "카페/전통찻집",
            "labels": ["자연을 즐기기 좋은"],
            # 중복과 빈 값은 스키마가 걸러 낸다.
            "matched": ["nature"],
        }
    ]


def test_place_reason_requires_place_id() -> None:
    """근거를 저장소에서 다시 읽으므로 place_id 없이는 부를 수 없다."""

    client = TestClient(app)

    response = client.post(
        "/api/chat/place-details/reason", json={"place_name": "한옥카페 선운각"}
    )

    assert response.status_code == 422


def test_place_reason_skips_without_preference_tags(monkeypatch) -> None:
    """태그가 없는 장소는 아예 부르지 않는다 — 만들 근거가 없다."""

    _preference_rows(monkeypatch, [])

    def _never_called():
        raise AssertionError("취향 태그가 없으면 LLM을 부르지 않아야 한다")

    monkeypatch.setattr(chat_route, "get_llm_provider", _never_called)
    client = TestClient(app)

    response = client.post(
        "/api/chat/place-details/reason",
        json={"place_id": "2832918", "place_name": "한옥카페 선운각"},
    )

    assert response.status_code == 200
    assert response.json()["ai_reason"] is None


def test_place_reason_survives_llm_failure(monkeypatch) -> None:
    """문장 생성이 죽어도 오류가 아니라 빈 문장으로 답한다 — 화면은 그대로 있는다."""

    _preference_rows(monkeypatch, _TAG_ROWS)

    class BrokenLLM:
        async def generate_place_reason(self, **kwargs):
            raise RuntimeError("gemini down")

    monkeypatch.setattr(chat_route, "get_llm_provider", BrokenLLM)
    client = TestClient(app)

    response = client.post(
        "/api/chat/place-details/reason",
        json={"place_id": "2832918", "place_name": "한옥카페 선운각"},
    )

    assert response.status_code == 200
    assert response.json()["ai_reason"] is None


def test_place_reason_skips_when_disabled(monkeypatch) -> None:
    """PLACE_REASON_ENABLED=false면 호출이 통째로 사라진다."""

    _preference_rows(monkeypatch, _TAG_ROWS)
    monkeypatch.setattr(chat_route.settings, "place_reason_enabled", False)

    def _never_called():
        raise AssertionError("꺼져 있으면 LLM을 부르지 않아야 한다")

    monkeypatch.setattr(chat_route, "get_llm_provider", _never_called)
    client = TestClient(app)

    response = client.post(
        "/api/chat/place-details/reason",
        json={"place_id": "2832918", "place_name": "한옥카페 선운각"},
    )

    assert response.json()["ai_reason"] is None


# --- SSE: 후속 질문은 done 뒤에 온다 (D-102) ---------------------------------


def _sse_events(body: str) -> list[tuple[str, dict]]:
    """SSE 본문을 (이벤트 이름, payload) 순서대로 푼다."""

    events: list[tuple[str, dict]] = []
    for frame in body.replace("\r\n", "\n").split("\n\n"):
        lines = [line for line in frame.split("\n") if line]
        name = next((line[6:].strip() for line in lines if line.startswith("event:")), None)
        data = "\n".join(line[5:].strip() for line in lines if line.startswith("data:"))
        if name and data:
            events.append((name, json.loads(data)))
    return events


@pytest.fixture
def streaming(monkeypatch) -> list[str]:
    """run_agent와 후속 질문 LLM을 대역으로 바꾼다. 만들어진 문구를 돌려준다."""

    generated = ["여기 주차되나요?", "다른 곳도 보여줘"]

    async def fake_run_agent(request: AgentRequest, **kwargs) -> AgentResponse:
        # 라우트가 Runtime 쪽 생성을 껐는지 여기서 잠근다 — 켜진 채로 두면 done이
        # 그 호출을 기다려 로딩이 한 번 더 뜬 것처럼 보인다.
        assert kwargs["generate_follow_ups"] is False
        return _fake_response()

    class _FollowUpLLM:
        async def generate_follow_up_suggestions(self, **kwargs):
            return provider_result(list(generated), source=ProviderSource.FAKE_LLM)

    monkeypatch.setattr(chat_route, "run_agent", fake_run_agent)
    monkeypatch.setattr(chat_route, "get_llm_provider", lambda: _FollowUpLLM())
    return generated


def test_stream_sends_follow_ups_after_done(streaming) -> None:
    """순서가 계약이다 — 화면은 done에서 로딩을 감추고 버튼만 뒤늦게 붙인다."""
    client = TestClient(app)

    response = client.post("/api/chat/stream", json={"user_input": "경복궁 근처 카페 추천해줘"})

    assert response.status_code == 200
    names = [name for name, _ in _sse_events(response.text)]
    assert names[-2:] == ["done", "follow_ups"]
    payload = dict(_sse_events(response.text))["follow_ups"]
    assert payload["suggestions"] == ["여기 주차되나요?", "다른 곳도 보여줘"]


def test_stream_omits_the_event_when_there_is_nothing_to_suggest(streaming) -> None:
    """빈 목록을 보내도 화면이 할 일이 없다 — 이벤트 자체를 생략한다."""
    streaming.clear()
    client = TestClient(app)

    response = client.post("/api/chat/stream", json={"user_input": "경복궁 근처 카페 추천해줘"})

    names = [name for name, _ in _sse_events(response.text)]
    assert names[-1] == "done"
    assert "follow_ups" not in names


def test_stream_still_completes_when_follow_ups_blow_up(monkeypatch) -> None:
    """done을 이미 보낸 뒤라, 여기서 예외가 새면 완결된 턴이 오류로 뒤집힌다."""

    async def fake_run_agent(request: AgentRequest, **kwargs) -> AgentResponse:
        return _fake_response()

    def exploding_llm():
        raise RuntimeError("provider 조립 실패")

    monkeypatch.setattr(chat_route, "run_agent", fake_run_agent)
    monkeypatch.setattr(chat_route, "get_llm_provider", exploding_llm)
    client = TestClient(app)

    response = client.post("/api/chat/stream", json={"user_input": "경복궁 근처 카페 추천해줘"})

    names = [name for name, _ in _sse_events(response.text)]
    assert names[-1] == "done"
    assert "error" not in names


# ---------------------------------------------------------------- 화면 기록


def _start_owned_session() -> str:
    """세션을 하나 만들어 둔다. 화면 기록은 세션이 있어야 남는다."""
    from app.state import service as state_service

    applied = state_service.apply(
        state_service.StateApplyRequest(intent="RECOMMEND", confirmed=True)
    )
    return applied.session_id


def _recorded(session_id: str):
    from app.state.store import get_store

    return get_store().get_session_messages(session_id)


def test_턴이_끝나면_화면_기록이_남는다(monkeypatch) -> None:
    """배선이 빠지면 지난 대화가 조용히 빈 채로 복원되므로 라우트에서 확인한다."""
    session_id = _start_owned_session()

    async def fake_run_agent(request: AgentRequest, *, principal=None) -> AgentResponse:
        return _fake_response(session_id)

    monkeypatch.setattr(chat_route, "run_agent", fake_run_agent)

    TestClient(app).post("/api/chat", json={"user_input": "안녕", "session_id": session_id})

    messages = _recorded(session_id)
    assert len(messages) == 1
    assert messages[0].user_input == "안녕"
    assert messages[0].payload["message"] == "테스트 응답"


# Runtime 안에서 남기면 이 값이 늘 비어 있었다 — 후속 질문은 라우트가 done 뒤에
# 붙이기 때문이다(실측: 그렇게 저장된 8건 전부 후속 질문 없음).
def test_화면_기록에_후속_질문이_담긴다(monkeypatch) -> None:
    session_id = _start_owned_session()

    async def fake_run_agent(request: AgentRequest, *, principal=None) -> AgentResponse:
        response = _fake_response(session_id)
        response.suggested_follow_ups = ["근처 카페도 볼까요?"]
        return response

    monkeypatch.setattr(chat_route, "run_agent", fake_run_agent)

    TestClient(app).post("/api/chat", json={"user_input": "안녕", "session_id": session_id})

    assert _recorded(session_id)[0].payload["suggested_follow_ups"] == ["근처 카페도 볼까요?"]


# 영어 화면의 번역도 Runtime 밖에서 일어난다. 안에서 남기면 영어로 대화한
# 사람이 지난 대화를 열었을 때 한국어가 나온다.
def test_영어_대화는_영어로_기록된다(monkeypatch) -> None:
    session_id = _start_owned_session()

    async def fake_run_agent(request: AgentRequest, *, principal=None) -> AgentResponse:
        return _fake_response(session_id)

    async def fake_localize(response: AgentResponse, *, language: str) -> AgentResponse:
        translated = response.model_copy(deep=True)
        translated.message = "Translated answer"
        return translated

    async def fake_request_for_runtime(request: AgentRequest) -> AgentRequest:
        """영어 입력을 한국어로 옮기는 단계. 여기 관심사가 아니라 그대로 넘긴다."""
        return request

    monkeypatch.setattr(chat_route, "run_agent", fake_run_agent)
    monkeypatch.setattr(chat_route, "_request_for_runtime", fake_request_for_runtime)
    monkeypatch.setattr(chat_route, "_response_for_user", fake_localize)

    TestClient(app).post(
        "/api/chat",
        json={"user_input": "hi", "session_id": session_id, "language": "en"},
    )

    assert _recorded(session_id)[0].payload["message"] == "Translated answer"


def test_없는_세션에는_화면_기록을_남기지_않는다(monkeypatch) -> None:
    async def fake_run_agent(request: AgentRequest, *, principal=None) -> AgentResponse:
        return _fake_response("sess_없는세션")

    monkeypatch.setattr(chat_route, "run_agent", fake_run_agent)

    TestClient(app).post("/api/chat", json={"user_input": "안녕"})

    assert _recorded("sess_없는세션") == []


def test_스트리밍_턴도_화면_기록을_남긴다(streaming, monkeypatch) -> None:
    """실사용 경로는 SSE다 — 여기서 빠지면 기록이 사실상 안 쌓인다."""
    session_id = _start_owned_session()

    async def fake_run_agent(request: AgentRequest, **kwargs) -> AgentResponse:
        return _fake_response(session_id)

    monkeypatch.setattr(chat_route, "run_agent", fake_run_agent)

    TestClient(app).post(
        "/api/chat/stream", json={"user_input": "경복궁 근처 카페", "session_id": session_id}
    )

    messages = _recorded(session_id)
    assert len(messages) == 1
    # 후속 질문은 done 뒤에 정해진다. 그 뒤에 기록해야 담긴다.
    assert messages[0].payload["suggested_follow_ups"] == ["여기 주차되나요?", "다른 곳도 보여줘"]


# done을 내보낸 뒤 후속 질문을 만드는 사이에 창을 닫으면 이 제너레이터가 그대로
# 닫힌다. 그 턴만 기록에서 빠지면 그 대화는 영영 "온전하지 않음"으로 판정돼
# 근사치로만 복원된다.
def test_done_뒤에_끊겨도_그_턴의_기록은_남는다(streaming, monkeypatch) -> None:
    session_id = _start_owned_session()

    async def fake_run_agent(request: AgentRequest, **kwargs) -> AgentResponse:
        return _fake_response(session_id)

    async def connection_dropped(*args, **kwargs):
        """창을 닫으면 이 자리에서 제너레이터가 취소된다.

        CancelledError는 BaseException이라 후속 질문을 감싼 except Exception이
        잡지 못한다 — 정확히 그 경로를 흉내 낸다.
        """
        raise asyncio.CancelledError()

    monkeypatch.setattr(chat_route, "run_agent", fake_run_agent)
    monkeypatch.setattr(chat_route, "_follow_ups_for_user", connection_dropped)

    with contextlib.suppress(asyncio.CancelledError):
        TestClient(app).post(
            "/api/chat/stream", json={"user_input": "경복궁 근처 카페", "session_id": session_id}
        )

    assert len(_recorded(session_id)) == 1


# 화면은 api_context를 읽지 않는다. 그대로 담으면 턴마다 좌표 사본이 쌓여
# 세션 하나가 곧 이동 경로가 된다 — 현재 위치는 agent_states에 한 벌 있으면 된다.
def test_화면_기록에_gps_좌표를_담지_않는다(monkeypatch) -> None:
    session_id = _start_owned_session()

    async def fake_run_agent(request: AgentRequest, *, principal=None) -> AgentResponse:
        response = _fake_response(session_id)
        response.state.api_context = ApiContextView(gps_location="37.5796,126.9770")
        return response

    monkeypatch.setattr(chat_route, "run_agent", fake_run_agent)

    TestClient(app).post("/api/chat", json={"user_input": "안녕", "session_id": session_id})

    payload = _recorded(session_id)[0].payload
    assert "api_context" not in payload["state"]
    # 나머지는 그대로다 — 화면이 그리는 값을 함께 깎으면 안 된다.
    assert payload["message"] == "테스트 응답"
