/*
 * 역할: TripBranch 도메인 endpoint별 API 함수를 제공한다.
 * 입력: 사용자 입력 문자열, 해석된 조건, 이미 노출된 place_id 목록.
 * 출력: 백엔드가 반환한 해석 조건 또는 추천 결과 모델.
 * 호출 시점: HomePage와 ChatPage에서 사용자 흐름 진행 시 호출된다.
 * TODO: 실제 endpoint가 늘어나면 파일을 도메인별로 나누고 요청 타입을 세분화한다.
 *
 * NOTE: /api/interpret은 이제 LLMOutput(intent + Intent별 조건)을 반환한다(기존
 * InterpretedConditions 계약에서 교체됨). interpretUserInput()은 기존 화면(HomePage/
 * ChatPage/ConditionDebugMessage)이 계속 InterpretedConditions로 동작하도록 RECOMMEND
 * 결과를 옛 형태로 변환해서 돌려준다 — RECOMMEND가 아니거나 조건이 비어 있으면 빈 값으로
 * 대체된다. Intent 분류·조건 추출 자체를 확인하려면 interpretDebug()를 대신 사용한다.
 */

import { apiClient, streamPost } from "./client";
import { isDetachedRequest } from "../state/chatAbortController";
import type {
  FavoritePlaceItem,
  FavoritesResponse,
  PhotoSimilarPlacesResponse,
  PlaceSearchResponse,
  AgentDebugRequest,
  AgentResponse,
  AgentStreamEvent,
  ChatRequest,
  ChatResponse,
  InterpretDebugRequest,
  InterpretResponse,
  ChatSessionDetail,
  ChatSessionSummary,
  ChatSessionsResponse,
  PreferencesResponse,
  SavedPreferenceItem,
  InterpretedConditions,
  LLMOutput,
  PlaceReasonResponse,
  RecommendationPlaceDetailResponse,
  RecommendationsResponse,
  SessionContextResponse,
  SavedPlacesResponse,
  SavedScheduleDetail,
  SavedScheduleSummary,
  SavedSchedulesResponse,
  ScheduleResult,
  TranscriptionResponse,
  WeatherCondition,
} from "../types";

function mapStatedWeatherToLegacy(weather: string | null | undefined): WeatherCondition | null {
  if (weather === "good") return "good";
  if (weather === "rain" || weather === "snow" || weather === "hot" || weather === "cold") {
    return "bad";
  }
  return null;
}

/*
 * Agent가 반경을 주지 않을 때 쓰는 기본값. 백엔드
 * place_search_policy.DEFAULT_PLACE_SEARCH_RADIUS_KM(2.0)과 맞춘다.
 */
const DEFAULT_SEARCH_RADIUS_KM = 2.0;

/*
 * LLMOutput의 RECOMMEND 조건을 화면 표시용 InterpretedConditions로 옮긴다.
 * /api/chat 전환 이후에는 추천 요청 본문이 아니라 조건 카드 표시에만 쓰인다.
 */
export function toDisplayConditions(output: LLMOutput): InterpretedConditions | null {
  return output.recommend ? toLegacyConditions(output) : null;
}

function toLegacyConditions(output: LLMOutput): InterpretedConditions {
  const conditions = output.recommend?.conditions;
  const categories = conditions?.place_tags.length
    ? conditions.place_tags
    : (conditions?.place_types ?? []);
  return {
    // 사용자가 위치를 말하지 않았으면 비워 둔다. 임의의 지명을 채우면 말한 적 없는
    // 장소를 조건 카드와 안내 문구에 그대로 보여주게 된다.
    location_query: conditions?.search_center ?? conditions?.current_location ?? "",
    preferred_categories: categories,
    weather_condition: mapStatedWeatherToLegacy(conditions?.weather),
    // 현재 UserConditions에는 반경 필드가 없어 항상 기본값이 쓰인다. Agent가 반경을
    // 제공하게 되면 여기서 그 값을 우선 사용한다.
    search_radius_km: conditions?.search_radius_km ?? DEFAULT_SEARCH_RADIUS_KM,
    // 구형 4필드로 축소되면서 버려지는 나머지 조건을 개발용 표시를 위해 보존한다.
    raw_conditions: conditions ?? null,
  };
}

