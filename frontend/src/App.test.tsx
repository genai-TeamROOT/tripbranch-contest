/*
 * 역할: 채팅형 사용자 흐름과 보호 라우팅을 검증하는 앱 통합 테스트.
 * 입력: mocked fetch 응답, feature flag 환경변수, 브라우저 상호작용.
 * 출력: 모드별 메시지 렌더링과 API 호출에 대한 assertion.
 * 호출 시점: vitest 실행 시 프론트엔드 smoke/regression 테스트로 호출된다.
 * TODO: 실제 다회 대화 의미 분석이 생기면 후속 입력 시나리오를 확장한다.
 */

import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import App from "./App";
import { setLocationCenter, setLocationOrigin } from "./state/locationSettings";
import { resetChatSessionsCache } from "./state/chatSessions";
import { resetSavedSchedulesCache } from "./state/savedSchedules";

/* jsdom에는 createImageBitmap도 canvas도 없어서 축소본이 항상 null이 된다. 그러면
   실패한 사진 말풍선에 남을 것이 없어 "사진이 남는지"를 확인할 수 없다. */
vi.mock("./utils/imageThumbnail", () => ({
  createThumbnailDataUrl: async () => "data:image/jpeg;base64,AAAA",
}));

// 실사용 흐름은 /api/chat 한 번으로 해석과 추천을 함께 받는다(AgentResponse).
// llm_output.recommend.conditions가 조건 카드 표시에 쓰이고, recommendations가
// 그대로 결과 메시지가 된다.
const interpretResponse = {
  intent: "RECOMMEND",
  status: "complete",
  recommend: {
    conditions: {
      current_location: null,
      search_center: "경복궁",
      place_types: [],
      place_tags: ["museum", "cafe"],
      weather: "rain",
      weather_intent: "AVOID",
      transport: null,
      max_travel_time: null,
      time_available: null,
      environment: "indoor",
      companion: null,
      budget: null,
      exclude_tags: [],
      special_requirements: [],
    },
  },
  info: null,
  modify: null,
  compare: null,
  general: null,
  out_of_scope: null,
  clarification: null,
};

const recommendationsResponse = {
  recommendations: [
    {
      place_id: "stub-museum-1",
      name: "테스트 박물관",
      category: "museum",
      distance_km: 0.4,
      remaining_minutes: 150,
      environment_type: "indoor",
      recommendation_reason: "비 오는 날 방문하기 좋은 실내 장소예요.",
      warnings: [],
    },
  ],
  unverified_recommendations: [
    {
      place_id: "stub-gallery-1",
      name: "운영시간 미확인 갤러리",
      category: "gallery",
      distance_km: 0.8,
      remaining_minutes: null,
      environment_type: "indoor",
      recommendation_reason: "선호한 문화 장소와 비슷한 장소예요.",
      warnings: ["방문 전에 운영 여부를 확인해주세요."],
    },
  ],
};

function chatResponse() {
  return {
    llm_output: interpretResponse,
    state: { session_id: "sess_test", run_id: "run_test", user_conditions: null },
    recommendations: { ...recommendationsResponse, elapsed_ms: 12.3 },
    message: "조건에 맞는 장소를 찾아봤어요.",
    suggested_follow_ups: ["테스트 박물관 운영시간 알려줘", "다른 곳도 보여줘"],
  };
}

