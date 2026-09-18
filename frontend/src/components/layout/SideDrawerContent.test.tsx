/*
 * 역할: 사이드바(셸 + SideDrawerContent)의 동작을 앱 통합 수준에서 검증한다.
 * 입력: 렌더된 App, 사용자 클릭·입력.
 * 출력: 즐겨찾기·채팅 히스토리 편집, 접힘 전환, 라우트 이동에 대한 assertion.
 * 호출 시점: vitest 실행 시.
 *
 * **즐겨찾기와 채팅 히스토리는 사는 곳이 다르다.** 즐겨찾기는 아직 localStorage
 * 목업이고(state/sidebarStorage.ts), 채팅 히스토리는 계정에서 온다
 * (GET /api/sessions, TP-222 후속). 그래서 씨앗도 각각 다른 곳에 심는다.
 *
 * 이름 바꾸기·삭제는 화면만 바꾸는 것이 아니라 서버에도 보내야 한다 — 화면에서만
 * 사라지고 서버에 남으면 다음에 열었을 때 되살아난다.
 */

import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test, vi } from "vitest";
import App from "../../App";
import { resetChatSessionsCache } from "../../state/chatSessions";
import { resetFavoritesSync } from "../../state/favoritesSync";
import { resetSavedSchedulesCache } from "../../state/savedSchedules";
import { isDetachedRequest } from "../../state/chatAbortController";
import { GUEST_SESSION, setMockSession } from "../../test/supabaseMock";

const SEED_FAVORITES = [
  { id: "fav-1", label: "회사 (역삼동)" },
  { id: "fav-2", label: "집 (성수동)" },
];

/** 계정에 쌓인 대화. 서버가 주는 모양 그대로다. */
const server = vi.hoisted(() => ({
  sessions: [] as {
    session_id: string;
    title: string;
    location: string | null;
    last_active_at: string;
  }[],
  renamed: [] as { id: string; title: string }[],
  deleted: [] as string[],
  scheduleRenamed: [] as { id: string; title: string }[],
  scheduleDeleted: [] as string[],
  resumed: [] as string[],
  /* 한 턴의 화면 기록. payload는 그 턴의 AgentResponse 그대로다. */
  transcript: {
    session_id: "chat-1",
    run_id: "run_1",
    user_id: null,
    user_input: "비 오는데 어디 갈까",
    recorded_at: "2026-09-03T08:58:23+09:00",
    payload: {
      llm_output: { intent: "RECOMMEND", status: "complete" },
      state: { session_id: "chat-1", run_id: "run_1" },
      message: "실내를 찾아볼게요",
      recommendations: {
        recommendations: [
          {
            place_id: "p1",
            name: "국립중앙박물관",
            category: "문화시설",
            distance_km: 1.2,
            remaining_minutes: 180,
            environment_type: "indoor",
            recommendation_reason: "비 오는 날 실내에서 오래 머물기 좋아요",
            explanations: [],
            warnings: [],
            score: 0.81,
            feature_scores: {},
            weights_used: {},
            taste_evidence: [],
          },
        ],
        unverified_recommendations: [],
        elapsed_ms: 1200,
      },
    },
  },
  /** 이어서 보낸 발화가 실어 나간 session_id. null이면 새 대화로 간 것이다. */
  chatSessionIds: [] as (string | null)[],
  /** GET /api/sessions를 실제로 부른 횟수. 사이드바 두 벌이 겹쳐 부르는지 본다. */
  listCalls: 0,
  /** 계정에 저장한 일정(SCHEDULE 카드 2). 대화와 별도 저장소라 따로 심는다. */
  schedules: [] as {
    id: string;
    title: string;
    session_id: string | null;
    created_at: string;
    updated_at: string;
  }[],
  /*
   * 두 턴짜리 화면 기록. 두 턴 모두 후속 질문을 달아 둔다 — 복원했을 때
   * 마지막 턴에만 버튼이 남아야 한다.
   */
  transcriptTurns() {
    return [
      {
        ...server.transcript,
        user_input: "첫 질문",
        payload: {
          ...server.transcript.payload,
          message: "첫 답변",
          recommendations: null,
          suggested_follow_ups: ["첫 턴의 후속 질문"],
        },
      },
      {
        ...server.transcript,
        payload: {
          ...server.transcript.payload,
          suggested_follow_ups: ["마지막 턴의 후속 질문"],
        },
      },
    ];
  },
  /** 켜면 기록이 말풍선보다 모자란 대화를 흉내 낸다(저장이 한 번 실패한 경우). */
  partialTranscript: false,
  /** 켜면 지난 대화 열기가 실패한다. */
  resumeFails: false,
  /** 켜면 streamChat이 응답을 붙들고 있는다 — 답변 대기 중 상황을 만든다. */
  holdStream: false,
  pending: null as ((event: { type: string; data: unknown }) => void) | null,
  releaseStream: null as (() => void) | null,
}));

