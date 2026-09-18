/* eslint-disable react-refresh/only-export-components */
/*
 * 역할: 채팅형 여행 추천 흐름의 메시지, 해석 조건, 추천 진행 상태를 보관한다.
 * 입력: 화면 이벤트와 API 응답을 표현한 reducer action.
 * 출력: TripProvider, useTripState, useTripDispatch hook.
 * 호출 시점: App에서 provider로 감싸고 HomePage/ChatPage가 상태를 읽거나 갱신할 때 호출된다.
 * TODO: 실제 세션 저장소나 서버 캐시가 생기면 메시지 persistence 계층을 분리한다.
 */

import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useReducer,
  type Dispatch,
  type ReactNode,
} from "react";
import type {
  ChatSessionDetail,
  PhotoSimilarPlace,
  AgentProgressEvent,
  AgentResponse,
  AgentStageTiming,
  AgentStreamResultEvent,
  ChatMessage,
  ChatPhase,
  DeveloperAuditTurn,
  Intent,
  InterpretedConditions,
  Language,
  LLMOutputStatus,
  RecommendationItem,
  RecommendationsResponse,
  ScheduleResult,
  SessionContextResponse,
  SavedPlaceItem,
  UserConditions,
} from "../types";
import {
  buildAgentMessages,
  buildPhotoSimilarMessage,
  buildRecommendationCaptionMessage,
  buildRecommendationMessages,
  createMessageId,
  isPhotoSimilarRecord,
} from "./agentMessages";
import { hasTimeGap } from "./timeSeparator";
import {
  findStreamInsertionIndex,
  findStreamingMessageIndex,
  freezeStreamingMessage,
} from "./streamingMessage";
import { attachRecommendationsToTurns } from "./pastRecommendations";
import { clearState, loadState, saveState } from "./storage";

export interface TripState {
  /** 사용자 화면 및 API 번역 경계에 적용할 언어. */
  language: Language;
  user_input: string;
  interpreted_conditions: InterpretedConditions | null;
  recommendations: RecommendationItem[];
  unverified_recommendations: RecommendationItem[];
  shown_place_ids: string[];
  messages: ChatMessage[];
  auditTurns: DeveloperAuditTurn[];
  phase: ChatPhase;
  error: string | null;
  /* Agent(B)가 발급한 대화 세션. 후속 발화에서 그대로 돌려보낸다. */
  session_id: string | null;
  /*
   * 마지막 턴이 오간 시각. 다음 발화 위에 시각 구분선을 넣을지 판단하는 데 쓴다.
   *
   * 지난 대화를 열면 그 대화의 마지막 턴 시각이 들어온다 — 몇 시간 뒤에 이어서
   * 물으면 그 발화 위에 지금 시각이 뜬다. 자리를 비웠다가 돌아온 것을 화면이
   * 그대로 보여주는 것이고, 메신저에서 늘 보던 모양이다.
   */
  last_turn_at: string | null;
  /* 최초 추천 시작 시 허용받은 브라우저 위치. 같은 세션의 후속 요청에도 재사용한다. */
  device_location: string | null;
  /** 브라우저에서 device_location을 마지막으로 받아온 시각(ms). */
  device_location_captured_at: number | null;
  /*
   * 사용자가 위치 재확인 질문에서 "이전 위치로 계속"을 눌러 다시 묻지 않기로 미룬
   * 마감 시각(ms). device_location_captured_at과 분리한 이유는
   * utils/locationRefresh.ts 상단 설명 참고 — capturedAt을 갱신하면 GPS를 다시
   * 받지 않았는데도 나이 표시가 리셋되는 문제가 있었다.
   */
  device_location_snoozed_until: number | null;
  /*
   * 직전 턴이 추천 없이 되묻기로 끝났는지. Agent는 "직전에 무엇을 되물었는지"를
   * 다음 턴 Intent 분류에 넘기지 않아서, 사용자가 "경복궁"처럼 짧게 답하면 INFO로
   * 분류돼 추천이 나오지 않는다. 입력창 placeholder로 더 온전한 문장을 유도한다.
   * TODO: Agent가 되묻기 맥락을 이어받게 되면 이 우회는 제거한다.
   */
  awaiting_clarification: boolean;
  /*
   * 최근에 후속 질문 버튼으로 보여준 문구(오래된 것이 앞, 최대 MAX_RECENT_FOLLOW_UPS개).
   * 다음 발화에 함께 보내면 서버가 같은 문구를 다시 권하지 않는다.
   *
   * **메시지 목록에서 뽑아 쓸 수 없어서 따로 쌓는다.** 후속 질문 버튼은 다음 발화가
   * 나가는 순간 걷어내므로(isPastTurnControl) 화면에는 늘 최신 한 벌만 있고 그것도 곧
   * 사라진다. 여기 쌓이는 것은 지우지 않는 별개의 기록이다.
   *
   * **누른 것과 안 누른 것을 함께 담는다.** 누른 문구는 발화로 남아 서버도 알지만,
   * 보여주기만 하고 안 누른 문구는 화면 말고 아는 곳이 없다 — 그게 이 값이 필요한
   * 이유다. 그래서 화면에 올리는 순간 쌓고, 눌렀는지는 보지 않는다.
   *
   * 대화를 새로 시작하거나 지난 대화를 열면 초기 상태로 돌아가 비워진다.
   */
  recent_follow_ups: string[];
  /*
   * 사용자가 담은 장소(담은 순서). 서버가 보관하는 상태를 화면이 비추기만 하며,
   * 진실의 원천은 항상 서버다 — 담기/빼기 응답과 세션 조회 결과로만 갱신한다.
   * 순서는 서버가 준 그대로 유지한다. 개수 상한 초과 시 이 순서로 잘리므로
   * 화면에서 정렬을 바꾸면 "왜 그 곳이 빠졌는지" 설명이 어긋난다.
   */
  saved_places: SavedPlaceItem[];
  agentProgress: AgentProgressEvent | null;
  streamingIntent: Intent | null;
}

