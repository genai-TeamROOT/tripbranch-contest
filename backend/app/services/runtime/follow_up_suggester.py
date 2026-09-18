"""턴이 끝난 뒤 버튼으로 보여줄 다음 발화 후보를 만든다.

역할: 방금 끝난 대화 한 턴(사용자 발화 + 우리 답변 + 이번 턴에 나간 장소들)을 LLM에
      넘겨, 사용자가 이어서 물을 법한 문구 0~3개를 받아 정제한다.
입력: 이 턴의 AgentRequest와 완성된 AgentResponse, LLMProvider.
출력: 버튼에 그대로 넣을 문구 목록. 만들 게 없거나 실패하면 빈 목록.
호출 시점: `run_agent_flow()`가 본체(`_run_agent_flow`)의 응답을 받은 직후 한 번.

**답변을 만드는 LLM 호출에 얹지 않고 별도로 부른다.** 답변 본문을 만드는 경로는
인텐트마다 다르고(GENERAL/INFO/COMPARE만 LLM 자유 생성, RECOMMEND는 카드 wrapper,
SCHEDULE·OUT_OF_SCOPE는 템플릿) 그중 셋은 텍스트를 스트리밍한다 — 같은 호출에서 JSON
필드를 함께 받으려면 스트리밍을 포기해야 한다. 여기서 따로 부르면 SSE 경로에서는 답변이
이미 화면에 다 뜬 뒤에 도는 호출이라 사용자가 기다리는 시간이 늘지 않는다.

실패는 조용히 빈 목록으로 낮춘다. 이 시점의 답변·카드는 이미 확정돼 있어서, 버튼을
못 만든 것 때문에 턴 전체를 실패시킬 이유가 없다.
"""

from __future__ import annotations

import logging

from app.errors import AppError
from app.providers.protocols import LLMProvider
from app.schemas import AgentRequest, AgentResponse, Intent, OutputStatus

logger = logging.getLogger(__name__)

# 버튼은 답변 아래 한 줄에 놓인다. 넷을 넘기면 줄이 접히고 고르는 비용이 답변을 읽는
# 비용보다 커진다.
MAX_SUGGESTIONS = 3

# 버튼 문구 길이 상한. 프롬프트에도 같은 값을 싣지만 지켰는지는 여기서 다시 검사한다.
#
# 처음에는 30자였는데 그 상한이 문구를 전보문처럼 만들었다("운영시간 알려줘"). 버튼에
# 들어가는 것보다 **사람이 실제로 칠 만한 문장**인 쪽이 중요하다 — 누르면 그게 그대로
# 사용자 발화가 되기 때문이다. 40자면 "여기 몇 시까지 하는지 알려줘" 같은 문장이 들어가고,
# 화면(`SuggestedFollowUps.tsx`)은 한 줄에 하나씩 흘려 담는다.
MAX_LABEL_LENGTH = 40

# 물음표를 떼어낼 문장 끝. **의문문이 될 수 없는 명령형 어미만 넣는다.**
#
# 후속 "질문"이라고 해서 전부 의문문은 아니다 — "이 근처 카페도 추천해줘"는 시키는
# 말이라 물음표가 붙으면 어색하다. 프롬프트에도 규칙을 적었지만 지켰는지는 여기서
# 확인한다.
#
# **일반 규칙으로 넓히지 않는다.** "얼마나 걸려?"나 "주차되나요?"는 물음표가 있어야
# 맞는 문장이고, 무엇이 질문인지를 어미만으로 가르는 규칙은 만들 수 없다. 그래서
# "-줘"처럼 의문문으로 읽힐 여지가 없는 어미만 목록으로 둔다. 목록에 없는 어미가
# 물음표를 달고 오면 그대로 통과하고, 그건 프롬프트 쪽에서 잡을 몫이다.
_IMPERATIVE_ENDINGS = ("줘", "다오", "해봐", "보여줘")

