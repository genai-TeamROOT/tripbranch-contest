/*
 * 역할: 개발자가 /api/chat Agent Runtime 결과를 발화별로 검증하는 전용 채팅 화면.
 * 입력: 사용자 발화, TripContext 세션/감사 기록.
 * 출력: 가운데 채팅, 오른쪽 Agent Runtime Audit 패널.
 * 호출 시점: /dev-chat 라우트가 활성화될 때 호출된다.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError } from "../api/client";
import {
  clearExchanges,
  fetchExchanges,
  setExchangeCapture,
  type ApiExchangeSnapshot,
} from "../api/dev";
import { fetchSessionState, streamChat, toDisplayConditions } from "../api/trip";
import { ChatComposer } from "../components/chat/ChatComposer";
import { ChatMessageList } from "../components/chat/ChatMessageList";
import { SavedPlacesBar } from "../components/chat/SavedPlacesBar";
import { ApiExchangePanel } from "../components/dev/ApiExchangePanel";
import { DeveloperAuditPanel } from "../components/dev/DeveloperAuditPanel";
import { StaleAreaBanner } from "../components/dev/StaleAreaBanner";
import { TurnLocationBadges } from "../components/dev/TurnLocationBadges";
import { ErrorBanner } from "../components/ErrorBanner";
import { LanguageSelector } from "../components/LanguageSelector";
import { AuthStatusBadge } from "../auth/AuthStatusBadge";
import { useLocationSettings } from "../hooks/useLocationSettings";
import { usePhotoSimilarSearch } from "../hooks/usePhotoSimilarSearch";
import { useSavedPlaces } from "../hooks/useSavedPlaces";
import {
  loadLocationSettings,
  syncLocationSettingsFromConditions,
} from "../state/locationSettings";
import { useTripDispatch, useTripState } from "../state/TripContext";
import { buildAgentStageTimings } from "../utils/agentTiming";
import { buildLocationChipModel } from "../utils/locationChip";
import { getLatestConversationPlaceName } from "../utils/conversationPlace";
import { getBrowserDeviceLocation } from "../utils/geolocation";
import {
  getLocationAgeMinutes,
  isLocationRefreshDue,
  LOCATION_RECONFIRM_AFTER_MS,
} from "../utils/locationRefresh";
import type { TravelOrigin } from "../types";

const STATUS_COMMAND = "/status";

const DEV_CHAT_TEXT = {
  ko: {
    subtitle: "개발자용 채팅 검증 화면",
    ops: "Ops 패널",
    userView: "사용자 화면",
    newChat: "새 대화",
    emptyTitle: "첫 발화를 입력해 검증을 시작하세요.",
    emptyDescription: "응답이 돌아오면 오른쪽에 Intent, 조건 병합, Tool/Scoring 요약이 턴별로 누적됩니다.",
    composer: "추가 조건을 입력해 주세요",
    requestMore: "다른 곳 보여줘",
    relaxRadius: "검색 범위를 넓혀서 다시 추천해줘",
    basedOn: (name: string) => `${name} 기준으로 다시 보기`,
    currentLocation: "현재 위치 기준으로 다시 보기",
    locationError: "위치를 가져오지 못했어요.",
    requestError: "추천을 불러오지 못했어요. 다시 시도해주세요.",
  },
  en: {
    subtitle: "Developer chat verification",
    ops: "Ops panel",
    userView: "User view",
    newChat: "New chat",
    emptyTitle: "Send a first message to start verification.",
    emptyDescription: "After a response arrives, the right panel accumulates the Intent, merged conditions, and Tool/Scoring summary for each turn.",
    composer: "Add another condition or ask a follow-up",
    requestMore: "Show more places",
    relaxRadius: "Search in a wider area",
    basedOn: (name: string) => `View results based on ${name}`,
    currentLocation: "View results based on my current location",
    locationError: "We couldn’t get your location.",
    requestError: "We couldn’t load recommendations. Please try again.",
  },
} as const;
interface PendingLocationRefresh {
  text: string;
  clarificationChoice?: string;
  travelOriginOverride?: TravelOrigin;
}

export function DeveloperChatPage() {
  const state = useTripState();
  const dispatch = useTripDispatch();
  const locationSettings = useLocationSettings();
  const handlePhotoSelect = usePhotoSimilarSearch();
  const navigate = useNavigate();
  const text = DEV_CHAT_TEXT[state.language];
  const chatScrollRef = useRef<HTMLDivElement | null>(null);
  const previousAuditTurnCountRef = useRef(0);
  const [selectedTurnId, setSelectedTurnId] = useState<string | null>(null);
  const [exchanges, setExchanges] = useState<ApiExchangeSnapshot | null>(null);
  const [exchangeError, setExchangeError] = useState<string | null>(null);
  // 개발자 채팅 전용 디버그 스위치. 켜두면 이후 모든 턴이 폐점 후보도 채점에
  // 포함한다 — no_data_closed 되묻기를 재현/우회하려고 매번 버튼을 누르지
  // 않아도 된다(실사용 피드백, 2026-08-13).
  const [debugIgnoreOperatingHours, setDebugIgnoreOperatingHours] = useState(false);
  const [pendingLocationRefresh, setPendingLocationRefresh] = useState<PendingLocationRefresh | null>(
    null,
  );

  const isLoading = state.phase === "interpreting" || state.phase === "recommending";
  const latestTurn = state.auditTurns.at(-1);

  const withExchangeErrors = useCallback(
    async (load: () => Promise<ApiExchangeSnapshot>) => {
      try {
        setExchanges(await load());
        setExchangeError(null);
      } catch (error) {
        setExchangeError(
          error instanceof ApiError
            ? error.message
            : "API 캡처 정보를 불러오지 못했어요.",
        );
      }
    },
    [],
  );

  const loadExchanges = useCallback(
    () => withExchangeErrors(fetchExchanges),
    [withExchangeErrors],
  );

  useEffect(() => {
    void loadExchanges();
  }, [loadExchanges]);

  useEffect(() => {
    const hasNewTurn = state.auditTurns.length > previousAuditTurnCountRef.current;
    previousAuditTurnCountRef.current = state.auditTurns.length;
    if (latestTurn && (selectedTurnId === null || hasNewTurn)) {
      setSelectedTurnId(latestTurn.id);
    }
  }, [latestTurn, selectedTurnId, state.auditTurns]);

  useEffect(() => {
    const scroller = chatScrollRef.current;
    if (!scroller) return;
    requestAnimationFrame(() => {
      if (typeof scroller.scrollTo === "function") {
        scroller.scrollTo({ top: scroller.scrollHeight, behavior: "smooth" });
        return;
      }
      scroller.scrollTop = scroller.scrollHeight;
    });
  }, [isLoading, state.messages.length]);

  const showStatus = useCallback(
    async (text: string) => {
      if (!state.session_id) {
        dispatch({
          type: "APPEND_SESSION_STATUS",
          payload: {
            userInput: text,
            status: null,
            error: "아직 세션이 없어요. 발화를 한 번 보낸 뒤에 다시 시도해주세요.",
          },
        });
        return;
      }
      try {
        const status = await fetchSessionState(state.session_id);
        dispatch({
          type: "APPEND_SESSION_STATUS",
          payload: { userInput: text, status, error: null },
        });
      } catch (error) {
        dispatch({
          type: "APPEND_SESSION_STATUS",
          payload: {
            userInput: text,
            status: null,
            error: error instanceof ApiError ? error.message : "세션 상태를 불러오지 못했어요.",
          },
        });
      }
    },
    [dispatch, state.session_id],
  );

  // 새로고침 직후 보관함을 서버 기준으로 다시 맞춘다(useSavedPlaces.ts 참고).
  const { refreshIfAny: refreshSavedIfAny } = useSavedPlaces();

  const send = useCallback(
    async (
      text: string,
      clarificationChoice?: string,
      deviceLocationOverride?: string,
      deviceLocationCapturedAt?: number,
      travelOriginOverride?: TravelOrigin,
      options?: { scheduleFromSaved?: boolean },
    ) => {
      let deviceLocation = deviceLocationOverride ?? state.device_location;
      let capturedAt = deviceLocationCapturedAt;
      // /dev-chat은 HomePage를 거치지 않고 바로 들어올 수 있어 첫 턴엔 위치가 없다.
      // null로 계속 보내면 위치를 아예 모르는 채로만 테스트하게 돼 travel_origin
      // 같은 위치 기반 기능을 이 화면에서 확인할 수 없다 — 아직 없을 때만
      // HomePage와 같은 방식으로 한 번 가져온다. 이미 있으면(override든 이전
      // 턴에 저장된 값이든) 다시 묻지 않는다.
      if (deviceLocation === null) {
        try {
          deviceLocation = await getBrowserDeviceLocation();
          capturedAt = Date.now();
        } catch (error) {
          dispatch({
            type: "SET_ERROR",
            payload:
              error instanceof Error ? error.message : DEV_CHAT_TEXT[state.language].locationError,
          });
          return;
        }
      }
      const conversationPlaceName = getLatestConversationPlaceName(state.messages);
      dispatch({
        type: "START_CHAT_TURN",
        payload: { userInput: text, deviceLocation, deviceLocationCapturedAt: capturedAt },
      });
      const startedAt = performance.now();
      const progressEvents = [] as import("../types").AgentProgressEvent[];
      let messageStartElapsedMs: number | null = null;
      let firstMessageDeltaElapsedMs: number | null = null;
      let receivedStreamResult = false;
      let receivedStreamMessage = false;
      try {
        await streamChat(
          {
            user_input: text,
            language: state.language,
            session_id: state.session_id,
            device_location: deviceLocation,
            /* 사용자 화면과 같은 값을 싣는다. 예전에는 기기 좌표만 보냈는데, 두
               화면이 같은 session_id를 쓰기 때문에 여기서 한 턴을 돌리면 서버에
               쌓인 위치 조건이 이 화면 기준으로 바뀌고 사용자 화면으로 돌아가면
               다시 채워지는 일이 반복됐다. 같은 설정으로 물어도 검색 기준이
               GPS·검색지·출발지로 갈려 보이던 원인의 상당 부분이 이것이었다
               (2026-09-08). 구조를 합치는 것은 TP-255에서 한다. */
            selected_search_center: loadLocationSettings().center,
            selected_current_location: loadLocationSettings().origin,
            conversation_place_name: conversationPlaceName,
            clarification_choice: clarificationChoice ?? null,
            travel_origin_override: travelOriginOverride ?? null,
            schedule_from_saved: options?.scheduleFromSaved ?? false,
            debug_ignore_operating_hours: debugIgnoreOperatingHours,
            /* 이미 보여준 후속 질문. 서버는 이 값을 만들 수 없다 — 보여주기만 하고
               누르지 않은 문구는 화면 말고 아는 곳이 없다. */
            recent_follow_ups: state.recent_follow_ups,
          },
          (event) => {
            if (event.type === "progress") {
              progressEvents.push(event.data);
              dispatch({ type: "SET_AGENT_PROGRESS", payload: event.data });
              return;
            }
            if (event.type === "result") {
              receivedStreamResult = true;
              dispatch({
                type: "APPEND_STREAM_RESULT",
                payload: { ...event.data, elapsedMsClient: performance.now() - startedAt },
              });
              return;
            }
            if (event.type === "message_start") {
              receivedStreamMessage = true;
              messageStartElapsedMs = event.data.elapsed_ms;
              dispatch({ type: "START_STREAM_MESSAGE", payload: { intent: event.data.intent } });
              return;
            }
            if (event.type === "message_delta") {
              firstMessageDeltaElapsedMs ??= event.data.elapsed_ms;
              dispatch({ type: "APPEND_STREAM_MESSAGE_DELTA", payload: { text: event.data.text } });
              return;
            }
            if (event.type === "follow_ups") {
              // done 뒤에 오는 유일한 이벤트다. 턴은 이미 끝나 로딩이 사라진
              // 상태이고, 버튼만 조금 늦게 붙는다.
              dispatch({
                type: "APPEND_FOLLOW_UP_SUGGESTIONS",
                payload: { suggestions: event.data.suggestions },
              });
              return;
            }
            if (event.type === "location_resolved") {
              /* 사용자 화면과 같이 반영한다. 발화가 위치를 바꾸면("쌍문동에
                 갈만한곳") 그 결과가 위치 설정으로 돌아와야 다음 턴도 같은 곳을
                 본다 — 이 화면만 안 받으면 두 화면이 서로 다른 위치를 들고
                 같은 세션을 건드린다. */
              syncLocationSettingsFromConditions(event.data);
              return;
            }
            if (event.type === "error") throw new ApiError(event.data);
            const response = event.data.response;
            /* location_resolved가 오지 않은 경로(단발 응답)를 위해 여기서도 한 번
               맞춘다 — 사용자 화면과 같다. 같은 값이면 아무것도 쓰지 않는다. */
            syncLocationSettingsFromConditions(response.state.user_conditions);
            const elapsedMsClient = performance.now() - startedAt;
            if (receivedStreamResult || receivedStreamMessage) {
              dispatch({
                type: "COMPLETE_STREAM_CHAT_TURN",
                payload: {
                  response,
                  elapsedMsClient,
                  serverElapsedMs: event.data.elapsed_ms,
                  stageTimings: buildAgentStageTimings(progressEvents, event.data.elapsed_ms, {
                    messageStartElapsedMs,
                    firstMessageDeltaElapsedMs,
                  }),
                  conditions: toDisplayConditions(response.llm_output),
                },
              });
              return;
            }
            dispatch({
              type: "APPEND_CHAT_TURN",
              payload: {
                userInput: text,
                intent: response.llm_output.intent,
                status: response.llm_output.status,
                conditions: toDisplayConditions(response.llm_output),
                mergedConditions: response.state.user_conditions,
                message: response.message,
                recommendations: response.recommendations,
                schedule: response.schedule,
                sessionId: response.state.session_id,
                agentResponse: response,
                showDebug: false,
                elapsedMsClient,
                ...(progressEvents.length > 0
                  ? {
                      serverElapsedMs: event.data.elapsed_ms,
                      stageTimings: buildAgentStageTimings(progressEvents, event.data.elapsed_ms, {
                        messageStartElapsedMs,
                        firstMessageDeltaElapsedMs,
                      }),
                    }
                  : {}),
              },
            });
          },
        );
      } catch (error) {
        const apiError = error instanceof ApiError ? error : null;
        dispatch({
          type: "APPEND_FAILED_CHAT_TURN",
          payload: {
            userInput: text,
            message: apiError?.message ?? DEV_CHAT_TEXT[state.language].requestError,
            code: apiError?.code ?? "internal_server_error",
            retryable: apiError?.retryable ?? true,
            details: apiError?.details ?? null,
            elapsedMsClient: performance.now() - startedAt,
          },
        });
      } finally {
        // 외부 호출은 이 턴에서 발생하므로 턴이 끝난 직후에만 다시 읽는다.
        // 주기 폴링을 걸면 아무 일도 없는 동안 요청만 늘어난다.
        void loadExchanges();
        // 이 턴에서 거절이 일어났다면 서버가 보관함에서도 뺐다(saved ∩ rejected = ∅).
        void refreshSavedIfAny();
      }
    },
    [
      debugIgnoreOperatingHours,
      dispatch,
      loadExchanges,
      refreshSavedIfAny,
      state.device_location,
      state.language,
      state.messages,
      state.recent_follow_ups,
      state.session_id,
    ],
  );

  /*
   * 보관함 CTA. ChatPage와 같은 배선이다 — 이 화면에 둔 이유는 오른쪽 감사 패널이
   * 여기 있기 때문이다. 카드 완료 조건("CTA 클릭 시 인텐트 분류 LLM 호출이 없다")을
   * CTA와 Audit이 한 화면에 있어야 확인할 수 있다.
   */
  const planFromSaved = useCallback(() => {
    const label = state.language === "en" ? "Plan a trip with these" : "이 장소들로 일정 짜기";
    void send(label, undefined, undefined, undefined, undefined, { scheduleFromSaved: true });
  }, [send, state.language]);

  const locationAgeMinutes = getLocationAgeMinutes(state.device_location_captured_at);

  const requestSend = useCallback(
    async (text: string, clarificationChoice?: string, travelOriginOverride?: TravelOrigin) => {
      if (
        isLocationRefreshDue(
          state.device_location,
          state.device_location_captured_at,
          state.device_location_snoozed_until,
        )
      ) {
        setPendingLocationRefresh({ text, clarificationChoice, travelOriginOverride });
        return;
      }
      await send(text, clarificationChoice, undefined, undefined, travelOriginOverride);
    },
    [
      send,
      state.device_location,
      state.device_location_captured_at,
      state.device_location_snoozed_until,
    ],
  );

  const usePreviousLocation = useCallback(() => {
    if (!pendingLocationRefresh) return;
    const pending = pendingLocationRefresh;
    setPendingLocationRefresh(null);
    // 실제 GPS는 다시 받지 않았으니 device_location_captured_at은 그대로 두고,
    // 재확인 질문만 30분 동안 미룬다 — 그래야 다음 턴 나이 표시가 실제 경과
    // 시간을 계속 정확히 보여준다(utils/locationRefresh.ts 참고).
    dispatch({
      type: "SNOOZE_LOCATION_REFRESH",
      payload: { until: Date.now() + LOCATION_RECONFIRM_AFTER_MS },
    });
    void send(pending.text, pending.clarificationChoice, undefined, undefined, pending.travelOriginOverride);
  }, [dispatch, pendingLocationRefresh, send]);

  const refreshBrowserLocation = useCallback(async () => {
    if (!pendingLocationRefresh) return;
    try {
      const deviceLocation = await getBrowserDeviceLocation({ forceFresh: true });
      const pending = pendingLocationRefresh;
      setPendingLocationRefresh(null);
      await send(
        pending.text,
        pending.clarificationChoice,
        deviceLocation,
        Date.now(),
        pending.travelOriginOverride,
      );
    } catch (error) {
      dispatch({
        type: "SET_ERROR",
        payload: error instanceof Error ? error.message : text.locationError,
      });
    }
  }, [dispatch, pendingLocationRefresh, send]);

  async function handleFollowUp(text: string) {
    if (isLoading) return;
    if (text.trim() === STATUS_COMMAND) {
      await showStatus(text.trim());
      return;
    }
    await requestSend(text);
  }

  /* 사용자 화면의 상단 위치 칩과 같은 모델을 쓴다. 다만 AppHeader를 그대로 못
     가져온다 — 그쪽은 사이드바 컨텍스트(AppShellContext)를 요구하고 이 화면은 3분할
     레이아웃이라 그 껍데기가 없다. 그래서 값만 같은 것을 쓰고 표시는 이 화면 말투로
     그린다.

     아래 TurnLocationBadges와 역할이 다르다 — 저것은 **직전 턴이 실제로 쓴** 위치고,
     이 칩은 **지금 설정돼 있어 다음 발화에 실려 갈** 위치다. 둘이 어긋나는 순간이
     바로 발화가 설정을 이긴 턴이라, 나란히 보여야 판단이 된다. */
  const locationChip = buildLocationChipModel(
    locationSettings,
    state.interpreted_conditions?.location_query ?? null,
    Boolean(state.device_location),
  );

  return (
    <main className="grid h-screen grid-cols-[380px_minmax(0,1fr)_480px] overflow-hidden bg-white text-gray-950 dark:bg-gray-950 dark:text-gray-50">
      <ApiExchangePanel
        snapshot={exchanges}
        error={exchangeError}
        onToggleCapture={(enabled) =>
          void withExchangeErrors(() => setExchangeCapture(enabled))
        }
        onClear={() => void withExchangeErrors(clearExchanges)}
        onRefresh={() => void loadExchanges()}
      />

      <section className="relative flex min-h-0 min-w-0 flex-col overflow-hidden">
        <header className="flex items-center justify-between gap-3 border-b border-gray-200 px-5 py-4 dark:border-gray-800">
          <div className="min-w-0">
            <h1 className="text-xl font-bold">TripBranch</h1>
            <p className="text-sm text-gray-500 dark:text-gray-400">
              {text.subtitle}
            </p>
            <p
              className="mt-1 truncate text-xs text-gray-500 dark:text-gray-400"
              title={locationChip.description}
            >
              <span className="text-gray-400 dark:text-gray-500">다음 발화에 실릴 위치 </span>
              {locationChip.kind === "single"
                ? locationChip.name
                : `${locationChip.origin} → ${locationChip.center}`}
              {locationChip.isDeviceLocationPending && (
                /* 이름은 "현재 위치"인데 좌표를 아직 못 받은 상태. 사용자 화면은
                   회색 점으로 말하는데 여기는 글자로 말한다. 이 구분이 없으면
                   좌표 없이 보낸 턴을 "GPS로 찾았겠지"로 잘못 읽는다. */
                <span className="text-gray-400 dark:text-gray-500"> (좌표 없음)</span>
              )}
            </p>
          </div>
          <div className="flex items-center gap-2">
            <LanguageSelector
              language={state.language}
              onChange={(language) => dispatch({ type: "SET_LANGUAGE", payload: language })}
            />
            <AuthStatusBadge />
            <button
              type="button"
              onClick={() => navigate("/dev-ops")}
              className="rounded-md border border-gray-300 px-3 py-1.5 text-sm dark:border-gray-700"
            >
              {text.ops}
            </button>
            <button
              type="button"
              onClick={() => navigate("/chat")}
              className="rounded-md border border-gray-300 px-3 py-1.5 text-sm dark:border-gray-700"
            >
              {text.userView}
            </button>
            <button
              type="button"
              onClick={() => {
                dispatch({ type: "RESET" });
                setSelectedTurnId(null);
              }}
              className="rounded-md border border-gray-300 px-3 py-1.5 text-sm dark:border-gray-700"
            >
              {text.newChat}
            </button>
          </div>
        </header>

        <div
          ref={chatScrollRef}
          className="min-h-0 flex-1 overflow-auto px-5 py-5 pb-[var(--tb-composer-h,0px)]"
        >
          {state.error && (
            <ErrorBanner
              message={state.error}
              onRetry={() => {
                if (state.user_input) void requestSend(state.user_input);
              }}
            />
          )}

          {state.messages.length === 0 ? (
            <div className="flex h-full items-center justify-center">
              <div className="max-w-md rounded-md border border-dashed border-gray-300 p-6 text-center dark:border-gray-700">
                <h2 className="text-lg font-semibold">{text.emptyTitle}</h2>
                <p className="mt-2 text-sm text-gray-500 dark:text-gray-400">
                  {text.emptyDescription}
                </p>
              </div>
            </div>
          ) : (
            <ChatMessageList
              messages={state.messages}
              showDebug={false}
              isDeveloperView
              isLoading={isLoading}
              deviceLocation={state.device_location}
              onRequestMore={() => void requestSend(text.requestMore)}
              onRelaxRadius={() => void requestSend(text.relaxRadius)}
              onSelectClarificationOption={(optionId, label) => void requestSend(label, optionId)}
              onSelectFollowUpSuggestion={(suggestion) => void requestSend(suggestion)}
              onSetLocation={() => navigate("/location")}
              onToggleTravelOrigin={(toggle) => {
                const label = toggle.alternative_origin === "search_center"
                  ? text.basedOn(toggle.alternative_origin_name)
                  : text.currentLocation;
                void requestSend(label, undefined, toggle.alternative_origin);
              }}
              locationRefresh={
                pendingLocationRefresh
                  ? {
                      ageMinutes: locationAgeMinutes,
                      onUsePrevious: usePreviousLocation,
                      onRefreshLocation: () => void refreshBrowserLocation(),
                    }
                  : null
              }
              progress={state.agentProgress}
              language={state.language}
            />
          )}
        </div>

        <div className="border-t border-gray-200 p-4 dark:border-gray-800">
          {/*
            직전 턴이 실제로 쓴 위치. 오른쪽 감사 패널이 아니라 여기 두는 이유는 폭이다 —
            패널은 좁아 지명이 잘린다. 선택된 턴이 아니라 마지막 턴을 보여준다: 이 자리는
            채팅 흐름의 끝이라 바로 위 대화와 같은 턴을 가리켜야 한다.
          */}
          {latestTurn && <StaleAreaBanner turn={latestTurn} />}
          {latestTurn && <TurnLocationBadges turn={latestTurn} />}
          <SavedPlacesBar
            onPlanFromSaved={planFromSaved}
            isLoading={isLoading}
            language={state.language}
          />
        </div>

        {/* 스크롤 칸 밖의 형제다 — 컴포저는 더 이상 sticky 가 아니다
            (2026-09-09, ChatComposer 주석). */}
        <ChatComposer
          disabled={isLoading}
          onSubmit={handleFollowUp}
          placeholder={text.composer}
          language={state.language}
          onPhotoSelect={handlePhotoSelect}
        />
      </section>

      <DeveloperAuditPanel
        turns={state.auditTurns}
        selectedTurnId={selectedTurnId}
        onSelectTurn={setSelectedTurnId}
        debugIgnoreOperatingHours={debugIgnoreOperatingHours}
        onToggleDebugIgnoreOperatingHours={setDebugIgnoreOperatingHours}
      />
    </main>
  );
}
