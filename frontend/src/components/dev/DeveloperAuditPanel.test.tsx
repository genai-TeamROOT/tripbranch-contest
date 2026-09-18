/*
 * 역할: C Tool 탭이 후보별 혼잡도 출처를 구분해 보여주는지 검증한다.
 * 입력: candidate_enrichment 실행 정보를 담은 감사 turn.
 * 출력: 근사치 표시, 직접 조회 표시, 해당 없는 행 숨김에 대한 assertion.
 * 호출 시점: vitest 실행 시 호출된다.
 */

import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { DeveloperAuditTurn, RecommendationItem, ToolExecutionDebug } from "../../types";
import { DeveloperAuditPanel } from "./DeveloperAuditPanel";

const enrichmentExecution: ToolExecutionDebug = {
  operation: "candidate_enrichment",
  request_id: "enrich-1",
  status: "success",
  latency_ms: 180,
  providers: [],
  context_items: [],
  rule_versions: {},
  resolved_location_name: null,
  resolved_location_address: null,
  error_code: null,
  clarification_code: null,
  is_proxy: null,
  candidate_status_counts: { success: 2 },
  candidate_concentration: [
    {
      place_id: "126510",
      name: "종묘",
      status: "success",
      is_proxy: false,
      proxy_place_name: null,
      proxy_distance_km: null,
    },
    {
      place_id: "999999",
      name: "이름없는 카페",
      status: "success",
      is_proxy: true,
      proxy_place_name: "서울 운현궁",
      proxy_distance_km: 0.15,
    },
  ],
};

function _turn(execution: ToolExecutionDebug): DeveloperAuditTurn {
  return {
    id: "turn-1",
    userInput: "안국역 근처 한산한 곳",
    intent: "RECOMMEND",
    status: "complete",
    message: "추천 결과예요.",
    sessionId: "session-1",
    runId: "run-1",
    elapsedMsClient: 1200,
    serverElapsedMs: 1100,
    stageTimings: [],
    extractedConditions: null,
    beforeConditions: null,
    afterConditions: null,
    recommendations: null,
    failure: null,
    response: {
      tool_executions: [execution],
      state: { api_context: null },
    } as unknown as DeveloperAuditTurn["response"],
  };
}

async function openToolsTab(execution: ToolExecutionDebug) {
  const user = userEvent.setup();
  render(
    <DeveloperAuditPanel
      turns={[_turn(execution)]}
      selectedTurnId="turn-1"
      onSelectTurn={() => {}}
      debugIgnoreOperatingHours={false}
      onToggleDebugIgnoreOperatingHours={() => {}}
    />,
  );
  await user.click(screen.getByRole("button", { name: "C Tool" }));
}

function scoringItem(overrides: Partial<RecommendationItem> = {}): RecommendationItem {
  return {
    place_id: "place-score-1",
    name: "혼밥 테스트 식당",
    category: "restaurant",
    category_label: "한식",
    distance_km: 0.42,
    remaining_minutes: 180,
    environment_type: "indoor",
    recommendation_reason: "테스트 추천이에요.",
    explanations: ["가까운 곳이에요."],
    warnings: [],
    score: 0.784,
    feature_scores: {
      weather: 1,
      remaining_operating_time: 0.75,
      distance: 0.8,
      taste: 0.85,
    },
    weights_used: {
      weather: 0.35,
      remaining_operating_time: 0.35,
      distance: 0.15,
      taste: 0.15,
    },
    taste_evidence: [
      { text: "혼자 편하게 식사하기 좋았어요.", similarity: 0.61 },
    ],
    taste_tag_score: 0.5,
    taste_tag_label: "혼자 가기 좋은",
    taste_tag_documents: 4,
    taste_tag_details: [
      {
        code: "alone",
        label: "혼자 가기 좋은",
        positive_document_count: 4,
        negative_document_count: 0,
        candidate_max_positive_document_count: 8,
        relative_score: 0.5,
      },
    ],
    taste_embedding_similarity: 0.61,
    taste_embedding_score: 0.82,
    taste_combined_score: 0.85,
    scoring_rank: 1,
    preference_tags: [
      { code: "alone", label: "혼자 가기 좋은", mention_count: 4, is_query_match: true },
    ],
    ...overrides,
  };
}

function renderScoringTab(turn: DeveloperAuditTurn) {
  render(
    <DeveloperAuditPanel
      turns={[turn]}
      selectedTurnId="turn-1"
      onSelectTurn={() => {}}
      debugIgnoreOperatingHours={false}
      onToggleDebugIgnoreOperatingHours={() => {}}
    />,
  );
}

