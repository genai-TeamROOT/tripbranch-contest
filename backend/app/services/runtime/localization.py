"""영어 UI와 기존 한국어 Agent Runtime 사이의 번역 경계.

언어별 상태를 B에 저장하지 않는다. 입력은 Runtime 직전에 한국어 사본으로 바꾸고,
출력은 프론트로 보내기 직전에 사용자 문장만 영어로 바꾼다. ID·좌표·점수·Intent와
장소명은 구조화/검색 기준값이므로 번역하지 않는다.
"""

from __future__ import annotations

from collections.abc import Callable

from app.providers.google_translate import GoogleTranslateProvider
from app.schemas import AgentRequest, AgentResponse


async def localize_request_for_runtime(
    request: AgentRequest, translator: GoogleTranslateProvider | None
) -> AgentRequest:
    """영어 발화만 한국어 Runtime 입력으로 바꾼 불변 사본을 반환한다."""

    if request.language != "en":
        return request
    assert translator is not None
    translated = await translator.translate_many(
        [request.user_input], source_language="en", target_language="ko"
    )
    return request.model_copy(update={"user_input": translated[0]})


async def localize_response_for_user(
    response: AgentResponse, *, language: str, translator: GoogleTranslateProvider | None
) -> AgentResponse:
    """영어 화면에 보이는 가변 문장만 한 번에 번역한다."""

    if language != "en":
        return response
    assert translator is not None
    localized = response.model_copy(deep=True)
    setters: list[Callable[[str], None]] = []
    texts: list[str] = []

    def add(value: str | None, setter: Callable[[str], None]) -> None:
        if value:
            texts.append(value)
            setters.append(setter)

    add(localized.message, lambda value: setattr(localized, "message", value))

    clarification = localized.llm_output.clarification
    if clarification is not None:
        add(clarification.message, lambda value: setattr(clarification, "message", value))
        for option in clarification.options:
            add(option.label, lambda value, option=option: setattr(option, "label", value))

    for index, suggestion in enumerate(localized.suggested_follow_ups):
        add(
            suggestion,
            lambda value, index=index: localized.suggested_follow_ups.__setitem__(index, value),
        )

    recommendations = localized.recommendations
    if recommendations is not None:
        for item in [*recommendations.recommendations, *recommendations.unverified_recommendations]:
            add(
                item.recommendation_reason,
                lambda value, item=item: setattr(item, "recommendation_reason", value),
            )
            for index, explanation in enumerate(item.explanations):
                add(
                    explanation,
                    lambda value, item=item, index=index: item.explanations.__setitem__(
                        index, value
                    ),
                )
            for index, warning in enumerate(item.warnings):
                add(
                    warning,
                    lambda value, item=item, index=index: item.warnings.__setitem__(index, value),
                )

    schedule = localized.schedule
    if schedule is not None:
        add(schedule.route_summary, lambda value: setattr(schedule, "route_summary", value))
        add(schedule.basis_note, lambda value: setattr(schedule, "basis_note", value))
        for item in schedule.items:
            add(item.reason, lambda value, item=item: setattr(item, "reason", value))
            for index, warning in enumerate(item.warnings):
                add(
                    warning,
                    lambda value, item=item, index=index: item.warnings.__setitem__(index, value),
                )

    card = localized.info_place_card
    if card is not None:
        add(card.overview, lambda value: setattr(card, "overview", value))
        add(
            card.population_current_message,
            lambda value: setattr(card, "population_current_message", value),
        )
        for key, answer in card.answer_fields.items():
            add(answer, lambda value, key=key: card.answer_fields.__setitem__(key, value))

    translated = await translator.translate_many(texts, source_language="ko", target_language="en")
    for setter, value in zip(setters, translated, strict=True):
        setter(value)
    return localized


async def localize_follow_ups_for_user(
    suggestions: list[str], *, language: str, translator: GoogleTranslateProvider | None
) -> list[str]:
    """후속 질문 버튼 문구만 따로 번역한다.

    SSE 경로는 `done`을 먼저 보내고 후속 질문을 그 뒤에 만들기 때문에, 응답 전체를
    번역하는 `localize_response_for_user()`가 이미 지나간 뒤에 문구가 생긴다. 그
    문구도 화면에 보이는 문장이라 같은 규칙을 타야 해서 이 진입점을 따로 둔다.

    **버튼을 누르면 이 영어 문구가 그대로 user_input으로 재전송되지만 문제되지
    않는다.** 영어 발화는 Runtime 직전에 다시 한국어로 번역되므로
    (`localize_request_for_runtime`) 왕복이 성립한다.
    """

    if language != "en" or not suggestions:
        return suggestions
    assert translator is not None
    return await translator.translate_many(
        suggestions, source_language="ko", target_language="en"
    )


__all__ = [
    "localize_follow_ups_for_user",
    "localize_request_for_runtime",
    "localize_response_for_user",
]
