"""후속 질문 제안(`services/runtime/follow_up_suggester.py`) 단위 테스트.

버튼 문구는 누르는 순간 그대로 사용자 발화가 된다. 그래서 여기서 잠그는 것은 "LLM을
불렀다"가 아니라 **LLM이 준 것을 그대로 화면에 올리지 않는다**는 쪽이다 — 개수·길이
상한, 중복 제거, 실패 시 침묵이 전부 그 이야기다.
"""

from __future__ import annotations

import pytest

from app.errors import ProviderUnavailableError
from app.providers.contracts import ProviderSource, provider_result
from app.schemas import (
    AgentRequest,
    AgentResponse,
    ClarificationPayload,
    Intent,
    LLMOutput,
    OutOfScopeCategory,
    OutOfScopePayload,
    OutputStatus,
    RecommendationItem,
    RecommendationResponse,
    Severity,
)
from app.services.runtime.follow_up_suggester import (
    MAX_LABEL_LENGTH,
    MAX_SUGGESTIONS,
    suggest_follow_ups,
)
from app.state.schema import UserConditions as StateUserConditions
from app.state.service import ApiContextView, StateApplyResponse


class _RecordingLLM:
    """전달받은 인자를 그대로 보관하는 최소 LLM 대역."""

    def __init__(self, suggestions: list[str] | None = None) -> None:
        self.suggestions = suggestions if suggestions is not None else ["다른 곳도 보여줘"]
        self.calls: list[dict[str, object]] = []

    async def generate_follow_up_suggestions(self, **kwargs: object):
        self.calls.append(kwargs)
        return provider_result(self.suggestions, source=ProviderSource.FAKE_LLM)


class _FailingLLM:
    def __init__(self, error: Exception) -> None:
        self.error = error

    async def generate_follow_up_suggestions(self, **kwargs: object):
        raise self.error


def _item(place_id: str, name: str) -> RecommendationItem:
    return RecommendationItem(
        place_id=place_id,
        name=name,
        category="cafe",
        distance_km=0.3,
        remaining_minutes=120,
        environment_type="indoor",
        recommendation_reason="가까워요.",
        explanations=[],
        warnings=[],
        score=0.8,
        feature_scores={},
        weights_used={},
    )


def _response(
    *,
    intent: Intent = Intent.RECOMMEND,
    status: OutputStatus = OutputStatus.COMPLETE,
    message: str = "이런 곳들을 찾아봤어요:",
    recommendations: RecommendationResponse | None = None,
    llm_output: LLMOutput | None = None,
) -> AgentResponse:
    return AgentResponse(
        llm_output=llm_output or LLMOutput(intent=intent, status=status),
        state=StateApplyResponse(
            session_id="sess_follow_up",
            run_id="run_follow_up",
            session_created=True,
            user_conditions=StateUserConditions(),
            api_context=ApiContextView(),
            condition_version=1,
            condition_changed=False,
        ),
        recommendations=recommendations,
        message=message,
    )


def _request(
    user_input: str = "경복궁 근처 카페 추천해줘",
    *,
    recent_follow_ups: list[str] | None = None,
) -> AgentRequest:
    return AgentRequest(
        user_input=user_input,
        session_id="sess_follow_up",
        recent_follow_ups=recent_follow_ups or [],
    )


@pytest.mark.asyncio
async def test_suggestions_pass_through_when_they_are_already_clean() -> None:
    llm = _RecordingLLM(["여기 주차되나요?", "이 근처 카페도 알려줘"])

    suggestions = await suggest_follow_ups(_request(), _response(), llm=llm)  # type: ignore[arg-type]

    assert suggestions == ["여기 주차되나요?", "이 근처 카페도 알려줘"]


@pytest.mark.asyncio
async def test_more_suggestions_than_the_cap_are_cut_instead_of_failing_the_turn() -> None:
    """상한 초과는 오류가 아니다 — 답변은 이미 확정됐으니 잘라 쓴다."""
    llm = _RecordingLLM([f"제안 {index}" for index in range(MAX_SUGGESTIONS + 3)])

    suggestions = await suggest_follow_ups(_request(), _response(), llm=llm)  # type: ignore[arg-type]

    assert len(suggestions) == MAX_SUGGESTIONS