# 실제로 나온 맞춤법 오류를 문구에서 걷어낸다.
#
# **일반 맞춤법 검사기가 아니다.** 한국어 맞춤법을 코드로 일반화하려면 형태소 분석기나
# 외부 검사 API가 필요한데, 버튼 문구 하나 때문에 의존성을 늘릴 자리가 아니다. 여기 있는
# 것은 이 슬롯에서 **실제로 관측된** 오류뿐이고, 나머지는 프롬프트의 맞춤법 규칙이 맡는다.
#
# "걷다"는 ㄷ 불규칙이라 "걷어서"가 아니라 "걸어서"다. 2026-08-27에 "운현궁 걷어서 얼마나
# 걸려?"가 버튼으로 나갔다. 걸어서 이동하는 맥락 밖에서 "걷어서"(걷어 올리다)가 쓰일 일이
# 이 슬롯에는 없어 그대로 바꾼다.
# 혼잡도를 묻는 문구인지 알아보는 표지. 이 문구에는 반드시 장소명이 있어야 한다.
#
# "주말에 사람 많아?"처럼 주어가 없으면 어디를 묻는지 화면만 보고는 알 수 없다. 대화
# 문맥으로 서버가 장소를 이어받기는 하지만, 버튼은 사용자가 읽고 고르는 것이라 읽어서
# 무엇을 묻는지 알 수 없으면 고를 수가 없다.
_CONGESTION_MARKERS = ("혼잡", "붐비", "붐빌", "사람 많", "사람이 많")

_TYPO_FIXES = (
    ("걷어서", "걸어서"),
    ("걷어가", "걸어가"),
    ("걷어도", "걸어도"),
    ("걷어야", "걸어야"),
)

# LLM에 넘길 장소 이름 수. 이번 턴에 화면에 나간 순서대로 앞에서 자른다 — 뒤쪽 카드는
# 사용자가 후속 질문의 대상으로 삼을 확률이 낮은데 토큰만 늘린다.
_MAX_PLACE_NAMES = 5

# 후속 질문을 만들지 않는 Intent.
#
# OUT_OF_SCOPE: 못 하는 요청을 받은 턴이다. 여기서 다음 발화를 권하면 거절 바로 뒤에
#               버튼을 들이미는 모양이 된다.
_SKIPPED_INTENTS = frozenset({Intent.OUT_OF_SCOPE})


def _place_names(response: AgentResponse) -> list[str]:
    """이번 턴에 화면으로 나간 장소 이름을 순서대로 모은다.

    **답변 텍스트만으로는 부족해서 필요하다.** RECOMMEND 성공 경로의 말풍선은
    카드 위에 붙는 고정 문구라(`response_composer._RECOMMEND_WRAPPER_MESSAGE`)
    거기엔 장소 이름이 한 글자도 없다. 이름을 따로 넘기지 않으면 모델은 "다른 곳
    보여줘" 수준의 일반적인 문구밖에 만들지 못한다.
    """

    names: list[str] = []
    recommendations = response.recommendations
    if recommendations is not None:
        names.extend(
            item.name
            for item in [
                *recommendations.recommendations,
                *recommendations.unverified_recommendations,
            ]
        )
    if response.schedule is not None:
        names.extend(item.place_name for item in response.schedule.items)
    if response.comparison is not None:
        names.extend(item.place_name for item in response.comparison.items)
    if response.info_place_card is not None and response.info_place_card.place_name:
        names.append(response.info_place_card.place_name)

    unique: list[str] = []
    for name in names:
        cleaned = name.strip()
        if cleaned and cleaned not in unique:
            unique.append(cleaned)
    return unique[:_MAX_PLACE_NAMES]