function streamResponse(
  response: {
    llm_output: { intent: string; [key: string]: unknown };
    state: unknown;
    recommendations: unknown;
    message: string;
    message_footnote?: string;
    suggested_follow_ups?: string[];
  } = chatResponse(),
) {
  const events: Array<{ event: string; data: unknown }> = [
    {
      event: "progress",
      data: { stage: "interpreting", message: "조건을 파악하고 있어요.", elapsed_ms: 1 },
    },
  ];
  /* 실제 서버는 조건 병합 직후, 도구 조회·채점·답변 스트리밍보다 앞서 이번 턴이
     쓸 위치를 알려준다(docs/design/agent-response-streaming.md 4.3절). 여기서
     빠뜨리면 화면이 그 이벤트를 안 받는 상태로만 검증된다. */
  const merged = (
    response.state as {
      user_conditions?: { current_location: string | null; search_center: string | null } | null;
    } | null
  )?.user_conditions;
  events.push({
    event: "location_resolved",
    data: {
      current_location: merged?.current_location ?? null,
      search_center: merged?.search_center ?? null,
      elapsed_ms: 2,
    },
  });
  if (response.recommendations) {
    events.push(
      {
        event: "result",
        data: {
          llm_output: response.llm_output,
          state: response.state,
          recommendations: response.recommendations,
          message: "이런 곳들을 찾아봤어요:",
          elapsed_ms: 2,
        },
      },
      {
        event: "message_start",
        data: { intent: response.llm_output.intent, elapsed_ms: 3 },
      },
      { event: "message_delta", data: { text: response.message, elapsed_ms: 4 } },
    );
  }
  // 실제 서버와 같은 순서를 흉내 낸다 — 후속 질문은 done **뒤에** 별도 이벤트로
  // 온다(D-102). done 응답 자체에는 담기지 않는다.
  const { suggested_follow_ups: followUps, ...doneResponse } = response;
  events.push({ event: "done", data: { response: doneResponse, elapsed_ms: 5 } });
  if (followUps && followUps.length > 0) {
    events.push({ event: "follow_ups", data: { suggestions: followUps, elapsed_ms: 6 } });
  }
  const payload = events
    .map(({ event, data }) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`)
    .join("");
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(new TextEncoder().encode(payload));
      controller.close();
    },
  });
  return new Response(stream, { headers: { "Content-Type": "text/event-stream" } });
}

/*
 * 채팅 관련 호출만 센다. 사이드바가 마운트되면 채팅 히스토리(/sessions)를 함께
 * 받아오는데, 전체 fetch 횟수를 세면 그 부수 요청까지 섞여 "채팅 요청이 몇 번
 * 나갔나"라는 원래 의도가 흐려진다.
 */
function chatCalls() {
  return vi.mocked(fetch).mock.calls.filter((call) => String(call[0]).includes("/chat"));
}

function mockFetch() {
  return vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.endsWith("/chat/stream")) return streamResponse();
    if (url.endsWith("/chat")) {
      return Response.json(chatResponse());
    }
    /* 사이드바 채팅 히스토리(TP-222 후속). 대화 흐름과 무관하지만 사이드바가
       마운트되면 항상 나가므로, 404로 두면 콘솔이 오류로 덮인다. */
    if (url.endsWith("/sessions")) {
      return Response.json({ sessions: [] });
    }
    /* 저장한 일정 목록도 사이드바가 마운트되면 항상 나간다(SCHEDULE 카드 2). */
    if (url.endsWith("/schedules")) {
      return Response.json({ items: [] });
    }
    /* 기능 스위치(GET /api/features)도 앱이 뜨면 한 번 나간다. 이 파일의 흐름은
       취향이 켜진 서버 기준이다 — 404로 두면 꺼짐으로 보고 취향 메뉴·캡션이 바뀐다.
       꺼진 경우는 SideDrawerContent.test.tsx와 RecommendationResultMessage.test.tsx가 본다. */
    if (url.endsWith("/features")) {
      return Response.json({ taste_enabled: true });
    }
    return Response.json({ error: { message: "not found" } }, { status: 404 });
  });
}

beforeEach(() => {
  sessionStorage.clear();
  localStorage.clear();
  resetChatSessionsCache();
  resetSavedSchedulesCache();
  window.history.pushState({}, "", "/");
  /* 이 버전은 브라우저 위치를 어떤 경로로도 묻지 않는다. 감시만 하는 가짜를 심어
     두고, 불리면 테스트가 잡도록 응답은 주지 않는다. */
  vi.stubGlobal("navigator", { geolocation: { getCurrentPosition: vi.fn() } });
  vi.stubGlobal("fetch", mockFetch());
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

/* 게스트 세션 확인이 끝나 관문(RequireUser)을 통과할 때까지 기다린다(D-062).
   렌더 직후에는 세션 조회가 아직 진행 중이라 홈 화면이 그려지지 않은 상태다. */
async function renderApp() {
  render(<App />);
  await screen.findByRole("button", { name: "추천 시작하기" });
}

/*
 * 컴포저가 스크롤 영역 안에 있으면 iOS에서 소프트 키보드가 뜬 동안 같이 흘러간다
 * (2026-09-09 실기기 실측 — `sticky bottom-0`이 통째로 죽어 "창바닥 − 컴포저바닥"이
 * scrollTop과 한 행도 빠짐없이 일치했다). 되돌리기 쉬운 구조라 자리를 잠가 둔다.
 *
 * jsdom 은 레이아웃을 하지 않아 실제로 흘러가는지는 볼 수 없다 — 대신 **컴포저와
 * 스크롤 칸 사이에 조상 관계가 없는지**를 본다. 그것이 이 구조의 전부다.
 */
function scrollableAncestorOf(node: HTMLElement): HTMLElement | null {
  let current = node.parentElement;
  while (current && current.tagName !== "MAIN" && current.tagName !== "SECTION") {
    if (/\boverflow-(y-)?auto\b/.test(current.className)) return current;
    current = current.parentElement;
  }
  return null;
}

function composerRoot(): HTMLElement {
  /* 홈과 채팅이 같은 문구를 쓴다(HomePage/ChatPage text.composer). */
  return screen
    .getByPlaceholderText("트리비에게 물어보세요")
    .closest("div.tb-composer-dock") as HTMLElement;
}

test("홈 컴포저는 스크롤 칸 밖에 있고 키보드만큼 올라갈 준비가 되어 있다", async () => {
  await renderApp();

  const composer = composerRoot();
  expect(composer).not.toBeNull();
  expect(scrollableAncestorOf(composer)).toBeNull();
  /* 올리는 것과 아래 여백이 다른 클래스다 — 스크롤 이동 버튼이 올리기만 같이 받는다. */
  expect(composer.className).toContain("tb-keyboard-lift");

  /* main 자체가 스크롤러이면 컴포저가 다시 그 안에 들어간 것과 같다. */
  const main = composer.closest("main") as HTMLElement;
  expect(main.className).toContain("overflow-hidden");
  expect(main.className).not.toContain("overflow-y-auto");

  /* 컴포저는 스크롤 영역 **위에 겹친다** — 그래야 내용이 유리 뒤로 지나간다.
     겹치는 만큼 스크롤 영역이 아래를 비워 두지 않으면 마지막 내용이 영영 가린다
     (실기기에서 "다른 장소 보기" 버튼이 흰 띠에 잘려 보였다, 2026-09-09). */
  expect(composer.className).toContain("absolute");
  const scroller = main.querySelector(":scope > div.overflow-y-auto") as HTMLElement;
  expect(scroller.className).toContain("pb-[var(--tb-composer-h,0px)]");
});

test("채팅 컴포저도 스크롤 칸 밖에 있다 — 자동 바닥 붙임이 가려 온 자리다", async () => {
  await renderApp();

  await userEvent.type(screen.getByPlaceholderText("트리비에게 물어보세요"), "비 오는 날 갈 곳");
  await userEvent.click(screen.getByRole("button", { name: "추천 시작하기" }));
  await screen.findByText("테스트 박물관");

  const composer = composerRoot();
  expect(scrollableAncestorOf(composer)).toBeNull();
  expect(composer.closest("main")?.className).toContain("overflow-hidden");
});

test("user chat hides condition debug card and shows recommendations", async () => {
  vi.stubEnv("VITE_SHOW_INTERPRETATION_DEBUG", "true");
  await renderApp();

  await userEvent.type(screen.getByPlaceholderText("트리비에게 물어보세요"), "비 오는 날 갈 곳");
  await userEvent.click(screen.getByRole("button", { name: "추천 시작하기" }));

  expect(screen.queryByText(/개발용 입력 해석 결과/)).not.toBeInTheDocument();
  // 홈 컴포저가 ChatComposer와 공유되면서(HomePage/ChatComposer 통합) 제출이
  // "칩으로 채우기 → 컴포저가 자기 state를 비우고 → 위치 조회 1틱을 더 거쳐"
  // /chat으로 넘어가므로, 클릭 직후 동기 조회 대신 비동기로 기다린다.
  expect((await screen.findAllByText("비 오는 날 갈 곳")).length).toBeGreaterThan(0);
  // Agent가 한 번에 끝내므로 중간 승인 버튼이 없고 추천이 함께 나온다.
  expect(screen.queryByRole("button", { name: "추천 진행" })).not.toBeInTheDocument();
  expect(await screen.findByText("테스트 박물관")).toBeInTheDocument();
  // 로그인 안 한 상태의 계정 자리는 사이드바에 상시 떠 있다(2026-09-06) — 채팅
  // 화면에서도 사이드바를 통해 이어진다. 데스크톱 사이드바(role=complementary)로
  // 좁혀서 찾는다(모바일 드로어도 같은 SideDrawerContent를 렌더해 중복된다).
  expect(
    within(screen.getByRole("complementary")).getByRole("button", { name: "로그인" }),
  ).toBeInTheDocument();
  /* 운영시간을 모르는 후보도 추천 장소 줄에 함께 들어간다(2026-09-08) — 전에는
     "운영시간을 확인할 수 없는 장소" 캡션으로 줄이 하나 더 그려졌다. */
  expect(screen.getByText("운영시간 미확인 갤러리")).toBeInTheDocument();
  expect(screen.queryByText("운영시간을 확인할 수 없는 장소")).not.toBeInTheDocument();
});

test("streamed recommendation renders the LLM tip, then caption, then cards", async () => {
  await renderApp();

  await userEvent.click(screen.getByText("비를 피할 실내 장소가 필요해"));
  await userEvent.click(screen.getByRole("button", { name: "추천 시작하기" }));

  // LLM 팁이 맨 위에서 실시간 생성된다(2026-09-09) — 팁 → 캡션 → 카드 순서.
  const tip = await screen.findByText("조건에 맞는 장소를 찾아봤어요.");
  const caption = await screen.findByText("트리비가 추천하는 관광명소 순위예요:");
  const firstCard = screen.getByText("테스트 박물관");
  expect(tip.compareDocumentPosition(caption) & Node.DOCUMENT_POSITION_FOLLOWING).not.toBe(0);
  expect(caption.compareDocumentPosition(firstCard) & Node.DOCUMENT_POSITION_FOLLOWING).not.toBe(
    0,
  );
});

test("user chat needs only one chat call", async () => {
  vi.stubEnv("VITE_SHOW_INTERPRETATION_DEBUG", "false");
  await renderApp();

  await userEvent.type(screen.getByPlaceholderText("트리비에게 물어보세요"), "비 오는 날 갈 곳");
  await userEvent.click(screen.getByRole("button", { name: "추천 시작하기" }));

  expect(screen.queryByText(/개발용 입력 해석 결과/)).not.toBeInTheDocument();
  expect(await screen.findByText("테스트 박물관")).toBeInTheDocument();

  await waitFor(() => expect(chatCalls()).toHaveLength(1));
  expect(String(chatCalls()[0][0])).toContain("/chat");
  const requestBody = JSON.parse(String(chatCalls()[0][1]?.body));
  /* 기기 좌표는 받지도 보내지도 않는다. */
  expect(requestBody).not.toHaveProperty("device_location");
});

test("says the location is not set instead of the old 종로구 or 현재 위치 default", async () => {
  /* 위치를 정하지도 않았고 대화도 없다. "종로구"는 지원 지역이 종로구뿐이던 시절의
     기본값이고, "현재 위치"는 기기 GPS를 쓰던 시절의 말이다 — 둘 다 지금은 사실과
     다르다. */
  await renderApp();

  const pill = screen.getByRole("button", {
    name: "위치 설정으로 이동 (위치를 아직 정하지 않았어요)",
  });
  expect(pill).toHaveTextContent("위치 미설정");
  expect(pill).not.toHaveTextContent("현재 위치");
});

test("shows the picked origin in the header pill when no center is set", async () => {
  /* 검색 기준을 비워두면 출발지가 검색 중심이 된다(agent_context의 사다리).
     위치 설정 화면의 칩과 헤더가 같은 사실을 말해야 한다. */
  setLocationOrigin("혜화역");
  await renderApp();

  expect(
    screen.getByRole("button", {
      name: "위치 설정으로 이동 (혜화역에서 출발, 혜화역 주변에서 검색)",
    }),
  ).toBeInTheDocument();
});

test("shows the picked search center in the header location pill", async () => {
  /* 고른 위치는 위치 설정 화면이 아니라 상단 위치 pill이 보여준다 - 화면을 나가도
     지금 어디를 기준으로 찾는지가 계속 보여야 한다. */
  setLocationCenter("안국역");
  await renderApp();

  /* 출발지를 정하지 않았으면 서버는 검색 기준에서 거리를 재므로 한 칸으로 접힌다. */
  expect(
    screen.getByRole("button", {
      name: "위치 설정으로 이동 (안국역에서 출발, 안국역 주변에서 검색)",
    }),
  ).toBeInTheDocument();
});

test("sends the search center picked on the location screen with the chat request", async () => {
  /* 위치 설정 화면에서 고른 값은 sessionStorage에 남는다(state/searchCenterStorage) —
     화면을 다시 거치지 않고 저장소에 직접 넣고, 발화 요청에 그 값이 실려 나가는지만
     본다. 필드 이름이 어긋나면 화면은 멀쩡한데 위치만 조용히 무시되므로 요청
     본문으로 못 박는다. */
  setLocationCenter("안국역");
  await renderApp();

  await userEvent.type(screen.getByPlaceholderText("트리비에게 물어보세요"), "카페 추천해줘");
  await userEvent.click(screen.getByRole("button", { name: "추천 시작하기" }));

  /* 이 화면은 발화 말고도 다른 요청을 보내므로(취향 조회 등) 순서로 집지 않고
     /chat 요청을 찾아 본문을 확인한다. */
  const fetchMock = vi.mocked(fetch);
  await waitFor(() =>
    expect(fetchMock.mock.calls.some((call) => String(call[0]).includes("/chat"))).toBe(true),
  );
  const chatCall = fetchMock.mock.calls.find((call) => String(call[0]).includes("/chat"));
  const requestBody = JSON.parse(String(chatCall?.[1]?.body));
  expect(requestBody.selected_search_center).toBe("안국역");
});

test("개발자 채팅도 위치 설정을 요청에 싣는다", async () => {
  /* 두 화면은 같은 session_id를 쓴다. 개발자 화면만 위치를 안 실어 보내면, 그 화면에서
     한 턴을 돌릴 때 서버에 쌓인 위치 조건이 바뀌고 사용자 화면으로 돌아가면 다시
     채워지는 일이 반복된다 — 같은 설정으로 물어도 검색 기준이 GPS·검색지·출발지로
     갈려 보이던 원인이었다(2026-09-08, 원인 추적에 몇 시간이 들었다).

     구조를 합치는 것은 TP-255에서 하고, 여기서는 두 화면이 같은 값을 보내는지만
     못 박는다. 이 테스트가 없으면 한쪽만 고쳐도 아무것도 깨지지 않는다. */
  setLocationOrigin("화곡역");
  setLocationCenter("서대문역");
  window.history.pushState({}, "", "/dev-chat");

  render(<App />);
  const composer = await screen.findByPlaceholderText("추가 조건을 입력해 주세요");
  await userEvent.type(composer, "카페 추천해줘");
  await userEvent.click(screen.getByRole("button", { name: "보내기" }));

  const fetchMock = vi.mocked(fetch);
  await waitFor(() =>
    expect(fetchMock.mock.calls.some((call) => String(call[0]).includes("/chat"))).toBe(true),
  );
  const chatCall = fetchMock.mock.calls.find((call) => String(call[0]).includes("/chat"));
  const requestBody = JSON.parse(String(chatCall?.[1]?.body));
  expect(requestBody.selected_current_location).toBe("화곡역");
  expect(requestBody.selected_search_center).toBe("서대문역");
});

/*
 * 발화가 정한 위치를 응답에서 되돌려 받는 흐름. 배선이 한쪽뿐이던 시절에는 위치
 * 설정 화면에서 고른 값만 발화에 실려 나가고, 발화가 그 위치를 바꿔도 저장소는
 * 예전 값을 계속 들고 있었다.
 */
function chatResponseWithConditions(currentLocation: string | null, searchCenter: string | null) {
  return {
    ...chatResponse(),
    state: {
      session_id: "sess_test",
      run_id: "run_test",
      user_conditions: { current_location: currentLocation, search_center: searchCenter },
    },
  };
}

/* 채팅 응답만 갈아끼우고 사이드바 부수 요청(/sessions·/schedules)은 원래대로 둔다. */
function mockFetchWithChatResponse(response: ReturnType<typeof chatResponseWithConditions>) {
  const base = mockFetch();
  return vi.fn(async (input: RequestInfo | URL) => {
    if (String(input).endsWith("/chat/stream")) return streamResponse(response);
    return base(input);
  });
}

/*
 * 이벤트를 두 덩어리로 나눠 흘린다. location_resolved까지만 보낸 채 멈춰 세워야
 * "카드가 뜨기 전에 칩이 이미 바뀌었는가"를 물을 수 있다 — 한 번에 다 보내면
 * 두 시점이 같은 틱에 붙어 순서가 검증되지 않는다.
 */
function gatedStreamResponse(response: ReturnType<typeof chatResponseWithConditions>) {
  const raw = streamResponse(response);
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  const stream = new ReadableStream<Uint8Array>({
    async start(controller) {
      const text = await raw.text();
      const marker = text.indexOf("event: result");
      controller.enqueue(new TextEncoder().encode(text.slice(0, marker)));
      await gate;
      controller.enqueue(new TextEncoder().encode(text.slice(marker)));
      controller.close();
    },
  });
  return {
    response: new Response(stream, { headers: { "Content-Type": "text/event-stream" } }),
    release,
  };
}

test("moves the header pill before the recommendation cards arrive", async () => {
  /* 위치는 조건 병합에서 확정되고 그 뒤 도구 조회·채점이 남는다 — 그 구간이 턴에서
     제일 길다. 결과를 기다렸다가 바꾸면, 사용자는 "광화문역 근처"라고 말해 놓고
     한참 동안 서대문역 기준으로 찾고 있는 줄 안다. */
  setLocationCenter("서대문역");
  const gated = gatedStreamResponse(chatResponseWithConditions("안국역", "광화문역"));
  const base = mockFetch();
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).endsWith("/chat/stream")) return gated.response;
      return base(input);
    }),
  );
  await renderApp();

  await userEvent.type(
    screen.getByPlaceholderText("트리비에게 물어보세요"),
    "지금 안국역인데 광화문역 근처 알려줘",
  );
  await userEvent.click(screen.getByRole("button", { name: "추천 시작하기" }));

  /* 아직 result를 안 보냈다 — 칩만 먼저 바뀌어 있어야 한다.

     findBy가 아니라 waitFor로 매번 다시 조회한다. 이 턴은 홈에서 시작해 채팅
     화면으로 넘어가는 중이라, findBy가 홈의 칩을 붙잡은 직후 그 노드가 화면
     교체로 떨어져 나가면 "찾았는데 document에 없다"로 깨진다. */
  await waitFor(() =>
    expect(
      screen.getByRole("button", {
        name: "위치 설정으로 이동 (안국역에서 출발, 광화문역 주변에서 검색)",
      }),
    ).toBeInTheDocument(),
  );
  expect(screen.queryByText("테스트 박물관")).not.toBeInTheDocument();

  gated.release();
  expect(await screen.findByText("테스트 박물관")).toBeInTheDocument();
});

test("shows the location the utterance picked in the header pill", async () => {
  /* "지금 안국역인데 광화문역 근처 알려줘" — 발화가 두 위치를 다 말하면 발화가
     이긴다(백엔드 _apply_selected_locations). 그 결과가 화면에 안 돌아오면
     사용자는 아직 서대문역을 기준으로 찾은 줄 안다. */
  setLocationOrigin("서대문역");
  setLocationCenter("서대문역");
  vi.stubGlobal(
    "fetch",
    mockFetchWithChatResponse(chatResponseWithConditions("안국역", "광화문역")),
  );
  await renderApp();

  await userEvent.type(
    screen.getByPlaceholderText("트리비에게 물어보세요"),
    "지금 안국역인데 광화문역 근처 알려줘",
  );
  await userEvent.click(screen.getByRole("button", { name: "추천 시작하기" }));

  expect(await screen.findByText("테스트 박물관")).toBeInTheDocument();
  expect(
    await screen.findByRole("button", {
      name: "위치 설정으로 이동 (안국역에서 출발, 광화문역 주변에서 검색)",
    }),
  ).toBeInTheDocument();
});

test("sends the location the utterance picked on the next turn", async () => {
  /* 표시보다 이쪽이 크다. 저장소가 서대문역을 들고 있으면 다음 발화에 그 값이
     selected_search_center로 다시 실려 나가고, 백엔드는 조건 병합보다 앞에서
     그것을 채워 세션의 광화문역을 덮어쓴다 — 대화로 옮긴 위치가 원위치된다. */
  setLocationCenter("서대문역");
  vi.stubGlobal("fetch", mockFetchWithChatResponse(chatResponseWithConditions(null, "광화문역")));
  await renderApp();

  await userEvent.type(
    screen.getByPlaceholderText("트리비에게 물어보세요"),
    "광화문역 근처 알려줘",
  );
  await userEvent.click(screen.getByRole("button", { name: "추천 시작하기" }));

  expect(await screen.findByText("테스트 박물관")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "다른 장소 보기" }));

  await waitFor(() => expect(chatCalls()).toHaveLength(2));
  const requestBody = JSON.parse(String(chatCalls()[1][1]?.body));
  expect(requestBody.selected_search_center).toBe("광화문역");
});

test("keeps the picked location when the server reports no location at all", async () => {
  /* 서버 조건이 비어 오는 대표적인 경우는 세션에 아직 RECOMMEND 조건이 없을 때다
     — 위치를 정해 두고 정보 질문부터 던지면 백엔드 _apply_selected_locations()가
     RECOMMEND에만 걸리므로 user_conditions가 빈 채로 온다. 그때 지우면 질문 하나에
     사용자가 손으로 고른 위치가 사라진다.

     null은 "지우라"가 아니라 "서버도 모른다"는 뜻이라는 것이 이 테스트의 전부라,
     응답 본문 자체는 기본 픽스처를 그대로 쓴다. */
  setLocationCenter("서대문역");
  vi.stubGlobal("fetch", mockFetchWithChatResponse(chatResponseWithConditions(null, null)));
  await renderApp();

  await userEvent.type(
    screen.getByPlaceholderText("트리비에게 물어보세요"),
    "경복궁 운영시간 알려줘",
  );
  await userEvent.click(screen.getByRole("button", { name: "추천 시작하기" }));

  expect(await screen.findByText("테스트 박물관")).toBeInTheDocument();
  expect(
    screen.getByRole("button", {
      name: "위치 설정으로 이동 (서대문역에서 출발, 서대문역 주변에서 검색)",
    }),
  ).toBeInTheDocument();
});

/*
 * 예전에는 기기 위치를 받은 지 30분이 지나면 후속 발화를 붙잡고 "이전 위치로 계속 /
 * 현재 위치 다시 가져오기"를 물었다. 기기 위치를 받지 않으니 붙잡을 이유도 없다.
 */
test("sends a follow-up right away no matter how long ago the chat started", async () => {
  const now = vi.spyOn(Date, "now");
  now.mockReturnValue(1_000);
  await renderApp();

  await userEvent.click(screen.getByText("비를 피할 실내 장소가 필요해"));
  await userEvent.click(screen.getByRole("button", { name: "추천 시작하기" }));
  await screen.findByText("테스트 박물관");

  now.mockReturnValue(60 * 60 * 1000 + 1_001);
  await userEvent.type(screen.getByPlaceholderText("트리비에게 물어보세요"), "다른 곳 보여줘");
  await userEvent.click(screen.getByRole("button", { name: "보내기" }));

  await waitFor(() => expect(chatCalls()).toHaveLength(2));
  expect(screen.queryByText(/지났어요/)).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /위치로 계속|다시 가져오기/ })).not.toBeInTheDocument();
  const requestBody = JSON.parse(String(chatCalls()[1][1]?.body));
  expect(requestBody.user_input).toBe("다른 곳 보여줘");
  expect(requestBody).not.toHaveProperty("device_location");
  expect(navigator.geolocation.getCurrentPosition).not.toHaveBeenCalled();
  now.mockRestore();
});

test("falls back to the existing chat endpoint when the SSE route is unavailable", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).endsWith("/chat/stream")) {
        return Response.json(
          {
            error: {
              code: "not_found",
              message: "not found",
              retryable: false,
              details: null,
            },
          },
          { status: 404 },
        );
      }
      return Response.json(chatResponse());
    }),
  );
  await renderApp();

  await userEvent.click(screen.getByText("비를 피할 실내 장소가 필요해"));
  await userEvent.click(screen.getByRole("button", { name: "추천 시작하기" }));

  expect(await screen.findByText("테스트 박물관")).toBeInTheDocument();
  expect(chatCalls()).toHaveLength(2);
});

test("starting a chat from home never asks for browser location", async () => {
  vi.stubEnv("VITE_SHOW_INTERPRETATION_DEBUG", "false");
  let resolveFetch: ((response: Response) => void) | undefined;
  vi.stubGlobal(
    "fetch",
    vi.fn(
      () =>
        new Promise<Response>((resolve) => {
          resolveFetch = resolve;
        }),
    ),
  );
  await renderApp();

  await userEvent.click(screen.getByText("비를 피할 실내 장소가 필요해"));
  await userEvent.click(screen.getByRole("button", { name: "추천 시작하기" }));

  /* 위치 설정도 대화도 없는 상태에서 시작해도 권한 팝업을 띄우지 않는다. 위치가
     필요하면 서버가 되묻는다(location_required). */
  expect(navigator.geolocation.getCurrentPosition).not.toHaveBeenCalled();
  // 응답을 기다리는 동안엔 안내 문구 한 줄만 뜬다(AgentProgressMessage).
  expect(await screen.findByRole("status")).toHaveTextContent(/중…$/);

  resolveFetch?.(streamResponse());
  expect(await screen.findByText("테스트 박물관")).toBeInTheDocument();
});

test("developer start opens dev chat with audit panel", async () => {
  vi.stubEnv("VITE_SHOW_INTERPRETATION_DEBUG", "false");
  await renderApp();

  await userEvent.click(screen.getByText("비를 피할 실내 장소가 필요해"));
  await userEvent.click(screen.getByRole("button", { name: "개발자용으로 시작" }));

  expect(await screen.findByText("Agent Runtime Audit")).toBeInTheDocument();
  expect((await screen.findAllByText(/Intent: RECOMMEND/)).length).toBeGreaterThan(0);
  expect(screen.getByText("TripBranch Developer Console")).toBeInTheDocument();
  // 개발자 화면 상단에도 신원 표시가 이어진다(D-062).
  expect(screen.getByText("게스트로 이용 중")).toBeInTheDocument();
  expect(screen.getAllByText(/비를 피할 실내 장소가 필요해/).length).toBeGreaterThan(1);
});

/*
 * TP-268 — 개발자용 진입 칩·라우트는 `import.meta.env.DEV`로만 가른다. 어드민
 * 계정 같은 런타임 권한 대신 빌드 시점 값을 쓴 이유는 `vite build` 산출물(시연
 * 영상·실제 배포)에서 로그인 상태와 무관하게 정적으로 사라지기 때문이다.
 *
 * vitest는 기본으로 DEV=true다(mode="test"가 "production"이 아니라서) — 그래서
 * 위 "developer start opens dev chat with audit panel" 같은 기존 테스트들은
 * 손대지 않아도 그대로 통과한다. 이 테스트만 명시적으로 false로 스텁한다.
 */
test("배포 빌드에서는 홈 화면에 개발자용 시작 칩이 없다", async () => {
  vi.stubEnv("DEV", false);
  await renderApp();

  expect(screen.queryByRole("button", { name: "개발자용으로 시작" })).not.toBeInTheDocument();
});

test("배포 빌드에서는 /dev-chat 주소로 들어가도 홈으로 돌아간다", async () => {
  /* 칩만 숨기면 URL을 직접 쳐서는 여전히 들어갈 수 있다 — 라우트 자체가
     없어야 한다(App.tsx). 안쪽 AppRoutes의 catch-all이 "/"로 돌려보낸다. */
  vi.stubEnv("DEV", false);
  window.history.pushState({}, "", "/dev-chat");

  render(<App />);

  await screen.findByRole("button", { name: "추천 시작하기" });
  expect(screen.queryByText("Agent Runtime Audit")).not.toBeInTheDocument();
});

test("developer audit turn cards remain selectable after multiple turns", async () => {
  vi.stubEnv("VITE_SHOW_INTERPRETATION_DEBUG", "false");
  await renderApp();

  await userEvent.click(screen.getByText("비를 피할 실내 장소가 필요해"));
  await userEvent.click(screen.getByRole("button", { name: "개발자용으로 시작" }));
  expect(await screen.findByText("Agent Runtime Audit")).toBeInTheDocument();

  await userEvent.type(screen.getByPlaceholderText("추가 조건을 입력해 주세요"), "광화문 근처에서");
  await userEvent.click(screen.getByRole("button", { name: "보내기" }));
  expect(await screen.findByText(/2\. 광화문 근처에서/)).toBeInTheDocument();

  await userEvent.click(screen.getByRole("button", { name: /1\. 비를 피할 실내 장소가 필요해/ }));

  const firstTurnCard = screen.getByRole("button", {
    name: /1\. 비를 피할 실내 장소가 필요해/,
  });
  expect(firstTurnCard.className).toContain("border-emerald-500");
});

test("위치를 하나도 정하지 않아도 대화는 좌표 없이 시작된다", async () => {
  /* 예전에는 여기서 기기 위치를 물었고, 그보다 전에는 거절하면 요청을 아예 안
     보냈다. 지금은 묻지도 막지도 않는다 — 위치 없이 보내면 백엔드가 어디서 찾을지
     되묻고(location_required), 사용자는 그 되묻기에 답해서 계속 갈 수 있다. */
  await renderApp();

  await userEvent.click(screen.getByText("비를 피할 실내 장소가 필요해"));
  await userEvent.click(screen.getByRole("button", { name: "추천 시작하기" }));

  await waitFor(() => expect(chatCalls().length).toBeGreaterThan(0));
  expect(navigator.geolocation.getCurrentPosition).not.toHaveBeenCalled();
  const requestBody = JSON.parse(String(chatCalls()[0]?.[1]?.body));
  expect(requestBody).not.toHaveProperty("device_location");
  expect(requestBody.selected_current_location).toBeNull();
  expect(requestBody.selected_search_center).toBeNull();
});

test("출발지를 정해 뒀으면 그 이름을 싣고 위치 권한은 묻지 않는다", async () => {
  /* 서버가 이동시간을 재는 출발점은 그 이름이고(D-067) 이름을 좌표로 바꾸는 일은
     백엔드가 한다(TP-256). */
  const getCurrentPosition = vi.fn();
  vi.stubGlobal("navigator", { geolocation: { getCurrentPosition } });
  setLocationOrigin("안국역");
  await renderApp();

  await userEvent.click(screen.getByText("비를 피할 실내 장소가 필요해"));
  await userEvent.click(screen.getByRole("button", { name: "추천 시작하기" }));

  await waitFor(() => expect(chatCalls().length).toBeGreaterThan(0));
  expect(getCurrentPosition).not.toHaveBeenCalled();
  const requestBody = JSON.parse(String(chatCalls()[0]?.[1]?.body));
  expect(requestBody.selected_current_location).toBe("안국역");
});

test("requesting more places sends a follow-up chat turn with the session id", async () => {
  vi.stubEnv("VITE_SHOW_INTERPRETATION_DEBUG", "false");
  await renderApp();

  await userEvent.click(screen.getByText("비를 피할 실내 장소가 필요해"));
  await userEvent.click(screen.getByRole("button", { name: "추천 시작하기" }));

  expect(await screen.findByText("테스트 박물관")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "다른 장소 보기" }));

  await waitFor(() => expect(screen.getAllByText("테스트 박물관")).toHaveLength(2));
  expect(chatCalls()).toHaveLength(2);
  const requestBody = JSON.parse(String(chatCalls()[1][1]?.body));
  // 제외 목록은 B가 단일 기준이라 프론트가 보내지 않는다.
  expect(requestBody.session_id).toBe("sess_test");
  expect(requestBody.user_input).toBe("다른 곳 보여줘");
});

test("지난 턴의 추천 버튼은 새 턴이 오면 사라지고 카드는 남는다", async () => {
  /*
   * 옛 버튼이 남아 있으면 그때 기준의 요청이 지금 맥락으로 나가 결과가 어긋난다.
   * 그래서 버튼만 별도 메시지로 두고 새 발화에서 걷어낸다 — 카드와 취향 표는
   * 그때 무엇을 받았는지 보여주는 기록이라 그대로 남긴다.
   */
  vi.stubEnv("VITE_SHOW_INTERPRETATION_DEBUG", "false");
  await renderApp();

  await userEvent.click(screen.getByText("비를 피할 실내 장소가 필요해"));
  await userEvent.click(screen.getByRole("button", { name: "추천 시작하기" }));

  expect(await screen.findByText("테스트 박물관")).toBeInTheDocument();
  expect(screen.getAllByRole("button", { name: "다른 장소 보기" })).toHaveLength(1);

  await userEvent.click(screen.getByRole("button", { name: "다른 장소 보기" }));

  // 카드는 두 턴 것이 모두 남는다.
  await waitFor(() => expect(screen.getAllByText("테스트 박물관")).toHaveLength(2));
  // 버튼은 방금 턴의 것 하나뿐이다.
  expect(screen.getAllByRole("button", { name: "다른 장소 보기" })).toHaveLength(1);
});

test("지난 턴의 되묻기 선택지는 새 턴이 오면 사라지고 문구는 남는다", async () => {
  /*
   * 되묻기는 통째로 지우지 않는다 — 그 메시지가 그 턴의 답변이라, 지우면
   * "질문 → (빈칸) → 사용자가 고른 답"이 되어 왜 그 답을 했는지 알 수 없다.
   * 누를 수 있는 것만 없애고 문구는 기록으로 남긴다.
   */
  vi.stubEnv("VITE_SHOW_INTERPRETATION_DEBUG", "false");
  let chatTurn = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/chat/stream")) {
        chatTurn += 1;
        // 첫 턴만 되묻는다. 선택지를 누르면 그것이 다음 발화가 되어 두 번째 턴이 된다.
        if (chatTurn === 1) {
          return streamResponse({
            llm_output: {
              ...interpretResponse,
              recommend: null,
              clarification: {
                options: [
                  { id: "indoor", label: "실내" },
                  { id: "outdoor", label: "실외" },
                ],
              },
            },
            state: { session_id: "sess_test", run_id: "run_test" },
            recommendations: null,
            message: "실내와 실외 중 어디가 좋으세요?",
          });
        }
        return streamResponse();
      }
      if (url.endsWith("/sessions")) return Response.json({ sessions: [] });
      if (url.endsWith("/schedules")) return Response.json({ items: [] });
      return Response.json({ error: { message: "not found" } }, { status: 404 });
    }),
  );
  await renderApp();

  await userEvent.click(screen.getByText("비를 피할 실내 장소가 필요해"));
  await userEvent.click(screen.getByRole("button", { name: "추천 시작하기" }));

  expect(await screen.findByText("실내와 실외 중 어디가 좋으세요?")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "실내" }));

  // 다음 턴이 도착하면 선택지는 사라진다.
  await waitFor(() =>
    expect(screen.queryByRole("button", { name: "실외" })).not.toBeInTheDocument(),
  );
  // 무엇을 물었는지는 기록으로 남는다.
  expect(screen.getByText("실내와 실외 중 어디가 좋으세요?")).toBeInTheDocument();
});

test("clarification turn hints a fuller phrasing in the composer placeholder", async () => {
  vi.stubEnv("VITE_SHOW_INTERPRETATION_DEBUG", "false");
  // 위치를 말하지 않아 Agent가 되묻는 상황: 추천 없이 메시지만 온다.
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      streamResponse({
        llm_output: { ...interpretResponse, recommend: null },
        state: { session_id: "sess_test", run_id: "run_test" },
        recommendations: null,
        message: "어디 근처에서 찾아드릴까요? 원하시는 지역을 알려주세요.",
      }),
    ),
  );
  await renderApp();

  await userEvent.click(screen.getByText("비를 피할 실내 장소가 필요해"));
  await userEvent.click(screen.getByRole("button", { name: "추천 시작하기" }));

  expect(await screen.findByText(/어디 근처에서 찾아드릴까요/)).toBeInTheDocument();
  // 발화를 대신 만들어 보내지 않고, 입력창 안내 문구만 바꾼다.
  expect(screen.getByPlaceholderText("경복궁 근처에서 찾아줘")).toBeInTheDocument();
});

test("unsupported region reply shows a short message with the district list as a footnote", async () => {
  // 서비스 지역 밖 요청: 본문은 짧고, 지원 구 목록은 message_footnote로 따로 온다(D-085).
  vi.stubEnv("VITE_SHOW_INTERPRETATION_DEBUG", "false");
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      streamResponse({
        llm_output: { ...interpretResponse, recommend: null },
        state: { session_id: "sess_test", run_id: "run_test" },
        recommendations: null,
        message: "이 위치는 지금 서비스 지역이 아니에요. 다른 위치를 말씀해 주세요.",
        message_footnote: "현재 서비스 지역: 서울특별시 종로구·중구·용산구·성동구",
      }),
    ),
  );
  await renderApp();

  await userEvent.click(screen.getByText("비를 피할 실내 장소가 필요해"));
  await userEvent.click(screen.getByRole("button", { name: "추천 시작하기" }));

  expect(
    await screen.findByText("이 위치는 지금 서비스 지역이 아니에요. 다른 위치를 말씀해 주세요."),
  ).toBeInTheDocument();
  expect(
    screen.getByText("현재 서비스 지역: 서울특별시 종로구·중구·용산구·성동구"),
  ).toBeInTheDocument();
});

test("chat route redirects without stored state", async () => {
  window.history.pushState({}, "", "/chat");

  await renderApp();

  expect(await screen.findByRole("button", { name: "추천 시작하기" })).toBeInTheDocument();
});

test("shows follow-up suggestions after an answer and sends the label as the next message", async () => {
  /* 되묻기 버튼과 달리 clarification_choice 없이 문구만 발화로 나간다. */
  await renderApp();

  await userEvent.click(screen.getByText("비를 피할 실내 장소가 필요해"));
  await userEvent.click(screen.getByRole("button", { name: "추천 시작하기" }));
  await screen.findByText("테스트 박물관");

  const suggestion = await screen.findByRole("button", {
    name: "테스트 박물관 운영시간 알려줘",
  });
  await userEvent.click(suggestion);

  await waitFor(() => expect(chatCalls()).toHaveLength(2));
  const secondBody = JSON.parse(String(chatCalls()[1][1]?.body));
  expect(secondBody.user_input).toBe("테스트 박물관 운영시간 알려줘");
  expect(secondBody.clarification_choice).toBeNull();
});

test("keeps only the latest turn's follow-up suggestions", async () => {
  /* 옛 턴의 버튼이 남으면 지난 답변 기준의 문구를 지금 맥락에 보내게 된다. */
  await renderApp();

  await userEvent.click(screen.getByText("비를 피할 실내 장소가 필요해"));
  await userEvent.click(screen.getByRole("button", { name: "추천 시작하기" }));
  await screen.findByText("테스트 박물관");
  await screen.findByRole("button", { name: "다른 곳도 보여줘" });

  await userEvent.click(screen.getByRole("button", { name: "다른 곳도 보여줘" }));

  await waitFor(() =>
    expect(screen.getAllByRole("group", { name: "이어서 물어볼 만한 질문" })).toHaveLength(1),
  );
});

test("renders no follow-up buttons when the server sends no follow_ups event", async () => {
  /* done 응답에는 문구가 없다 — 버튼은 오직 done 뒤의 follow_ups 이벤트에서 온다. */
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/chat/stream")) {
        return streamResponse({ ...chatResponse(), suggested_follow_ups: [] });
      }
      return Response.json({ error: { message: "not found" } }, { status: 404 });
    }),
  );
  await renderApp();

  await userEvent.click(screen.getByText("비를 피할 실내 장소가 필요해"));
  await userEvent.click(screen.getByRole("button", { name: "추천 시작하기" }));
  await screen.findByText("테스트 박물관");

  expect(screen.queryByRole("group", { name: "이어서 물어볼 만한 질문" })).not.toBeInTheDocument();
});

// --- 위치·일정 내비게이션(package_D/DESIGN_SYSTEM.md §5) ---------------------

/*
 * **위치·일정은 취향 설정과 같은 전체 페이지다**(2026-09-07 사용자 결정).
 * 예전에는 모바일에서 홈 위에 겹치는 바텀시트로 떴는데, 그 둘만 다른 취급을
 * 받을 이유가 없어 전체 페이지로 통일했다 — 이 아래 세 테스트가 그 전환을
 * 잠근다. 옛 시트 동작(§5.2 바텀시트)을 검증하던 자리다.
 */
test("사이드바에서 위치 설정을 열면 전체 페이지로 뜨고, 브라우저 뒤로가기로 홈에 돌아온다", async () => {
  await renderApp();

  // 데스크톱 사이드바(role=complementary)로 좁힌다 — 모바일 드로어도 같은
  // SideDrawerContent를 렌더해 "위치 설정" 텍스트가 중복된다.
  const sidebar = within(screen.getByRole("complementary"));
  await userEvent.click(sidebar.getByRole("button", { name: "위치 설정" }));

  // LocationPage에는 별도 제목이 없다 — 항상 있는 장소 검색 입력으로 화면이
  // 열렸는지 확인한다.
  expect(await screen.findByLabelText("장소 검색")).toBeInTheDocument();
  // 새 페이지로 갈아치운 것이라 밑에 깔린 홈이 DOM에서 빠진다(시트였다면 남아
  // 있었을 것이다).
  expect(screen.queryByRole("button", { name: "추천 시작하기" })).not.toBeInTheDocument();
  // 시트가 아니라 닫기(X)는 없다. 뒤로가기 화살표도 없다(2026-09-07) — 헤더가
  // 돌아가는 버튼을 아예 그리지 않으므로, 돌아가는 길은 브라우저 뒤로가기뿐이다.
  expect(screen.queryByRole("button", { name: "닫기" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "뒤로가기" })).not.toBeInTheDocument();

  window.history.back();

  await waitFor(() =>
    expect(screen.queryByLabelText("장소 검색")).not.toBeInTheDocument(),
  );
  expect(screen.getByRole("button", { name: "추천 시작하기" })).toBeInTheDocument();
});

test("사이드바 상시 패널이 보이는 폭(데스크톱)에서도 위치 설정에 뒤로가기 화살표가 없다", async () => {
  // useIsDesktopSidebar가 참을 반환하도록 matchMedia를 데스크톱 폭으로 흉내낸다.
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: true,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  }));
  await renderApp();

  const sidebar = within(screen.getByRole("complementary"));
  await userEvent.click(sidebar.getByRole("button", { name: "위치 설정" }));

  expect(await screen.findByLabelText("장소 검색")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "닫기" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "추천 시작하기" })).not.toBeInTheDocument();
  // 뒤로가기 화살표가 없다(2026-09-07) — 폭에 상관없이 헤더가 화살표를 그리지
  // 않는다. 데스크톱 폭에서도 되살아나지 않는지 이 폭에서 따로 확인한다.
  expect(screen.queryByRole("button", { name: "뒤로가기" })).not.toBeInTheDocument();
});

/*
 * 일정도 위치와 같은 전체 페이지다. SchedulePage 자체는 별도 파일에서 직접
 * 렌더해 검증하고, 여기서는 "사이드바에서 눌렀을 때 실제로 그 화면으로
 * 가는가"만 본다 — 이 배선이 빠지면 대화가 사라진다.
 */
test("사이드바에서 일정을 열면 전체 페이지로 뜬다", async () => {
  await renderApp();

  const sidebar = within(screen.getByRole("complementary"));
  await userEvent.click(sidebar.getByRole("button", { name: "일정" }));

  expect(await screen.findByText("저장한 일정이 없어요. 채팅에서 일정을 저장하면 여기에 모여요.")).toBeInTheDocument();
  // 새 페이지로 갈아치운 것이라 밑에 깔린 홈이 DOM에서 빠진다.
  expect(screen.queryByRole("button", { name: "추천 시작하기" })).not.toBeInTheDocument();
  // 뒤로가기 화살표는 그리지 않는다(2026-09-07) — 돌아가는 길은 브라우저
  // 뒤로가기다.
  expect(screen.queryByRole("button", { name: "뒤로가기" })).not.toBeInTheDocument();
});

// --- 응답 대기 중 취소(package_D/DESIGN_SYSTEM.md §7.2) ------------------------

test("응답을 기다리는 동안 중단을 누르면 로딩이 멈추고 오류 없이 끝난다", async () => {
  await renderApp();
  await userEvent.click(screen.getByText("비를 피할 실내 장소가 필요해"));
  await userEvent.click(screen.getByRole("button", { name: "추천 시작하기" }));
  await screen.findByText("테스트 박물관");

  // 후속 발화는 일부러 끝나지 않는 스트림으로 받는다 — 곧바로 완료되면 중단이
  // 실제로 완료 전에 걸리는지 확인할 수 없다.
  let streamController!: ReadableStreamDefaultController<Uint8Array>;
  const pendingStream = new ReadableStream<Uint8Array>({
    start(controller) {
      streamController = controller;
    },
  });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/chat/stream")) {
        // 실제 fetch는 신호가 중단되면 응답 body 스트림도 함께 끊는다 — 이
        // stub도 같은 계약을 흉내 내야 "중단" 버튼이 정말 읽기를 멈추는지
        // 검증할 수 있다.
        init?.signal?.addEventListener("abort", () => {
          streamController.error(new DOMException("The operation was aborted.", "AbortError"));
        });
        return new Response(pendingStream, {
          headers: { "Content-Type": "text/event-stream" },
        });
      }
      return Response.json({ error: { message: "not found" } }, { status: 404 });
    }),
  );

  await userEvent.type(screen.getByPlaceholderText("트리비에게 물어보세요"), "다른 조건 추가");
  await userEvent.click(screen.getByRole("button", { name: "보내기" }));

  // progress 이벤트 하나를 흘려 "생각 중" 상태를 만든다.
  streamController.enqueue(
    new TextEncoder().encode(
      `event: progress\ndata: ${JSON.stringify({
        stage: "interpreting",
        message: "조건을 파악하고 있어요.",
        elapsed_ms: 1,
      })}\n\n`,
    ),
  );

  await userEvent.click(await screen.findByRole("button", { name: "중단" }));

  // 컴포저가 다시 평소의 "보내기" 버튼으로 돌아오고, 오류 배너는 뜨지 않는다.
  expect(await screen.findByRole("button", { name: "보내기" })).toBeInTheDocument();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

test("텍스트가 이미 온 상태에서 중단하면 거기까지만 남기고 얼린다", async () => {
  await renderApp();
  await userEvent.click(screen.getByText("비를 피할 실내 장소가 필요해"));
  await userEvent.click(screen.getByRole("button", { name: "추천 시작하기" }));
  await screen.findByText("테스트 박물관");

  let streamController!: ReadableStreamDefaultController<Uint8Array>;
  const pendingStream = new ReadableStream<Uint8Array>({
    start(controller) {
      streamController = controller;
    },
  });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/chat/stream")) {
        init?.signal?.addEventListener("abort", () => {
          streamController.error(new DOMException("The operation was aborted.", "AbortError"));
        });
        return new Response(pendingStream, {
          headers: { "Content-Type": "text/event-stream" },
        });
      }
      return Response.json({ error: { message: "not found" } }, { status: 404 });
    }),
  );

  await userEvent.type(screen.getByPlaceholderText("트리비에게 물어보세요"), "다른 조건 추가");
  await userEvent.click(screen.getByRole("button", { name: "보내기" }));

  const encoder = new TextEncoder();
  streamController.enqueue(
    encoder.encode(
      `event: message_start\ndata: ${JSON.stringify({ intent: "RECOMMEND", elapsed_ms: 1 })}\n\n`,
    ),
  );
  streamController.enqueue(
    encoder.encode(
      `event: message_delta\ndata: ${JSON.stringify({ text: "여기까지 답했어요", elapsed_ms: 2 })}\n\n`,
    ),
  );

  await screen.findByText("여기까지 답했어요");
  await userEvent.click(await screen.findByRole("button", { name: "중단" }));

  // 중단해도 이미 온 텍스트는 사라지지 않고 그대로 남는다.
  expect(await screen.findByRole("button", { name: "보내기" })).toBeInTheDocument();
  expect(screen.getByText("여기까지 답했어요")).toBeInTheDocument();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  // done 뒤에만 오는 후속 질문은 연결이 끊겼으니 뜨지 않는다.
  expect(screen.queryByRole("group", { name: "이어서 물어볼 만한 질문" })).not.toBeInTheDocument();
});

// --- 홈 화면의 사진 추가 버튼 --------------------------------------------------

test("홈 화면에서도 사진을 올릴 수 있고, 고르면 /chat으로 넘어가 결과를 보여준다", async () => {
  /* 사진 검색은 위치가 있어야 요청이 나간다. 기기 위치는 받지 않으므로 이름으로 정해 둔다. */
  setLocationCenter("성수동");
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/places/similar-by-photo")) {
        return Response.json({
          places: [
            {
              content_id: "photo-place-1",
              title: "감성 카페",
              similarity: 0.82,
              photo_count: 3,
              address: "서울 성동구",
              image_url: null,
            },
          ],
          center_name: "성수동",
          /* 홈에서 사진부터 올리면 세션이 없어서 서버가 여기서 발급한다.
             화면이 이 값을 저장해야 이어지는 발화가 같은 대화로 붙는다. */
          session_id: "photo-session-1",
          candidate_count: 12,
          truncated_count: 0,
          elapsed_ms: 400,
        });
      }
      return Response.json({ error: { message: "not found" } }, { status: 404 });
    }),
  );
  await renderApp();

  // 홈 컴포저에도 ChatPage와 같은 "+" 버튼이 있어야 한다(사진 없이는 대화
  // 시작 전에는 이 버튼 자체가 안 그려지는 회귀가 있었다).
  await userEvent.click(screen.getByRole("button", { name: "사진 추가" }));
  await userEvent.click(screen.getByRole("menuitem", { name: "갤러리" }));

  const file = new File(["x"], "cafe.jpg", { type: "image/jpeg" });
  const galleryInput = screen.getByTestId("photo-gallery-input") as HTMLInputElement;
  fireEvent.change(galleryInput, { target: { files: [file] } });

  // 결과는 메시지로 쌓이므로 /chat으로 넘어가야 보인다.
  expect(await screen.findByText("감성 카페")).toBeInTheDocument();
  expect(screen.getByPlaceholderText("트리비에게 물어보세요")).toBeInTheDocument();
  // 사진만 덩그러니 두지 않는다 — 무엇을 요청한 턴인지가 화면에 남아야 한다.
  expect(screen.getByText("이 사진과 비슷한 장소 추천해줘")).toBeInTheDocument();
});

test("사진으로 시작한 대화에 이어 말하면 같은 세션으로 붙는다", async () => {
  /* 사진 검색은 위치가 있어야 요청이 나간다. 기기 위치는 받지 않으므로 이름으로 정해 둔다. */
  setLocationCenter("성수동");
  /*
   * 서버가 발급한 session_id를 화면이 저장하지 않으면 이어지는 발화가 또 새
   * 대화를 시작해, 방금 한 사진 검색이 혼자 남는다.
   */
  const base = mockFetch();
  const sentBodies: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/places/similar-by-photo")) {
        return Response.json({
          places: [
            {
              content_id: "photo-place-1",
              title: "감성 카페",
              similarity: 0.82,
              photo_count: 3,
              address: "서울 성동구",
              image_url: null,
            },
          ],
          center_name: "성수동",
          session_id: "photo-session-1",
          candidate_count: 12,
          truncated_count: 0,
          elapsed_ms: 400,
        });
      }
      sentBodies.push(String(init?.body ?? ""));
      return base(input);
    }),
  );
  await renderApp();

  await userEvent.click(screen.getByRole("button", { name: "사진 추가" }));
  await userEvent.click(screen.getByRole("menuitem", { name: "갤러리" }));
  const file = new File(["x"], "cafe.jpg", { type: "image/jpeg" });
  fireEvent.change(screen.getByTestId("photo-gallery-input"), { target: { files: [file] } });
  await screen.findByText("감성 카페");

  await userEvent.type(screen.getByPlaceholderText("트리비에게 물어보세요"), "그중에 첫 번째");
  await userEvent.click(screen.getByRole("button", { name: "보내기" }));

  await waitFor(() =>
    expect(sentBodies.some((body) => body.includes("photo-session-1"))).toBe(true),
  );
});

/*
 * **홈의 취향 줄을 지웠다**(2026-09-07 사용자 결정). 저장해 둔 취향을 홈에서
 * 한 번 더 보여주던 줄과, 그것이 흐르던 띠를 함께 뺐다 — 그 동작을 잠그던
 * 테스트 두 개도 여기서 지운다.
 *
 * 지우면서 함께 사라진 것: 취향을 저장하면 홈으로 보내는데(PreferencesPage
 * handleSave), 저장됐다는 확인이 이 줄이었다. 지금은 홈에 아무 표시도 남지
 * 않는다.
 */
// --- 실패한 턴을 대화 안에 남긴다(TP-245) ------------------------------------

/*
 * 예전에는 오류가 대화 목록 맨 위 배너에 떴다. 대화가 길어지면 화면 밖으로
 * 밀려나는데 이 화면은 새 답변마다 맨 아래로 스크롤하므로, 사용자는 늘 배너에서
 * 가장 먼 곳에 있었다. "다시 시도"가 달려 있는데도 누를 수가 없었다.
 */
function failingFetch() {
  return vi.fn(async () =>
    Response.json(
      { error: { code: "internal_server_error", message: "서버가 응답하지 않았어요." } },
      { status: 500 },
    ),
  );
}

async function sendFirstUtterance() {
  await renderApp();
  await userEvent.type(screen.getByPlaceholderText("트리비에게 물어보세요"), "비 오는 날 갈 곳");
  await userEvent.click(screen.getByRole("button", { name: "추천 시작하기" }));
}

test("요청이 실패하면 사유가 대화 맨 아래에 남고 발화도 그대로 남는다", async () => {
  await sendFirstUtterance();
  await screen.findByText("테스트 박물관");

  vi.stubGlobal("fetch", failingFetch());
  await userEvent.type(screen.getByPlaceholderText("트리비에게 물어보세요"), "다른 곳");
  await userEvent.click(screen.getByRole("button", { name: "보내기" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("서버가 응답하지 않았어요.");
  /* 실패한 발화도 대화에 남는다 — 무엇에 대한 실패인지가 위아래로 읽혀야 한다. */
  expect(screen.getByText("다른 곳")).toBeInTheDocument();
  /* 앞 턴의 결과는 건드리지 않는다. */
  expect(screen.getByText("테스트 박물관")).toBeInTheDocument();
  /* 입력창이 풀려야 다시 쓸 수 있다. */
  expect(screen.getByRole("button", { name: "보내기" })).toBeInTheDocument();
});

test("실패한 턴의 다시 시도를 누르면 같은 발화를 다시 보낸다", async () => {
  await sendFirstUtterance();
  await screen.findByText("테스트 박물관");

  vi.stubGlobal("fetch", failingFetch());
  await userEvent.type(screen.getByPlaceholderText("트리비에게 물어보세요"), "다른 곳");
  await userEvent.click(screen.getByRole("button", { name: "보내기" }));
  await screen.findByRole("alert");

  const base = mockFetch();
  const sentBodies: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      sentBodies.push(String(init?.body ?? ""));
      return base(input);
    }),
  );
  await userEvent.click(screen.getByRole("button", { name: "다시 시도" }));

  await waitFor(() => expect(sentBodies.some((body) => body.includes("다른 곳"))).toBe(true));
});

test("사진 검색이 실패해도 올린 사진은 대화에 남는다", async () => {
  /* 사진 검색은 위치가 있어야 요청이 나간다. 기기 위치는 받지 않으므로 이름으로 정해 둔다. */
  setLocationCenter("성수동");
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/places/similar-by-photo")) {
        return Response.json(
          { error: { code: "internal_server_error", message: "사진 검색이 실패했어요." } },
          { status: 500 },
        );
      }
      return Response.json({ sessions: [], items: [] });
    }),
  );
  await renderApp();

  await userEvent.click(screen.getByRole("button", { name: "사진 추가" }));
  await userEvent.click(screen.getByRole("menuitem", { name: "갤러리" }));
  const file = new File(["x"], "cafe.jpg", { type: "image/jpeg" });
  fireEvent.change(screen.getByTestId("photo-gallery-input"), { target: { files: [file] } });

  expect(await screen.findByRole("alert")).toHaveTextContent("사진 검색이 실패했어요.");
  /* 예전에는 실패하면 사진 말풍선을 지웠다. 채팅이 실패해도 발화는 남기면서
     사진만 지우는 것은 일관되지 않았다. */
  expect(await screen.findByAltText("올린 사진")).toBeInTheDocument();
});

/*
 * 위치 칩에는 GPS 점(깜빡이는 초록·회색)이 없다. 예전에는 기기 좌표를 받으면 칩이
 * 깜빡였는데, 이 버전은 좌표를 받지 않는다 — 발화 전후 어느 쪽에서도 붙으면 안 된다.
 */
test("위치 칩에는 발화 전에도 후에도 GPS 점이 없다", async () => {
  vi.stubEnv("VITE_SHOW_INTERPRETATION_DEBUG", "false");
  const { container } = render(<App />);
  await screen.findByRole("button", { name: "추천 시작하기" });

  expect(container.querySelector(".animate-ping")).toBeNull();

  await userEvent.click(screen.getByText("비를 피할 실내 장소가 필요해"));
  await userEvent.click(screen.getByRole("button", { name: "추천 시작하기" }));
  await screen.findByText("테스트 박물관");

  expect(container.querySelector(".animate-ping")).toBeNull();
});

/*
 * 좌표를 들고 있던 옛 저장본(v6)으로 새로고침해도 그 좌표를 되살리지 않는다 —
 * 저장 버전을 7로 올려 통째로 버린다(state/storage.ts).
 */
test("기기 좌표가 남은 옛 저장본은 버리고 위치 미설정으로 뜬다", async () => {
  vi.stubEnv("VITE_SHOW_INTERPRETATION_DEBUG", "false");
  sessionStorage.setItem(
    "tripbranch_state",
    JSON.stringify({
      version: 6,
      state: {
        language: "ko",
        user_input: "",
        interpreted_conditions: null,
        recommendations: [],
        unverified_recommendations: [],
        shown_place_ids: [],
        messages: [],
        auditTurns: [],
        phase: "ready",
        error: null,
        session_id: null,
        device_location: "37.5665,126.9780",
        device_location_captured_at: Date.now(),
        device_location_snoozed_until: null,
        awaiting_clarification: false,
        saved_places: [],
        agentProgress: null,
        streamingIntent: null,
      },
    }),
  );

  const { container } = render(<App />);
  await screen.findByRole("button", { name: "추천 시작하기" });

  expect(
    screen.getByRole("button", { name: "위치 설정으로 이동 (위치를 아직 정하지 않았어요)" }),
  ).toBeInTheDocument();
  expect(container.querySelector(".animate-ping")).toBeNull();
});

// --- 사진 검색의 위치 정하기 -------------------------------------------------

/*
 * 위치를 정하는 규칙은 일반 채팅과 같다. 다른 것은 텍스트냐 사진이냐뿐이다 —
 * 위치 설정의 검색 기준 → 출발지 순으로 쓰고, 둘 다 없으면 요청하지 않는다.
 */

function photoFetch(onPhotoRequest?: (form: FormData) => void) {
  const base = mockFetch();
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.endsWith("/places/similar-by-photo")) {
      onPhotoRequest?.(init?.body as FormData);
      return Response.json({
        places: [
          {
            content_id: "photo-place-1",
            title: "감성 카페",
            similarity: 0.82,
            photo_count: 3,
            address: "서울 성동구",
            image_url: null,
          },
        ],
        center_name: "성수동",
        session_id: "photo-session-1",
        candidate_count: 12,
        truncated_count: 0,
        elapsed_ms: 400,
      });
    }
    return base(input);
  });
}

async function uploadPhoto() {
  await userEvent.click(screen.getByRole("button", { name: "사진 추가" }));
  await userEvent.click(screen.getByRole("menuitem", { name: "갤러리" }));
  const file = new File(["x"], "cafe.jpg", { type: "image/jpeg" });
  fireEvent.change(screen.getByTestId("photo-gallery-input"), { target: { files: [file] } });
}

test("위치 설정에서 정한 검색 기준으로 사진을 찾는다", async () => {
  /* 예전에는 위치 설정을 아예 안 읽어서, 검색 기준을 정해 둔 사용자가 사진을
     올려도 그 값이 요청에 실리지 않았다. 서버도 알 길이 없어(세션 조건은 채팅을
     보내야 채워진다) 위치를 정했는데도 "어디 근처에서 찾을까요?"가 나왔다. */
  let sentForm: FormData | undefined;
  vi.stubGlobal("fetch", photoFetch((form) => (sentForm = form)));
  setLocationCenter("성수동");
  await renderApp();

  await uploadPhoto();
  await screen.findByText("감성 카페");

  expect(sentForm?.get("location_query")).toBe("성수동");
});

test("검색 기준이 없으면 출발지를 쓴다", async () => {
  /* 서버의 사진 경로도 search_center → current_location 순으로 찾는다. */
  let sentForm: FormData | undefined;
  vi.stubGlobal("fetch", photoFetch((form) => (sentForm = form)));
  setLocationOrigin("안국역");
  await renderApp();

  await uploadPhoto();
  await screen.findByText("감성 카페");

  expect(sentForm?.get("location_query")).toBe("안국역");
});

test("사진 검색은 기기 위치를 묻지 않고 좌표도 싣지 않는다", async () => {
  let sentForm: FormData | undefined;
  vi.stubGlobal("fetch", photoFetch((form) => (sentForm = form)));
  setLocationCenter("성수동");
  await renderApp();

  await uploadPhoto();
  await screen.findByText("감성 카페");

  expect(navigator.geolocation.getCurrentPosition).not.toHaveBeenCalled();
  expect(sentForm?.get("latitude")).toBeNull();
  expect(sentForm?.get("longitude")).toBeNull();
});

test("위치가 하나도 없으면 요청하지 않고 위치를 정하도록 안내한다", async () => {
  /* 보내봐야 서버가 location_required로 되돌려줄 뿐이고, 그것은 오류 배너로 나와서
     사용자가 할 수 있는 일이 없었다. */
  let photoRequests = 0;
  vi.stubGlobal("fetch", photoFetch(() => (photoRequests += 1)));
  await renderApp();

  await uploadPhoto();

  expect(await screen.findByText(/위치를 정하고 사진을 다시 올려/)).toBeInTheDocument();
  expect(photoRequests).toBe(0);
  /* 기기 위치로 메우지도 않는다. */
  expect(navigator.geolocation.getCurrentPosition).not.toHaveBeenCalled();
  /* 오류 배너로 띄우지 않는다 — 실패가 아니라 아직 답하지 않은 물음이다. */
  expect(screen.queryByRole("alert")).toBeNull();
});

test("안내의 위치 정하기를 누르면 위치 설정 화면으로 간다", async () => {
  vi.stubGlobal("fetch", photoFetch());
  await renderApp();

  await uploadPhoto();
  await screen.findByText(/위치를 정하고 사진을 다시 올려/);
  await userEvent.click(screen.getByRole("button", { name: "위치 정하기" }));

  await waitFor(() => expect(window.location.pathname).toBe("/location"));
});

test("사진으로 시작한 대화가 바로 채팅 히스토리에 올라간다", async () => {
  /* 사진 검색은 위치가 있어야 요청이 나간다. 기기 위치는 받지 않으므로 이름으로 정해 둔다. */
  setLocationCenter("성수동");
  /*
   * 예전에는 사진 검색이 phase를 안 건드려 초기값 idle에 머물렀다. 사이드바가
   * 목록을 다시 받는 조건이 "phase가 ready이고 session_id가 있을 때"라
   * (SideDrawerContent), 다음 발화가 ready로 바꿀 때까지 목록에 안 나타났다.
   */
  const sessionListCalls: string[] = [];
  const base = mockFetch();
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/places/similar-by-photo")) {
        return Response.json({
          places: [
            {
              content_id: "photo-place-1",
              title: "감성 카페",
              similarity: 0.82,
              photo_count: 3,
              address: "서울 성동구",
              image_url: null,
            },
          ],
          center_name: "성수동",
          session_id: "photo-session-1",
          candidate_count: 12,
          truncated_count: 0,
          elapsed_ms: 400,
        });
      }
      if (url.endsWith("/sessions")) {
        sessionListCalls.push(url);
        return Response.json({
          sessions: sessionListCalls.length > 1
            ? [
                {
                  session_id: "photo-session-1",
                  title: "성수동 사진으로 찾은 곳",
                  location: "성수동",
                  last_active_at: "2026-09-09T09:00:00+09:00",
                },
              ]
            : [],
        });
      }
      return base(input);
    }),
  );
  await renderApp();

  await uploadPhoto();
  await screen.findByText("감성 카페");

  /* 발화를 하나도 더 보내지 않았는데 목록을 다시 받아야 한다. */
  await waitFor(() => expect(sessionListCalls.length).toBeGreaterThan(1));
  expect(await screen.findAllByText("성수동 사진으로 찾은 곳")).not.toHaveLength(0);
});

test("창을 새로 열고 사진이 첫 턴인 대화를 열어도 화면이 살아 있다", async () => {
  /*
   * 사진 검색 기록은 payload가 AgentResponse가 아니다. 복원이 그것을 모르고
   * buildAgentMessages에 넘기면 llm_output을 읽다가 터져 화면이 통째로 죽는다.
   */
  const base = mockFetch();
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/sessions")) {
        return Response.json({
          sessions: [
            {
              session_id: "photo-session-1",
              title: "성수동 사진으로 찾은 곳",
              location: "성수동",
              last_active_at: "2026-09-09T09:00:00+09:00",
            },
          ],
        });
      }
      if (url.endsWith("/sessions/photo-session-1/resume")) {
        return Response.json({
          session_id: "photo-session-1",
          title: "성수동 사진으로 찾은 곳",
          turns: [
            {
              user_input: "이 사진과 비슷한 장소 추천해줘",
              assistant_message: "성수동 주변에서 분위기가 닮은 곳 1곳을 찾았어요.",
              intent: null,
              question_type: null,
              place_names: ["감성 카페"],
              offered_action: null,
              at: "2026-09-09T09:00:00+09:00",
            },
          ],
          recommendations: [],
          messages: [
            {
              session_id: "photo-session-1",
              run_id: null,
              user_id: null,
              user_input: "이 사진과 비슷한 장소 추천해줘",
              payload: {
                kind: "photo_similar",
                session_id: "photo-session-1",
                center_name: "성수동",
                candidate_count: 12,
                truncated_count: 0,
                elapsed_ms: 400,
                places: [
                  {
                    content_id: "photo-place-1",
                    title: "감성 카페",
                    similarity: 0.82,
                    photo_count: 3,
                    address: "서울 성동구",
                    image_url: null,
                  },
                ],
              },
              recorded_at: "2026-09-09T09:00:00+09:00",
            },
          ],
          restore_from_messages: true,
          last_active_at: "2026-09-09T09:00:00+09:00",
          resumable: true,
        });
      }
      return base(input);
    }),
  );
  await renderApp();

  const entries = await screen.findAllByText("성수동 사진으로 찾은 곳");
  await userEvent.click(entries[0]);

  /* 되돌아온 화면이 그려져야 한다 — 터지면 여기서 아무것도 못 찾는다. */
  expect(await screen.findByText("감성 카페")).toBeInTheDocument();
  expect(screen.getByText("이 사진과 비슷한 장소 추천해줘")).toBeInTheDocument();
  expect(screen.getByText("[사용자 입력 사진]")).toBeInTheDocument();
});