@pytest.mark.asyncio
async def test_labels_longer_than_a_button_are_dropped() -> None:
    """버튼 한 칸에 안 들어가는 문구는 화면에서 두 줄로 접힌다."""
    too_long = "가" * (MAX_LABEL_LENGTH + 1)
    llm = _RecordingLLM([too_long, "짧은 제안"])

    suggestions = await suggest_follow_ups(_request(), _response(), llm=llm)  # type: ignore[arg-type]

    assert suggestions == ["짧은 제안"]


@pytest.mark.asyncio
async def test_duplicates_and_the_question_just_asked_are_removed() -> None:
    llm = _RecordingLLM(["다른 곳도 보여줘", "다른 곳도 보여줘", "경복궁 근처 카페 추천해줘"])

    suggestions = await suggest_follow_ups(_request(), _response(), llm=llm)  # type: ignore[arg-type]

    assert suggestions == ["다른 곳도 보여줘"]


@pytest.mark.asyncio
async def test_clarification_turn_gets_no_suggestions() -> None:
    """되묻기 턴에는 이미 그 턴의 선택지 버튼이 붙는다(clarification-options.md)."""
    llm = _RecordingLLM()
    response = _response(
        llm_output=LLMOutput(
            intent=Intent.RECOMMEND,
            status=OutputStatus.NEEDS_CLARIFICATION,
            clarification=ClarificationPayload(message="어디 근처에서 찾을까요?"),
        ),
        message="어디 근처에서 찾을까요?",
    )

    suggestions = await suggest_follow_ups(_request(), response, llm=llm)  # type: ignore[arg-type]

    assert suggestions == []
    assert llm.calls == []


@pytest.mark.asyncio
async def test_out_of_scope_turn_gets_no_suggestions() -> None:
    llm = _RecordingLLM()
    response = _response(
        llm_output=LLMOutput(
            intent=Intent.OUT_OF_SCOPE,
            status=OutputStatus.COMPLETE,
            out_of_scope=OutOfScopePayload(
                category=OutOfScopeCategory.UNRELATED, severity=Severity.LOW
            ),
        ),
        message="여행 관련 질문만 도와드릴 수 있어요.",
    )

    suggestions = await suggest_follow_ups(_request(), response, llm=llm)  # type: ignore[arg-type]

    assert suggestions == []
    assert llm.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error", [ProviderUnavailableError("Gemini"), RuntimeError("예상 못 한 오류")]
)
async def test_provider_failure_is_silent(error: Exception) -> None:
    """버튼을 못 만든 것 때문에 이미 완성된 답변을 실패시키지 않는다."""

    suggestions = await suggest_follow_ups(
        _request(), _response(), llm=_FailingLLM(error)  # type: ignore[arg-type]
    )

    assert suggestions == []


@pytest.mark.asyncio
async def test_shown_place_names_reach_the_model() -> None:
    """**LLM이 실제로 읽는 값을 채우는지 본다.**

    RECOMMEND 성공 경로의 말풍선은 카드 위 고정 문구라 장소 이름이 한 글자도 없다.
    이름을 안 넘기면 호출은 성공하는데 나오는 제안은 "다른 곳 보여줘" 수준으로만
    남는다 — 통과하지만 아무것도 검증하지 못하는 상태가 된다.
    """
    llm = _RecordingLLM()
    response = _response(
        recommendations=RecommendationResponse(
            recommendations=[_item("place-1", "블루보틀 삼청"), _item("place-2", "커피한약방")],
            unverified_recommendations=[_item("place-3", "테라로사 광화문")],
            elapsed_ms=10,
        )
    )

    await suggest_follow_ups(_request(), response, llm=llm)  # type: ignore[arg-type]

    assert llm.calls[0]["place_names"] == ["블루보틀 삼청", "커피한약방", "테라로사 광화문"]
    assert llm.calls[0]["intent"] is Intent.RECOMMEND
    assert llm.calls[0]["user_input"] == "경복궁 근처 카페 추천해줘"


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["car", "walk", "public", None])
async def test_transport_condition_reaches_the_model(transport: str | None) -> None:
    """주차 질문을 권할 자리인지 가릴 근거를 실제로 넘기는지 본다.

    프롬프트에 "walk/public이면 주차 질문을 빼라"고 적어도 이 값이 안 넘어가면 모델은
    판단할 재료가 없다. 걷겠다고 말한 사용자에게 주차 자리를 묻게 하면 버튼 하나를
    통째로 버리는 셈이 된다.
    """
    llm = _RecordingLLM()
    response = _response()
    response.state.user_conditions.transport = transport

    await suggest_follow_ups(_request(), response, llm=llm)  # type: ignore[arg-type]

    assert llm.calls[0]["transport"] == transport