it("D Scoring에서 원점수·가중치·기여점과 임베딩·태그 결합 과정을 보여준다", async () => {
  const user = userEvent.setup();
  const turn = _turn(enrichmentExecution);
  const item = scoringItem();
  turn.recommendations = {
    recommendations: [item],
    unverified_recommendations: [],
    elapsed_ms: 20,
  };

  renderScoringTab(turn);
  await user.click(screen.getByRole("button", { name: "D Scoring" }));

  const card = within(screen.getByTestId("scoring-breakdown-place-score-1"));
  expect(card.getByText("총점")).toBeInTheDocument();
  expect(card.getByText("원점수")).toBeInTheDocument();
  expect(card.getByText("가중치")).toBeInTheDocument();
  expect(card.getByText("기여점")).toBeInTheDocument();
  // 임베딩 환산점수와 태그 상대점수가 한 식으로 결합돼 최종 취향 점수가 된다.
  expect(card.getByText("최종 0.85")).toBeInTheDocument();
  expect(card.getByText("0.82 + 0.35 × 0.50 × (1 - 0.82) = 0.85")).toBeInTheDocument();
  expect(card.getByText("태그 점수 0.50")).toBeInTheDocument();
  expect(card.getByText("임베딩 환산점수 0.82")).toBeInTheDocument();
  expect(card.getByText("“혼자 편하게 식사하기 좋았어요.”")).toBeInTheDocument();

  await user.click(card.getByText("태그 계산 자세히 보기"));
  expect(card.getByText("4 / 8 = 0.50")).toBeInTheDocument();

  await user.click(card.getByText("원본 점수 JSON 보기"));
  expect(card.getByText(/"taste_combined_score"/)).toBeInTheDocument();
});

it("추천되지 않은 채점 후보와 점수 계산 전 탈락 후보를 따로 펼쳐 볼 수 있다", async () => {
  const user = userEvent.setup();
  const turn = _turn(enrichmentExecution);
  const shown = scoringItem();
  const hidden = scoringItem({
    place_id: "place-score-2",
    name: "채점만 된 식당",
    score: 0.41,
    scoring_rank: 6,
  });
  turn.recommendations = {
    recommendations: [shown],
    unverified_recommendations: [],
    scoring_candidates: [hidden, shown],
    scoring_excluded_candidates: [
      {
        place_id: "place-closed",
        name: "문 닫은 식당",
        category: "restaurant",
        distance_km: 0.3,
        reason: "closed",
      },
    ],
    elapsed_ms: 20,
  };

  renderScoringTab(turn);
  await user.click(screen.getByRole("button", { name: "D Scoring" }));

  expect(screen.queryByTestId("scoring-breakdown-place-score-2")).not.toBeVisible();
  await user.click(screen.getByText("추천되지 않은 채점 후보 1곳 점수 보기"));
  const hiddenCard = within(screen.getByTestId("scoring-breakdown-place-score-2"));
  expect(hiddenCard.getByText("6. 채점만 된 식당")).toBeInTheDocument();
  expect(hiddenCard.getByText("채점 후 미노출")).toBeInTheDocument();

  await user.click(screen.getByText("점수 계산 전 탈락 후보 1곳 보기"));
  expect(screen.getByText("문 닫은 식당")).toBeInTheDocument();
  expect(screen.getByText("현재 운영시간 아님")).toBeInTheDocument();
});

it("빌려온 값과 직접 조회한 값을 구분해서 보여준다", async () => {
  await openToolsTab(enrichmentExecution);

  expect(screen.getByText("후보별 혼잡도 출처")).toBeInTheDocument();
  // 어느 후보가 어디서 얼마나 떨어진 값을 빌렸는지가 핵심이다.
  expect(screen.getByText("근사치 ← 서울 운현궁 (0.15km)")).toBeInTheDocument();
  expect(screen.getByText("직접 조회")).toBeInTheDocument();
  expect(screen.getByText(/근사치 1건/)).toBeInTheDocument();
});

it("후보 보강 섹션에는 해석된 위치·주소를 띄우지 않는다", async () => {
  // 후보가 여럿이라 한 칸으로 표현되지 않는 항목이다. 빈 값으로 두면 채워져야
  // 하는데 빠진 것처럼 보인다.
  await openToolsTab(enrichmentExecution);

  expect(screen.queryByText("해석된 위치")).not.toBeInTheDocument();
  expect(screen.queryByText("해석된 주소")).not.toBeInTheDocument();
});

it("근사치가 없으면 전부 직접 조회했다고 알린다", async () => {
  await openToolsTab({
    ...enrichmentExecution,
    candidate_concentration: [enrichmentExecution.candidate_concentration![0]],
  });

  expect(
    screen.getByText("값이 있는 후보는 모두 자기 매핑으로 직접 조회했어요."),
  ).toBeInTheDocument();
});