const initialTripState: TripState = {
  language: "ko",
  user_input: "",
  interpreted_conditions: null,
  recommendations: [],
  unverified_recommendations: [],
  shown_place_ids: [],
  messages: [],
  auditTurns: [],
  phase: "idle",
  error: null,
  session_id: null,
  last_turn_at: null,
  device_location: null,
  device_location_captured_at: null,
  device_location_snoozed_until: null,
  awaiting_clarification: false,
  saved_places: [],
  recent_follow_ups: [],
  agentProgress: null,
  streamingIntent: null,
};

type TripAction =
  | { type: "SET_LANGUAGE"; payload: Language }
  | { type: "RESTORE_SESSION"; payload: ChatSessionDetail }
  | { type: "START_INTERPRETING" }
  | { type: "ADD_INTERPRETATION"; payload: InterpretedPayload }
  | { type: "UPDATE_CONDITIONS"; payload: Partial<InterpretedConditions> }
  | { type: "MARK_DEBUG_CONFIRMED" }
  | { type: "START_RECOMMENDATIONS"; payload?: { conditions?: InterpretedConditions } }
  | {
      type: "APPEND_RECOMMENDATIONS";
      payload: RecommendationsResponse & { elapsed_ms_client: number };
    }
  | {
      type: "START_CHAT_TURN";
      payload: {
        userInput: string;
        deviceLocation?: string | null;
        deviceLocationCapturedAt?: number | null;
      };
    }
  | { type: "APPEND_CHAT_TURN"; payload: ChatTurnPayload }
  | { type: "SET_AGENT_PROGRESS"; payload: AgentProgressEvent }
  | { type: "APPEND_STREAM_RESULT"; payload: AgentStreamResultEvent & { elapsedMsClient: number } }
  | { type: "START_STREAM_MESSAGE"; payload: { intent: Intent } }
  | { type: "APPEND_STREAM_MESSAGE_DELTA"; payload: { text: string } }
  | {
      type: "COMPLETE_STREAM_CHAT_TURN";
      payload: {
        response: AgentResponse;
        elapsedMsClient: number;
        serverElapsedMs: number;
        stageTimings: AgentStageTiming[];
        conditions: InterpretedConditions | null;
      };
    }
  /* done 뒤에 도착하는 후속 질문 버튼. 턴은 이미 끝나 있다. */
  | { type: "APPEND_FOLLOW_UP_SUGGESTIONS"; payload: { suggestions: string[] } }
  | {
      type: "APPEND_FAILED_CHAT_TURN";
      payload: {
        userInput: string;
        message: string;
        code: string;
        retryable: boolean;
        details: unknown;
        elapsedMsClient: number;
      };
    }
  /* 로컬 테스트용 "/status" 결과. 대화 상태는 바꾸지 않고 메시지만 덧붙인다. */
  | {
      type: "APPEND_SESSION_STATUS";
      payload: { userInput: string; status: SessionContextResponse | null; error: string | null };
    }
  /* 사진을 고른 즉시. 결과를 기다리는 동안 사진과 "찾는 중"을 먼저 보여준다. */
  | { type: "START_PHOTO_SIMILAR"; payload: { messageId: string; imageUrl: string | null } }
  /* 축소본은 만드는 데 시간이 걸려(createImageBitmap) START_PHOTO_SIMILAR보다
     늦게 완성될 수 있다 — 완성되면 이 액션으로 그 메시지에만 채워 넣는다. */
  | { type: "SET_PHOTO_SIMILAR_IMAGE"; payload: { messageId: string; imageUrl: string } }
  | {
      type: "RESOLVE_PHOTO_SIMILAR";
      payload: {
        messageId: string;
        /* 서버가 발급했을 수 있다 — 홈에서 발화 없이 사진부터 올린 경우가 그렇다. */
        sessionId: string;
        centerName: string;
        places: PhotoSimilarPlace[];
        candidateCount: number;
        elapsedMs: number;
      };
    }
  /* 검색이 실패했을 때. 사진 말풍선을 남겨두면 영원히 "찾는 중"이 된다. */
  | { type: "FAIL_PHOTO_SIMILAR"; payload: { messageId: string } }
  /* 보낼 위치가 없어 요청을 아예 하지 않았을 때. 실패와 나누는 이유는 사용자가
     할 일이 달라서다 — 이쪽은 위치를 먼저 정해야 한다. */
  | { type: "PHOTO_SIMILAR_NEEDS_LOCATION"; payload: { messageId: string } }
  /*
   * 실패한 턴을 대화에 한 줄로 남긴다(TP-245). SET_ERROR와 달리 state.error를
   * 건드리지 않는다 — 채팅 화면에서 오류가 배너와 메시지 두 곳으로 갈리면
   * 어디에 뜨는지 예측할 수 없게 된다.
   */
  | { type: "FAIL_TURN"; payload: { message: string; retryInput?: string } }
  | { type: "SET_ERROR"; payload: string }
  | { type: "CLEAR_ERROR" }
  | { type: "SET_SAVED_PLACES"; payload: { items: SavedPlaceItem[] } }
  | { type: "SNOOZE_LOCATION_REFRESH"; payload: { until: number } }
  | { type: "SET_DEVICE_LOCATION"; payload: { deviceLocation: string; capturedAt: number } }
  | { type: "CANCEL_CHAT_TURN" }
  | { type: "RESET" };

/* /api/chat 한 번의 응답을 화면 메시지로 옮기기 위한 입력. */
interface ChatTurnPayload {
  userInput: string;
  intent: Intent;
  conditions: InterpretedConditions | null;
  mergedConditions: UserConditions | null;
  message: string;
  recommendations: RecommendationsResponse | null;
  schedule?: ScheduleResult | null;
  sessionId: string | null;
  status: LLMOutputStatus;
  agentResponse: AgentResponse;
  showDebug: boolean;
  elapsedMsClient: number;
  /** SSE progress 이벤트가 있는 응답은 카드 스트림 여부와 무관하게 단계 시간을 보존한다. */
  serverElapsedMs?: number;
  stageTimings?: AgentStageTiming[];
}