vi.mock("../../api/trip", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api/trip")>();
  return {
    ...actual,
    fetchChatSessions: async () => {
      server.listCalls += 1;
      return { sessions: server.sessions };
    },
    fetchSavedSchedules: async () => ({ items: server.schedules }),
    /* 사이드바는 조회가 아니라 resume을 부른다 — 만료된 대화를 되살려야 이어
       물었을 때 같은 세션에 붙는다. resume의 응답은 항상 resumable: true다. */
    resumeChatSession: async (sessionId: string) => {
      const found = server.sessions.find((item) => item.session_id === sessionId);
      if (!found || server.resumeFails) throw new Error("없는 대화");
      server.resumed.push(sessionId);
      return {
        session_id: sessionId,
        title: found.title,
        /* 추천은 그 턴이 기록되기 전에 남는다(실측 평균 97초 먼저). */
        recommendations: [
          {
            place_id: "p1",
            run_id: "run_1",
            name: "국립중앙박물관",
            rank: 1,
            distance_km: 1.2,
            environment_type: "indoor",
            reason: null,
            shown_at: "2026-09-03T08:58:23+09:00",
          },
        ],
        /* 화면 기록. 있으면 화면은 turns/recommendations 대신 이것만 쓴다.
           chat-1에만 둬서 두 경로를 한 파일에서 함께 본다. */
        /* 화면 기록으로 되돌릴 수 있는지는 **백엔드가 판정해서 알려준다.**
           chat-1은 기록이 온전하고, chat-2는 기록이 없는 옛 대화다.
           partialTranscript를 켜면 저장이 한 번 실패한 대화가 된다. */
        restore_from_messages: sessionId === "chat-1" && !server.partialTranscript,
        /* **온전하지 않아도 기록은 함께 온다.** 서버는 있는 것을 그대로 주고
           쓸지 말지는 restore_from_messages가 정한다 — 화면이 messages가
           비었는지로 판단하면 이 경우를 놓친다. */
        messages: sessionId === "chat-1" ? server.transcriptTurns() : [],
        turns: server.partialTranscript
          ? [
              {
                user_input: "첫 질문",
                assistant_message: "첫 답변",
                intent: "RECOMMEND",
                place_names: [],
                at: "2026-09-03T09:00:00+09:00",
              },
              {
                user_input: "빠진 질문",
                assistant_message: "빠진 답변",
                intent: "RECOMMEND",
                place_names: [],
                at: "2026-09-03T09:05:00+09:00",
              },
            ]
          : [
              {
                user_input: "비 오는데 어디 갈까",
                assistant_message: "실내를 찾아볼게요",
                intent: "RECOMMEND",
                place_names: [],
                at: "2026-09-03T09:00:00+09:00",
              },
            ],
        last_active_at: found.last_active_at,
        resumable: true,
      };
    },
    /*
     * 한 턴을 끝까지 흉내 낸다. done을 보내지 않으면 phase가 ready가 되지 않아
     * "턴이 끝났을 때"에 걸린 동작(사이드바 목록 갱신)을 볼 수 없다.
     */
    streamChat: async (
      request: { session_id: string | null; user_input: string },
      onEvent: (event: { type: string; data: unknown }) => void,
      signal?: AbortSignal,
    ) => {
      /* 응답이 늦게 오는 상황을 만든다. 테스트가 직접 done을 쏠 수 있게 콜백을
         넘겨두고, 실제 SSE와 같이 끊기면 AbortError로 끝난다. */
      if (server.holdStream) {
        /* 실제 streamChat과 같은 판정이다 — 끊겼거나 화면에서 떼어진 요청의
           이벤트는 흘리지 않는다. 떼어내기는 요청을 끊지 않으므로 aborted만
           보면 이 경우를 놓친다. */
        server.pending = (event) => {
          if (signal?.aborted || isDetachedRequest(signal)) return;
          onEvent(event);
        };
        await new Promise<void>((resolve, reject) => {
          server.releaseStream = resolve;
          signal?.addEventListener("abort", () =>
            reject(new DOMException("aborted", "AbortError")),
          );
        });
        return;
      }
      server.chatSessionIds.push(request.session_id);
      /* 서버는 첫 턴에 세션을 만들고 그 발화를 제목으로 붙인다. */
      const sessionId = request.session_id ?? "chat-new";
      if (!server.sessions.some((item) => item.session_id === sessionId)) {
        server.sessions = [
          {
            session_id: sessionId,
            title: request.user_input,
            location: null,
            last_active_at: "2026-09-03T10:00:00+09:00",
          },
          ...server.sessions,
        ];
      }
      onEvent({
        type: "done",
        data: {
          elapsed_ms: 10,
          response: {
            ...server.transcript.payload,
            state: { session_id: sessionId, run_id: "run_new" },
            message: "찾아볼게요",
            recommendations: null,
          },
        },
      });
    },
    renameChatSession: async (sessionId: string, title: string) => {
      server.renamed.push({ id: sessionId, title });
      server.sessions = server.sessions.map((item) =>
        item.session_id === sessionId ? { ...item, title } : item,
      );
      return { ...server.sessions[0], title };
    },
    deleteChatSession: async (sessionId: string) => {
      server.deleted.push(sessionId);
      server.sessions = server.sessions.filter((item) => item.session_id !== sessionId);
      return { session_id: sessionId, deleted: true };
    },
    /* 저장한 일정은 대화와 다른 저장소다 — 기록도 따로 받아 둬야 한쪽 동작이
       다른 쪽 배열에 섞여 통과하는 일이 없다(TP-233). */
    renameSavedSchedule: async (scheduleId: string, title: string) => {
      server.scheduleRenamed.push({ id: scheduleId, title });
      server.schedules = server.schedules.map((item) =>
        item.id === scheduleId ? { ...item, title } : item,
      );
      return { ...server.schedules[0], title };
    },
    deleteSavedSchedule: async (scheduleId: string) => {
      server.scheduleDeleted.push(scheduleId);
      server.schedules = server.schedules.filter((item) => item.id !== scheduleId);
      return { id: scheduleId, deleted: true };
    },
  };
});

beforeEach(() => {
  sessionStorage.clear();
  localStorage.clear();
  localStorage.setItem("tb_favorites", JSON.stringify(SEED_FAVORITES));
  window.history.pushState({}, "", "/");
  resetChatSessionsCache();
  resetFavoritesSync();
  resetSavedSchedulesCache();
  server.schedules = [];
  server.sessions = [
    {
      session_id: "chat-1",
      title: "비 오는 날 아이와 함께 갈 곳",
      location: null,
      last_active_at: "2026-09-03T09:00:00+09:00",
    },
    {
      session_id: "chat-2",
      title: "갑자기 뜬 2시간, 카페 추천",
      location: "성수동",
      last_active_at: "2026-09-02T09:00:00+09:00",
    },
  ];
  server.renamed = [];
  server.deleted = [];
  server.scheduleRenamed = [];
  server.scheduleDeleted = [];
  server.resumed = [];
  server.chatSessionIds = [];
  server.listCalls = 0;
  server.partialTranscript = false;
  server.resumeFails = false;
  server.holdStream = false;
  server.pending = null;
  server.releaseStream = null;
});

/*
 * 렌더 직후에는 게스트 세션 조회가 끝나지 않아 관문(RequireUser)이 로딩 문구만
 * 그린다. 셸이 붙을 때까지 기다린 뒤에 사이드바를 만진다(App.test.tsx와 같은 이유).
 */
async function renderApp() {
  render(<App />);
  await screen.findByRole("button", { name: "추천 시작하기" });
  /* 채팅 히스토리는 계정에서 비동기로 온다. 셸만 기다리면 목록이 아직 비어 있어
     "메뉴" 버튼을 찾을 수 없다. */
  await waitFor(() =>
    expect(within(sidebar()).getByText("비 오는 날 아이와 함께 갈 곳")).toBeInTheDocument(),
  );
}

/** 사이드바는 셸 안에 상시 렌더된다. jsdom에는 CSS가 없어 폭 분기와 무관하게 잡힌다. */
function sidebar() {
  return screen.getByRole("complementary");
}

/*
 * **계정 팝업은 로그인한 사람에게만 있다**(§6, 2026-09-06). 게스트 자리에는
 * "로그인" 버튼 하나뿐이라, 팝업을 만지는 테스트는 먼저 계정 세션으로 갈아 끼운다.
 */
