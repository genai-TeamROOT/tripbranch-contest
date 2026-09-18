"""그래프 노드를 관측 span으로 감싸는 래퍼(`_observed`) 회귀 테스트.

**왜 콜백이 아니라 직접 감싸나**: Langfuse가 주는 LangChain `CallbackHandler`는
`langchain` **본체**를 import한다. 우리는 langgraph가 끌고 온 `langchain-core`만
두고 본체·통합 패키지는 의도적으로 안 넣었다(pyproject.toml). 2026-08-25에 콜백으로
붙여봤다가 `ModuleNotFoundError`로 **조용히 꺼지는 것**을 실측으로 확인했다 — 앱은
멀쩡했고 노드 span만 통째로 안 생겼다.

**여기서 잡는 것은 시그니처다.** LangGraph는 노드의 시그니처를 보고 `config`를
넘길지 정한다(`_internal/_runnable.py`). 감싼 함수가 `*args`로만 보이면 `state`
하나만 넘겨서 `TypeError: missing 1 required positional argument: 'config'`로 죽는다.
`functools.wraps`를 빠뜨렸을 때 실제로 스위트 103건이 깨졌다.
"""

from __future__ import annotations

import inspect
import json
from contextlib import contextmanager
from typing import TypedDict

import pytest
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from app.schemas import (
    CandidateConcentrationDebug,
    LocationDebug,
    RecommendationItem,
    RecommendationResponse,
    TasteEvidenceQuote,
    ToolContextItemDebug,
    ToolExecutionDebug,
    ToolProviderDebug,
)
from app.services.runtime.graph import (
    _observed,
    _summarize_answer,
    _summarize_finalize,
    _summarize_scoring,
    _summarize_tool_fetch,
    concentration_source_rows,
)


class _State(TypedDict):
    value: int


async def _node_with_config(state: _State, config: RunnableConfig) -> dict[str, int]:
    return {"value": state["value"] + 1}


def test_wrapper_keeps_the_original_signature() -> None:
    """LangGraph가 `config`를 넘길지 판단하는 근거다 — 표시용이 아니다."""
    wrapped = _observed("tool_fetch", _node_with_config)

    assert list(inspect.signature(wrapped).parameters) == ["state", "config"]
    assert wrapped.__name__ == "_node_with_config"


@pytest.mark.asyncio
async def test_wrapped_node_still_runs_inside_a_real_graph() -> None:
    """시그니처가 깨지면 여기서 TypeError로 죽는다.

    관측이 꺼져 있어도(테스트 기본값) 래퍼는 그대로 통과해야 한다 — 감싼 것 자체가
    동작을 바꾸면 안 된다.
    """
    graph = StateGraph(_State)
    graph.add_node("tool_fetch", _observed("tool_fetch", _node_with_config))
    graph.add_node("scoring", _observed("scoring", _node_with_config))
    graph.add_edge(START, "tool_fetch")
    graph.add_edge("tool_fetch", "scoring")
    graph.add_edge("scoring", END)

    result = await graph.compile().ainvoke({"value": 1})

    assert result == {"value": 3}


@pytest.mark.asyncio
async def test_wrapper_does_not_swallow_node_failures() -> None:
    """관측이 삼켜도 되는 건 자기 실패지 노드의 실패가 아니다."""

    async def _broken(state: _State, config: RunnableConfig) -> dict[str, int]:
        raise ValueError("노드 실패")

    wrapped = _observed("scoring", _broken)

    with pytest.raises(ValueError, match="노드 실패"):
        await wrapped({"value": 1}, {})


def test_pipeline_nodes_are_registered_wrapped() -> None:
    """실제 파이프라인 그래프의 단계가 전부 감싸여 있는지 확인한다.

    이름은 B의 Trace `step`과 같아야 두 기록을 나란히 읽을 수 있다.
    """
    from app.services.runtime.graph import build_recommend_pipeline_graph

    compiled = build_recommend_pipeline_graph()

    assert {"tool_fetch", "scoring", "schedule", "finalize"} <= set(compiled.nodes)