export async function interpretUserInput(user_input: string) {
  const response = await apiClient.post<InterpretResponse>("/interpret", { user_input });
  return toLegacyConditions(response.output);
}

export async function interpretDebug(request: InterpretDebugRequest) {
  const response = await apiClient.post<InterpretResponse>("/interpret", request);
  return response.output;
}

export function getRecommendations(
  conditions: InterpretedConditions & { shown_place_ids: string[] },
) {
  // raw_conditions는 개발용 표시 전용이라 요청 본문에서 제외한다.
  const payload = { ...conditions };
  delete payload.raw_conditions;
  return apiClient.post<RecommendationsResponse>("/recommendations", payload);
}

/** 추천 카드 클릭 시 LLM 없이 C의 장소 상세정보만 단건 조회한다. */
export function fetchRecommendationPlaceDetails(request: {
  place_id?: string | null;
  place_name: string;
}) {
  return apiClient.post<RecommendationPlaceDetailResponse>("/chat/place-details", request);
}

/**
 * "AI가 추천하는 이유" 문장을 만들어 받는다. 상세조회가 끝난 **뒤에** 부른다 —
 * 한 호출로 묶으면 이 문장을 기다리는 동안 주소·운영시간·사진이 통째로 안 나온다.
 *
 * 추천/수정 카드로 열었을 때만 부른다. INFO 카드와 사진 검색 결과에는 그 절이
 * 없어서, 부르면 읽히지 않을 문장에 클릭마다 LLM 값을 치른다.
 *
 * `place_id`가 필수인 것은 서버가 문장의 근거(취향 태그·후기)를 그 id로 다시 읽기
 * 때문이다. `category_label`은 문장이 장소 종류를 잘못 말하지 않게 넘기는 값으로,
 * 카드만 알고 있다(서버가 읽는 상세에는 분류 필드가 없다).
 *
 * `matched_preference_codes`는 이번 추천에서 사용자 취향과 맞은 태그 코드
 * (`preference_tags[].is_query_match`)다. 서버가 상위 3태그만 문장 근거로 넘기는데
 * 그 순서가 "이 장소에서 많이 언급된 순서"라, 이 값이 없으면 사용자가 말한 취향에
 * 걸린 태그가 근거에서 통째로 빠질 수 있다. 라벨이나 후기 문장은 보내지 않는다 —
 * 서버가 저장소에서 다시 읽고, 화면이 주는 것은 고르는 기준(코드)뿐이다.
 */
export function fetchPlaceAiReason(request: {
  place_id: string;
  place_name: string;
  category_label?: string | null;
  matched_preference_codes?: string[];
}) {
  return apiClient.post<PlaceReasonResponse>("/chat/place-details/reason", request);
}

export function runAgentDebug(request: AgentDebugRequest) {
  return apiClient.post<AgentResponse>("/agent-debug", request);
}

/*
 * 실사용 흐름(HomePage/ChatPage)의 단일 진입점. Intent 분류 → 조건 병합 → Tool →
 * Scoring → 챗봇 메시지까지 한 번의 호출로 끝난다.
 * agent-debug와 응답 형태는 같지만 라우트가 다르다 — 개발용 패널과 실사용 경로를
 * 섞지 않기 위함이다.
 */
export function sendChat(request: ChatRequest) {
  return apiClient.post<ChatResponse>("/chat", request);
}

/** 녹음한 WAV를 전사만 한다. 이 결과를 `/chat`으로 자동 전송하지 않는다. */
export function transcribeAudio(audio: Blob) {
  return apiClient.postBinary<TranscriptionResponse>("/transcribe", audio, "audio/wav");
}