const ACCOUNT_SESSION = {
  ...GUEST_SESSION,
  user: { ...GUEST_SESSION.user, is_anonymous: false, email: "trip@example.com" },
} as typeof GUEST_SESSION;

async function renderAppAsAccount() {
  setMockSession(ACCOUNT_SESSION);
  await renderApp();
}

/*
 * 계정 항목(로그아웃)은 사이드바 바닥의 계정 버튼을 눌러야 나온다(§6).
 *
 * 이름으로 찾지 않는다 — 그 버튼의 이름은 신원 표시 자체라(이름·이메일) 표시가
 * 바뀔 때마다 테스트가 같이 흔들린다. 사이드바에서 메뉴를 여는 버튼은 이것 하나다.
 */
function accountButton() {
  return within(sidebar()).getByRole("button", { expanded: false });
}

/* setup()으로 만든 인스턴스와 전역 userEvent를 둘 다 받는다 — 이 파일은 두 방식을
   섞어 쓴다. 필요한 것은 click 하나뿐이라 그만 받는다. */
async function openAccountMenu(user: { click: (element: Element) => Promise<void> }) {
  await user.click(accountButton());
}

/*
 * 로그아웃은 이 기기에 남은 값을 전부 지워야 한다 — 같은 브라우저에서 다음 사람이
 * 앞사람의 취향·즐겨찾기를 이어받으면 안 된다. 화면에서 실제로 눌러 확인한다.
 */
test("로그아웃하면 이 기기의 취향·즐겨찾기가 남지 않는다", async () => {
  const user = userEvent.setup();
  localStorage.setItem(
    "tb_preferences",
    JSON.stringify([{ label: "조용한 곳", source: "preference", codes: ["quiet"] }]),
  );
  sessionStorage.setItem(
    "tb_location_settings",
    JSON.stringify({ origin: null, center: "안국역" }),
  );
  await renderAppAsAccount();

  await openAccountMenu(user);
  await user.click(within(sidebar()).getByRole("menuitem", { name: /로그아웃/ }));

  await waitFor(() => expect(localStorage.getItem("tb_preferences")).toBeNull());
  expect(localStorage.getItem("tb_favorites")).toBeNull();
  expect(sessionStorage.getItem("tb_location_settings")).toBeNull();
});

/*
 * 즐겨찾기 테스트 4개를 지웠다(2026-09-04) — 목록이 사이드바에서 빠졌다. 옮기지
 * 않은 이유는 **위치 설정 화면이 이미 같은 일을 더 많이 하고 그쪽 테스트가 있기**
 * 때문이다(`pages/LocationPage.test.tsx`).
 *
 * 그중 "위치 설정 화면에서 지운 즐겨찾기가 사이드바에서도 바로 빠진다"는 두 화면이
 * `useFavorites`로 저장소를 공유하는 것을 잠근 가드였다(jjinsword,
 * `fix: 즐겨찾기를 두 화면이 함께 보게 한다`). 소비자가 위치 화면 하나만 남아
 * 검증 대상이 없어졌다 — **훅의 동기화 자체는 남겨 뒀다.** 지우면 나중에 다른
 * 화면이 즐겨찾기를 쓸 때 같은 버그가 다시 난다.
 *
 * "로그아웃하면 이 기기의 취향·즐겨찾기가 남지 않는다"는 남겼다 —
 * localStorage만 보므로 사이드바 UI와 무관하다.
 */

test("채팅 히스토리 이름을 바꾸면 새 이름이 남는다", async () => {
  const user = userEvent.setup();
  await renderApp();

  await user.click(
    within(sidebar()).getByRole("button", { name: "비 오는 날 아이와 함께 갈 곳 메뉴" }),
  );
  await user.click(screen.getByRole("menuitem", { name: "이름 바꾸기" }));

  const input = screen.getByRole("textbox", { name: "대화 이름" });
  await user.clear(input);
  await user.type(input, "비 오는 날 실내 코스{Enter}");

  expect(within(sidebar()).getByText("비 오는 날 실내 코스")).toBeInTheDocument();
  expect(within(sidebar()).queryByText("비 오는 날 아이와 함께 갈 곳")).not.toBeInTheDocument();
});

test("채팅 히스토리를 삭제하면 목록에서 빠진다", async () => {
  const user = userEvent.setup();
  await renderApp();

  await user.click(
    within(sidebar()).getByRole("button", { name: "갑자기 뜬 2시간, 카페 추천 메뉴" }),
  );
  await user.click(screen.getByRole("menuitem", { name: "삭제" }));

  expect(within(sidebar()).queryByText("갑자기 뜬 2시간, 카페 추천")).not.toBeInTheDocument();
});

test("사이드바를 접으면 레일만 남고 다시 펼칠 수 있다", async () => {
  const user = userEvent.setup();
  await renderApp();

  await user.click(screen.getByRole("button", { name: "사이드바 접기" }));

  /* 접힘 레일에는 아이콘만 남는다 — 목록 제목이 사라진다. 지표로 쓰던 "즐겨찾기"
     제목이 사이드바에서 빠져(2026-09-04) "채팅 히스토리"로 바꿨다. */
  expect(within(sidebar()).queryByText("채팅 히스토리")).not.toBeInTheDocument();
  /* 레일 아이콘의 이름은 title·aria-label로만 남는다. 펼침 쪽과 같은 문구여야
     한다 — 라벨이 두 곳에 따로 적혀 있어 한쪽만 바뀌기 쉽다(2026-09-04에 "홈"을
     "새 채팅"으로 바꿨을 때 레일 쪽이 테스트에 안 걸렸다). */
  expect(within(sidebar()).getByRole("button", { name: "새 채팅" })).toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: "사이드바 펼치기" }));
  expect(within(sidebar()).getByText("채팅 히스토리")).toBeInTheDocument();
});

test("취향 설정으로 이동하면 취향 선택 화면이 뜬다", async () => {
  const user = userEvent.setup();
  await renderApp();

  await user.click(within(sidebar()).getByRole("button", { name: "취향 설정" }));

  expect(screen.getByText(/끌리시나요/)).toBeInTheDocument();
});

/* 화면에서만 지우고 서버에 남기면 다음에 열었을 때 되살아난다. */
test("이름 바꾸기와 삭제가 서버까지 간다", async () => {
  const user = userEvent.setup();
  await renderApp();

  await user.click(
    within(sidebar()).getByRole("button", { name: "비 오는 날 아이와 함께 갈 곳 메뉴" }),
  );
  await user.click(screen.getByRole("menuitem", { name: "이름 바꾸기" }));
  const input = screen.getByRole("textbox", { name: "대화 이름" });
  await user.clear(input);
  await user.type(input, "새 이름{Enter}");

  await waitFor(() => expect(server.renamed).toEqual([{ id: "chat-1", title: "새 이름" }]));

  await user.click(
    within(sidebar()).getByRole("button", { name: "갑자기 뜬 2시간, 카페 추천 메뉴" }),
  );
  await user.click(screen.getByRole("menuitem", { name: "삭제" }));

  await waitFor(() => expect(server.deleted).toEqual(["chat-2"]));
});

