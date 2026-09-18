/*
 * 역할: AgentResponse 하나를 화면 말풍선 목록(ChatMessage[])으로 바꾼다.
 * 입력: 한 턴의 AgentResponse와 그 턴에만 있는 값(사용자 발화, 실측 지연).
 * 출력: 그 턴에 화면으로 나갈 메시지들. 순서가 곧 화면 순서다.
 * 호출 시점: 실시간 응답을 받았을 때(TripContext의 APPEND_CHAT_TURN),
 *            지난 대화를 되돌릴 때(RESTORE_SESSION).
 *
 * **이 함수가 한 벌인 것이 요점이다.** 지난 대화를 "그때와 같게" 보이려면
 * 복원 경로가 실시간 경로와 같은 규칙으로 그려야 하는데, 규칙을 두 군데에 두면
 * 한쪽만 고쳐지는 순간 조용히 갈라진다. 백엔드가 AgentResponse를 통째로
 * 보관하는 것(session_messages)도 이 함수를 다시 태우기 위해서다.
 *
 * 조건 디버그 카드와 개발자 Audit은 여기서 만들지 않는다 — 그 둘은 그 턴에
 * 화면으로 나간 것이 아니라 개발자 화면의 부가 정보이고, 복원 대상도 아니다.
 */

import type {
  AgentResponse,
  ChatMessage,
  PhotoSimilarPlacesResponse,
  RecommendationItem,
  TravelOriginToggle,
} from "../types";

/*
 * 화면 기록이 사진 검색 턴임을 알리는 표시. 서버가 붙인다
 * (routes/photo_similar.py의 PHOTO_SEARCH_RECORD_KIND).
 *
 * **표시가 없으면 AgentResponse다.** 이 키가 생기기 전의 기록에는 없으므로,
 * 없는 쪽을 기존 동작으로 두는 것이 하위 호환이다.
 */
const PHOTO_SIMILAR_RECORD_KIND = "photo_similar";

/*
 * 화면 기록 하나가 사진 검색 턴인지 가른다.
 *
 * 사진 검색은 조건 병합을 타지 않아 payload가 AgentResponse가 아니다 — 가르지
 * 않고 buildAgentMessages에 넘기면 `response.llm_output.intent`를 읽다가 터져
 * 그 대화 전체가 복원되지 않는다.
 */
export function isPhotoSimilarRecord(
  payload: unknown,
): payload is PhotoSimilarPlacesResponse & { kind: string } {
  return (
    typeof payload === "object" &&
    payload !== null &&
    (payload as { kind?: unknown }).kind === PHOTO_SIMILAR_RECORD_KIND
  );
}

/*
 * 사진 검색 턴 하나를 되돌린다.
 *
 * **사진은 없다.** 원본은 서버가 임베딩만 하고 버렸고 축소본은 그 브라우저의
 * sessionStorage에만 있어, 다른 기기에서 열면 가져올 데가 없다. 사진 자리에는
 * 왜 안 보이는지를 대신 놓는다(restored) — 빈 자리만 남으면 사용자는 사진이
 * 사라진 것인지 원래 없던 것인지 알 수 없다.
 */
export function buildPhotoSimilarMessage(
  payload: PhotoSimilarPlacesResponse,
): ChatMessage {
  return {
    id: createMessageId("photo"),
    type: "photo_similar_result",
    imageUrl: null,
    restored: true,
    status: "done",
    centerName: payload.center_name,
    places: payload.places,
    candidateCount: payload.candidate_count,
    // 복원에는 잴 대상이 없다. buildAgentMessages가 지연시간에 0을 넘기는 것과 같다.
    elapsedMs: 0,
  };
}

export function createMessageId(prefix: string) {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return `${prefix}-${crypto.randomUUID()}`;
  }
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

/*
 * 추천 카드 앞에 뜨는 고정 캡션 한 줄을 만든다. 결과가 0건이면 만들지 않는다 —
 * "관광명소 순위예요"가 "결과 없음" 문구 위에 뜨면 어색하다.
 *
 * 별도 메시지 타입(`recommendation_caption`)으로 뗀 이유는 위치다 — 카드
 * (`recommendation_result`)와 한 메시지에 있으면 LLM 팁을 그 위에 끼워 넣을
 * 자리가 없다. 팁(LLM) → 캡션 → 카드 순서를 만들려고 셋을 각자 다른 메시지로
 * 둔다(2026-09-09, 사용자 피드백으로 캡션도 팁보다 아래로 확정).
 */