it("실패한 턴은 오류 배너와 상세 확인 버튼을 보여준다", async () => {
  const user = userEvent.setup();
  const failedTurn: DeveloperAuditTurn = {
    id: "turn-error",
    userInput: "경복궁 근처 카페 추천해줘",
    intent: "ERROR",
    status: "error",
    message: "Gemini 응답이 지연되고 있어요. 잠시 후 다시 시도해주세요.",
    sessionId: "session-1",
    runId: null,
    elapsedMsClient: 32000,
    serverElapsedMs: null,
    stageTimings: [],
    extractedConditions: null,
    beforeConditions: null,
    afterConditions: null,
    recommendations: null,
    response: null,
    failure: {
      code: "provider_timeout",
      message: "Gemini 응답이 지연되고 있어요. 잠시 후 다시 시도해주세요.",
      retryable: true,
      details: { upstream_detail: "timeout after 3 attempts" },
    },
  };

  render(
    <DeveloperAuditPanel
      turns={[failedTurn]}
      selectedTurnId="turn-error"
      onSelectTurn={() => {}}
      debugIgnoreOperatingHours={false}
      onToggleDebugIgnoreOperatingHours={() => {}}
    />,
  );

  expect(screen.getByText(/오류 발생 · provider_timeout/)).toBeInTheDocument();
  expect(
    screen.getByText("Gemini 응답이 지연되고 있어요. 잠시 후 다시 시도해주세요."),
  ).toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: "오류 상세 확인" }));

  expect(screen.getByText(/"code": "provider_timeout"/)).toBeInTheDocument();
  expect(screen.getByText(/"upstream_detail": "timeout after 3 attempts"/)).toBeInTheDocument();
});

it("소요시간 탭에서 단계별 합계와 C 세부 시간을 보여준다", async () => {
  const user = userEvent.setup();
  const turn = _turn(enrichmentExecution);
  turn.response = {
    ...turn.response,
    llm_execution: {
      calls: [
        {
          operation: "classify_intent",
          attempted_models: ["gemini-2.5-flash"],
          served_model: "gemini-2.5-flash",
          latency_ms: 420,
        },
        {
          operation: "extract_recommend_conditions",
          attempted_models: ["gemini-2.5-flash"],
          served_model: "gemini-2.5-flash",
          latency_ms: 1430,
        },
      ],
    },
  } as DeveloperAuditTurn["response"];
  turn.stageTimings = [
    {
      stage: "interpreting",
      message: "요청 의도와 조건을 파악하고 있어요.",
      started_at_ms: 0,
      duration_ms: 850,
    },
    {
      stage: "merging_conditions",
      message: "이전 대화 조건을 반영하고 있어요.",
      started_at_ms: 850,
      duration_ms: 30,
    },
    {
      stage: "fetching_context",
      message: "장소·운영시간·날씨 정보를 찾고 있어요.",
      started_at_ms: 880,
      duration_ms: 180,
    },
    {
      stage: "scoring",
      message: "조건에 맞게 장소 순위를 계산하고 있어요.",
      started_at_ms: 1060,
      duration_ms: 40,
    },
    {
      stage: "scheduling",
      message: "장소 순서와 머무는 시간을 구성하고 있어요.",
      started_at_ms: 1100,
      duration_ms: 500,
    },
    {
      stage: "composing_message",
      message: "추천 결과를 안내하고 있어요.",
      started_at_ms: 1600,
      duration_ms: 600,
      time_to_first_token_ms: 240,
    },
  ];
  render(
    <DeveloperAuditPanel
      turns={[turn]}
      selectedTurnId="turn-1"
      onSelectTurn={() => {}}
      debugIgnoreOperatingHours={false}
      onToggleDebugIgnoreOperatingHours={() => {}}
    />,
  );

  await user.click(screen.getByRole("button", { name: "소요시간" }));

  expect(screen.getByText("이번 요청 총 소요")).toBeInTheDocument();
  expect(screen.getByText("LLM 의도·조건 추출")).toBeInTheDocument();
  expect(screen.getByText("세션 상태 병합")).toBeInTheDocument();
  expect(screen.getByText("장소·정보 조회")).toBeInTheDocument();
  expect(screen.getByText("추천 순위 계산")).toBeInTheDocument();
  expect(screen.getByText("일정 편성")).toBeInTheDocument();
  expect(screen.getByText("답변 생성·정리")).toBeInTheDocument();
  expect(screen.getByText(/첫 글자 도착\(TTFT\) · 240ms/)).toBeInTheDocument();
  expect(screen.getByText(/classify_intent · gemini-2.5-flash · 420ms/)).toBeInTheDocument();
  expect(
    screen.getByText(/extract_recommend_conditions · gemini-2.5-flash · 1.4초/),
  ).toBeInTheDocument();
  expect(screen.getByText(/후보 혼잡도 보강 · 180ms · success/)).toBeInTheDocument();
});