/* 장소 이름이 아니라 위치다 — "블루보틀 성수"는 그 대화가 무엇이었는지
   말해주지 않지만 "성수동"은 말해준다. */
test("대화의 위치가 목록에 함께 보인다", async () => {
  await renderApp();

  expect(within(sidebar()).getByText("성수동")).toBeInTheDocument();
});

/* 목록만 만들고 못 열게 두면 "눌러도 아무 일이 없는" 화면이 된다. */
test("히스토리를 누르면 지난 대화가 채팅 화면에 펼쳐진다", async () => {
  const user = userEvent.setup();
  await renderApp();

  await user.click(
    within(sidebar()).getByRole("button", { name: "비 오는 날 아이와 함께 갈 곳 대화 열기" }),
  );

  expect(await screen.findByText("비 오는데 어디 갈까")).toBeInTheDocument();
  expect(screen.getByText("실내를 찾아볼게요")).toBeInTheDocument();
});

/* 보이는 말풍선은 최근 5턴뿐이라 대화 전체가 아니다. 말없이 두면 사용자는
   이게 전부인 줄로 안다. */
test("옛 대화를 열면 앞부분이 없다고 밝힌다", async () => {
  const user = userEvent.setup();
  await renderApp();

  await user.click(
    within(sidebar()).getByRole("button", { name: "갑자기 뜬 2시간, 카페 추천 대화 열기" }),
  );

  expect(await screen.findByText(/앞부분은 남아 있지 않아요/)).toBeInTheDocument();
});

/*
 * 이 파일에서 가장 중요한 테스트다. 화면 기록을 따로 저장한 목적이 이것이다 —
 * 지난 대화가 "비슷하게"가 아니라 **그때 그대로** 나와야 한다. 실시간과 같은
 * buildAgentMessages를 태우므로 진짜 추천 카드가 그려진다.
 */
test("화면 기록이 있으면 그때 본 화면 그대로 펼쳐진다", async () => {
  const user = userEvent.setup();
  await renderApp();

  await user.click(
    within(sidebar()).getByRole("button", { name: "비 오는 날 아이와 함께 갈 곳 대화 열기" }),
  );

  expect(await screen.findByText("실내를 찾아볼게요")).toBeInTheDocument();
  /* 근사치 카드("그때 추천받은 곳")가 아니라 실제 추천 카드다 — 순위와 담기
     토글은 실제 카드에만 있다(PastRecommendationMessage는 둘 다 그리지 않는다).
     전에는 추천 이유 문구로 이걸 가렸는데, 2026-09-08에 카드에서 그 줄을 뺐다. */
  expect(screen.getByText("추천 장소")).toBeInTheDocument();
  expect(screen.getByText("국립중앙박물관")).toBeInTheDocument();
  expect(screen.getByText("1위")).toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: "국립중앙박물관 보관함에 담기" }),
  ).toBeInTheDocument();
  expect(screen.queryByText("그때 추천받은 곳")).not.toBeInTheDocument();
});

/* 화면 기록이 쌓이기 전의 옛 대화. 손실은 있지만 통째로 안 보이는 것보다 낫다. */
test("화면 기록이 없는 옛 대화는 저장된 조각으로 펼쳐진다", async () => {
  const user = userEvent.setup();
  await renderApp();

  await user.click(
    within(sidebar()).getByRole("button", { name: "갑자기 뜬 2시간, 카페 추천 대화 열기" }),
  );

  expect(await screen.findByText("그때 추천받은 곳")).toBeInTheDocument();
  expect(screen.getByText("국립중앙박물관")).toBeInTheDocument();
  /* 저장된 값만 보여준다 — 거리·실내외는 있고 점수·운영시간은 기록이 없다. */
  expect(screen.getByText("1.2km · 실내")).toBeInTheDocument();
});

/* 옛 대화만 "마지막 부분"이다. 전체가 나오는데 그렇게 말하면 거짓이 된다. */
/* 기록이 온전한 대화에는 시각만 뜨고, 빠진 게 있다는 말은 붙지 않는다. */
test("화면 기록이 온전하면 앞부분이 없다고 말하지 않는다", async () => {
  const user = userEvent.setup();
  await renderApp();

  await user.click(
    within(sidebar()).getByRole("button", { name: "비 오는 날 아이와 함께 갈 곳 대화 열기" }),
  );

  /* 날짜 표기는 오늘·어제면 그렇게 부르므로 실행 날짜에 따라 달라진다.
     이 테스트가 보는 것은 "시각이 뜨고, 빠진 게 있다는 말은 없다"다.
     **시각도 브라우저 시간대를 따른다** — KST 기준으로 적어 두면 UTC로 도는
     CI에서만 깨진다. 기록된 시각을 화면과 같은 방식으로 포맷해 견준다. */
  const shownAt = new Date(server.transcript.recorded_at)
    .toLocaleTimeString("ko-KR", { hour: "numeric", minute: "2-digit" })
    .replace(/\s+/g, " ");
  expect(await screen.findByText(new RegExp(shownAt))).toBeInTheDocument();
  expect(screen.queryByText(/앞부분은 남아 있지 않아요/)).not.toBeInTheDocument();
});

/*
 * 이 파일에서 가장 중요한 테스트다. 지난 대화를 여는 목적은 읽는 것이 아니라
 * 이어가는 것이고, 그건 다음 발화가 **같은 session_id**를 실어 나가야만
 * 성립한다. 비어서 나가면 백엔드가 새 세션을 만들어 목록에 줄이 하나 더 생긴다.
 */
test("지난 대화를 열고 이어 물으면 같은 세션으로 나간다", async () => {
  const user = userEvent.setup();
  await renderApp();

  await user.click(
    within(sidebar()).getByRole("button", { name: "비 오는 날 아이와 함께 갈 곳 대화 열기" }),
  );
  await screen.findByText("비 오는데 어디 갈까");
  await user.type(screen.getByPlaceholderText("트리비에게 물어보세요"), "그럼 근처 카페는?{Enter}");

  await waitFor(() => expect(server.chatSessionIds).toEqual(["chat-1"]));
  expect(server.resumed).toEqual(["chat-1"]);
});

