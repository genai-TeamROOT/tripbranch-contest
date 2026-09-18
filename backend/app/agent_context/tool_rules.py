"""A의 정규화 조건을 C가 실행할 Tool 계획으로 변환한다."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.agent_context.schemas import UserConditions

TOOL_EXECUTION_RULE_VERSION = "context-tool-plan-v1"

# 날씨 조회를 생략하는 weather_intent 값. "상관없다고 명시함"만 해당한다.
_WEATHER_SKIP_INTENTS = frozenset({"IGNORE"})
# 발화에 날씨가 있다는 뜻인 값. 그 값이 실제로 채워졌을 때만 조회를 생략한다.
_WEATHER_STATED_INTENTS = frozenset({"AVOID", "ENJOY"})


class ContextTool(StrEnum):
    """초기 추천 Context 수집 단계에서 선택할 수 있는 Tool."""

    RESOLVE_LOCATION = "resolve_location"
    SEARCH_PLACES = "search_places"
    GET_WEATHER = "get_weather"
    GET_HOLIDAYS = "get_holidays"
    GET_CONCENTRATION = "get_concentration"


@dataclass(frozen=True)
class ToolExecutionPlan:
    """한 번의 초기 Context 요청에서 실행할 Tool 집합."""

    tools: frozenset[ContextTool]
    rule_version: str = TOOL_EXECUTION_RULE_VERSION

    def requires(self, tool: ContextTool) -> bool:
        return tool in self.tools


def build_tool_execution_plan(
    conditions: UserConditions, *, places_only: bool = False
) -> ToolExecutionPlan:
    """조건과 요청 성격에 따라 Context 수집 Tool을 선택한다.

    위치·장소·공휴일은 추천과 운영정보 판단에 필요하다. 날씨는 쓸 값이 없을 때만
    조회한다 — 자세한 기준은 _requires_weather() 참고. Concentration은 D가 선정한
    상위 후보를 보강하는 후조회이므로 초기 계획에 포함하지 않는다.

    `places_only`는 같은 턴 안의 보충 조회다(`AgentContextRequest.resolved_search_center`).
    **위치·날씨·공휴일을 계산해도 A가 버린다** — 보충 배치에서는 places만 합치고
    나머지는 첫 배치 값을 그대로 쓰기 때문이다. 그래서 아예 부르지 않는다. 기준점은
    A가 첫 조회에서 확정한 좌표를 그대로 넘겨받으므로 위치 해석도 필요 없다.
    """

    if places_only:
        return ToolExecutionPlan(tools=frozenset({ContextTool.SEARCH_PLACES}))

    tools = {
        ContextTool.RESOLVE_LOCATION,
        ContextTool.SEARCH_PLACES,
        ContextTool.GET_HOLIDAYS,
    }
    if _requires_weather(conditions):
        tools.add(ContextTool.GET_WEATHER)
    return ToolExecutionPlan(tools=frozenset(tools))


def _requires_weather(conditions: UserConditions) -> bool:
    """날씨를 조회할지 판단한다.

    - IGNORE("날씨 상관없어" 명시): 쓰지 않을 값이라 조회하지 않는다.
    - AVOID/ENJOY: 사용자가 말한 날씨를 D가 쓴다. 다만 발화에서 5단계 값을 뽑지
      못하는 경우가 있어(실측 2026-08-05: "날씨 안 좋으니까 실내로", "바람 많이
      부는데" 등 6건 중 3건이 weather=null) 그때는 조회해서 채운다. 조회하지 않으면
      날씨를 분명히 말한 사용자에게만 날씨가 빠지는 결과가 된다.
    - NO_MENTION(또는 과도기 null): 언급이 없어도 조회한다. 말하지 않았다고 무시하면
      비 오는 날 야외 장소를 그대로 추천하게 된다(int-01-recommend.md §10).
    """
    if conditions.weather_intent in _WEATHER_SKIP_INTENTS:
        return False
    if conditions.weather_intent in _WEATHER_STATED_INTENTS:
        return conditions.weather is None
    return True


__all__ = [
    "ContextTool",
    "TOOL_EXECUTION_RULE_VERSION",
    "ToolExecutionPlan",
    "build_tool_execution_plan",
]
