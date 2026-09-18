/*
 * 역할: 사용자 입력과 추천 결과를 시간순 메시지로 누적하는 채팅 화면.
 * 입력: TripContext의 메시지/조건/phase와 후속 입력 이벤트.
 * 출력: ChatMessageList, 오류 배너, 하단 ChatComposer.
 * 호출 시점: /chat 라우트가 활성화되고 대화 상태가 있을 때 호출된다.
 *
 * 모든 발화는 /api/chat 한 번으로 처리된다 — Intent 분류·조건 병합·Tool 조회·
 * Scoring·메시지 조립을 Agent Runtime이 전부 수행하므로, 화면은 응답을 메시지로
 * 옮기기만 한다. "다른 장소 보기"/"검색 범위 넓히기"도 같은 경로로 자연어를 보낸다
 * (MODIFY Intent). 제외 목록의 단일 기준은 세션(B)이라 프론트가 따로 넘기지 않는다.
 * TODO: 스트리밍 응답이 생기면 메시지 append 경로를 확장한다.
 */

import { motion } from "framer-motion";
import { ChevronDown, ChevronUp } from "lucide-react";
import { useCallback, useRef, useState } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import { ApiError } from "../api/client";
import { fetchSessionState, streamChat, toDisplayConditions } from "../api/trip";
import { ChatComposer } from "../components/chat/ChatComposer";
import { ChatMessageList } from "../components/chat/ChatMessageList";
import { SavedPlacesChip } from "../components/chat/SavedPlacesChip";
import { useAutoScrollToBottom } from "../hooks/useAutoScrollToBottom";
import { useScrollEdgeButton } from "../hooks/useScrollEdgeButton";
import { AppHeader } from "../components/layout/AppHeader";
import { usePhotoSimilarSearch } from "../hooks/usePhotoSimilarSearch";
import { useSavedPlaces } from "../hooks/useSavedPlaces";
import {
  beginChatRequest,
  cancelChatRequest,
  endChatRequest,
  wasCancelledByUser,
} from "../state/chatAbortController";
import {
  loadLocationSettings,
  syncLocationSettingsFromConditions,
} from "../state/locationSettings";
import { useLocationSettings } from "../hooks/useLocationSettings";
import { useTripDispatch, useTripState } from "../state/TripContext";
import type { TravelOrigin } from "../types";
import { buildAgentStageTimings } from "../utils/agentTiming";
import { buildLocationChipModel, readSubstitutedOrigin } from "../utils/locationChip";
import { getLatestConversationPlaceName } from "../utils/conversationPlace";
import { getBrowserDeviceLocation } from "../utils/geolocation";
import {
  getLocationAgeMinutes,
  isLocationRefreshDue,
  LOCATION_RECONFIRM_AFTER_MS,
} from "../utils/locationRefresh";

/*
 * 로컬 테스트용 슬래시 명령. Agent에 보내지 않고 GET /api/state/{session_id}로
 * 현재 누적 조건을 조회해 화면에만 표시한다. 커밋하지 않는 확인용 경로다.
 */
const STATUS_COMMAND = "/status";

const CHAT_TEXT = {
  ko: {
    developer: "개발자용 보기",
    requestError: "추천을 불러오지 못했어요.",
    composer: "트리비에게 물어보세요",
    clarificationComposer: "경복궁 근처에서 찾아줘",
    requestMore: "다른 곳 보여줘",
    relaxRadius: "검색 범위를 넓혀서 다시 추천해줘",
    basedOn: (name: string) => `${name} 기준으로 다시 보기`,
    currentLocation: "현재 위치 기준으로 다시 보기",
  },
  en: {
    developer: "Developer view",
    requestError: "We couldn’t load recommendations.",
    composer: "Ask Trivi",
    clarificationComposer: "Find somewhere near Gyeongbokgung",
    requestMore: "Show more places",
    relaxRadius: "Search in a wider area",
    basedOn: (name: string) => `View results based on ${name}`,
    currentLocation: "View results based on my current location",
  },
} as const;
interface PendingLocationRefresh {
  text: string;
  clarificationChoice?: string;
  travelOriginOverride?: TravelOrigin;
}