def _search_place(response: AgentResponse) -> str | None:
    """이번 대화가 잡고 있는 검색 장소. 없으면 None.

    B가 누적한 조건에서 읽는다 — 사용자가 말한 지명(`search_center`)이 우선이고,
    없으면 현재 위치로 잡힌 지명(`current_location`)을 쓴다. 추천 카드 이름과 겹치지
    않는 별개 값이다.
    """

    conditions = response.state.user_conditions
    for value in (conditions.search_center, conditions.current_location):
        if value and value.strip():
            return value.strip()
    return None


def _should_suggest(response: AgentResponse) -> bool:
    """이 턴에 버튼을 붙일 자리인지 판단한다."""

    if response.llm_output.status is OutputStatus.NEEDS_CLARIFICATION:
        # 서버가 이미 되물은 턴이다. 되묻기 자체가 버튼을 달고 나가기도 해서
        # (ClarificationPayload.options) 두 종류의 버튼이 같은 말풍선에 겹친다.
        return False
    if response.llm_output.intent in _SKIPPED_INTENTS:
        return False
    return bool(response.message.strip())


def _mentions_a_place(label: str, known_places: list[str]) -> bool:
    """이번 턴에 등장한 장소 이름이 문구 안에 있는지 본다."""

    return any(place and place in label for place in known_places)


def _drops_congestion_without_a_place(label: str, known_places: list[str]) -> bool:
    """혼잡도를 묻는데 어디를 묻는지 없는 문구인가.

    프롬프트에도 규칙을 적지만, 지켜졌는지는 여기서 본다. 장소명이 없는 혼잡도 질문은
    버튼으로 읽었을 때 무엇을 묻는지 알 수 없어 그대로 버린다 — 세 개를 채우는 것보다
    읽어서 이해되는 두 개가 낫다.
    """

    if not any(marker in label for marker in _CONGESTION_MARKERS):
        return False
    return not _mentions_a_place(label, known_places)


def _comparable(label: str) -> str:
    """두 문구가 같은 말인지 견주려고 표기 차이를 지운다.

    글자 그대로 비교하면 걸러야 할 것을 놓친다. 같은 요청이 물음표 하나, 띄어쓰기
    하나만 달라도 다른 문자열이 되기 때문이다 — "여기 주차되나요?"와 "여기 주차되나요"는
    사용자에게 같은 버튼이다.

    **표기만 지우고 뜻은 건드리지 않는다.** 조사나 어미까지 손대면 서로 다른 요청이
    같은 것으로 뭉개진다("경복궁 가는 길 막혀?"와 "경복궁 가는 길 막혔어?"는 같지만,
    "경복궁 주차돼?"와 "경복궁 주차장 있어?"는 다르게 남아야 한다). 여기서 지우는 것은
    공백과 문장 끝 부호뿐이다.
    """

    return "".join(label.split()).rstrip("?？!！.。").casefold()


def _fix_known_typos(label: str) -> str:
    """관측된 맞춤법 오류만 바로잡는다. 목록에 없는 것은 건드리지 않는다."""

    for wrong, right in _TYPO_FIXES:
        label = label.replace(wrong, right)
    return label


def _strip_stray_question_mark(label: str) -> str:
    """명령형 문장에 붙은 물음표를 뗀다. 그 밖에는 손대지 않는다."""

    stripped = label.rstrip("?？").rstrip()
    if stripped != label and stripped.endswith(_IMPERATIVE_ENDINGS):
        return stripped
    return label