/** 실제 진행 상태·추천 카드·요약 문장을 순차 수신하는 SSE 채팅 경로. */
export function streamChat(
  request: ChatRequest,
  onEvent: (event: AgentStreamEvent) => void,
  /** 응답 중단 기능(§7.2)이 넘기는 신호. 중단되면 단발 API로 낮추지 않는다. */
  signal?: AbortSignal,
) {
  let receivedEvent = false;
  return streamPost<unknown>(
    "/chat/stream",
    request,
    (event, data) => {
      /* 화면에서 떼어졌거나 끊긴 요청의 이벤트는 흘리지 않는다. 지난 대화를 열면
         그 요청을 화면에서 떼어내는데(detachChatRequest — 서버가 답변을 저장할 수
         있게 요청 자체는 계속 둔다), 그대로 두면 방금 연 대화에 앞 대화의 답변이
         붙는다. */
      if (signal?.aborted || isDetachedRequest(signal)) return;
      if (
        event === "progress" ||
        event === "result" ||
        event === "location_resolved" ||
        event === "message_start" ||
        event === "message_delta" ||
        event === "done" ||
        event === "follow_ups" ||
        event === "error"
      ) {
        receivedEvent = true;
        onEvent({ type: event, data } as AgentStreamEvent);
      }
    },
    signal,
  ).catch(async (error: unknown) => {
    // 사용자가 직접 중단한 요청이면 낮추지 않고 그대로 알린다 — 여기서 단발
    // API로 다시 보내면 "중단"을 눌렀는데 응답이 나오는 것으로 보인다.
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    // SSE endpoint 자체를 사용할 수 없는 구버전 배포·프록시 환경만 기존 단발 API로
    // 낮춘다. progress/result를 하나라도 받은 뒤의 오류는 이미 일부 화면이 그려진
    // 상태라, 중복 실행을 피하기 위해 호출자에게 그대로 전달한다.
    if (receivedEvent) throw error;
    const response = await sendChat(request);
    onEvent({ type: "done", data: { elapsed_ms: 0, response } });
  });
}

/*
 * 로컬 테스트용 "/status" 명령이 읽는 세션 상태 조회(GET /api/state/{session_id}).
 * 추천 흐름을 건드리지 않고 B가 보관 중인 누적 조건을 그대로 확인한다.
 */
export function fetchSessionState(sessionId: string) {
  return apiClient.get<SessionContextResponse>(`/state/${encodeURIComponent(sessionId)}`);
}

/*
 * 올린 사진과 분위기가 닮은 장소를 찾는다(POST /api/places/similar-by-photo).
 *
 * 위치는 지역명이 좌표를 이긴다 — 사용자가 적은 쪽이 의도이고 좌표는 적지
 * 않았을 때의 기본값이다. 둘 다 없으면 서버가 location_required로 되묻는다.
 */
export function searchPlacesByPhoto(params: {
  image: File;
  /** 대화 세션. 앞 턴이 잡은 위치("안국역" 등)를 서버가 이어받는다. */
  sessionId?: string | null;
  /* 위치는 이름으로만 보낸다. 서버는 latitude/longitude도 받지만 이 버전은 기기
     좌표를 받지 않아 보낼 좌표가 없다. */
  locationQuery?: string | null;
  limit?: number;
}) {
  const form = new FormData();
  form.append("image", params.image);
  if (params.sessionId) form.append("session_id", params.sessionId);
  if (params.locationQuery?.trim()) form.append("location_query", params.locationQuery.trim());
  if (params.limit != null) form.append("limit", String(params.limit));
  return apiClient.postForm<PhotoSimilarPlacesResponse>("/places/similar-by-photo", form);
}

/*
 * 위치 설정 화면의 장소 검색. 서버가 Naver 지역 검색 결과를 서울 안으로 좁혀
 * 돌려준다 - 이 화면이 정하는 값은 사용자의 현재 위치가 아니라 추천을 찾을
 * 위치이고, 지원 지역이 서울 25개 구이기 때문이다.
 */