def test_early_return_nodes_are_registered_wrapped() -> None:
    """이 두 노드는 span이 아예 없었다 — `_observed()`를 안 씌워서다.

    GENERAL 턴의 trace에는 `agent_turn` 밑에 generation 하나만 떠 있어서, 노드가
    얼마나 걸렸고 답변이 실제로 나갔는지가 화면에서 안 보였다.
    """
    from app.services.runtime.graph import build_early_return_graph
    from app.services.runtime.graph.nodes.general import general_answer_node
    from app.services.runtime.graph.nodes.static_answer import static_answer_node

    compiled = build_early_return_graph()

    assert {"general_answer", "static_answer"} <= set(compiled.nodes)
    for name, original in (
        ("general_answer", general_answer_node),
        ("static_answer", static_answer_node),
    ):
        bound = compiled.nodes[name].bound
        # 감싸지 않았으면 원본이 그대로라 `__wrapped__`가 없다.
        assert bound.afunc.__wrapped__ is original
        # 감싼 뒤에도 LangGraph가 `config`를 넘기려면 원본 시그니처가 보여야 한다.
        assert "config" in bound.func_accepts


def test_answer_summary_records_the_length_not_the_text() -> None:
    """같은 문자열이 바로 아래 generation output에 이미 있다 — 두 번 실을 이유가 없다."""
    assert _summarize_answer({"answer": "안녕하세요, 무엇을 도와드릴까요?"}) == {
        "answer_length": 18
    }
    # 노드가 답을 못 채웠으면 span에 빈 값을 적지 않는다.
    assert _summarize_answer({"answer": None}) is None


# --- span 요약: 무엇을 싣고 무엇을 빼는가 -------------------------------------


def _item(place_id: str, score: float) -> RecommendationItem:
    return RecommendationItem(
        place_id=place_id,
        name=f"장소 {place_id}",
        category="cafe",
        distance_km=0.42,
        remaining_minutes=120,
        environment_type="indoor",
        recommendation_reason="조건을 종합한 추천이에요.",
        explanations=["현재 위치에서 가까운 장소예요."],
        warnings=[],
        score=score,
        feature_scores={"distance": 0.7234891, "weather": 0.8, "taste": None},
        weights_used={"distance": 0.2, "weather": 0.4},
        taste_evidence=[TasteEvidenceQuote(text="조용하고 아늑했어요", similarity=0.61)],
    )


def _response(items: list[RecommendationItem]) -> RecommendationResponse:
    return RecommendationResponse(
        recommendations=items,
        unverified_recommendations=[],
        elapsed_ms=0,
        excluded_closed_place_ids=["p9", "p10"],
    )


def test_scoring_summary_keeps_the_scores_that_explain_the_ranking() -> None:
    summary = _summarize_scoring({"recommendations": _response([_item("p1", 0.7213)])})

    assert summary is not None
    ranked = summary["ranked"][0]
    assert ranked["place_id"] == "p1"
    assert ranked["score"] == 0.721
    # 소수점을 줄인다 — 0.7234891은 화면에서 읽는 데 방해만 된다.
    assert ranked["features"] == {"distance": 0.723, "weather": 0.8, "taste": None}
    assert ranked["weights"] == {"distance": 0.2, "weather": 0.4}
    assert summary["excluded_closed_count"] == 2


def test_scoring_summary_carries_the_prose_that_explains_the_score() -> None:
    """근거 문장·설명을 싣는다 — 2026-08-26에 뒤집은 결정이다.

    원래는 "점수를 읽는 데 방해된다"고 뺐는데, 정작 **왜 그 점수가 나왔나**를 쫓을 때
    근거 문장이 없어 span만으로는 답이 안 나왔다. Audit 화면의 "D Scoring" 탭과 같은
    값을 싣는다.
    """
    summary = _summarize_scoring({"recommendations": _response([_item("p1", 0.7)])})

    assert summary is not None
    ranked = summary["ranked"][0]
    assert ranked["explanations"] == ["현재 위치에서 가까운 장소예요."]
    assert ranked["warnings"] == []
    assert ranked["taste_evidence"] == [{"text": "조용하고 아늑했어요", "similarity": 0.61}]
    # 화면이 카테고리·거리로 후보를 가려내므로 함께 싣는다.
    assert ranked["category"] == "cafe"
    assert ranked["distance_km"] == 0.42


