"""조기 반환 경로 그래프가 노드 사이에 돌리는 상태(강의 61-2의 "공유 서류철").

`run_agent_flow()`의 조기 반환 블록(Tool/Scoring 없이 끝나는 경로)이 이 그래프의
범위다 — GENERAL·OUT_OF_SCOPE·되묻기, 그리고 아직 Tool을 안 타는 INFO/COMPARE
낙오 케이스가 여기로 온다(docs/design/langgraph-adoption.md §6.1 2단계).

`AgentState`(app/state/schema.py)를 그대로 쓰지 않는 이유: 그건 B가 세션에 보관하는
누적 상태이고, 이건 한 턴 안에서 노드끼리 주고받는 작업용 서류철이라 수명이 다르다.
3단계에서 그래프가 흐름 전체를 가지게 되면 두 개의 관계를 다시 정한다.
"""

from __future__ import annotations

from typing import TypedDict

from app.schemas import ConversationTurnView, LLMOutput


class EarlyReturnState(TypedDict):
    """Tool/Scoring 없이 끝나는 한 턴의 작업 상태."""

    # 분류·추출이 끝난 결과. 그래프는 이 값을 읽기만 하고 바꾸지 않는다.
    llm_output: LLMOutput
    # GENERAL 답변을 조각으로 흘려보낼지. SSE 경로에서만 True다
    # (단발 POST /api/chat은 False라 한 번에 완성 문자열만 만든다).
    stream_general: bool
    # 이 세션에서 이미 거절된 상황 제안의 action_id 목록(대화층 4단계). GENERAL
    # 답변이 같은 제안을 다시 권하지 않으려면 여기서부터 compose_chat_message()까지
    # 전달돼야 한다 — 그래프 상태 밖(session_context)에는 접근할 수 없다.
    rejected_offer_actions: list[str]
    # 최근 대화(오래된 것이 앞). 답변 문장이 앞 턴과 이어지도록 생성 단계까지
    # 전달한다 — rejected_offer_actions와 같은 이유로 그래프 상태에 실어야 한다
    # (노드는 session_context에 접근할 수 없다).
    history: list[ConversationTurnView]
    # 답변 노드가 채우는 칸.
    answer: str | None


__all__ = ["EarlyReturnState"]
