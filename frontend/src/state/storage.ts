/*
 * 역할: TripContext 상태를 sessionStorage에 저장하고 복원한다.
 * 입력: TripState 객체 또는 브라우저 sessionStorage에 남아 있는 JSON 문자열.
 * 출력: 저장 성공 여부 없이 side effect, 복원된 TripState 또는 null.
 * 호출 시점: TripProvider 초기화, 상태 변경, RESET 처리 시 호출된다.
 * TODO: 상태 버전이 바뀌면 migration 경로와 만료 정책을 추가한다.
 */

import type { TripState } from "./TripContext";
import type { ChatMessage, ChatPhase } from "../types";

const STORAGE_KEY = "tripbranch_state";
// 5: 개발자용 발화별 감사 기록(auditTurns)을 함께 저장.
// 6: 위치 재확인 스누즈 마감 시각(device_location_snoozed_until) 추가.
const STORAGE_VERSION = 6;

interface StoredState {
  version: number;
  state: TripState;
}

function isTripState(value: unknown): value is TripState {
  if (!value || typeof value !== "object") return false;
  const state = value as Record<string, unknown>;
  return (
    typeof state.user_input === "string" &&
    Array.isArray(state.recommendations) &&
    Array.isArray(state.unverified_recommendations) &&
    Array.isArray(state.shown_place_ids) &&
    Array.isArray(state.messages) &&
    Array.isArray(state.auditTurns) &&
    /* 이 필드가 없던 저장본도 그대로 복원한다. 저장 버전을 올리면 대신 그 대화가
       통째로 버려진다 — 이어물을 버튼 목록 하나 때문에 치를 값이 아니다. 없으면
       TripProvider가 초기값(빈 배열)으로 채운다. */
    (state.recent_follow_ups === undefined || Array.isArray(state.recent_follow_ups)) &&
    state.messages.every(isChatMessage) &&
    isChatPhase(state.phase) &&
    (state.device_location === null || typeof state.device_location === "string") &&
    (state.device_location_captured_at === undefined ||
      state.device_location_captured_at === null ||
      typeof state.device_location_captured_at === "number") &&
    (state.device_location_snoozed_until === undefined ||
      state.device_location_snoozed_until === null ||
      typeof state.device_location_snoozed_until === "number") &&
    (state.error === null || typeof state.error === "string") &&
    (state.language === undefined || state.language === "ko" || state.language === "en")
  );
}

function isChatPhase(value: unknown): value is ChatPhase {
  return (
    value === "idle" ||
    value === "interpreting" ||
    value === "waiting_for_debug_confirmation" ||
    value === "recommending" ||
    value === "ready" ||
    value === "error"
  );
}

