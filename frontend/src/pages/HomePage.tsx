/*
 * 역할: 첫 사용자 질문을 입력받아 해석 API를 호출하고 채팅 흐름을 시작한다.
 * 입력: 컴포저의 user_input 문자열과 상황 버튼 선택.
 * 출력: TripContext 메시지/조건 저장, /chat 이동, 로딩/오류 상태.
 * 호출 시점: 사용자가 루트 화면에서 여행 상황을 제출할 때 호출된다.
 * TODO: 위치 권한과 추천 예시를 실제 서비스 데이터에 맞게 보강한다.
 * 근거: package_D/DESIGN_SYSTEM.md §10.1·§10.5.
 *
 * ChatPage와 같은 ChatComposer를 쓴다 — Figma가 홈도 채팅형 하단 고정 바를 쓰기
 * 때문이다. 상황 예시 칩은 입력창을 채우기만 하고 전송하지 않는다(§10.5) —
 * "개발자용으로 시작"도 같은 텍스트로 고를 수 있어야 해서다.
 */

import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError } from "../api/client";
import { streamChat, toDisplayConditions } from "../api/trip";
import { ChatComposer } from "../components/chat/ChatComposer";
import { ErrorBanner } from "../components/ErrorBanner";
import { AppHeader } from "../components/layout/AppHeader";
import { usePhotoSimilarSearch } from "../hooks/usePhotoSimilarSearch";
import { beginChatRequest, endChatRequest, wasCancelledByUser } from "../state/chatAbortController";
import {
  loadLocationSettings,
  syncLocationSettingsFromConditions,
} from "../state/locationSettings";
import { useLocationSettings } from "../hooks/useLocationSettings";
import { useTripDispatch, useTripState } from "../state/TripContext";
import { buildAgentStageTimings } from "../utils/agentTiming";
import { buildLocationChipModel, readSubstitutedOrigin } from "../utils/locationChip";
import { getBrowserDeviceLocation } from "../utils/geolocation";

const HOME_TEXT = {
  ko: {
    /*
     * 문장 가운데 한 낱말을 부각한다(2026-09-07). 이 화면이 무엇에 대한
     * 화면인지가 "일정"과 "상황" 두 낱말에 다 들어 있다 — 앞뒤를 따로 들고
     * 있어야 그 자리에만 색·굵기를 줄 수 있다.
     */
    headline: { lead: "갑자기 ", accent: "일정", tail: "이 바뀌셨나요?" },
    subtitle: {
      lead: "지금 ",
      accent: "상황",
      tail: "을 말해주시면 바로 대체 장소를 찾아볼게요.",
    },
    /* 두 문장이라 좁은 화면에서는 문장마다 한 줄씩 간다 — 렌더 쪽 주석 참고. */
    locationNotice: {
      first: "추천 시작 시 브라우저가 위치 권한을 요청합니다.",
      second: "허용한 위치는 현재 채팅 세션의 장소 탐색 기준으로 사용됩니다.",
    },
    prompts: [
      "비를 피할 실내 장소가 필요해",
      "남은 시간이 1시간 정도야",
      "근처 카페나 박물관을 찾고 싶어",
    ],
    placeholder: "트리비에게 물어보세요",
    start: "추천 시작하기",
    developer: "개발자용으로 시작",
    requestError: "입력을 처리하지 못했어요.",
  },
  en: {
    headline: { lead: "Did your ", accent: "plans", tail: " change suddenly?" },
    subtitle: {
      lead: "Tell us your ",
      accent: "situation",
      tail: ", and we’ll find a place to visit in Seoul.",
    },
    locationNotice: {
      first: "Your browser will ask for location permission before starting.",
      second: "We use it as the search point for this chat session.",
    },
    prompts: [
      "I need an indoor place to avoid the rain",
      "I have about one hour left",
      "Find a café or museum nearby",
    ],
    placeholder: "Ask Trivi",
    start: "Start recommendations",
    developer: "Start in developer view",
    requestError: "We couldn’t process your request.",
  },
} as const;