def _clean(
    suggestions: list[str],
    *,
    user_input: str,
    known_places: list[str],
    already_shown: list[str],
) -> list[str]:
    """모델이 준 문구를 화면에 올릴 수 있는 형태로 좁힌다.

    프롬프트에 적은 개수·길이 상한은 부탁이고 실제 계약은 여기다. 상한을 넘겨 받아도
    턴을 실패시키지 않고 잘라 쓴다 — 버튼은 답변에 딸린 부가물이라 없는 편이 잘못된
    것보다 낫고, 잘못된 것보다는 몇 개 적은 편이 낫다.

    already_shown은 화면이 최근에 버튼으로 보여준 문구다(AgentRequest.recent_follow_ups).
    모델에도 같은 목록을 넘겨 피하게 하지만, 지켜졌는지는 여기서 본다.
    """

    seen = {_comparable(user_input)}
    seen.update(_comparable(shown) for shown in already_shown)
    cleaned: list[str] = []
    for suggestion in suggestions:
        label = _fix_known_typos(_strip_stray_question_mark(" ".join(suggestion.split())))
        if not label or len(label) > MAX_LABEL_LENGTH:
            continue
        # 방금 한 질문도, 최근에 이미 버튼으로 보여준 문구도 다시 권하지 않는다.
        key = _comparable(label)
        if key in seen:
            continue
        if _drops_congestion_without_a_place(label, known_places):
            continue
        seen.add(key)
        cleaned.append(label)
        if len(cleaned) == MAX_SUGGESTIONS:
            break
    return cleaned


async def suggest_follow_ups(
    request: AgentRequest, response: AgentResponse, *, llm: LLMProvider
) -> list[str]:
    """이 턴 뒤에 보여줄 후속 질문 문구를 만든다. 실패하면 빈 목록.

    response.suggested_follow_ups가 이미 채워져 있으면 그대로 돌려주고 LLM을
    부르지 않는다 — GENERAL 상황 턴의 제안 버튼(대화층 4단계, agent_runtime.py의
    GENERAL 조기 반환 분기)이 이 자리를 미리 채워 둔다. 무엇을 제안할지는 이미
    코드(situational_offers)가 정했으므로 여기서 다시 만들 이유가 없다.
    """

    if response.suggested_follow_ups:
        return list(response.suggested_follow_ups)

    if not _should_suggest(response):
        return []

    place_names = _place_names(response)
    search_place = _search_place(response)
    # 화면이 최근에 버튼으로 보여준 문구. 개수·길이는 AgentRequest 검증기가 이미 잘랐다.
    already_shown = list(request.recent_follow_ups)
    try:
        result = await llm.generate_follow_up_suggestions(
            user_input=request.user_input,
            intent=response.llm_output.intent,
            assistant_message=response.message,
            place_names=place_names,
            # 카드 이름과 별개다 — "안국역 근처 카페 추천해줘"의 "안국역"은 카드 이름
            # 어디에도 없어서, 안 넘기면 지역을 가리키는 후속 질문을 만들 근거가 없다.
            search_place=search_place,
            # 주차 질문을 권할 자리인지 모델이 가릴 근거. 도보·대중교통으로 움직이는
            # 사용자에게 주차 자리를 묻게 하면 버튼 하나를 통째로 버리는 셈이 된다.
            transport=response.state.user_conditions.transport,
            # 이미 권한 문구. 아래 _clean()이 다시 걸러내지만, 모델이 처음부터 다른
            # 방향을 잡게 하는 쪽이 본질적이다 — 걸러내기만 하면 세 자리를 채우지
            # 못하고 버튼이 한두 개로 줄어든다.
            already_suggested=already_shown,
            max_suggestions=MAX_SUGGESTIONS,
            max_label_length=MAX_LABEL_LENGTH,
        )
    except AppError:
        # Provider 장애(타임아웃·rate limit)는 이 기능에서 흔한 실패다. 스택까지
        # 남기면 정상 운영 중에도 로그가 시끄러워져 메시지만 남긴다.
        logger.warning("후속 질문 제안 실패(답변에는 영향 없음)", exc_info=True)
        return []
    except Exception:
        logger.warning("후속 질문 제안에서 예상 못 한 오류", exc_info=True)
        return []

    return _clean(
        result.data,
        user_input=request.user_input,
        known_places=[*place_names, *([search_place] if search_place else [])],
        already_shown=already_shown,
    )


__all__ = ["MAX_LABEL_LENGTH", "MAX_SUGGESTIONS", "suggest_follow_ups"]