function isChatMessage(value: unknown): value is ChatMessage {
  if (!value || typeof value !== "object") return false;
  const message = value as Record<string, unknown>;
  if (typeof message.id !== "string" || typeof message.type !== "string") return false;
  if (message.type === "user_text" || message.type === "assistant_text") {
    return typeof message.text === "string";
  }
  if (message.type === "interpretation_summary") {
    return typeof message.text === "string";
  }
  /* TP-245. 이 케이스가 없으면 요청이 한 번이라도 실패한 대화가 새로고침에서
     통째로 버려진다 — schedule_result·place_info_result·photo_similar_result가
     같은 이유로 겪었던 일이다. retryInput은 없을 수 있다(위치 갱신 실패 등). */
  if (message.type === "turn_error") {
    return (
      typeof message.text === "string" &&
      (message.retryInput === undefined || typeof message.retryInput === "string")
    );
  }
  if (message.type === "condition_debug") {
    return (
      typeof message.userInput === "string" &&
      typeof message.conditions === "object" &&
      (message.status === "pending" || message.status === "confirmed")
    );
  }
  /* 로컬 테스트용 "/status" 결과. 복원돼도 화면 표시 외의 영향은 없다. */
  if (message.type === "session_status") {
    return (
      (message.status === null || typeof message.status === "object") &&
      (message.error === null || typeof message.error === "string")
    );
  }
  /* 위 schedule_result와 같은 이유다 — 이 케이스가 없으면 사진 검색을 한 번이라도
     한 대화가 새로고침에서 통째로 사라진다. */
  if (message.type === "photo_similar_result") {
    /* status는 나중에 추가한 필드라 없는 저장분이 있을 수 있다 — 없으면 done으로
       본다. 여기서 false를 주면 옛 세션이 통째로 버려진다. */
    return (
      Array.isArray(message.places) &&
      typeof message.centerName === "string" &&
      typeof message.candidateCount === "number"
    );
  }
  if (message.type === "recommendation_result") {
    return (
      Array.isArray(message.recommendations) &&
      Array.isArray(message.unverified_recommendations) &&
      typeof message.elapsed_ms === "number" &&
      typeof message.server_elapsed_ms === "number"
    );
  }
  /* SCHEDULE-10 후속: 이 케이스가 없으면 schedule_result 메시지가 하나라도 있는
     세션은 isTripState()가 messages.every(isChatMessage)에서 걸려 세션 전체가
     복원 실패로 버려진다(place_info_result도 마찬가지였다) — 새로고침하면 SCHEDULE/INFO
     질문을 한 번이라도 한 대화가 통째로 사라지던 버그. */
  if (message.type === "schedule_result") {
    const schedule = message.schedule as Record<string, unknown> | null | undefined;
    return (
      typeof message.elapsed_ms === "number" &&
      !!schedule &&
      typeof schedule === "object" &&
      Array.isArray(schedule.items) &&
      typeof schedule.total_duration_min === "number" &&
      typeof schedule.route_summary === "string" &&
      typeof schedule.basis_note === "string" &&
      typeof schedule.elapsed_ms === "number"
    );
  }
  if (message.type === "place_info_result") {
    const card = message.card as Record<string, unknown> | null | undefined;
    return (
      !!card &&
      typeof card === "object" &&
      typeof card.question_type === "string" &&
      typeof card.answer_fields === "object" &&
      card.answer_fields !== null
    );
  }
  /* 턴 하나가 완결될 때마다 항상 붙는 좋아요/싫어요 컨트롤. 이 케이스가 없으면
     피드백 버튼이 뜬 대화(사실상 대부분의 정상 대화)가 새로고침 시 통째로
     복원 실패로 버려진다 — schedule_result/place_info_result와 같은 버그. */
  if (message.type === "feedback") {
    return typeof message.sessionId === "string" && typeof message.runId === "string";
  }
  if (message.type === "compare_result") {
    const comparison = message.comparison as Record<string, unknown> | null | undefined;
    return (
      !!comparison &&
      typeof comparison === "object" &&
      (comparison.criteria === "time" ||
        comparison.criteria === "travel_time" ||
        comparison.criteria === "overall") &&
      Array.isArray(comparison.items)
    );
  }
  if (message.type === "clarification") {
    return typeof message.text === "string" && Array.isArray(message.options);
  }
  /* 턴이 끝날 때마다 붙는 후속 질문 버튼(D-102). 이 케이스가 없으면 후속 질문이
     한 번이라도 뜬 대화 — 사실상 모든 정상 대화 — 가 새로고침에서 통째로 버려진다.
     schedule_result/place_info_result/feedback과 같은 버그의 다섯 번째다. */
  if (message.type === "follow_up_suggestions") {
    return Array.isArray(message.suggestions);
  }
  /* 추천 결과에서 갈라져 나온 버튼과 취향 표. 위와 같은 이유로 케이스를 반드시
     둔다 — 빠지면 추천이 한 번이라도 나온 대화가 새로고침에서 통째로 버려진다.
     같은 버그의 여섯 번째·일곱 번째가 될 자리다. */
  if (message.type === "recommendation_actions") {
    return typeof message.has_no_results === "boolean";
  }
  if (message.type === "preference_tag_summary") {
    return Array.isArray(message.items);
  }
  if (message.type === "schedule_actions") {
    return typeof message.has_no_schedule === "boolean";
  }
  return false;
}

export function loadState(): TripState | null {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<StoredState>;
    if (parsed.version !== STORAGE_VERSION || !isTripState(parsed.state)) return null;
    // 스트리밍 진행 상태는 새로고침 뒤에 복원하면 이미 끊긴 HTTP 연결을 계속 표시하게
    // 된다. 이전 버전 저장본에 이 필드가 없던 경우까지 함께 null로 정규화한다.
    return {
      ...parsed.state,
      // 기존 저장본(v5)은 위치를 받은 시각이 없다. 정확한 시각을 알 수 없는 좌표는
      // 다음 후속 요청에서 갱신 여부를 사용자에게 묻는다.
      device_location_captured_at: parsed.state.device_location_captured_at ?? null,
      // 기존 저장본(v5 이하)은 스누즈 마감이 없다. null이면 정상적으로 재확인
      // 여부를 다시 판단한다.
      device_location_snoozed_until: parsed.state.device_location_snoozed_until ?? null,
      // 기존 저장본에는 언어가 없으므로 한국어로 자연스럽게 이어간다.
      language: parsed.state.language ?? "ko",
      agentProgress: null,
      streamingIntent: null,
    };
  } catch {
    return null;
  }
}

export function saveState(state: TripState): void {
  try {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify({ version: STORAGE_VERSION, state }));
  } catch {
    // sessionStorage can be unavailable; the app still works for the current tab.
  }
}

export function clearState(): void {
  sessionStorage.removeItem(STORAGE_KEY);
}