@pytest.mark.asyncio
async def test_a_natural_sentence_fits_within_the_label_cap() -> None:
    """상한이 문구를 전보문으로 만들지 않는지 본다.

    30자였을 때는 "운영시간 알려줘" 수준으로 줄어야 들어갔다. 누르면 그게 그대로 사용자
    발화가 되므로, 사람이 실제로 칠 만한 문장이 상한 안에 들어와야 한다.
    """
    natural = "여기 몇 시까지 하는지 알려줘"
    parking = "주차할 자리 지금 있는지 봐줘"
    assert len(natural) <= MAX_LABEL_LENGTH
    assert len(parking) <= MAX_LABEL_LENGTH

    llm = _RecordingLLM([natural, parking])

    suggestions = await suggest_follow_ups(_request(), _response(), llm=llm)  # type: ignore[arg-type]

    assert suggestions == [natural, parking]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("이 근처 카페도 추천해줘?", "이 근처 카페도 추천해줘"),
        ("이 장소들로 일정 짜줘?", "이 장소들로 일정 짜줘"),
        ("운영시간 알려줘 ?", "운영시간 알려줘"),
        # 진짜 의문문은 그대로 둔다 — 물음표가 있어야 맞는 문장이다.
        ("여기 주차되나요?", "여기 주차되나요?"),
        ("거기까지 얼마나 걸려?", "거기까지 얼마나 걸려?"),
    ],
)
async def test_question_mark_is_dropped_only_from_commands(given: str, expected: str) -> None:
    """후속 '질문'이라고 전부 의문문은 아니다.

    "-줘"는 시키는 말이라 물음표가 붙으면 어색하다. 반대로 "주차되나요?"에서 물음표를
    떼면 그쪽이 틀린 문장이 되므로, 어미로 가를 수 있는 경우에만 손댄다.
    """
    llm = _RecordingLLM([given])

    suggestions = await suggest_follow_ups(_request(), _response(), llm=llm)  # type: ignore[arg-type]

    assert suggestions == [expected]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("given", "expected"),
    [
        # 2026-08-27 실제로 버튼에 나갔던 문구.
        ("운현궁 걷어서 얼마나 걸려?", "운현궁 걸어서 얼마나 걸려?"),
        ("거기까지 걷어가면 몇 분이야?", "거기까지 걸어가면 몇 분이야?"),
        # 이미 맞는 문구는 그대로 둔다.
        ("운현궁 걸어서 얼마나 걸려?", "운현궁 걸어서 얼마나 걸려?"),
    ],
)
async def test_observed_spelling_error_is_corrected(given: str, expected: str) -> None:
    """"걷다"는 ㄷ 불규칙이라 "걷어서"가 아니라 "걸어서"다.

    문구가 그대로 사용자 발화가 되고 화면에도 그대로 실리므로, 프롬프트 지시에만 맡기지
    않고 관측된 오류는 코드에서도 걷어낸다.
    """
    llm = _RecordingLLM([given])

    suggestions = await suggest_follow_ups(_request(), _response(), llm=llm)  # type: ignore[arg-type]

    assert suggestions == [expected]


@pytest.mark.asyncio
async def test_congestion_question_without_a_place_is_dropped() -> None:
    """혼잡도를 묻는데 어디를 묻는지 없으면 버린다.

    대화 문맥으로 서버가 장소를 이어받기는 하지만, 버튼은 사용자가 읽고 고르는 것이다 —
    읽어서 무엇을 묻는지 알 수 없으면 고를 수가 없다.
    """
    llm = _RecordingLLM(["주말에 사람 많아?", "주말 안국역 많이 혼잡해?"])
    response = _response()
    response.state.user_conditions.search_center = "안국역"

    suggestions = await suggest_follow_ups(_request(), response, llm=llm)  # type: ignore[arg-type]

    assert suggestions == ["주말 안국역 많이 혼잡해?"]