export function searchPlaces(query: string) {
  return apiClient.get<PlaceSearchResponse>(`/places/search?query=${encodeURIComponent(query)}`);
}

/*
 * 장소 보관함(SCHEDULE-12). 담기/빼기는 인텐트 분류를 거치지 않는 전용 REST다 —
 * 버튼 클릭은 해석할 여지가 없는 결정적 동작이라 /chat을 통하면 오분류 위험과
 * LLM 지연이 그대로 붙는다.
 *
 * 세 함수 모두 담긴 목록 전체를 반환하므로, 호출부는 낙관적으로 먼저 갱신하고
 * 응답으로 확정하면 된다.
 */
export function fetchSavedPlaces(sessionId: string) {
  return apiClient.get<SavedPlacesResponse>(`/state/${encodeURIComponent(sessionId)}/saved-places`);
}

export function savePlace(sessionId: string, placeId: string) {
  return apiClient.post<SavedPlacesResponse>(
    `/state/${encodeURIComponent(sessionId)}/saved-places`,
    { place_id: placeId },
  );
}

export function removeSavedPlace(sessionId: string, placeId: string) {
  return apiClient.del<SavedPlacesResponse>(
    `/state/${encodeURIComponent(sessionId)}/saved-places/${encodeURIComponent(placeId)}`,
  );
}

/*
 * 계정 단위 취향(TP-222 후속). 세션 API들과 달리 경로에 session_id가 없다 —
 * 취향은 세션에 속하지 않고 사람에게 붙는 값이라 대화를 새로 시작해도 유지된다.
 *
 * **이 두 호출만 토큰이 없으면 401이다.** 다른 엔드포인트는 토큰 없는 요청도
 * 통과시키지만(백엔드 Phase 4 전 과도기), 취향은 신원이 곧 저장 키라 어디에
 * 저장할지가 정해지지 않는다. 호출부는 실패를 삼키지 말고 로컬 값으로
 * 되돌아가야 한다(state/preferenceSync.ts).
 */
export function fetchPreferences() {
  return apiClient.get<PreferencesResponse>("/preferences");
}

export function replacePreferences(items: readonly SavedPreferenceItem[]) {
  return apiClient.put<PreferencesResponse>("/preferences", { items });
}

/*
 * 계정 단위 즐겨찾기. 취향과 같은 자리의 값이라 경로에 session_id가 없고,
 * **토큰이 없으면 401**이다 - 신원이 곧 저장 키다.
 */
export function fetchFavorites() {
  return apiClient.get<FavoritesResponse>("/favorites");
}

export function replaceFavorites(items: readonly FavoritePlaceItem[]) {
  return apiClient.put<FavoritesResponse>("/favorites", { items });
}

/*
 * 회원 탈퇴. **본문이 없는 것이 핵심이다** — 누구를 지울지는 서버가 토큰에서만
 * 읽는다(backend/app/routes/account.py). 여기서 user_id를 실어 보낼 수 있게
 * 만들면 그 값이 어딘가에서 바뀌는 순간 남의 계정을 지우게 된다.
 *
 * 응답의 건수는 화면에 쓰지 않는다. 사용자에게 "대화 7개를 지웠어요"라고 알릴
 * 이유가 없고, 실패 조사용으로만 서버가 돌려준다.
 */
export function deleteAccount() {
  return apiClient.del<{ deleted_sessions: number; deleted_schedules: number }>("/account");
}

/*
 * 사이드바 채팅 히스토리(TP-222 후속). 목록은 세션에 속하지 않아 경로에
 * session_id가 없다 — /state/{session_id} 아래에 두면 "sessions"를 session_id로
 * 받아 삼킨다.
 *
 * fetchChatSessions는 preferences와 같이 **토큰이 없으면 401**이다. 신원이 곧
 * 조회 키라 누구의 목록인지가 정해지지 않기 때문이다.
 */