export function ChatPage() {
  const state = useTripState();
  const dispatch = useTripDispatch();
  const locationSettings = useLocationSettings();
  const navigate = useNavigate();
  const text = CHAT_TEXT[state.language];

  const isLoading = state.phase === "interpreting" || state.phase === "recommending";
  const hasConversation = state.messages.length > 0;
  const messagesContainerRef = useRef<HTMLDivElement | null>(null);
  /* 입력창에 포커스가 가서 모바일 키보드가 뜨면 이 값이 채워진다 — 헤더까지
     함께 스크롤되어 밀려 올라가는 대신, 아래에서 <main> 자체의 높이를 줄인다. */
  useAutoScrollToBottom(messagesContainerRef, isLoading);
  const {
    isVisible: isScrollButtonVisible,
    direction: scrollButtonDirection,
    isScrollable,
    scrollToTop,
    scrollToBottom,
  } = useScrollEdgeButton(messagesContainerRef);
  const [pendingLocationRefresh, setPendingLocationRefresh] =
    useState<PendingLocationRefresh | null>(null);

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
      /*
       * 위치 인자가 이미 다섯이라 여섯 번째를 또 붙이면 호출부에서
       * `send(t, undefined, undefined, undefined, undefined, true)`가 된다.
       * 이후 확장은 이 객체에 담는다.
       */
      options?: { scheduleFromSaved?: boolean },
    ) => {
      const deviceLocation = deviceLocationOverride ?? state.device_location;
      const conversationPlaceName = getLatestConversationPlaceName(state.messages);
      dispatch({
        type: "START_CHAT_TURN",
        payload: {
          userInput: text,
          deviceLocation: deviceLocationOverride,
          deviceLocationCapturedAt,
        },
      });
      // 사용자가 입력하거나 버튼을 누른 시점부터 결과를 dispatch할 때까지를 잰다.
      const startedAt = performance.now();
      const progressEvents = [] as import("../types").AgentProgressEvent[];
      let messageStartElapsedMs: number | null = null;
      let firstMessageDeltaElapsedMs: number | null = null;
      let receivedStreamResult = false;
      let receivedStreamMessage = false;
      const controller = beginChatRequest();
      try {
        await streamChat(
          {
            user_input: text,
            language: state.language,
            session_id: state.session_id,
            device_location: deviceLocation,
            selected_search_center: loadLocationSettings().center,
            selected_current_location: loadLocationSettings().origin,
            conversation_place_name: conversationPlaceName,
            clarification_choice: clarificationChoice ?? null,
            travel_origin_override: travelOriginOverride ?? null,
            schedule_from_saved: options?.scheduleFromSaved ?? false,
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
            /* 조건 병합 직후에 온다 — 도구 조회·채점·답변 스트리밍보다 앞이라,
               발화로 위치를 바꾸면 결과를 기다리지 않고 상단 칩이 먼저 바뀐다. */
            if (event.type === "location_resolved") {
              syncLocationSettingsFromConditions(event.data);
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
            if (event.type === "error") {
              throw new ApiError(event.data);
            }
            const response = event.data.response;
            /* 위 location_resolved의 백스톱이다. SSE를 못 쓰는 환경은 단발 API로
               낮춰 done만 받으므로(streamChat의 catch), 그 경로에는 위 이벤트가
               아예 없다. 값이 같으면 헬퍼가 아무것도 쓰지 않아 두 번 불러도
               무해하다. 두 dispatch 분기 앞에 두어 스트리밍이든 아니든 지나간다. */
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
                conditions: toDisplayConditions(response.llm_output),
                mergedConditions: response.state.user_conditions,
                message: response.message,
                recommendations: response.recommendations,
                schedule: response.schedule,
                sessionId: response.state.session_id,
                status: response.llm_output.status,
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
          controller.signal,
        );
      } catch (error) {
        if (error instanceof DOMException && error.name === "AbortError") {
          // 끊긴 이유가 두 가지다. "중단" 버튼이면 오던 말풍선을 거기까지 얼려
          // 남기고(§7.2), 지난 대화를 열어 밀려난 것이면 아무것도 건드리지
          // 않는다 — 화면에는 이미 다른 대화가 그려져 있다.
          if (wasCancelledByUser(controller)) dispatch({ type: "CANCEL_CHAT_TURN" });
          return;
        }
        dispatch({
          type: "FAIL_TURN",
          payload: {
            message:
              error instanceof ApiError ? error.message : CHAT_TEXT[state.language].requestError,
            retryInput: text,
          },
        });
      } finally {
        endChatRequest(controller);
      }
      // 이 턴에서 거절이 일어났다면 서버가 보관함에서도 뺐다(saved ∩ rejected = ∅).
      // 그 결과는 AgentResponse.state(StateApplyResponse)에 실려 오지 않으므로
      // 여기서 다시 읽는다. 담긴 것이 없으면 호출 자체를 건너뛴다.
      await refreshSavedIfAny();
    },
    [
      dispatch,
      refreshSavedIfAny,
      state.device_location,
      state.language,
      state.messages,
      state.recent_follow_ups,
      state.session_id,
    ],
  );

  /*
   * 보관함 CTA. 인텐트 분류를 건너뛰도록 schedule_from_saved만 세우고, user_input에는
   * 버튼 label을 채운다 — 채팅 이력에 "무엇을 눌렀는지"가 남아야 하기 때문이다.
   *
   * requestSend가 아니라 send를 직접 부른다. 위치 재확인 게이트는 GPS 나이가
   * 기준인데, 이 턴이 쓰는 좌표는 담을 때 찍힌 스냅샷이라 지금 위치를 다시
   * 받아도 편성 결과가 달라지지 않는다.
   */
  const planFromSaved = useCallback(() => {
    const label = state.language === "en" ? "Plan a trip with these" : "이 장소들로 일정 짜기";
    void send(label, undefined, undefined, undefined, undefined, { scheduleFromSaved: true });
  }, [send, state.language]);

  const handlePhotoSelect = usePhotoSimilarSearch();

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
    void send(
      pending.text,
      pending.clarificationChoice,
      undefined,
      undefined,
      pending.travelOriginOverride,
    );
  }, [dispatch, pendingLocationRefresh, send]);

  const refreshBrowserLocation = useCallback(async () => {
    if (!pendingLocationRefresh) return;
    try {
      // 버튼 클릭이라는 사용자 제스처 안에서 호출해야 브라우저가 위치 권한을 다시 요청할 수 있다.
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
      /* 위치 갱신은 사용자가 고른 발화가 아니라 그 앞단계라 다시 보낼 값이 없다 —
         retryInput 없이 사유만 남긴다. */
      dispatch({
        type: "FAIL_TURN",
        payload: {
          message: error instanceof Error ? error.message : "현재 위치를 가져오지 못했어요.",
        },
      });
    }
  }, [dispatch, pendingLocationRefresh, send]);

  if (!hasConversation) {
    return <Navigate to="/" replace />;
  }

  async function handleFollowUp(text: string) {
    if (isLoading) return;
    if (text.trim() === STATUS_COMMAND) {
      await showStatus(text.trim());
      return;
    }
    await requestSend(text);
  }

  /* 위치 설정 화면과 같은 것을 보여준다 — 두 화면이 같은 사실을 말해야 한다.
     출발지와 검색 기준이 다르면 둘 다 보인다. 하나만 고르면 카드의 이동시간을
     어디서 쟀는지가 화면에서 사라진다(D-067, utils/locationChip 주석).

     설정이 아무것도 없을 때만 직전 턴이 해석한 위치로 떨어진다 — 대화가 이미
     있으면 서버가 그 위치를 들고 있어서 다음 발화도 거기서 찾는다.

     예전 기본값이던 "종로구"는 뺐다. 지원 지역이 종로구뿐이던 시절의 값이라
     지금은 사실이 아니고, 아무것도 모를 때 실제로 쓰이는 것은 기기 좌표다. */
  const locationChip = buildLocationChipModel(
    locationSettings,
    state.interpreted_conditions?.location_query ?? null,
    Boolean(state.device_location),
    /* 직전 턴이 사용자 위치를 몰라 검색지에서 거리를 쟀으면 칩도 그렇게 말한다
       (utils/locationChip.ts). 그런 턴이 아니거나 아직 한 턴도 없으면 null이라
       지금까지와 같은 모양이다. */
    readSubstitutedOrigin(state.auditTurns.at(-1)?.response),
  );

  return (
    <main className="relative flex h-full flex-col overflow-hidden">
      {/*
       * 헤더도 스크롤 영역 **위에 겹친다**(2026-09-09). 컴포저와 같은 이유다 —
       * 흐름 안에 두면 그 자리가 죽은 칸이 되어 내용이 헤더 밑에서 잘려 보인다.
       * 겹치는 만큼 아래 스크롤 칸이 위를 비운다(--tb-header-h, AppHeader가
       * 자기 높이를 재서 알려준다).
       */}
      <div className="absolute inset-x-0 top-0 z-20">
        <AppHeader location={locationChip} />
      </div>

      {/*
       * **스크롤은 이 칸만 한다**(2026-09-09). 전에는 main 자체가 스크롤러였고
       * 컴포저가 그 안에서 sticky 로 바닥에 붙어 있었는데, iOS 에서 소프트
       * 키보드가 뜬 동안 그 sticky 가 죽었다(ChatComposer 주석). 채팅에서는
       * 자동 바닥 붙임이 늘 맨 아래로 끌어당겨 티가 안 났을 뿐, 같은 버그가
       * 여기에도 있었다 — 키보드를 띄운 채 위로 올려 읽으면 드러난다.
       *
       * 컴포저는 이 칸의 형제이면서 absolute 로 그 위에 겹치므로, 겹치는
       * 만큼(--tb-composer-h) 아래에 자리를 비워 둔다 — 안 그러면 마지막
       * 메시지가 컴포저에 영영 가린다.
       */}
      <div className="flex min-h-0 flex-1 flex-col overflow-y-auto overscroll-none pb-[var(--tb-composer-h,0px)] pt-[var(--tb-header-h,0px)]">
        <div
          ref={messagesContainerRef}
          className="mx-auto flex w-full max-w-2xl flex-1 flex-col gap-4 px-4 pb-4"
        >
          {/* 브랜드 표기·언어 전환·신원 표시는 사이드바가 맡는다(DESIGN_SYSTEM.md
            6.17). "처음부터"는 사이드바 "홈"과 동작이 같아 중복이라 뺐다.
            화면 설명 문구도 뺐다 — 무엇을 하는 화면인지는 대화 자체로 드러난다. */}
          {/* HomePage의 같은 자리 칩과 같은 이유로 로컬 개발 서버에서만 실제로
              그린다(App.tsx가 /dev-chat 라우트 자체도 같은 조건으로 막는다).
              통째로 안 그리면 이 줄의 높이가 사라져 아래 메시지 목록이 그만큼
              따라 올라온다(2026-09-10 실사용 확인) — 자리는 항상 지키고 칩만
              숨긴다. onClick을 삼항으로 감싸는 이유는 HomePage와 같다: DEV가
              배포 빌드에서 false로 굳으면 esbuild가 그 분기를 접어
              "/dev-chat" 문자열도 함께 사라진다(빌드 산출물로 확인). */}
          <div className="flex items-center justify-end gap-3">
            <button
              type="button"
              aria-hidden={!import.meta.env.DEV}
              tabIndex={import.meta.env.DEV ? undefined : -1}
              onClick={import.meta.env.DEV ? () => navigate("/dev-chat") : undefined}
              className={`rounded-full bg-chip px-3 py-1.5 text-xs font-medium text-ink transition-colors hover:bg-sky-light ${
                import.meta.env.DEV ? "" : "invisible"
              }`}
            >
              {text.developer}
            </button>
          </div>

          <ChatMessageList
            messages={state.messages}
            showDebug={false}
            isLoading={isLoading}
            deviceLocation={state.device_location}
            onRequestMore={() => void requestSend(text.requestMore)}
            onRelaxRadius={() => void requestSend(text.relaxRadius)}
            onSelectClarificationOption={(optionId, label) => void requestSend(label, optionId)}
            // 되묻기 버튼과 달리 override 없이 문구만 보낸다 — 사용자가 직접 입력한
            // 것과 같은 경로로 분류를 태운다.
            onSelectFollowUpSuggestion={(suggestion) => void handleFollowUp(suggestion)}
            onSetLocation={() => navigate("/location")}
            onRetryTurn={(input) => void requestSend(input)}
            onToggleTravelOrigin={(toggle) => {
              const label =
                toggle.alternative_origin === "search_center"
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
        </div>
      </div>

      {/* **움직일 때만 뜨고, 움직인 방향으로 간다**(2026-09-08). 전에는 상시로
          떠 있으면서 방향을 "지금 맨 위 근처인가"로 정했다 — 버튼이 늘 본문을
          가렸고, 위로 올리는 중인데 아래로 가는 버튼이 보이는 경우가 있었다.
          판정은 useScrollEdgeButton에 있다.

          **버튼을 붙였다 떼지 않는다.** 지금은 이 띠가 흐름 밖(absolute)이라
          붙였다 떼도 스크롤 높이가 흔들리지 않지만, 그래도 항상 두고 opacity로만
          보이거나 숨긴다 — 사라지는 애니메이션이 돌 자리가 필요하다.

          원래 이유는 달랐다(2026-09-08). 그때는 이 래퍼가 sticky라 흐름에서
          자리를 차지해서, 조건부로 렌더하면 뜰 때마다 scrollHeight가 40px 늘고
          사라질 때 40px 줄었다(실측 4691 ↔ 4731). 맨 아래에 있으면 줄어든 만큼
          브라우저가 scrollTop을 깎고(3879 → 3839) 그 이벤트가 "위로 올렸다"로
          읽혀, 버튼이 다시 뜨고 또 사라지는 고리가 됐다. **그 고리는 흐름 밖으로
          나오면서 사라졌다** — 다시 흐름 안으로 들일 일이 있으면 되살아난다.

          숨을 때 aria-hidden과 tabIndex=-1을 함께 준다 — 보이지 않는 버튼이
          스크린리더에 읽히거나 탭 순서에 남지 않게.

          **sticky를 뗐다**(2026-09-09). 컴포저가 스크롤 위에 겹치는 absolute가
          되면서 이 띠도 같은 방식으로 컴포저 바로 위에 세운다 — 스크롤과 무관한
          자리라 붙일 대상이 없다. tb-keyboard-lift 로 컴포저와 같은 만큼 올라간다.

          **이동 버튼과 일정 칩이 한 띠를 쓴다**(2026-09-09). 칩은 전에 헤더
          오른쪽에 있었는데, 눌러야 하는 두 동작이 화면 위아래로 갈려 있었다.
          지금은 손이 가는 자리인 입력창 바로 위에 모인다.

          양옆 flex-1이 같은 폭이라 가운데 버튼이 **칩 너비와 무관하게** 정중앙에
          선다. 칩은 담은 곳이 없으면 스스로 사라지는데, 그때도 버튼 자리는 그대로다.
          items-end 라 높이가 다른 둘의 아랫변이 맞는다. */}
      <div className="tb-keyboard-lift pointer-events-none absolute inset-x-0 bottom-[var(--tb-composer-h,0px)] z-30 mx-auto flex w-full max-w-2xl items-end px-4">
        <div className="flex-1" />
        <motion.button
          type="button"
          animate={{
            opacity: isScrollable && isScrollButtonVisible ? 1 : 0,
            scale: isScrollable && isScrollButtonVisible ? 1 : 0.9,
          }}
          transition={{ duration: 0.18 }}
          aria-hidden={!(isScrollable && isScrollButtonVisible)}
          tabIndex={isScrollable && isScrollButtonVisible ? 0 : -1}
          onClick={scrollButtonDirection === "up" ? scrollToTop : scrollToBottom}
          aria-label={scrollButtonDirection === "up" ? "대화 맨 위로 이동" : "대화 맨 아래로 이동"}
          className={`flex h-10 w-10 items-center justify-center rounded-full border border-white bg-white/60 text-ink shadow-resting backdrop-blur-md transition-colors hover:bg-white/80 ${
            isScrollable && isScrollButtonVisible ? "pointer-events-auto" : "pointer-events-none"
          }`}
        >
          {scrollButtonDirection === "up" ? <ChevronUp size={18} /> : <ChevronDown size={18} />}
        </motion.button>
        <div className="pointer-events-auto flex flex-1 justify-end">
          <SavedPlacesChip
            onPlanFromSaved={planFromSaved}
            isLoading={isLoading}
            language={state.language}
          />
        </div>
      </div>

      <ChatComposer
        disabled={isLoading}
        onSubmit={handleFollowUp}
        onCancel={isLoading ? cancelChatRequest : undefined}
        placeholder={state.awaiting_clarification ? text.clarificationComposer : text.composer}
        language={state.language}
        onPhotoSelect={handlePhotoSelect}
      />
    </main>
  );
}