def test_scoring_summary_caps_the_evidence_quotes_but_says_how_many_there_were() -> None:
    """`taste_evidence`는 RPC가 찾은 만큼 전부 들어 있어 상한이 없다.

    한 이벤트가 너무 커지면 Langfuse가 통째로 버려서 span 자체가 사라진다. 잘라내되
    **잘랐다는 사실이 보여야** 근거가 하나뿐인 것과 구분된다.
    """
    quotes = [TasteEvidenceQuote(text=f"근거 {i}", similarity=0.5) for i in range(25)]
    item = _item("p1", 0.7).model_copy(update={"taste_evidence": quotes})
    summary = _summarize_scoring({"recommendations": _response([item])})

    assert summary is not None
    assert len(summary["ranked"][0]["taste_evidence"]) == 10
    assert summary["ranked"][0]["taste_evidence_count"] == 25


def test_scoring_summary_caps_how_many_candidates_it_carries() -> None:
    summary = _summarize_scoring(
        {"recommendations": _response([_item(f"p{i}", 0.5) for i in range(25)])}
    )

    assert summary is not None
    assert len(summary["ranked"]) == 10
    # 잘라낸 것과 별개로 전체 건수는 남긴다 — 안 그러면 10곳뿐인 줄 안다.
    assert summary["ranked_count"] == 25


def test_finalize_summary_records_the_message_length_not_its_text() -> None:
    """답변 원문은 발화만큼이나 사용자 것이다. 필요하면 generation output에 이미 있다."""

    class _Resp:
        recommendations = _response([_item("p1", 0.7), _item("p2", 0.6)])
        message = "경복궁 근처 조용한 카페 두 곳을 찾았어요."
        schedule = None
        comparison = None

    summary = _summarize_finalize({"response": _Resp()})

    assert summary is not None
    assert summary["card_order"] == ["p1", "p2"]
    assert summary["card_count"] == 2
    assert summary["message_length"] == len(_Resp.message)
    assert "경복궁" not in json.dumps(summary, ensure_ascii=False)


def test_summaries_return_none_when_the_node_produced_nothing() -> None:
    assert _summarize_scoring({}) is None
    assert _summarize_finalize({}) is None


@pytest.mark.asyncio
async def test_a_failing_summary_does_not_break_the_node() -> None:
    """요약이 터져도 노드 결과는 그대로 나가야 한다."""

    def _explode(_: object) -> dict[str, object]:
        raise RuntimeError("요약 실패")

    wrapped = _observed("scoring", _node_with_config, _explode)

    assert await wrapped({"value": 1}, {}) == {"value": 2}


# --- tool_fetch 요약: 빈 상자를 채우되 인자는 안 싣는다 ------------------------


def _execution(**overrides: object) -> ToolExecutionDebug:
    defaults: dict[str, object] = {
        "operation": "context_fetch",
        "request_id": "req-1",
        "status": "success",
        "latency_ms": 812,
        "providers": [
            ToolProviderDebug(source="kakao_local", status="success"),
            ToolProviderDebug(source="kma_weather", status="unavailable"),
        ],
        "context_items": [
            ToolContextItemDebug(key="location", fetched=True, status="success"),
            ToolContextItemDebug(
                key="weather", fetched=True, status="unavailable", error_code="upstream_timeout"
            ),
            ToolContextItemDebug(key="holidays", fetched=False),
        ],
        "rule_versions": {"operating_hours": "1.2.0"},
        "resolved_location_name": "경복궁",
        "resolved_location_address": "서울 종로구 사직로 161",
    }
    defaults.update(overrides)
    return ToolExecutionDebug(**defaults)


def test_tool_fetch_summary_shows_which_providers_ran_and_what_failed() -> None:
    summary = _summarize_tool_fetch({"tool_executions": [_execution()]})

    assert summary is not None
    call = summary["calls"][0]
    assert summary["call_count"] == 1
    assert call["operation"] == "context_fetch"
    assert call["latency_ms"] == 812
    assert call["providers"] == [
        {"source": "kakao_local", "status": "success"},
        {"source": "kma_weather", "status": "unavailable"},
    ]
    assert call["item_errors"] == {"weather": "upstream_timeout"}
    assert call["rule_versions"] == {"operating_hours": "1.2.0"}


