from __future__ import annotations

import pytest

from app.schemas import (
    AgentRequest,
    AgentResponse,
    Intent,
    LLMOutput,
    OutputStatus,
    RecommendationItem,
    RecommendationResponse,
)
from app.services.runtime.localization import (
    localize_request_for_runtime,
    localize_response_for_user,
)
from app.state.schema import UserConditions as StateUserConditions
from app.state.service import ApiContextView, StateApplyResponse


class FakeTranslator:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], str, str]] = []

    async def translate_many(
        self, texts: list[str], *, source_language: str, target_language: str
    ) -> list[str]:
        self.calls.append((texts, source_language, target_language))
        return [f"{target_language}:{text}" for text in texts]


def _response() -> AgentResponse:
    return AgentResponse(
        llm_output=LLMOutput(intent=Intent.RECOMMEND, status=OutputStatus.COMPLETE),
        state=StateApplyResponse(
            session_id="sess_translation",
            run_id="run_translation",
            session_created=True,
            user_conditions=StateUserConditions(),
            api_context=ApiContextView(),
            condition_version=1,
            condition_changed=False,
        ),
        recommendations=RecommendationResponse(
            recommendations=[
                RecommendationItem(
                    place_id="place-1",
                    name="경복궁",
                    category="attraction",
                    distance_km=0.2,
                    remaining_minutes=120,
                    environment_type="outdoor",
                    recommendation_reason="가까운 궁궐이에요.",
                    explanations=["도보로 가까워요."],
                    warnings=["비가 오면 우산이 필요해요."],
                    score=0.9,
                    feature_scores={},
                    weights_used={},
                )
            ],
            unverified_recommendations=[],
            elapsed_ms=10,
        ),
        message="경복궁을 추천드려요.",
    )


@pytest.mark.asyncio
async def test_english_request_is_translated_without_changing_language_or_session() -> None:
    translator = FakeTranslator()
    original = AgentRequest(
        user_input="Find an indoor museum near Gyeongbokgung",
        language="en",
        session_id="sess_keep",
    )

    localized = await localize_request_for_runtime(original, translator)  # type: ignore[arg-type]

    assert original.user_input == "Find an indoor museum near Gyeongbokgung"
    assert localized.user_input == "ko:Find an indoor museum near Gyeongbokgung"
    assert localized.language == "en"
    assert localized.session_id == "sess_keep"
    assert translator.calls == [([original.user_input], "en", "ko")]


@pytest.mark.asyncio
async def test_english_response_translates_display_text_but_preserves_place_identifiers() -> None:
    translator = FakeTranslator()
    original = _response()

    localized = await localize_response_for_user(original, language="en", translator=translator)  # type: ignore[arg-type]

    assert original.message == "경복궁을 추천드려요."
    assert localized.message == "en:경복궁을 추천드려요."
    assert localized.recommendations is not None
    item = localized.recommendations.recommendations[0]
    assert item.name == "경복궁"
    assert item.place_id == "place-1"
    assert item.recommendation_reason == "en:가까운 궁궐이에요."
    assert item.explanations == ["en:도보로 가까워요."]
    assert item.warnings == ["en:비가 오면 우산이 필요해요."]


@pytest.mark.asyncio
async def test_follow_up_suggestions_are_translated_for_english_screens() -> None:
    """버튼 문구도 화면에 보이는 문장이다.

    빠뜨리면 영어 화면 아래에 한국어 버튼만 남는다 — 눌러도 동작은 하지만(발화가
    다시 한국어로 번역돼 Runtime에 들어간다) 화면이 두 언어로 갈린다.
    """
    translator = FakeTranslator()
    response = _response()
    response.suggested_follow_ups = ["여기 주차되나요?", "다른 곳도 보여줘"]

    localized = await localize_response_for_user(
        response, language="en", translator=translator  # type: ignore[arg-type]
    )

    assert localized.suggested_follow_ups == ["en:여기 주차되나요?", "en:다른 곳도 보여줘"]