interface InterpretedPayload {
  userInput: string;
  conditions: InterpretedConditions;
  showDebug: boolean;
}

/*
 * 새 턴이 시작될 때 걷어낼 메시지인가. 지난 답변을 기준으로 만들어진 버튼들이고,
 * 남겨두면 그때 기준의 요청이 지금 맥락으로 나간다 — 옛 되묻기나 옛 추천의
 * "다른 장소 보기"를 누르면 결과가 어긋난다.
 *
 * 카드·취향 표 같은 기록은 여기 들어오지 않는다. 지우면 대화를 위로 올렸을 때
 * 그때 무엇을 받았는지가 사라진다. 버튼만 갈라서 메시지로 둔 것도 이 때문이다.
 */
export function isPastTurnControl(message: ChatMessage): boolean {
  return (
    message.type === "follow_up_suggestions" ||
    message.type === "recommendation_actions" ||
    message.type === "schedule_actions"
  );
}

/*
 * 다음 발화에 함께 보낼 "이미 보여준 후속 질문" 목록의 상한.
 * backend/app/schemas.py의 MAX_RECENT_FOLLOW_UPS와 같은 값이다 — 더 보내도 서버가
 * 뒤에서부터 자른다. 왜 셋인지는 그쪽 주석에 있다 — 넓히면 권할 것이 남지 않는다.
 */
export const MAX_RECENT_FOLLOW_UPS = 3;

/*
 * 두 문구가 같은 말인지 견주기 위한 표기 정리. 공백과 문장 끝 부호만 지운다.
 * 서버(follow_up_suggester._comparable)와 같은 규칙이라 양쪽이 같은 것을 같다고 본다.
 */
function comparableFollowUp(label: string): string {
  return label.replace(/\s+/g, "").replace(/[?？!！.。]+$/, "").toLowerCase();
}

/*
 * 이번 턴에 보여준 문구를 기록에 얹는다. 겹치는 것은 새 쪽만 남기고, 상한을 넘으면
 * 오래된 것부터 버린다.
 *
 * 상한이 세 개라 사실상 직전 턴의 버튼만 남는다. 직전 턴이 두 개만 냈으면 그 앞 턴의
 * 문구 하나가 자리에 남으므로, 매번 최신 한 벌로 갈아치우는 것과는 다르다.
 */
export function mergeRecentFollowUps(existing: string[], incoming: string[]): string[] {
  const incomingKeys = new Set(incoming.map(comparableFollowUp));
  const kept = existing.filter((label) => !incomingKeys.has(comparableFollowUp(label)));
  return [...kept, ...incoming].slice(-MAX_RECENT_FOLLOW_UPS);
}

/*
 * 지난 턴의 버튼을 걷어낸 메시지 목록. 되묻기는 통째로 지우지 않고 선택지만
 * 비운다 — 그 메시지가 그 턴의 답변 자체라(agentMessages의 clarification 분기)
 * 지우면 "질문 → (빈칸) → 사용자가 고른 답"이 되어 왜 그렇게 답했는지가 사라진다.
 * 문구는 기록으로 남기고 누를 수 있는 것만 없앤다.
 */
function withoutPastTurnControls(messages: ChatMessage[]): ChatMessage[] {
  return messages
    .filter((message) => !isPastTurnControl(message))
    .map((message) =>
      message.type === "clarification" && message.options.length > 0
        ? { ...message, options: [] }
        : message,
    );
}

/** 지금 타이프라이터가 채우고 있는 assistant_text 메시지의 인덱스. 없으면 -1. */
function buildInterpretationSummary(conditions: InterpretedConditions) {
  const categories =
    conditions.preferred_categories.length > 0
      ? `${conditions.preferred_categories.join(", ")} 중심으로`
      : "조건에 맞춰";
  const weather =
    conditions.weather_condition === "bad"
      ? "비나 날씨 변수를 고려해서"
      : conditions.weather_condition === "good"
        ? "걷기 좋은 날씨를 고려해서"
        : "";
  // 위치를 말하지 않았으면 장소를 지어내지 않는다. 백엔드가 어디서 찾을지 되묻는다.
  const place = conditions.location_query ? `${conditions.location_query} 근처에서 ` : "";
  return `${place}${weather} ${categories} 찾아볼게요.`;
}

function buildInterpretationMessages(payload: InterpretedPayload): ChatMessage[] {
  const userMessage: ChatMessage = {
    id: createMessageId("user"),
    type: "user_text",
    text: payload.userInput,
  };

  if (payload.showDebug) {
    return [
      userMessage,
      {
        id: createMessageId("debug"),
        type: "condition_debug",
        userInput: payload.userInput,
        conditions: payload.conditions,
        mergedConditions: null,
        status: "pending",
      },
    ];
  }

  return [
    userMessage,
    {
      id: createMessageId("summary"),
      type: "interpretation_summary",
      text: buildInterpretationSummary(payload.conditions),
    },
  ];
}