it("소요시간 탭에서 답변 생성 호출의 재시도 여부와 소요 시간을 보여준다 (D-076 검토 후속)", async () => {
  const user = userEvent.setup();
  const turn = _turn(enrichmentExecution);
  turn.response = {
    ...turn.response,
    llm_execution: {
      calls: [
        {
          operation: "generate_compare_summary",
          attempted_models: ["gemini-3.5-flash"],
          served_model: "gemini-3.5-flash",
          latency_ms: 13200,
          retry_count: 1,
        },
      ],
    },
  } as DeveloperAuditTurn["response"];
  turn.stageTimings = [
    {
      stage: "composing_message",
      message: "비교 결과를 정리하고 있어요.",
      started_at_ms: 0,
      duration_ms: 13200,
    },
  ];

  render(
    <DeveloperAuditPanel
      turns={[turn]}
      selectedTurnId="turn-1"
      onSelectTurn={() => {}}
      debugIgnoreOperatingHours={false}
      onToggleDebugIgnoreOperatingHours={() => {}}
    />,
  );

  await user.click(screen.getByRole("button", { name: "소요시간" }));

  // 예전에는 composing_message 단계에서 latency_ms를 아예 안 보여줬다 — 이제는 보인다.
  expect(screen.getByText(/generate_compare_summary · gemini-3.5-flash · 13.2초/)).toBeInTheDocument();
  // retry_count=1(재시도 1회, 총 시도 2회)이면 "시도 2회"가 보인다.
  expect(screen.getByText("시도 2회")).toBeInTheDocument();
});

it("소요시간 탭에서 재시도가 없었던 호출도 시도 1회로 표시한다", async () => {
  const user = userEvent.setup();
  const turn = _turn(enrichmentExecution);
  turn.response = {
    ...turn.response,
    llm_execution: {
      calls: [
        {
          operation: "classify_intent",
          attempted_models: ["gemini-3.5-flash-lite"],
          served_model: "gemini-3.5-flash-lite",
          latency_ms: 300,
          retry_count: 0,
        },
      ],
    },
  } as DeveloperAuditTurn["response"];
  turn.stageTimings = [
    {
      stage: "interpreting",
      message: "요청 의도와 조건을 파악하고 있어요.",
      started_at_ms: 0,
      duration_ms: 300,
    },
  ];

  render(
    <DeveloperAuditPanel
      turns={[turn]}
      selectedTurnId="turn-1"
      onSelectTurn={() => {}}
      debugIgnoreOperatingHours={false}
      onToggleDebugIgnoreOperatingHours={() => {}}
    />,
  );

  await user.click(screen.getByRole("button", { name: "소요시간" }));

  // "재시도 여부가 아예 표시되는지 확인이 안 된다"는 혼란을 없애기 위해, 재시도가
  // 0이어도 "시도 1회"를 항상 보여준다 — 값이 있는지 없는지 자체가 의미 있는 정보다.
  expect(screen.getByText("시도 1회")).toBeInTheDocument();
});

it("LLM 추출 탭에서 재시도가 있었던 호출에 안내 문구를 보여준다", async () => {
  const user = userEvent.setup();
  const turn = _turn(enrichmentExecution);
  turn.response = {
    ...turn.response,
    llm_execution: {
      calls: [
        {
          operation: "generate_compare_summary",
          attempted_models: ["gemini-3.5-flash"],
          served_model: "gemini-3.5-flash",
          latency_ms: 13200,
          retry_count: 1,
        },
        {
          operation: "classify_intent",
          attempted_models: ["gemini-3.5-flash-lite"],
          served_model: "gemini-3.5-flash-lite",
          latency_ms: 300,
          retry_count: 0,
        },
      ],
    },
  } as DeveloperAuditTurn["response"];

  render(
    <DeveloperAuditPanel
      turns={[turn]}
      selectedTurnId="turn-1"
      onSelectTurn={() => {}}
      debugIgnoreOperatingHours={false}
      onToggleDebugIgnoreOperatingHours={() => {}}
    />,
  );

  await user.click(screen.getByRole("button", { name: "LLM 추출" }));

  // retry_count=1(재시도 있었음) — 경고색 안내 문구.
  expect(
    screen.getByText(/시도 2회 끝에 성공 — 소요 시간에 재시도 대기가 포함돼 있어요\./),
  ).toBeInTheDocument();
  // retry_count=0(재시도 없었음)도 항상 표시하되, 문구와 색이 다르다.
  expect(screen.getByText("시도 1회로 끝났어요 — 재시도 없음.")).toBeInTheDocument();
});