export function fetchChatSessions() {
  return apiClient.get<ChatSessionsResponse>("/sessions");
}

/*
 * 저장한 일정. (SCHEDULE 카드 2)
 *
 * 다섯 다 **토큰이 없으면 401**이다 — 신원이 곧 저장 키라, 누구의 일정인지가
 * 정해지지 않으면 저장할 자리도 돌려줄 목록도 없다(/preferences·/sessions와 같다).
 *
 * 경로가 /state/{sessionId} 아래가 아닌 이유는 저장한 일정이 특정 세션에 속하지
 * 않기 때문이다. 세션은 30일 뒤 정리되지만 저장한 일정은 남는다.
 */
export function fetchSavedSchedules() {
  return apiClient.get<SavedSchedulesResponse>("/schedules");
}

/*
 * 일정을 저장한다. **제목을 화면이 만들어 보낸다** — 서버는 payload를 열어보지
 * 않기로 되어 있어(saved_schedules 모듈) 일정 내용에서 제목을 뽑을 수 없다.
 *
 * 같은 (신원, run_id)를 다시 보내면 새로 만들지 않고 이미 있는 것을 돌려준다.
 * 실패가 아니라 성공이며 응답도 처음 저장한 것과 같으므로, 화면은 두 경우를
 * 구분할 필요가 없다.
 */
export function saveSchedule(input: {
  title: string;
  payload: ScheduleResult;
  sessionId?: string;
  runId?: string;
}) {
  return apiClient.post<SavedScheduleDetail>("/schedules", {
    title: input.title,
    payload: input.payload,
    session_id: input.sessionId ?? null,
    run_id: input.runId ?? null,
  });
}

export function fetchSavedSchedule(scheduleId: string) {
  return apiClient.get<SavedScheduleDetail>(`/schedules/${encodeURIComponent(scheduleId)}`);
}

export function renameSavedSchedule(scheduleId: string, title: string) {
  return apiClient.patch<SavedScheduleSummary>(
    `/schedules/${encodeURIComponent(scheduleId)}/title`,
    { title },
  );
}

/* 이미 없으면 오류가 아니라 deleted=false다. 남의 것을 지우려 하면 403이다. */
export function deleteSavedSchedule(scheduleId: string) {
  return apiClient.del<{ id: string; deleted: boolean }>(
    `/schedules/${encodeURIComponent(scheduleId)}`,
  );
}

export function renameChatSession(sessionId: string, title: string) {
  return apiClient.patch<ChatSessionSummary>(`/state/${encodeURIComponent(sessionId)}/title`, {
    title,
  });
}

/* 세션 전체를 지운다. 대화 목록에서 한 줄을 지우는 것이 곧 그 대화를 지우는 것이다. */
export function deleteChatSession(sessionId: string) {
  return apiClient.del<{ session_id: string; deleted: boolean }>(
    `/state/${encodeURIComponent(sessionId)}`,
  );
}

/*
 * 지난 대화를 이어갈 수 있게 되살린다. 사이드바에서 한 줄을 눌렀을 때 쓴다.
 *
 * **쓰기다.** 만료된 세션을 다시 active로 돌리고 낡은 조건(날씨·GPS·되묻기)을
 * 버린다. 그래서 응답의 resumable은 항상 true다. 백엔드에는 같은 내용을 돌려주는
 * GET /sessions/{id}도 있는데 화면은 쓰지 않는다 — 조회가 쓰기를 겸하면 목록을
 * 미리 불러오기만 해도 세션이 되살아나기 때문에 나눠 둔 것이고, 이쪽이 화면이
 * 필요로 하는 동작이다.
 */
export function resumeChatSession(sessionId: string) {
  return apiClient.post<ChatSessionDetail>(`/sessions/${encodeURIComponent(sessionId)}/resume`, {});
}