export function buildRecommendationCaptionMessage({
  hasResults,
}: {
  hasResults: boolean;
}): ChatMessage | null {
  if (!hasResults) return null;
  return { id: createMessageId("result-caption"), type: "recommendation_caption" };
}

/*
 * 추천 응답 하나를 화면 메시지 셋으로 편다 — 카드 → 취향 표 → 버튼
 * 순서다(2026-09-09, 사용자 피드백으로 표가 버튼보다 위로 왔다 — "다른 장소
 * 보기"/"아래 입력창에 이어서 적어주세요" 버튼·안내는 표 아래에 온다).
 *
 * **세 경로가 이 함수를 같이 쓴다**(단발 응답·스트리밍 result·레거시
 * APPEND_RECOMMENDATIONS). 예전에 추천 메시지를 각자 조립하다가 한 곳만 고쳐져
 * 경로에 따라 화면이 갈라진 적이 있어, 조립 규칙을 여기 한 벌만 둔다.
 *
 * 버튼을 따로 떼는 이유는 수명이다 — 다음 발화가 나가면 버튼 메시지만 걷어내고
 * 카드와 표는 기록으로 남긴다(TripContext의 isPastTurnControl).
 */
export function buildRecommendationMessages({
  recommendations,
  unverifiedRecommendations,
  travelOriginToggle,
  elapsedMsClient,
  serverElapsedMs,
}: {
  recommendations: RecommendationItem[];
  unverifiedRecommendations: RecommendationItem[];
  travelOriginToggle?: TravelOriginToggle | null;
  elapsedMsClient: number;
  serverElapsedMs: number;
}): ChatMessage[] {
  const messages: ChatMessage[] = [
    {
      id: createMessageId("result"),
      type: "recommendation_result",
      recommendations,
      unverified_recommendations: unverifiedRecommendations,
      travel_origin_toggle: travelOriginToggle,
      elapsed_ms: elapsedMsClient,
      server_elapsed_ms: serverElapsedMs,
    },
  ];

  const taggedItems = [...recommendations, ...unverifiedRecommendations].filter(
    (item) => (item.preference_tags?.length ?? 0) > 0,
  );
  // 태그가 하나도 없으면 표가 스스로 null을 반환하므로 빈 메시지를 만들지 않는다.
  if (taggedItems.length > 0) {
    messages.push({
      id: createMessageId("result-tags"),
      type: "preference_tag_summary",
      items: taggedItems.map((item) => ({
        place_id: item.place_id,
        name: item.name,
        preference_tags: item.preference_tags,
      })),
    });
  }

  messages.push({
    id: createMessageId("result-actions"),
    type: "recommendation_actions",
    travel_origin_toggle: travelOriginToggle,
    has_no_results: recommendations.length === 0 && unverifiedRecommendations.length === 0,
  });

  return messages;
}

interface BuildOptions {
  /** 그 턴의 사용자 발화. 피드백 위젯이 무엇에 대한 평가인지 기록하는 데 쓴다. */
  userInput: string;
  /**
   * 요청부터 응답까지의 클라이언트 실측 시간(ms). 개발자 화면에서만 보인다.
   * 복원에는 잴 대상이 없으므로 0이 넘어온다.
   */
  elapsedMsClient: number;
}