function tripReducer(state: TripState, action: TripAction): TripState {
  switch (action.type) {
    case "SET_LANGUAGE":
      return { ...state, language: action.payload };
    case "START_INTERPRETING":
      return { ...state, phase: "interpreting", error: null };
    case "ADD_INTERPRETATION": {
      const messages = buildInterpretationMessages(action.payload);
      return {
        ...state,
        user_input: action.payload.userInput,
        interpreted_conditions: action.payload.conditions,
        recommendations: [],
        unverified_recommendations: [],
        messages: [...state.messages, ...messages],
        phase: action.payload.showDebug ? "waiting_for_debug_confirmation" : "recommending",
        error: null,
      };
    }
    case "UPDATE_CONDITIONS":
      if (!state.interpreted_conditions) return state;
      return {
        ...state,
        interpreted_conditions: { ...state.interpreted_conditions, ...action.payload },
      };
    case "MARK_DEBUG_CONFIRMED":
      return {
        ...state,
        messages: state.messages.map((message) =>
          message.type === "condition_debug" && message.status === "pending"
            ? { ...message, status: "confirmed" }
            : message,
        ),
      };
    case "START_RECOMMENDATIONS":
      return {
        ...state,
        interpreted_conditions: action.payload?.conditions ?? state.interpreted_conditions,
        phase: "recommending",
        error: null,
      };
    case "APPEND_RECOMMENDATIONS": {
      const shownIds = [
        ...action.payload.recommendations,
        ...action.payload.unverified_recommendations,
      ].map((item) => item.place_id);
      const captionMessage = buildRecommendationCaptionMessage({
        hasResults: shownIds.length > 0,
      });
      return {
        ...state,
        recommendations: action.payload.recommendations,
        unverified_recommendations: action.payload.unverified_recommendations,
        shown_place_ids: Array.from(new Set([...state.shown_place_ids, ...shownIds])),
        messages: [
          ...state.messages,
          ...(captionMessage ? [captionMessage] : []),
          ...buildRecommendationMessages({
            recommendations: action.payload.recommendations,
            unverifiedRecommendations: action.payload.unverified_recommendations,
            elapsedMsClient: action.payload.elapsed_ms_client,
            serverElapsedMs: action.payload.elapsed_ms,
          }),
        ],
        phase: "ready",
        error: null,
      };
    }
    /*
     * 사이드바 히스토리에서 지난 대화를 불러온다.
     *
     * **session_id를 채운다** — 사이드바가 부르는 것은 조회가 아니라
     * POST /sessions/{id}/resume이고, 그 응답이 온 시점의 세션은 살아 있다.
     * 그래서 이어 물으면 같은 대화에 붙는다(목록에 줄이 하나 더 생기지 않고,
     * 저장된 턴이 그대로 맥락으로 넘어간다).
     *
     * 그래도 resumable을 확인하고 넣는다. 되살리기가 실패해 조회 응답으로
     * 물러난 경우에는 false로 오고, 그때 id를 채우면 화면은 "이어진다"고
     * 말하는데 백엔드는 새 세션을 만드는 상태가 된다.
     *
     * **restore_from_messages면 화면 기록만 쓴다.** 그 안에 그 턴의 AgentResponse가
     * 통째로 들어 있어, 실시간과 같은 buildAgentMessages를 다시 태우면 그때 본
     * 화면이 그대로 나온다. 지연시간만 0으로 넘긴다 — 복원에는 잴 대상이 없다.
     *
     * 아니면 예전 방식으로 되돌린다(말풍선 + 저장된 조각으로 만든 장소 카드).
     * 기록이 없는 옛 대화와, 저장이 한 번 실패해 턴이 빠진 대화가 여기로 온다.
     * 손실이 있지만 통째로 안 보이거나 턴이 조용히 빠진 채로 보이는 것보다 낫다.
     * 온전한지 판정하는 것은 백엔드다 — 같은 계산을 두 군데 두지 않는다.
     */
    case "RESTORE_SESSION": {
      const restored: ChatMessage[] = [];

      /*
       * 언제 오간 대화인지 맨 위에 한 줄로 밝힌다. 예전에는 "지난 대화예요"라는
       * 배너였는데, 메신저의 시각 구분선이 읽지 않아도 뜻이 통하고 화면도 덜
       * 차지한다. 첫 턴의 시각 하나만 둔다 — 턴마다 붙이면 몇 분 간격의 줄이
       * 계속 끼어들어 대화가 끊겨 보인다.
       */
      const startedAt = action.payload.restore_from_messages
        ? action.payload.messages[0]?.recorded_at
        : action.payload.turns[0]?.at;
      if (startedAt) {
        restored.push({
          id: createMessageId("time"),
          type: "time_separator",
          at: startedAt,
          /* 옛 대화는 남은 말풍선으로만 되돌아온다. 앞부분이 없다는 사실을
             배너 대신 이 줄에 붙여 밝힌다. */
          partial: !action.payload.restore_from_messages,
        });
      }

      if (action.payload.restore_from_messages) {
        const lastIndex = action.payload.messages.length - 1;
        action.payload.messages.forEach((record, index) => {
          /*
           * 사진 검색 턴은 AgentResponse가 아니다 — 조건 병합을 타지 않아 그
           * 턴의 응답이 그대로 들어 있다. 여기서 가르지 않으면 아래
           * buildAgentMessages가 llm_output을 읽다가 터져 대화 전체가 복원되지
           * 않는다.
           *
           * 발화 말풍선을 따로 만들지 않는 이유는 컴포넌트가 사진 자리 아래에
           * 그 문구를 직접 그리기 때문이다 — 여기서도 만들면 두 번 나온다.
           */
          if (isPhotoSimilarRecord(record.payload)) {
            restored.push(buildPhotoSimilarMessage(record.payload));
            return;
          }
          if (record.user_input) {
            restored.push({
              id: createMessageId("user"),
              type: "user_text",
              text: record.user_input,
            });
          }
          const turn = buildAgentMessages(record.payload, {
            userInput: record.user_input ?? "",
            elapsedMsClient: 0,
          });
          /*
           * 후속 질문 버튼은 마지막 답변에만 남긴다. 실시간에서도 새 발화가
           * 나가는 순간 옛 버튼을 걷어내므로(START_CHAT_TURN), 대화가 끝난
           * 모습은 마지막 턴에만 버튼이 붙어 있는 상태다. 전부 되살리면 지난
           * 답변 기준의 문구를 눌러 지금 맥락과 어긋난 요청이 나간다.
           */
          restored.push(
            ...(index === lastIndex
              ? turn
              : withoutPastTurnControls(turn)),
          );
        });
      } else {
        const attached = attachRecommendationsToTurns(
          action.payload.turns,
          action.payload.recommendations,
        );

        action.payload.turns.forEach((turn, index) => {
          restored.push({
            id: createMessageId("user"),
            type: "user_text",
            text: turn.user_input,
          });
          if (turn.assistant_message) {
            restored.push({
              id: createMessageId("assistant"),
              type: "assistant_text",
              text: turn.assistant_message,
            });
          }
          for (const group of attached[index]) {
            restored.push({
              id: createMessageId("past-places"),
              type: "past_recommendation_result",
              places: group,
            });
          }
        });
      }

      return {
        ...initialTripState,
        language: state.language,
        device_location: state.device_location,
        device_location_captured_at: state.device_location_captured_at,
        messages: restored,
        session_id: action.payload.resumable ? action.payload.session_id : null,
        last_turn_at: action.payload.restore_from_messages
          ? (action.payload.messages.at(-1)?.recorded_at ?? null)
          : (action.payload.turns.at(-1)?.at ?? null),
        phase: "idle",
      };
    }
    case "START_CHAT_TURN": {
      const nowIso = new Date().toISOString();
      return {
        ...state,
        user_input: action.payload.userInput,
        device_location: action.payload.deviceLocation ?? state.device_location,
        device_location_captured_at:
          action.payload.deviceLocationCapturedAt ?? state.device_location_captured_at,
        // 진짜 GPS를 새로 받은 턴이면(capturedAt이 실려 왔으면) 미뤄둔 재확인
        // 마감도 함께 해제한다 — 방금 받은 위치가 이미 최신이라 미룰 이유가 없다.
        device_location_snoozed_until:
          action.payload.deviceLocationCapturedAt != null
            ? null
            : state.device_location_snoozed_until,
        // 옛 턴의 후속 질문 버튼은 새 발화가 나가는 순간 걷어낸다. 남겨두면 대화를
        // 위로 올렸을 때 어느 답변에 대한 제안인지 알 수 없고, 지난 답변 기준의
        // 문구를 눌러 지금 맥락과 어긋난 요청이 나간다.
        last_turn_at: nowIso,
        messages: [
          ...freezeStreamingMessage(
            withoutPastTurnControls(state.messages),
          ),
          /* 자리를 비웠다가 돌아와 이어 묻는 발화라면 그 위에 지금 시각을 둔다.
             바로 이어지는 발화에는 넣지 않는다 — 몇 분 간격의 줄이 계속 끼어들면
             대화가 끊겨 보인다. */
          ...(hasTimeGap(state.last_turn_at, nowIso)
            ? [{ id: createMessageId("time"), type: "time_separator" as const, at: nowIso }]
            : []),
          { id: createMessageId("user"), type: "user_text", text: action.payload.userInput },
        ],
        phase: "recommending",
        error: null,
        agentProgress: null,
        streamingIntent: null,
      };
    }
    case "SET_AGENT_PROGRESS":
      return { ...state, agentProgress: action.payload };
    case "APPEND_STREAM_RESULT": {
      const { recommendations, state: streamState, llm_output, elapsedMsClient } = action.payload;
      const shownIds = [
        ...recommendations.recommendations,
        ...recommendations.unverified_recommendations,
      ].map((item) => item.place_id);
      const captionMessage = buildRecommendationCaptionMessage({
        hasResults: shownIds.length > 0,
      });
      return {
        ...state,
        recommendations: recommendations.recommendations,
        unverified_recommendations: recommendations.unverified_recommendations,
        shown_place_ids: Array.from(new Set([...state.shown_place_ids, ...shownIds])),
        session_id: streamState.session_id ?? state.session_id,
        streamingIntent: llm_output.intent,
        // (LLM 팁이 곧 여기 맨 앞에 끼어든다, START_STREAM_MESSAGE) → 캡션 → 카드.
        // 팁은 message_start/delta가 recommendation_caption 바로 앞에 끼워
        // 넣는다(findStreamInsertionIndex) — 캡션·카드보다도 위로 온다.
        messages: [
          ...state.messages,
          ...(captionMessage ? [captionMessage] : []),
          ...buildRecommendationMessages({
            recommendations: recommendations.recommendations,
            unverifiedRecommendations: recommendations.unverified_recommendations,
            travelOriginToggle: recommendations.travel_origin_toggle,
            elapsedMsClient,
            serverElapsedMs: recommendations.elapsed_ms,
          }),
        ],
      };
    }
    case "START_STREAM_MESSAGE": {
      // 캡션(없으면 카드) 바로 앞에 끼워 넣는다 — LLM 팁이 이제 맨 위에서
      // 실시간으로 채워진다(findStreamInsertionIndex). RECOMMEND가 아닌 턴은
      // 캡션도 카드도 없어 기존처럼 배열 끝에 붙는다.
      const insertAt = findStreamInsertionIndex(state.messages);
      const streamMessage: ChatMessage = {
        id: createMessageId("assistant-stream"),
        type: "assistant_text",
        text: "…",
        intent: action.payload.intent,
        streaming: true,
      };
      return {
        ...state,
        streamingIntent: action.payload.intent,
        messages: [
          ...state.messages.slice(0, insertAt),
          streamMessage,
          ...state.messages.slice(insertAt),
        ],
      };
    }
    case "APPEND_STREAM_MESSAGE_DELTA": {
      const streamIndex = findStreamingMessageIndex(state.messages);
      const streamingMessage = state.messages[streamIndex];
      if (streamingMessage?.type === "assistant_text" && streamingMessage.streaming) {
        return {
          ...state,
          messages: state.messages.map((message, index) =>
            index === streamIndex
              ? {
                  ...streamingMessage,
                  // "…"는 빈 말풍선의 로딩 표기일 뿐 실제 답변 본문에 남기지 않는다.
                  text:
                    streamingMessage.text === "…"
                      ? action.payload.text
                      : `${streamingMessage.text}${action.payload.text}`,
                }
              : message,
          ),
        };
      }
      // message_start 없이 delta부터 온 엣지 케이스도 같은 자리에 끼워 넣는다 —
      // 안 그러면 이 말풍선만 카드 아래로 갈라진다.
      const insertAt = findStreamInsertionIndex(state.messages);
      const fallbackMessage: ChatMessage = {
        id: createMessageId("assistant-stream"),
        type: "assistant_text",
        text: action.payload.text,
        intent: state.streamingIntent ?? undefined,
        status: "complete",
        streaming: true,
      };
      return {
        ...state,
        messages: [
          ...state.messages.slice(0, insertAt),
          fallbackMessage,
          ...state.messages.slice(insertAt),
        ],
      };
    }
    case "COMPLETE_STREAM_CHAT_TURN": {
      const { response, elapsedMsClient, serverElapsedMs, stageTimings, conditions } =
        action.payload;
      const recommendations = response.recommendations;
      const auditTurn: DeveloperAuditTurn = {
        id: createMessageId("audit"),
        userInput: state.user_input,
        intent: response.llm_output.intent,
        status: response.llm_output.status,
        message: response.message,
        sessionId: response.state.session_id,
        runId: response.state.run_id ?? null,
        deviceLocation: state.device_location,
        elapsedMsClient,
        serverElapsedMs,
        stageTimings,
        extractedConditions: conditions,
        beforeConditions: state.auditTurns.at(-1)?.afterConditions ?? null,
        afterConditions: response.state.user_conditions ?? null,
        recommendations,
        response,
        failure: null,
      };
      const streamIndex = findStreamingMessageIndex(state.messages);
      const streamingMessage = state.messages[streamIndex];
      const isRecommendTurn =
        response.llm_output.intent === "RECOMMEND" || response.llm_output.intent === "MODIFY";
      // 카드 캡션은 이제 RecommendationResultMessage가 항상 그린다. RECOMMEND/
      // MODIFY에서 팁이 한 글자도 안 왔으면(freezeStreamingMessage와 같은 규칙)
      // response.message(고정 문구 폴백)로 채우지 않고 빈 말풍선을 지운다 —
      // 채우면 캡션과 같은 말이 또 한 번 뜬다. 다른 Intent는 기존 폴백을 유지한다.
      const streamedMessages =
        streamingMessage?.type === "assistant_text" && streamingMessage.streaming
          ? streamingMessage.text === "…" && isRecommendTurn
            ? state.messages.filter((_, index) => index !== streamIndex)
            : state.messages.map((message, index) =>
                index === streamIndex
                  ? {
                      ...streamingMessage,
                      text:
                        streamingMessage.text === "…"
                          ? response.message ||
                            (state.language === "en"
                              ? "Here are some places that match your preferences."
                              : "이런 곳들을 찾아봤어요:")
                          : streamingMessage.text,
                      intent: response.llm_output.intent,
                      status: response.llm_output.status,
                      streaming: false,
                    }
                  : message,
              )
          : state.messages;
      const trailingMessages: ChatMessage[] = [];
      if (response.info_place_card !== null && response.info_place_card !== undefined) {
        trailingMessages.push({
          id: createMessageId("place-info"),
          type: "place_info_result",
          card: response.info_place_card,
        });
      }
      if (
        response.secondary_info_place_card !== null &&
        response.secondary_info_place_card !== undefined
      ) {
        // 근처 주차장 → 공영주차장처럼 짝인 실시간 질문의 둘째 카드다(TP-115). 같은
        // 말풍선에 합치지 않고 별도 메시지로 순차 표시해, 답변 아래로 하나씩
        // 쌓이는 기존 카드 흐름과 동일하게 보이게 한다.
        trailingMessages.push({
          id: createMessageId("place-info-secondary"),
          type: "place_info_result",
          card: response.secondary_info_place_card,
        });
      }
      if (response.comparison !== null && response.comparison !== undefined) {
        trailingMessages.push({
          id: createMessageId("compare"),
          type: "compare_result",
          comparison: response.comparison,
        });
      }
      if (response.state.run_id) {
        trailingMessages.push({
          id: createMessageId("feedback"),
          type: "feedback",
          sessionId: response.state.session_id,
          runId: response.state.run_id,
          intent: response.llm_output.intent,
          userInput: state.user_input,
          assistantMessage: response.message,
        });
      }
      if (response.suggested_follow_ups && response.suggested_follow_ups.length > 0) {
        trailingMessages.push({
          id: createMessageId("follow-up"),
          type: "follow_up_suggestions",
          suggestions: response.suggested_follow_ups,
        });
      }
      const messages = [...streamedMessages, ...trailingMessages];
      return {
        ...state,
        // 누른 것과 안 누른 것을 가리지 않고, 화면에 올린 순간 기록에 얹는다.
        recent_follow_ups: mergeRecentFollowUps(
          state.recent_follow_ups,
          response.suggested_follow_ups ?? [],
        ),
        interpreted_conditions: conditions ?? state.interpreted_conditions,
        recommendations: recommendations?.recommendations ?? state.recommendations,
        unverified_recommendations:
          recommendations?.unverified_recommendations ?? state.unverified_recommendations,
        session_id: response.state.session_id ?? state.session_id,
        messages,
        auditTurns: [...state.auditTurns, auditTurn],
        awaiting_clarification: false,
        agentProgress: null,
        streamingIntent: null,
        phase: "ready",
        error: null,
      };
    }
    case "APPEND_CHAT_TURN": {
      const { conditions, intent, message, recommendations, schedule, showDebug } = action.payload;
      const messages: ChatMessage[] = [];
      // 옵션 A: 조건 카드는 유지하되 확인 버튼은 없다 — Agent가 해석과 추천을 한 번에
      // 끝내므로 중간에 사용자가 진행을 승인할 지점이 없다.
      if (showDebug && conditions) {
        messages.push({
          id: createMessageId("debug"),
          type: "condition_debug",
          userInput: action.payload.userInput,
          conditions,
          mergedConditions: action.payload.mergedConditions,
          intent,
          status: "confirmed",
        });
      }
      messages.push(
        ...buildAgentMessages(action.payload.agentResponse, {
          userInput: action.payload.userInput,
          elapsedMsClient: action.payload.elapsedMsClient,
        }),
      );

      const shownIds = recommendations
        ? [...recommendations.recommendations, ...recommendations.unverified_recommendations].map(
            (item) => item.place_id,
          )
        : [];
      const auditTurn: DeveloperAuditTurn = {
        id: createMessageId("audit"),
        userInput: action.payload.userInput,
        intent,
        status: action.payload.status,
        message,
        sessionId: action.payload.sessionId,
        runId: action.payload.agentResponse.state.run_id ?? null,
        deviceLocation: state.device_location,
        elapsedMsClient: action.payload.elapsedMsClient,
        serverElapsedMs:
          action.payload.serverElapsedMs ??
          recommendations?.elapsed_ms ??
          schedule?.elapsed_ms ??
          null,
        stageTimings: action.payload.stageTimings ?? [],
        extractedConditions: conditions,
        beforeConditions: state.auditTurns.at(-1)?.afterConditions ?? null,
        afterConditions: action.payload.agentResponse.state.user_conditions ?? null,
        recommendations,
        response: action.payload.agentResponse,
        failure: null,
      };

      return {
        ...state,
        interpreted_conditions: conditions ?? state.interpreted_conditions,
        recommendations: recommendations?.recommendations ?? [],
        unverified_recommendations: recommendations?.unverified_recommendations ?? [],
        // 제외 목록의 단일 기준은 B다. 화면 표시용으로만 누적한다.
        shown_place_ids: Array.from(new Set([...state.shown_place_ids, ...shownIds])),
        session_id: action.payload.sessionId ?? state.session_id,
        // 추천/일정을 기대한 발화인데 결과가 없으면 Agent가 조건을 되물은 것으로 본다.
        awaiting_clarification:
          recommendations === null &&
          !schedule &&
          (intent === "RECOMMEND" || intent === "MODIFY" || intent === "SCHEDULE"),
        messages: [...state.messages, ...messages],
        auditTurns: [...state.auditTurns, auditTurn],
        phase: "ready",
        error: null,
      };
    }
    case "APPEND_FOLLOW_UP_SUGGESTIONS": {
      if (action.payload.suggestions.length === 0) return state;
      return {
        ...state,
        recent_follow_ups: mergeRecentFollowUps(
          state.recent_follow_ups,
          action.payload.suggestions,
        ),
        // 이 턴에 이미 붙은 버튼이 있으면 갈아끼운다. 단발 /api/chat 폴백은 응답
        // 안에 문구를 실어 보내므로, 두 경로가 겹쳐 두 벌이 쌓이는 것을 막는다.
        messages: [
          ...state.messages.filter((message) => message.type !== "follow_up_suggestions"),
          {
            id: createMessageId("follow-up"),
            type: "follow_up_suggestions",
            suggestions: action.payload.suggestions,
          },
        ],
      };
    }
    case "APPEND_FAILED_CHAT_TURN": {
      const auditTurn: DeveloperAuditTurn = {
        id: createMessageId("audit-error"),
        userInput: action.payload.userInput,
        intent: "ERROR",
        status: "error",
        message: action.payload.message,
        sessionId: state.session_id,
        runId: null,
        deviceLocation: state.device_location,
        elapsedMsClient: action.payload.elapsedMsClient,
        serverElapsedMs: null,
        stageTimings: [],
        extractedConditions: null,
        beforeConditions: state.auditTurns.at(-1)?.afterConditions ?? null,
        afterConditions: state.auditTurns.at(-1)?.afterConditions ?? null,
        recommendations: null,
        response: null,
        failure: {
          code: action.payload.code,
          message: action.payload.message,
          retryable: action.payload.retryable,
          details: action.payload.details,
        },
      };
      return {
        ...state,
        auditTurns: [...state.auditTurns, auditTurn],
        phase: "error",
        error: action.payload.message,
      };
    }
    case "START_PHOTO_SIMILAR":
      return {
        ...state,
        messages: [
          // 사진 검색도 새 턴이다 — START_CHAT_TURN과 같은 이유로 옛 버튼을 걷어낸다.
          ...withoutPastTurnControls(state.messages),
          {
            id: action.payload.messageId,
            type: "photo_similar_result",
            imageUrl: action.payload.imageUrl,
            status: "loading",
            centerName: "",
            places: [],
            candidateCount: 0,
            elapsedMs: 0,
          },
        ],
      };
    case "SET_PHOTO_SIMILAR_IMAGE":
      return {
        ...state,
        messages: state.messages.map((message) =>
          message.id === action.payload.messageId && message.type === "photo_similar_result"
            ? { ...message, imageUrl: action.payload.imageUrl }
            : message,
        ),
      };
    case "RESOLVE_PHOTO_SIMILAR":
      return {
        ...state,
        /*
         * 서버가 발급한 세션을 여기서 받는다. 홈에서 발화 없이 사진부터 올리면
         * 보낼 때는 세션이 없고 이 응답이 그 대화의 시작이다 — 저장하지 않으면
         * 이어지는 발화가 또 새 대화를 만들어 사진 턴이 혼자 남는다.
         */
        session_id: action.payload.sessionId,
        /*
         * 사진 검색도 끝난 턴이다. phase를 그대로 두면 사진으로 시작한 대화가
         * 사이드바 목록에 안 나타난다 — 목록을 다시 받는 조건이 "phase가 ready이고
         * session_id가 있을 때"라(SideDrawerContent), 초기값 idle에 머물러 있으면
         * 다음 발화가 ready로 바꿀 때까지 갱신이 안 걸린다.
         *
         * 입력창에는 영향이 없다. isLoading은 interpreting·recommending만 본다.
         */
        phase: "ready" as const,
        messages: state.messages.map((message) =>
          message.id === action.payload.messageId && message.type === "photo_similar_result"
            ? {
                ...message,
                status: "done",
                centerName: action.payload.centerName,
                places: action.payload.places,
                candidateCount: action.payload.candidateCount,
                elapsedMs: action.payload.elapsedMs,
              }
            : message,
        ),
      };
    case "PHOTO_SIMILAR_NEEDS_LOCATION":
      /*
       * 오류 배너(FAIL_TURN)를 띄우지 않는다. 이것은 실패가 아니라 아직 답하지
       * 않은 물음이고, 무엇을 하라는 안내는 사진 바로 아래에 붙어야 읽힌다.
       */
      return {
        ...state,
        messages: state.messages.map((message) =>
          message.id === action.payload.messageId && message.type === "photo_similar_result"
            ? { ...message, status: "location_required" as const }
            : message,
        ),
      };
    case "FAIL_PHOTO_SIMILAR":
      /*
       * 올린 사진은 남긴다(TP-245). 예전에는 지웠는데, 그 근거는 "말풍선을 남기면
       * 무엇이 잘못됐는지 모른 채 사진만 덩그러니 남는다"였다. 이제 사유가 바로
       * 뒤에 turn_error 한 줄로 붙으므로 그 근거가 사라졌다. 채팅 요청이 실패해도
       * 사용자 발화는 남기면서 사진만 지우는 것은 일관되지 않기도 했다.
       */
      return {
        ...state,
        messages: state.messages.map((message) =>
          message.id === action.payload.messageId && message.type === "photo_similar_result"
            ? { ...message, status: "failed" }
            : message,
        ),
      };
    case "APPEND_SESSION_STATUS":
      return {
        ...state,
        messages: [
          ...state.messages,
          { id: createMessageId("user"), type: "user_text", text: action.payload.userInput },
          {
            id: createMessageId("status"),
            type: "session_status",
            status: action.payload.status,
            error: action.payload.error,
          },
        ],
      };
    case "FAIL_TURN":
      return {
        ...state,
        /* 입력창을 푸는 것이 목적이다. phase "error"를 읽는 코드는 없고, 실패
           사실은 이제 메시지가 들고 있다. */
        phase: "ready",
        messages: [
          ...state.messages,
          {
            id: createMessageId("turn-error"),
            type: "turn_error",
            text: action.payload.message,
            retryInput: action.payload.retryInput,
          },
        ],
      };
    case "SET_ERROR":
      return { ...state, phase: "error", error: action.payload };
    case "CLEAR_ERROR":
      return { ...state, error: null, phase: state.messages.length > 0 ? "ready" : "idle" };
    case "SET_SAVED_PLACES":
      return { ...state, saved_places: action.payload.items };
    case "SNOOZE_LOCATION_REFRESH":
      return { ...state, device_location_snoozed_until: action.payload.until };
    case "SET_DEVICE_LOCATION":
      // 위치 설정 화면에서 "위치 다시 가져오기"를 눌렀을 때. 채팅 턴을 거치지
      // 않고도 다음 요청부터 새 좌표를 쓰도록 미리 갱신해 둔다.
      return {
        ...state,
        device_location: action.payload.deviceLocation,
        device_location_captured_at: action.payload.capturedAt,
        device_location_snoozed_until: null,
      };
    case "CANCEL_CHAT_TURN": {
      // 응답 대기 중 "중단"을 눌렀을 때(§7.2). 아직 생각 중 단계라 타이프라이터
      // 메시지가 없으면(로딩 버블만 있었으면) 아무 것도 안 남기고, 이미 일부
      // 텍스트가 온 상태라면 거기까지만 확정해 얼린다 — 뒤이어 올 카드·후속
      // 질문 이벤트는 연결이 끊겨 더 오지 않으므로 따로 걷어낼 것이 없다.
      const streamIndex = findStreamingMessageIndex(state.messages);
      const streamingMessage = state.messages[streamIndex];
      let messages = state.messages;
      if (streamingMessage?.type === "assistant_text" && streamingMessage.streaming) {
        messages =
          streamingMessage.text === "…"
            ? state.messages.filter((_, index) => index !== streamIndex)
            : state.messages.map((message, index) =>
                index === streamIndex ? { ...streamingMessage, streaming: false } : message,
              );
      }
      return {
        ...state,
        messages,
        phase: messages.length > 0 ? "ready" : "idle",
        agentProgress: null,
        streamingIntent: null,
      };
    }
    case "RESET":
      clearState();
      // 새 대화여도 사용자가 고른 화면 언어는 유지한다.
      return { ...initialTripState, language: state.language };
    default:
      return state;
  }
}

const TripStateContext = createContext<TripState | null>(null);
const TripDispatchContext = createContext<Dispatch<TripAction> | null>(null);

export function TripProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(
    tripReducer,
    initialTripState,
    // 기본값 위에 저장본을 덮는다. 그냥 `loadState() ?? initial`로 두면 새 필드를
    // 추가할 때마다 구버전 저장본에서 그 필드가 undefined로 복원돼, 처음 읽는
    // 쪽에서 터진다(storage.ts가 과거에 겪은 것과 같은 종류의 문제다).
    () => ({ ...initialTripState, ...(loadState() ?? {}) }),
  );
  const value = useMemo(() => state, [state]);

  useEffect(() => {
    saveState(state);
  }, [state]);

  return (
    <TripStateContext.Provider value={value}>
      <TripDispatchContext.Provider value={dispatch}>{children}</TripDispatchContext.Provider>
    </TripStateContext.Provider>
  );
}

export function useTripState() {
  const value = useContext(TripStateContext);
  if (!value) throw new Error("useTripState must be used inside TripProvider");
  return value;
}

export function useTripDispatch() {
  const value = useContext(TripDispatchContext);
  if (!value) throw new Error("useTripDispatch must be used inside TripProvider");
  return value;
}