def test_tool_fetch_summary_separates_not_queried_from_failed() -> None:
    """`fetched=False`는 실패가 아니라 아예 안 부른 것이다 — 섞이면 오진한다."""
    summary = _summarize_tool_fetch({"tool_executions": [_execution()]})

    assert summary is not None
    assert summary["calls"][0]["items"] == {
        "location": "success",
        "weather": "unavailable",
        "holidays": "skipped",
    }


def test_tool_fetch_summary_carries_the_resolved_location() -> None:
    """장소명·주소를 싣는다 — 2026-08-26에 뒤집은 결정이다.

    **이 값은 사용자가 어디를 찾는지 그대로 드러낸다.** 원래는 `capture_content`를
    켜도 안 새게 두 겹으로 막았는데, 화면에 보이는 값이 trace에 없어 원인을 쫓을
    때마다 API 응답을 따로 받아야 했다. 이제 방어선은 그 스위치 하나다.
    """
    summary = _summarize_tool_fetch({"tool_executions": [_execution()]})

    assert summary is not None
    call = summary["calls"][0]
    assert call["resolved_location_name"] == "경복궁"
    assert call["resolved_location_address"] == "서울 종로구 사직로 161"


def test_tool_fetch_summary_carries_all_three_locations_separately() -> None:
    """셋은 서로 다를 수 있고 **다른 것 자체가 관측 대상이다**(TP-112).

    `route_origin.source`가 `search_center`면 사용자 위치를 몰라 검색 위치로 대체한
    턴이라, 거리·경로 표기가 사실과 어긋날 수 있다.
    """
    summary = _summarize_tool_fetch(
        {
            "tool_executions": [
                _execution(
                    search_location=LocationDebug(
                        name="경복궁", source="query", latitude=37.5796, longitude=126.977
                    ),
                    route_origin=LocationDebug(
                        source="search_center", latitude=37.5796, longitude=126.977
                    ),
                )
            ]
        }
    )

    assert summary is not None
    locations = summary["calls"][0]["locations"]
    assert locations["search"] == {"name": "경복궁", "source": "query"}
    assert locations["route_origin"]["source"] == "search_center"
    # 값이 없는 것과 안 실은 것이 구분돼야 한다.
    assert locations["user"] is None


def test_tool_fetch_summary_never_carries_the_coordinates() -> None:
    """`source`는 남기고 **위경도는 뺀다**(2026-08-26 결정).

    팀원이 테스트하는 자리의 실좌표라 스위치 하나에 맡길 값이 아니다. `source`만
    있으면 "사용자 위치를 몰라 검색 위치로 대체했나"는 그대로 읽힌다.
    """
    summary = _summarize_tool_fetch(
        {
            "tool_executions": [
                _execution(
                    user_location=LocationDebug(
                        source="device_gps", latitude=37.5796, longitude=126.977
                    )
                )
            ]
        }
    )

    blob = json.dumps(summary, ensure_ascii=False)
    assert "37.5796" not in blob
    assert "126.977" not in blob
    assert "latitude" not in blob
    assert "longitude" not in blob


def test_tool_fetch_summary_shows_where_each_concentration_value_came_from() -> None:
    """근사치가 섞이는 게 정상 상태라(활성 844건 중 매핑 100건) 건수로는 못 가린다."""
    summary = _summarize_tool_fetch(
        {
            "tool_executions": [
                _execution(
                    candidate_status_counts={"success": 2},
                    candidate_concentration=[
                        CandidateConcentrationDebug(
                            place_id="p1", name="후보1", status="success", is_proxy=False
                        ),
                        CandidateConcentrationDebug(
                            place_id="p2",
                            name="후보2",
                            status="success",
                            is_proxy=True,
                            proxy_place_name="경복궁",
                            proxy_distance_km=0.31,
                        ),
                    ],
                )
            ]
        }
    )

    assert summary is not None
    call = summary["calls"][0]
    assert call["candidate_status_counts"] == {"success": 2}
    rows = call["candidate_concentration"]
    # 상태만 보면 둘 다 "success 1건"이라 같아 보인다 — 출처가 갈라져 있어야 한다.
    assert [row["is_proxy"] for row in rows] == [False, True]
    assert rows[1]["proxy_place_name"] == "경복궁"
    assert rows[1]["proxy_distance_km"] == 0.31