export function buildAgentMessages(
  response: AgentResponse,
  { userInput, elapsedMsClient }: BuildOptions,
): ChatMessage[] {
  const messages: ChatMessage[] = [];
  const intent = response.llm_output.intent;
  const message = response.message;
  const clarificationOptions = response.llm_output.clarification?.options;

  /*
   * **되묻기만 한 턴에는 피드백 버튼을 붙이지 않는다**(2026-09-08).
   *
   * 되묻기는 답이 아니라 질문이다 — "어떤 걸 찾으세요?"에 좋아요/싫어요를
   * 매기면 무엇에 대한 평가인지 알 수 없고, 그 점수가 추천 품질 자료로 섞인다.
   *
   * **이건 mintee의 결정을 덮는 것이다.** mintee가 ef43ea16(15:33)에
   * `!isClarificationTurn` 조건을 넣었다가 5분 뒤 2d29192c(15:38)에서 스스로
   * 지웠고, 그 커밋 타입이 fix였다 — 없는 것을 버그로 본 것이다. 다만 본문이
   * 제목 한 줄뿐이라 **이유는 기록돼 있지 않다.** 사용자 결정으로 되돌린다.
   *
   * 되묻기와 결과 카드가 **함께** 온 턴은 그대로 붙인다. 그 턴에는 평가할
   * 대상(카드)이 있고, 없애면 카드에 대한 피드백까지 사라진다.
   */
  const isClarificationTurn = Boolean(
    message && clarificationOptions && clarificationOptions.length > 0,
  );
  const hasResultCards = Boolean(
    response.recommendations ||
      response.schedule ||
      response.info_place_card ||
      response.secondary_info_place_card ||
      response.comparison,
  );
  const isClarificationOnlyTurn = isClarificationTurn && !hasResultCards;

  /*
   * **답변(LLM 팁) → 캡션 → 카드**(2026-09-09, 사용자 피드백으로 캡션보다도
   * 위로 확정). 답변은 라이브 SSE에서도 맨 위에서 실시간으로 채워진다
   * (TripContext의 START_STREAM_MESSAGE가 캡션 앞자리에 끼워 넣는다) — 복원
   * 화면도 같은 규칙을 따라야 그때 본 화면과 순서가 갈리지 않는다.
   *
   * 일정·장소정보·비교는 카드 앞에 캡션이 없어 답변만 먼저 나가고 카드가
   * 뒤따른다 — 아래 순서를 그대로 둔다.
   */
  if (message && clarificationOptions && clarificationOptions.length > 0) {
    // 인텐트가 모호해 되묻기 버튼이 붙은 턴 — assistant_text 대신 clarification
    // 메시지로 push해서 같은 문구가 두 번 렌더링되지 않게 한다
    // (docs/design/clarification-options.md 6절).
    messages.push({
      id: createMessageId("clarification"),
      type: "clarification",
      text: message,
      options: clarificationOptions,
    });
  } else if (message) {
    messages.push({
      id: createMessageId("assistant"),
      type: "assistant_text",
      text: message,
      intent,
      status: response.llm_output.status,
      footnote: response.message_footnote ?? undefined,
    });
  }

  if (response.recommendations) {
    const hasResults =
      response.recommendations.recommendations.length +
        response.recommendations.unverified_recommendations.length >
      0;
    const caption = buildRecommendationCaptionMessage({ hasResults });
    if (caption) messages.push(caption);
    messages.push(
      ...buildRecommendationMessages({
        recommendations: response.recommendations.recommendations,
        unverifiedRecommendations: response.recommendations.unverified_recommendations,
        travelOriginToggle: response.recommendations.travel_origin_toggle,
        elapsedMsClient,
        serverElapsedMs: response.recommendations.elapsed_ms,
      }),
    );
  }

  if (response.schedule) {
    messages.push({
      id: createMessageId("schedule"),
      type: "schedule_result",
      schedule: response.schedule,
      elapsed_ms: elapsedMsClient,
      /* 저장 버튼이 쓴다(SCHEDULE 카드 2). 복원된 대화에서도 payload에 그대로
         들어 있어 지난 일정을 나중에 저장할 수 있다. */
      run_id: response.state?.run_id ?? undefined,
      session_id: response.state?.session_id ?? undefined,
    });
    /* 재편성 버튼은 별도 메시지다 — 다음 발화가 나가면 이것만 걷어내고 일정
       카드는 기록으로 남긴다(recommendation_actions와 같은 규칙). */
    messages.push({
      id: createMessageId("schedule-actions"),
      type: "schedule_actions",
      has_no_schedule: response.schedule.items.length === 0,
    });
  }

  if (response.info_place_card) {
    messages.push({
      id: createMessageId("info-place"),
      type: "place_info_result",
      card: response.info_place_card,
    });
  }

  if (response.secondary_info_place_card) {
    // 근처 주차장 → 공영주차장처럼 짝인 실시간 질문의 둘째 카드다(TP-115).
    messages.push({
      id: createMessageId("info-place-secondary"),
      type: "place_info_result",
      card: response.secondary_info_place_card,
    });
  }

  if (response.comparison) {
    messages.push({
      id: createMessageId("compare"),
      type: "compare_result",
      comparison: response.comparison,
    });
  }

  if (response.state.run_id && !isClarificationOnlyTurn) {
    messages.push({
      id: createMessageId("feedback"),
      type: "feedback",
      sessionId: response.state.session_id,
      runId: response.state.run_id,
      intent,
      userInput,
      assistantMessage: message,
    });
  }

  if (response.suggested_follow_ups && response.suggested_follow_ups.length > 0) {
    messages.push({
      id: createMessageId("follow-up"),
      type: "follow_up_suggestions",
      suggestions: response.suggested_follow_ups,
    });
  }

  return messages;
}