/*
 * 새로고침해야 목록에 나타나면 방금 한 대화가 없는 것처럼 보인다.
 *
 * 사이드바가 두 벌 마운트돼 있어(데스크톱 패널 + 모바일 드로어) 같은 계기에
 * 둘 다 목록을 다시 받아오려 하는데, 겹쳐도 서버는 한 번만 부른다.
 */
test("새 대화를 시작하면 새로고침 없이 목록에 뜬다", async () => {
  const user = userEvent.setup();
  await renderApp();
  const before = server.listCalls;

  await user.type(screen.getByPlaceholderText("트리비에게 물어보세요"), "방금 시작한 대화");
  await user.click(screen.getByRole("button", { name: "추천 시작하기" }));

  await waitFor(() => expect(within(sidebar()).getByText("방금 시작한 대화")).toBeInTheDocument());
  /* 두 벌이 동시에 물어도 요청은 하나다. */
  expect(server.listCalls - before).toBe(1);
});

/*
 * 폰으로 처음 들어오면 게스트 신원이 발급되고(RequireUser) 그 계정의 대화는
 * 0건이다. 그 뒤 로그인하면 그 계정의 대화가 보여야 하는데, 목록 캐시가 게스트일
 * 때 받은 빈 목록을 신원이 바뀐 뒤에도 그대로 돌려주어 **새로고침해야만** 보였다
 * (state/chatSessions.ts의 cachedUserId).
 */
test("게스트로 들어와 로그인하면 새로고침 없이 그 계정의 대화가 뜬다", async () => {
  server.sessions = [];
  render(<App />);
  await screen.findByRole("button", { name: "추천 시작하기" });
  await waitFor(() =>
    expect(within(sidebar()).getByText("아직 대화 기록이 없어요")).toBeInTheDocument(),
  );

  server.sessions = [
    {
      session_id: "chat-9",
      title: "로그인한 계정의 대화",
      location: null,
      last_active_at: "2026-09-10T09:00:00+09:00",
    },
  ];
  /* 로그인은 **uid가 다른** 신원으로 갈아타는 것이다 — 게스트가 가입해 승계되는
     경로(updateUser)는 uid가 그대로라 목록을 다시 받아올 이유가 없다. */
  act(() => {
    setMockSession({
      ...GUEST_SESSION,
      user: {
        ...GUEST_SESSION.user,
        id: "00000000-0000-0000-0000-000000000002",
        is_anonymous: false,
        email: "trip@example.com",
      },
    } as typeof GUEST_SESSION);
  });

  await waitFor(() =>
    expect(within(sidebar()).getByText("로그인한 계정의 대화")).toBeInTheDocument(),
  );
});

/*
 * 답변을 기다리는 중에 다른 대화를 열면, 오던 답변이 **그 대화에** 붙는 버그가
 * 있었다. 요청은 앞 대화의 것이라 서버에는 앞 대화로 저장되는데 화면만 다른
 * 대화에 나타난다 — 사용자는 하지도 않은 질문의 답을 보게 된다.
 */
test("답변 대기 중에 다른 대화를 열면 그 답변이 따라오지 않는다", async () => {
  const user = userEvent.setup();
  await renderApp();
  server.holdStream = true;

  await user.type(screen.getByPlaceholderText("트리비에게 물어보세요"), "앞 대화의 질문");
  await user.click(screen.getByRole("button", { name: "추천 시작하기" }));
  await waitFor(() => expect(server.pending).not.toBeNull());

  await user.click(
    within(sidebar()).getByRole("button", { name: "비 오는 날 아이와 함께 갈 곳 대화 열기" }),
  );
  await screen.findByText("실내를 찾아볼게요");

  /* 뒤늦게 도착한 앞 대화의 답변. 끊긴 요청이라 화면에 닿으면 안 된다. */
  server.pending?.({
    type: "done",
    data: {
      elapsed_ms: 10,
      response: {
        ...server.transcript.payload,
        state: { session_id: "chat-앞", run_id: "run_앞" },
        message: "앞 대화의 답변",
        recommendations: null,
      },
    },
  });
  server.releaseStream?.();

  /* **없음을 확인하려면 먼저 흘려보내야 한다.** waitFor는 조건이 처음부터
     참이면 그 자리에서 끝나므로, 늦게 도착할 답변을 기다리지 않고 통과한다. */
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
    await new Promise((resolve) => setTimeout(resolve, 0));
  });

  expect(screen.queryByText("앞 대화의 답변")).not.toBeInTheDocument();
  expect(screen.getByText("실내를 찾아볼게요")).toBeInTheDocument();
});

/*
 * 목록에 여러 줄이 있는데 어느 것이 열려 있는지 표시가 없으면, 대화를
 * 이어가면서도 자기가 어디 있는지 모른다.
 */
test("지금 보고 있는 대화가 목록에서 표시된다", async () => {
  const user = userEvent.setup();
  await renderApp();

  await user.click(
    within(sidebar()).getByRole("button", { name: "비 오는 날 아이와 함께 갈 곳 대화 열기" }),
  );
  await screen.findByText("실내를 찾아볼게요");

  const rows = within(sidebar()).getAllByRole("listitem");
  const current = rows.filter((row) => row.getAttribute("aria-current") === "true");
  expect(current).toHaveLength(1);
  expect(current[0]).toHaveTextContent("비 오는 날 아이와 함께 갈 곳");
});

/*
 * 첫 줄의 라벨과 동작이 짝이어야 한다. 예전 라벨은 "홈"이었는데 누르면 세션을
 * 지우고(`RESET`) 첫 화면으로 가므로 실제 동작은 "새 채팅"이다 — 2026-09-04에
 * 라벨을 그쪽으로 맞췄다.
 *
 * **라벨이 아무 테스트에도 안 잠겨 있었다**(되돌려 확인했다). 문구만 잠그면
 * 이름만 바뀌고 동작이 따라오지 않는 경우를 못 잡으니 둘을 같이 본다.
 */
test("새 채팅을 누르면 대화가 비워지고 첫 화면으로 간다", async () => {
  const user = userEvent.setup();
  await renderApp();

  /* 대화를 하나 열어 화면에 메시지를 남긴다. */
  await user.click(
    within(sidebar()).getByRole("button", { name: "비 오는 날 아이와 함께 갈 곳 대화 열기" }),
  );
  await screen.findByText("첫 답변");

  await user.click(within(sidebar()).getByRole("button", { name: "새 채팅" }));

  await waitFor(() => expect(screen.queryByText("첫 답변")).not.toBeInTheDocument());
  expect(await screen.findByRole("button", { name: "추천 시작하기" })).toBeInTheDocument();
});