export function HomePage() {
  const dispatch = useTripDispatch();
  const state = useTripState();
  const navigate = useNavigate();
  const text = HOME_TEXT[state.language];

  /*
   * **취향은 홈에서 더 이상 읽지 않는다**(2026-09-07). 저장해 둔 취향을 보여주던
   * 줄을 지우면서 syncPreferences() 호출도 함께 뺐다 — 이제 이 기기와 계정의
   * 취향을 맞추는 것은 취향 설정 화면을 열 때뿐이다.
   */
  const locationSettings = useLocationSettings();

  const [userInput, setUserInput] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const searchByPhoto = usePhotoSimilarSearch();

  /*
   * ChatPage와 같은 훅을 쓴다(usePhotoSimilarSearch). searchByPhoto는 호출되자마자
   * (첫 await 전까지) 대화에 메시지를 동기적으로 추가한다 — 그 뒤에 이동해야
   * ChatPage의 hasConversation 가드가 "대화 없음"으로 보고 홈으로 되돌리지
   * 않는다(먼저 이동부터 하면 메시지가 아직 없어 튕겨 나간다).
   */
  async function handlePhotoSelect(file: File) {
    const pending = searchByPhoto(file);
    navigate("/chat");
    await pending;
  }

  async function startChat(input: string, targetPath = "/chat") {
    const trimmed = input.trim();
    if (!trimmed || isLoading) return;

    setIsLoading(true);
    setErrorMessage(null);

    /*
     * **출발지를 정해 뒀으면 GPS를 부르지 않는다.** 예전에는 무조건 물어보고 실패하면
     * 대화 자체를 막았다. 그런데 위치 설정에서 안국역을 골라 둔 사용자에게 기기 좌표는
     * 필요 없다 — 서버가 이동시간을 재는 출발점은 그 이름이고(D-067), 이름을 좌표로
     * 바꾸는 일은 백엔드가 한다. 필요도 없는 권한 팝업을 띄우고, 거절하면 아무것도 못
     * 하게 만들던 자리였다.
     *
     * **GPS 실패가 곧 중단이 되지 않게 한다.** 좌표 없이 보내면 백엔드가 어디서
     * 찾을지 되묻는다(location_required). 예전에는 여기서 막혀 사용자가 할 수 있는
     * 일이 없었다 — 거절했으면 되묻기에 답해서 계속 갈 수 있어야 한다.
     */
    const settings = loadLocationSettings();
    let deviceLocation: string | null = state.device_location;
    let capturedAt: number | null = null;
    if (!settings.origin && !deviceLocation) {
      try {
        // 사용자 동작 직후 호출해야 브라우저가 위치 권한 팝업을 정상적으로 표시한다.
        deviceLocation = await getBrowserDeviceLocation();
        capturedAt = Date.now();
      } catch {
        /* 좌표 없이 보낸다. 화면에 오류를 띄우지 않는 이유는 답변이 곧 되묻기로
           이어져, 오류 문구와 되묻기가 겹쳐 뜨면 무엇을 하라는 건지 흐려지기
           때문이다. */
        deviceLocation = null;
      }
    }

    // 위치 확보 직후 채팅 화면으로 이동한다. 응답을 기다리는 동안 A→B→C→D 처리
    // 단계를 동적으로 보여주고, /api/chat 완료 시 실제 결과로 교체한다.
    dispatch({ type: "RESET" });
    dispatch({
      type: "START_CHAT_TURN",
      payload: {
        userInput: trimmed,
        deviceLocation: deviceLocation ?? undefined,
        deviceLocationCapturedAt: capturedAt ?? undefined,
      },
    });
    navigate(targetPath);

    const startedAt = performance.now();
    const progressEvents = [] as import("../types").AgentProgressEvent[];
    let messageStartElapsedMs: number | null = null;
    let firstMessageDeltaElapsedMs: number | null = null;
    let receivedStreamResult = false;
    let receivedStreamMessage = false;
    // 발화를 보내자마자 /chat으로 이동하므로(위), 실제 요청은 이 화면이 언마운트된
    // 뒤에도 계속 진행된다 — ChatPage의 "중단" 버튼이 닿을 수 있도록 이 요청도
    // 같은 전역 컨트롤러에 등록한다(state/chatAbortController.ts).
    const controller = beginChatRequest();
    try {
      await streamChat(
        {
          user_input: trimmed,
          language: state.language,
          session_id: null,
          device_location: deviceLocation,
          selected_search_center: settings.center,
          selected_current_location: settings.origin,
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
            // done 뒤에 오는 유일한 이벤트다. 첫 턴은 여기서 /chat으로 넘어간 뒤에
            // 도착할 수 있는데, TripContext가 라우터 위에 있어 그대로 반영된다.
            dispatch({
              type: "APPEND_FOLLOW_UP_SUGGESTIONS",
              payload: { suggestions: event.data.suggestions },
            });
            return;
          }
          if (event.type === "error") throw new ApiError(event.data);

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
              userInput: trimmed,
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
        // ChatPage와 같은 이유다 — "중단" 버튼일 때만 뒷정리한다.
        if (wasCancelledByUser(controller)) dispatch({ type: "CANCEL_CHAT_TURN" });
        return;
      }
      /* 이 시점에는 이미 /chat으로 넘어가 있다(위 navigate). 그래서 홈의 배너가
         아니라 대화 안에 남긴다 — 사용자가 보고 있는 화면이 거기다. */
      dispatch({
        type: "FAIL_TURN",
        payload: {
          message: error instanceof ApiError ? error.message : text.requestError,
          retryInput: trimmed,
        },
      });
    } finally {
      endChatRequest(controller);
    }
  }

  /*
   * ChatPage와 같은 계산이다(같은 세션 조건이 아직 남아 있으면 홈으로 돌아와도
   * 보여준다) — 브라우저 뒤로가기로 대화가 있던 홈에 돌아오는 경우가 그렇다.
   * "홈"을 새로 눌렀다면 사이드바 goHome()이 RESET을 먼저 보내 이 값도 비운다.
   * 아직 해석된 지명이 없으면(첫 진입) 실제 서비스 지원 지역인 "종로구"를
   * 기본값으로 쓴다 — 헤더에 위치 버튼이 항상 보여야 한다.
   */
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
       * 키보드가 뜬 동안 그 sticky 가 죽어 내용과 같이 흘러갔다(ChatComposer
       * 주석). 컴포저는 이 칸의 형제이면서 absolute 로 그 위에 겹치므로, 겹치는
       * 만큼(--tb-composer-h, 컴포저가 자기 높이를 재서 알려준다) 아래에 자리를
       * 비워 둔다 — 안 그러면 마지막 내용이 컴포저에 영영 가린다.
       */}
      <div className="flex min-h-0 flex-1 flex-col overflow-y-auto overscroll-none pb-[var(--tb-composer-h,0px)] pt-[var(--tb-header-h,0px)]">
        {/*
         * 세로 간격을 gap 하나로 고르게 주지 않는다. 헤드라인이 위를, 오브가 남는
         * 가운데를 갖고, 나머지는 컴포저 쪽으로 내려붙는다 — 요소를 빼거나 순서를
         * 바꾸지 않고 **남는 공간을 어디에 줄지**만 정한 것이다(2026-09-07).
         */}
        {/* min-h-0 이 있어야 아래 오브 칸이 남는 높이에 맞춰 줄어든다. flex 자식은
          기본이 min-height:auto 라 내용보다 작아지지 않고, 그러면 짧은 화면에서
          오브가 칸을 뚫고 나가 홈 전체가 스크롤된다(2026-09-07 실측). */}
        <div className="relative z-10 mx-auto flex w-full min-h-0 max-w-2xl flex-1 flex-col px-4 pb-4 pt-2">
          {/* 칩은 로컬 개발 서버에서만 실제로 그린다 — 배포 빌드에서는 같은
              노드를 안 보이게·못 누르게 바꿔치기만 한다. 통째로 안 그리면
              (`{DEV && <div>...}`) 이 줄이 차지하던 높이가 사라져 아래 오브가
              그만큼 따라 올라온다(2026-09-10 실사용 확인) — 자리는 항상 지키고
              칩만 숨긴다.

              onClick은 삼항으로 감싼다. `import.meta.env.DEV`가 배포 빌드에서
              정적으로 false로 굳으면 esbuild가 `false ? A : B`를 B로 접어 A쪽의
              "/dev-chat" 문자열도 함께 사라진다 — `{DEV && ...}`로 감쌌을 때와
              같은 방식이고, 실제 빌드 산출물을 grep해 사라지는 것을 확인했다.
              라우트 자체도 App.tsx에서 막혀 있어 어차피 눌려도 갈 곳이 없다. */}
          <div className="flex items-center justify-end">
            {/* 채우기만 하고 전송은 안 한다(§10.5) — 입력이 있어야 의미 있어
              비어 있으면 비활성. */}
            <button
              type="button"
              disabled={!import.meta.env.DEV || isLoading || !userInput.trim()}
              aria-hidden={!import.meta.env.DEV}
              tabIndex={import.meta.env.DEV ? undefined : -1}
              onClick={
                import.meta.env.DEV ? () => void startChat(userInput, "/dev-chat") : undefined
              }
              className={`rounded-full border border-border px-3 py-1.5 text-xs font-medium text-muted transition-colors hover:border-sky-soft hover:text-ink disabled:opacity-50 ${
                import.meta.env.DEV ? "" : "invisible"
              }`}
            >
              {text.developer}
            </button>
          </div>

          {/*
           * 오브가 맨 위에 오고 글이 그 아래에 가운데로 붙는다(2026-09-07).
           *
           * 오브는 장식이라 aria-hidden 이다 — 누르는 기능도 움직임도 없다.
           * 화면이 낮으면 이미지 자체가 작아진다(.tb-orb__img).
           */}
          {/* pt 로 히어로를 아래로 내린다(2026-09-07). 아래 flex-1 칸이 그만큼
            줄어들어 아래쪽 묶음은 제자리에 남는다. */}
          <div className="flex flex-col items-center pt-10 text-center">
            <img
              src="/glass-object.png"
              alt=""
              aria-hidden
              width={76}
              height={75}
              decoding="async"
              className="tb-orb__img"
            />
            {/*
             * 한 줄이다(2026-09-07). 두 줄로 쪼개 놓으면 가운데 정렬에서 윗줄이
             * 짧아 축이 흔들려 보인다.
             *
             * 좁은 화면에서 접히지 않게 글자를 줄인다 — 24px 이면 "갑자기 일정이
             * 바뀌셨나요?" 가 241px 이라 360px 화면의 본문 폭(328px)에 들어간다.
             *
             * **nowrap 은 쓰지 않는다.** 영어 문구가 더 길어서(Did your plans change
             * suddenly?) 안 접히는 대신 칸을 넘어간다 — 한 줄로 만들려다 가로로
             * 삐져나가면 더 나쁘다. 안 들어가는 날에는 얌전히 접히게 둔다.
             *
             * 부각하는 낱말은 "일정"과 "상황" 둘 다 브랜드색이다(2026-09-07 사용자
             * 결정). 이 화면이 무엇에 대한 화면인지가 그 두 낱말에 다 들어 있다.
             */}
            <h1 className="mt-5 text-2xl font-bold leading-[1.32] tracking-[-0.035em] text-ink sm:text-[30px]">
              {text.headline.lead}
              <span className="text-brand">{text.headline.accent}</span>
              {text.headline.tail}
            </h1>
            <p className="mt-3 text-[13px] leading-relaxed text-muted sm:text-sm">
              {text.subtitle.lead}
              <span className="font-semibold text-brand">{text.subtitle.accent}</span>
              {text.subtitle.tail}
            </p>
            <div className="mt-6 flex flex-wrap items-start justify-center gap-2">
              {text.prompts.map((prompt) => (
                <button
                  key={prompt}
                  type="button"
                  disabled={isLoading}
                  onClick={() => setUserInput(prompt)}
                  /* 프로스티드 — ChatComposer·AppHeader 가 이미 쓰는 언어다. 유리
                   오브가 뜬 화면에서 누를 수 있는 것도 같이 떠 보이게 한다. */
                  className="rounded-full border border-white bg-white/60 px-4 py-2.5 text-left text-sm font-medium text-ink shadow-resting backdrop-blur-md transition-colors hover:bg-white/80 disabled:opacity-50"
                >
                  {prompt}
                </button>
              ))}
            </div>
          </div>

          {/* 남는 세로 공간은 여기가 갖는다 — 위는 히어로, 아래는 컴포저에 붙는다. */}
          <div className="min-h-3 flex-1" />

          {errorMessage && <ErrorBanner message={errorMessage} />}

          {/*
           * 위치 권한 고지. 채팅 바 바로 위다(2026-09-07) — 권한을 실제로 묻는 것은
           * 여기서 보내는 순간이라, 누르기 직전에 읽히는 자리가 맞다.
           *
           * 예전에는 통짜 파란 패널이라 제목 다음으로 큰 색 덩어리였다. 고지는 먼저
           * 읽히는 글이 아니라 필요할 때 찾는 글이라 잔글씨로 내렸다.
           *
           * **text-muted(대비 4.76:1)보다 옅은 text-gray-400(2026-09-07)** — 실제
           * 대비는 2.56:1로 WCAG AA(4.5:1)에 못 미친다. 11px 잔글씨라 원래도 본문
           * 기준을 넘기지 못했었지만, 이 값은 명백히 더 내려간다. 필수로 읽어야
           * 하는 안내가 아니라(안 읽어도 기능은 그대로 동작한다) 이 화면에서 가장
           * 낮은 우선순위로 두기로 한 사용자 결정을 존중해 그대로 적용한다 —
           * 다른 잔글씨(RecommendationDetailPreviewModal의 11px 캡션)에도 이미
           * 쓰이는 값이라 새 색을 들이는 것도 아니다.
           *
           * **break-keep 이 있어야 낱말이 안 쪼개진다.** 한글은 기본값에서 아무
           * 글자에서나 줄이 갈려 "채팅 세 / 션의" 처럼 끊겼다(360px 실측). keep-all
           * 은 띄어쓰기에서만 끊는다.
           *
           * text-balance 는 뺐다 — 문장마다 block 이 되면 각 문장 안에서만 균형을
           * 맞추므로 두 번째 문장이 두 줄로 쪼개질 여지만 생긴다.
           */}
          <p className="mt-3 break-keep text-center text-[11px] leading-relaxed text-gray-400">
            {/*
             * 문장마다 한 줄이다. 좁은 화면에서는 block, sm 이상에서는 inline —
             * 넓으면 두 문장이 한 줄에 다 들어간다.
             *
             * 그냥 흘려보내면 두 번째 문장 첫머리("허용한")가 첫 줄 끝에 붙어
             * 문장이 어디서 갈리는지 안 보였다(2026-09-07).
             */}
            <span className="block sm:inline">{text.locationNotice.first}</span>{" "}
            <span className="block sm:inline">{text.locationNotice.second}</span>
          </p>
        </div>
      </div>

      <ChatComposer
        disabled={isLoading}
        value={userInput}
        onChange={setUserInput}
        onSubmit={async (submitted) => {
          setErrorMessage(null);
          await startChat(submitted);
        }}
        placeholder={text.placeholder}
        language={state.language}
        sendLabel={text.start}
        onPhotoSelect={handlePhotoSelect}
      />
    </main>
  );
}