def test_concentration_rows_are_built_by_one_function_for_both_spans() -> None:
    """`tool_fetch`와 `concentration_enrichment`가 같은 함수를 쓴다.

    같은 사실을 두 모양으로 적으면 한쪽만 고쳤을 때 조용히 어긋난다.
    """
    execution = _execution(
        candidate_concentration=[
            CandidateConcentrationDebug(place_id="p1", name="후보1", status="success")
        ]
    )
    summary = _summarize_tool_fetch({"tool_executions": [execution]})

    assert summary is not None
    assert summary["calls"][0]["candidate_concentration"] == concentration_source_rows(execution)


def test_tool_fetch_summary_marks_a_turn_that_ended_before_scoring() -> None:
    """조기 종료는 Tool 기록이 없다 — 값이 없는 것과 건너뛴 것이 구분돼야 한다."""
    summary = _summarize_tool_fetch({"response": object()})

    assert summary == {"terminal": True, "call_count": 0, "calls": []}


def test_tool_fetch_summary_is_none_when_the_node_did_nothing() -> None:
    assert _summarize_tool_fetch({}) is None


def test_tool_fetch_summary_caps_how_many_calls_it_carries() -> None:
    summary = _summarize_tool_fetch({"tool_executions": [_execution() for _ in range(25)]})

    assert summary is not None
    assert len(summary["calls"]) == 10
    assert summary["call_count"] == 25


# --- intent 태그: 루트를 연 뒤에도 붙는다 --------------------------------------


def test_intent_tag_is_built_from_the_turn_intent() -> None:
    """지금 태그는 scoring·env뿐이라 RECOMMEND와 INFO가 한 덩어리로 섞여 있다."""
    from app.schemas import Intent, LLMOutput, OutputStatus
    from app.services.runtime.graph import _intent_tag

    output = LLMOutput(intent=Intent.RECOMMEND, status=OutputStatus.COMPLETE)

    assert _intent_tag(output) == ["intent:RECOMMEND"]


@pytest.mark.asyncio
async def test_pipeline_entry_opens_the_intent_tag_scope() -> None:
    """태그가 실릴 observation이 그 범위 안에 생겨야 trace 태그가 된다.

    2026-08-25 실측: 루트 span을 연 뒤 `trace_attributes(tags=...)`를 열어도 trace
    태그에 합쳐진다. 단 그 안에서 observation이 하나도 안 생기면 실릴 데가 없다.
    그래서 그래프 진입에 뒀다 — 안에서 노드 span이 반드시 생긴다.
    """
    from unittest.mock import patch

    from app.schemas import Intent, LLMOutput, OutputStatus
    from app.services.runtime import graph as graph_module

    scopes: list[list[str]] = []

    @contextmanager
    def _spy(**kwargs: object):
        tags = kwargs.get("tags")
        if tags:
            scopes.append(list(tags))  # type: ignore[arg-type]
        yield

    graph = StateGraph(_State)
    graph.add_node("only", _observed("scoring", _node_with_config))
    graph.add_edge(START, "only")
    graph.add_edge("only", END)

    with (
        patch.object(graph_module, "trace_attributes", _spy),
        patch.object(graph_module, "_EARLY_RETURN_GRAPH", graph.compile()),
    ):
        # 조기 반환 경로도 같은 규칙을 따른다.
        try:
            await graph_module.run_early_return_graph(
                LLMOutput(intent=Intent.INFO, status=OutputStatus.COMPLETE), llm=object()
            )
        except KeyError:
            pass  # 더미 그래프라 answer 키가 없다 — 태그 범위만 확인한다

    assert scopes == [["intent:INFO"]]