/*
 * 접힘 레일의 라벨은 title·aria-label로만 남아 **화면에 글자가 없다.** 그래서
 * 영어 작업(PR #367)에서 통째로 빠져 한글 고정이었고, 테스트도 한국어로만 돌아
 * 아무도 못 잡았다(2026-09-04에 발견).
 *
 * 문구는 펼침 사이드바와 같아야 한다 — 같은 버튼이 접힘/펼침에 따라 다른 이름을
 * 가지면 스크린리더 사용자에게 두 버튼으로 들린다.
 */
test("영어로 바꾸면 접힘 레일의 이름도 영어가 된다", async () => {
  const user = userEvent.setup();
  await renderApp();

  await user.click(within(sidebar()).getByRole("button", { name: "English" }));
  await user.click(screen.getByRole("button", { name: "Collapse sidebar" }));

  const rail = sidebar();
  for (const name of ["New chat", "Preferences", "Location", "Schedule"]) {
    expect(within(rail).getByRole("button", { name })).toBeInTheDocument();
  }
  /* 펼침 쪽 문구와 같은지도 본다. */
  await user.click(screen.getByRole("button", { name: "Expand sidebar" }));
  expect(within(sidebar()).getByRole("button", { name: "New chat" })).toBeInTheDocument();
});

/* 홈처럼 세션이 없는 화면에서는 아무 줄도 켜지지 않아야 한다. */
test("대화를 열기 전에는 켜진 줄이 없다", async () => {
  await renderApp();

  const rows = within(sidebar()).getAllByRole("listitem");
  expect(rows.filter((row) => row.getAttribute("aria-current") === "true")).toEqual([]);
});

/* 실시간에서도 새 발화가 나가면 옛 버튼은 걷힌다. 전부 되살리면 지난 답변
   기준의 문구를 눌러 지금 맥락과 어긋난 요청이 나간다. */
test("복원한 대화의 후속 질문은 마지막 답변에만 남는다", async () => {
  const user = userEvent.setup();
  await renderApp();

  await user.click(
    within(sidebar()).getByRole("button", { name: "비 오는 날 아이와 함께 갈 곳 대화 열기" }),
  );
  await screen.findByText("첫 답변");

  expect(screen.getByRole("button", { name: "마지막 턴의 후속 질문" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "첫 턴의 후속 질문" })).not.toBeInTheDocument();
});

/*
 * 화면에 실제로 나가는 순서는 캡션 → 답변(LLM 팁) → 카드다(2026-09-09, LLM
 * 팁이 카드 위에서 실시간 생성되도록 바뀜). 되돌릴 때 카드를 위에 놓으면 그때
 * 본 화면과 위아래가 뒤집힌다.
 */