@pytest.mark.asyncio
async def test_congestion_question_may_name_a_shown_place() -> None:
    """검색 장소가 아니라 추천 카드의 장소를 지목해도 된다."""
    llm = _RecordingLLM(["지금 커피한약방 사람 많아?"])
    response = _response(
        recommendations=RecommendationResponse(
            recommendations=[_item("place-2", "커피한약방")],
            unverified_recommendations=[],
            elapsed_ms=10,
        )
    )

    suggestions = await suggest_follow_ups(_request(), response, llm=llm)  # type: ignore[arg-type]

    assert suggestions == ["지금 커피한약방 사람 많아?"]


@pytest.mark.asyncio
async def test_non_congestion_questions_are_left_alone() -> None:
    """장소명 요구는 혼잡도 문구에만 건다 — 다른 문구까지 좁히면 멀쩡한 제안이 사라진다."""
    llm = _RecordingLLM(["다른 곳도 보여줘", "이 장소들로 일정 짜줘"])

    suggestions = await suggest_follow_ups(_request(), _response(), llm=llm)  # type: ignore[arg-type]

    assert suggestions == ["다른 곳도 보여줘", "이 장소들로 일정 짜줘"]


@pytest.mark.asyncio
async def test_search_place_reaches_the_model() -> None:
    """"안국역 근처 카페 추천해줘"의 "안국역"은 카드 이름 어디에도 없다.

    안 넘기면 모델이 지역을 지목한 혼잡도 질문을 만들 근거 자체가 없어, 규칙을 아무리
    적어도 주어 없는 문구밖에 못 만든다.
    """
    llm = _RecordingLLM()
    response = _response()
    response.state.user_conditions.search_center = "안국역"

    await suggest_follow_ups(_request(), response, llm=llm)  # type: ignore[arg-type]

    assert llm.calls[0]["search_place"] == "안국역"


@pytest.mark.asyncio
async def test_already_shown_suggestions_are_dropped() -> None:
    """최근에 버튼으로 보여준 문구는 다시 올리지 않는다.

    이 기능이 없을 때는 같은 세 개가 턴마다 되풀이됐다. 셋을 다 눌러봐도 다음 턴에 또
    같은 셋이 나와서, 이어물을 곳이 사실상 없었다.
    """
    llm = _RecordingLLM(["다른 곳도 보여줘", "이 장소들로 일정 짜줘"])

    suggestions = await suggest_follow_ups(
        _request(recent_follow_ups=["다른 곳도 보여줘"]),
        _response(),
        llm=llm,  # type: ignore[arg-type]
    )

    assert suggestions == ["이 장소들로 일정 짜줘"]


@pytest.mark.asyncio
async def test_already_shown_comparison_ignores_spacing_and_question_marks() -> None:
    """표기만 다른 같은 문구도 같은 것으로 본다.

    글자 그대로 비교하면 물음표 하나, 띄어쓰기 하나 차이로 같은 버튼이 다시 나온다 —
    사용자에게는 구분되지 않는 차이다.
    """
    llm = _RecordingLLM(["여기 주차 되나요", "경복궁 지금 붐벼?"])

    suggestions = await suggest_follow_ups(
        _request(recent_follow_ups=["여기 주차되나요?"]),
        _response(),
        llm=llm,  # type: ignore[arg-type]
    )

    assert suggestions == ["경복궁 지금 붐벼?"]


@pytest.mark.asyncio
async def test_already_shown_list_reaches_the_model() -> None:
    """걸러내기만으로는 부족하다 — 모델이 처음부터 다른 방향을 잡아야 한다.

    제외 목록을 안 넘기면 모델은 매번 같은 셋을 만들고, 호출부가 그걸 다 버려서 버튼이
    한 개도 안 남는 턴이 생긴다.
    """
    llm = _RecordingLLM()

    await suggest_follow_ups(
        _request(recent_follow_ups=["다른 곳도 보여줘"]),
        _response(),
        llm=llm,  # type: ignore[arg-type]
    )

    assert llm.calls[0]["already_suggested"] == ["다른 곳도 보여줘"]


@pytest.mark.asyncio
async def test_everything_filtered_out_yields_no_buttons() -> None:
    """다 걸러지면 빈 목록이다. 억지로 채우지 않는다."""
    llm = _RecordingLLM(["다른 곳도 보여줘"])

    suggestions = await suggest_follow_ups(
        _request(recent_follow_ups=["다른 곳도 보여줘"]),
        _response(),
        llm=llm,  # type: ignore[arg-type]
    )

    assert suggestions == []