test("복원한 대화에서 답변이 추천 카드보다 위에 온다", async () => {
  const user = userEvent.setup();
  await renderApp();

  await user.click(
    within(sidebar()).getByRole("button", { name: "비 오는 날 아이와 함께 갈 곳 대화 열기" }),
  );

  const answer = await screen.findByText("실내를 찾아볼게요");
  const card = screen.getByText("국립중앙박물관");
  expect(answer.compareDocumentPosition(card) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
});

/*
 * 기록 저장이 실패해도 응답은 막지 않는다(그게 맞다). 그래서 턴 하나가 빠진
 * 기록이 있을 수 있는데, "하나라도 있으면 기록만 쓴다"로 판정하면 그 대화는
 * **조용히 일부만** 보인다 — 사용자는 자기가 한 말이 사라진 것으로 본다.
 */
test("기록이 온전하지 않으면 예전 방식으로 되돌린다", async () => {
  const user = userEvent.setup();
  server.partialTranscript = true;
  await renderApp();

  await user.click(
    within(sidebar()).getByRole("button", { name: "비 오는 날 아이와 함께 갈 곳 대화 열기" }),
  );

  /* 기록에 없던 턴까지 나온다 — 저장된 말풍선으로 되돌렸다는 뜻이다. */
  expect(await screen.findByText("빠진 질문")).toBeInTheDocument();
  /* 그리고 전부가 아니라는 것을 화면이 밝힌다. */
  expect(screen.getByText(/앞부분은 남아 있지 않아요/)).toBeInTheDocument();
});

/* 열기가 실패하면 화면은 그대로 남는다. 그런데 오던 답변까지 버리면, 아무 일도
   일어나지 않은 것처럼 보이면서 기다리던 답변만 사라진다. */
test("지난 대화 열기가 실패하면 오던 답변을 버리지 않는다", async () => {
  const user = userEvent.setup();
  await renderApp();
  server.holdStream = true;

  await user.type(screen.getByPlaceholderText("트리비에게 물어보세요"), "기다리던 질문");
  await user.click(screen.getByRole("button", { name: "추천 시작하기" }));
  await waitFor(() => expect(server.pending).not.toBeNull());

  server.resumeFails = true;
  await user.click(
    within(sidebar()).getByRole("button", { name: "비 오는 날 아이와 함께 갈 곳 대화 열기" }),
  );
  await waitFor(() => expect(server.listCalls).toBeGreaterThan(1));

  /* 열기는 실패했으니 답변은 그대로 도착해야 한다. */
  server.pending?.({
    type: "done",
    data: {
      elapsed_ms: 10,
      response: {
        ...server.transcript.payload,
        state: { session_id: "chat-대기", run_id: "run_대기" },
        message: "기다리던 답변",
        recommendations: null,
      },
    },
  });
  server.releaseStream?.();

  expect(await screen.findByText("기다리던 답변")).toBeInTheDocument();
});

/*
 * 보고 있는 대화를 지웠는데 화면에 그대로 두면, 이어 물었을 때 없는 session_id가
 * 나가 백엔드가 조용히 새 세션을 만든다 — 사용자는 같은 대화를 이어간 줄로 안다.
 */
test("보고 있는 대화를 지우면 화면도 비운다", async () => {
  const user = userEvent.setup();
  await renderApp();
  await user.click(
    within(sidebar()).getByRole("button", { name: "비 오는 날 아이와 함께 갈 곳 대화 열기" }),
  );
  await screen.findByText("실내를 찾아볼게요");

  await user.click(
    within(sidebar()).getByRole("button", { name: "비 오는 날 아이와 함께 갈 곳 메뉴" }),
  );
  await user.click(screen.getByRole("menuitem", { name: "삭제" }));

  await waitFor(() => expect(screen.queryByText("실내를 찾아볼게요")).not.toBeInTheDocument());
});

/* 다른 대화를 지우는 것은 보고 있는 대화에 영향이 없어야 한다. */
test("다른 대화를 지워도 보고 있는 대화는 그대로다", async () => {
  const user = userEvent.setup();
  await renderApp();
  await user.click(
    within(sidebar()).getByRole("button", { name: "비 오는 날 아이와 함께 갈 곳 대화 열기" }),
  );
  await screen.findByText("실내를 찾아볼게요");

  await user.click(
    within(sidebar()).getByRole("button", { name: "갑자기 뜬 2시간, 카페 추천 메뉴" }),
  );
  await user.click(screen.getByRole("menuitem", { name: "삭제" }));

  expect(screen.getByText("실내를 찾아볼게요")).toBeInTheDocument();
});

/*
 * 자리를 비웠다가 돌아와 이어 묻는 발화 위에는 지금 시각이 뜬다. 위쪽 지난
 * 대화와 아래쪽 새 발화가 언제 오간 것인지 갈라 보이게 하는 것이 목적이다.
 */
test("지난 대화를 이어가면 새 발화 위에 지금 시각이 뜬다", async () => {
  const user = userEvent.setup();
  await renderApp();
  await user.click(
    within(sidebar()).getByRole("button", { name: "비 오는 날 아이와 함께 갈 곳 대화 열기" }),
  );
  await screen.findByText("실내를 찾아볼게요");
  const before = screen.getAllByText(/오전|오후/).length;

  await user.type(screen.getByPlaceholderText("트리비에게 물어보세요"), "이어서 물어봄{Enter}");

  await waitFor(() => expect(screen.getAllByText(/오전|오후/).length).toBe(before + 1));
});

/*
 * 로그인 안 한 사람에게 이 버튼은 **계정으로 넘어가는 유일한 입구**다(2026-09-06).
 *
 * 예전에는 계정 팝업 안의 "계정 만들기"가 그 자리였다. 진입이 게스트로 자동으로
 * 열리게 바뀌면서 로그인 화면이 게스트를 통과시키게 됐고(LoginPage), 가입은 그
 * 화면의 "회원가입" 링크로 닿는다 — 팝업 안에 같은 입구를 두 개 둘 이유가 없다.
 *
 * **이 버튼이 없으면 로그인·가입 어느 쪽에도 도달할 수 없다.** 앱 안에서 /login으로
 * 가는 길이 여기뿐이다.
 */
test("로그인 안 한 상태면 사이드바에서 로그인 화면으로 갈 수 있다", async () => {
  await renderApp();

  await userEvent.click(within(sidebar()).getByRole("button", { name: "로그인" }));

  expect(await screen.findByLabelText("이메일")).toBeInTheDocument();
  /* 가입도 여기서 이어진다 — 그 화면이 게스트 세션을 그대로 승격시킨다. */
  expect(screen.getByRole("link", { name: "회원가입" })).toBeInTheDocument();
});

test("로그인한 계정에는 로그인 버튼 대신 계정 표시가 온다", async () => {
  await renderAppAsAccount();

  expect(within(sidebar()).queryByRole("button", { name: "로그인" })).not.toBeInTheDocument();
  expect(within(sidebar()).getAllByText("trip@example.com").length).toBeGreaterThan(0);
});

/*
 * **게스트 로그아웃 확인 테스트 3개를 지웠다**(2026-09-06). 게스트에게 로그아웃
 * 자리가 아예 없어져서(그 자리는 "로그인" 버튼이다) 확인 단계에 닿을 길이 없다.
 *
 * 계정 사용자는 확인 없이 나간다 — 다시 로그인하면 그대로 돌아오므로, 되돌릴 수
 * 있는 동작에까지 확인을 붙이면 확인이라는 신호가 값싸진다.
 */
test("로그아웃하면 관문으로 튕기지 않고 로그인 안 한 상태로 앱에 남는다", async () => {
  await renderAppAsAccount();

  await openAccountMenu(userEvent);
  await userEvent.click(within(sidebar()).getByRole("menuitem", { name: /로그아웃/ }));

  /* 로그인 화면으로 보내지 않는다 — 관문이 게스트 신원을 새로 발급해 같은 자리에서
     앱이 계속 열려 있다(RequireUser). */
  expect(await within(sidebar()).findByRole("button", { name: "로그인" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "추천 시작하기" })).toBeInTheDocument();
});

/*
 * 사이드바 바닥은 **이메일이 아니라 이름**을 낸다.
 *
 * 가입 화면이 이름을 받는데도(`SignupPage`: "AI가 추천할 때 이 이름으로 불러드려요")
 * 여기에는 늘 이메일이 떴다 — `identityLabel`의 후보 순서가 email 먼저라서다.
 * 그 함수는 `AuthStatusBadge`도 쓰므로 순서를 뒤집는 대신 `identityDisplay`를
 * 따로 만들었고, 이 테스트가 사이드바가 그쪽을 쓰는 것을 잠근다.
 */

/*
 * 로그인 안 한 사람에게는 신원 표시를 그리지 않는다. 보여줄 것이 "게스트 /
 * 게스트로 이용 중"뿐이라 이름 자리를 차지하고도 아무것도 알려주지 못한다 —
 * 그 자리에는 할 수 있는 동작이 오는 게 낫다.
 */
test("로그인 안 한 상태에는 신원 표시 대신 로그인 버튼만 있다", async () => {
  await renderApp();

  expect(within(sidebar()).getByRole("button", { name: "로그인" })).toBeInTheDocument();
  expect(within(sidebar()).queryByText("게스트로 이용 중")).not.toBeInTheDocument();
  /* 열 팝업 자체가 없다. */
  expect(within(sidebar()).queryByRole("button", { expanded: false })).not.toBeInTheDocument();
});

/* 계정 사용자는 계정 표시 그대로다 — 이 화면은 바뀌지 않았다. */
test("계정 이름과 이메일은 계정 버튼에 두 줄로 남는다", async () => {
  setMockSession({
    ...ACCOUNT_SESSION,
    user: { ...ACCOUNT_SESSION.user, user_metadata: { name: "나종원" } },
  } as typeof GUEST_SESSION);
  await renderApp();

  const account = accountButton();
  expect(within(account).getByText("나종원")).toBeInTheDocument();
  expect(within(account).getByText("trip@example.com")).toBeInTheDocument();
});

/* 로그아웃은 되돌릴 수 없다. 상시 눌리는 자리에 두지 않는다. */
test("로그아웃은 계정 팝업을 열기 전에는 보이지 않는다", async () => {
  await renderAppAsAccount();

  expect(within(sidebar()).queryByRole("menuitem", { name: /로그아웃/ })).not.toBeInTheDocument();

  await openAccountMenu(userEvent);

  expect(within(sidebar()).getByRole("menuitem", { name: /로그아웃/ })).toBeInTheDocument();
});

test("팝업 바깥을 누르면 닫힌다", async () => {
  await renderAppAsAccount();
  await openAccountMenu(userEvent);

  await userEvent.click(within(sidebar()).getByRole("button", { name: "계정 메뉴 닫기" }));

  expect(within(sidebar()).queryByRole("menuitem", { name: /로그아웃/ })).not.toBeInTheDocument();
});

/*
 * 저장한 일정 목록 테스트 6개는 `components/schedule/SavedScheduleList.test.tsx`로
 * 옮겼다(2026-09-04) — 목록이 사이드바에서 일정 화면으로 옮겨갔다. 그중
 * "대화와 저장한 일정의 id가 겹쳐도 메뉴는 하나만 뜬다"는 두 목록이 메뉴 상태를
 * 한 벌로 나눠 쓸 때만 성립하던 가드라 옮기지 않고 지웠다.
 */

/*
 * **접었을 때도 계정에 닿아야 한다**(2026-09-06).
 *
 * 레일은 SideDrawerContent 를 아예 그리지 않아서, 접어 두면 로그인도 로그아웃도
 * 할 수 없었다 — 펴야만 되는 동작이 있으면 접기가 기능을 감추는 셈이다.
 *
 * 두 신원을 짝으로 잠근다. 한쪽만 보면 "계정일 때만 나오는" 구현으로도 통과한다.
 */
test("사이드바를 접어도 로그인 입구가 레일에 남는다", async () => {
  const user = userEvent.setup();
  await renderApp();

  await user.click(screen.getByRole("button", { name: "사이드바 접기" }));

  await user.click(within(sidebar()).getByRole("button", { name: "로그인" }));
  expect(await screen.findByLabelText("이메일")).toBeInTheDocument();
});

test("사이드바를 접어도 계정 프로필을 눌러 로그아웃할 수 있다", async () => {
  const user = userEvent.setup();
  await renderAppAsAccount();

  await user.click(screen.getByRole("button", { name: "사이드바 접기" }));

  /* 레일에는 글자가 없으므로 이름·부제가 버튼의 접근 가능한 이름이 된다 — 펼친
     쪽 버튼도 그 둘을 품고 있어 같은 문구로 읽힌다. */
  await user.click(within(sidebar()).getByRole("button", { name: /trip@example\.com/ }));
  await user.click(within(sidebar()).getByRole("menuitem", { name: /로그아웃/ }));

  /* 로그아웃하면 관문으로 튕기지 않고 로그인 안 한 상태로 남는다 — 레일도
     로그인 입구로 바뀐다. */
  expect(await within(sidebar()).findByRole("button", { name: "로그인" })).toBeInTheDocument();
});

/* 레일 계정은 펼친 사이드바와 **같은 컴포넌트**다. 한 벌 더 만들면 로그아웃이 두
   곳에 생기고, 한쪽만 고쳐지면 접었을 때와 폈을 때가 갈린다. 팝업 내용이 양쪽에서
   같은지 확인해 그 사실을 잠근다. */
test("레일 계정 팝업도 펼친 쪽과 같은 내용을 낸다", async () => {
  const user = userEvent.setup();
  await renderAppAsAccount();

  await user.click(screen.getByRole("button", { name: "사이드바 접기" }));
  await user.click(within(sidebar()).getByRole("button", { name: /trip@example\.com/ }));

  /* 신원 헤더 + 닉네임 변경 + 로그아웃 + 회원 탈퇴. 그 밖의 줄은 만들지 않는다
     (갈 화면이 없다). 탈퇴는 2026-09-15에 더했고, 되돌릴 수 없어 확인 단계를
     거치므로 이 줄을 누르는 것만으로는 아무 일도 일어나지 않는다
     (SidebarAccount.test.tsx). */
  expect(within(sidebar()).getAllByText("trip@example.com").length).toBeGreaterThan(0);
  expect(within(sidebar()).getAllByRole("menuitem")).toHaveLength(3);
  expect(within(sidebar()).getByRole("menuitem", { name: /닉네임 변경/ })).toBeInTheDocument();
  expect(within(sidebar()).getByRole("menuitem", { name: /로그아웃/ })).toBeInTheDocument();
  expect(within(sidebar()).getByRole("menuitem", { name: /회원 탈퇴/ })).toBeInTheDocument();
});

/*
 * 닉네임 변경. 화면 이동 없이 팝업 안에서 끝난다 — 신원 헤더 자리가 입력칸으로
 * 바뀌었다가, 저장하면 팝업이 닫히고 계정 버튼에 새 이름이 바로 남는다.
 */
test("닉네임을 바꾸면 계정 버튼에 새 이름이 반영된다", async () => {
  const user = userEvent.setup();
  await renderAppAsAccount();

  await openAccountMenu(user);
  await user.click(within(sidebar()).getByRole("menuitem", { name: "닉네임 변경" }));

  const input = within(sidebar()).getByLabelText("닉네임");
  await user.clear(input);
  await user.type(input, "나종원");
  await user.click(within(sidebar()).getByRole("button", { name: "저장" }));

  /* 저장하면 팝업이 닫힌다 — 로그아웃 메뉴가 다시 안 보이는 것으로 확인한다. */
  await waitFor(() =>
    expect(within(sidebar()).queryByRole("menuitem", { name: /로그아웃/ })).not.toBeInTheDocument(),
  );
  expect(within(accountButton()).getByText("나종원")).toBeInTheDocument();
});

test("닉네임을 비우고 저장하면 오류를 보여주고 입력칸이 그대로 남는다", async () => {
  const user = userEvent.setup();
  await renderAppAsAccount();

  await openAccountMenu(user);
  await user.click(within(sidebar()).getByRole("menuitem", { name: "닉네임 변경" }));
  await user.clear(within(sidebar()).getByLabelText("닉네임"));
  await user.click(within(sidebar()).getByRole("button", { name: "저장" }));

  expect(within(sidebar()).getByText("닉네임을 입력해 주세요.")).toBeInTheDocument();
  expect(within(sidebar()).getByLabelText("닉네임")).toBeInTheDocument();
});

test("닉네임 변경 중 취소를 누르면 저장 없이 원래 메뉴로 돌아간다", async () => {
  const user = userEvent.setup();
  await renderAppAsAccount();

  await openAccountMenu(user);
  await user.click(within(sidebar()).getByRole("menuitem", { name: "닉네임 변경" }));
  const input = within(sidebar()).getByLabelText("닉네임");
  await user.clear(input);
  await user.type(input, "안 쓸 이름");
  await user.click(within(sidebar()).getByRole("button", { name: "취소" }));

  expect(within(sidebar()).getByRole("menuitem", { name: /로그아웃/ })).toBeInTheDocument();
  expect(within(sidebar()).queryByText("안 쓸 이름")).not.toBeInTheDocument();
});
