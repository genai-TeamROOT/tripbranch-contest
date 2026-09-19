/*
 * 역할: 일정 화면의 빈 상태(짠 일정 없음)를 검증한다.
 * 호출 시점: vitest 실행 시.
 */

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { AuthProvider } from "../auth/AuthContext";
import { AppShellProvider } from "../components/layout/AppShellContext";
import { TripProvider } from "../state/TripContext";
import { resetSavedSchedulesCache } from "../state/savedSchedules";
import { SchedulePage } from "./SchedulePage";

beforeEach(() => {
  sessionStorage.clear();
  /* 저장 목록 캐시는 모듈 수준이라 같은 파일의 앞 테스트 결과가 그대로 남는다.
     이 화면은 그 목록으로 무엇을 그릴지 정하므로 테스트마다 비운다. */
  resetSavedSchedulesCache();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

test("저장한 일정이 없으면 저장하라고 안내한다", async () => {
  const user = userEvent.setup();
  render(
    <AuthProvider>
      <MemoryRouter initialEntries={["/schedule"]}>
        <AppShellProvider>
          <TripProvider>
            <SchedulePage />
          </TripProvider>
        </AppShellProvider>
      </MemoryRouter>
    </AuthProvider>,
  );

  /*
   * **받아오기 전에는 안내가 없다.** 저장한 일정이 있는 사람에게 "저장한 일정이
   * 없어요"가 한 번 스쳤다 사라지면 안 된다 — 그래서 목록이 도착하기 전인 이
   * 순간을 먼저 확인한다(비동기 대기 없이 바로 본다).
   */
  expect(screen.queryByText("저장한 일정이 없어요. 채팅에서 일정을 저장하면 여기에 모여요.")).not.toBeInTheDocument();

  /* 목록이 도착하고, 비어 있으니 그제야 안내가 뜬다. */
  expect(await screen.findByText("저장한 일정이 없어요. 채팅에서 일정을 저장하면 여기에 모여요.")).toBeInTheDocument();

  /* **검색바와 달력은 비어 있어도 함께 보인다**(2026-09-16). 저장이 하나 생기는
     순간 이 요소들이 튀어나오지 않게 틀을 늘 같은 자리에 둔다. */
  expect(screen.getByRole("textbox", { name: "저장한 일정 검색" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "지난 주" })).toBeInTheDocument();
  /* 안내가 두 개 겹치지 않는다. */
  expect(screen.queryByText("조건에 맞는 저장한 일정이 없어요.")).not.toBeInTheDocument();

  const cta = screen.getByRole("button", { name: "홈에서 일정 짜기" });
  await user.click(cta);
});

/* TripProvider는 sessionStorage(state/storage.ts)에서 복원한다 — APPEND_CHAT_TURN을
   온전히 재현하려면 AgentResponse 전체가 필요해 무거우니, 저장 형식을 직접
   심어 넣는다(isChatMessage의 schedule_result 분기가 요구하는 필드만 채움). */
function seedScheduleState() {
  sessionStorage.setItem(
    "tripbranch_state",
    JSON.stringify({
      version: 7,
      state: {
        language: "ko",
        user_input: "",
        interpreted_conditions: null,
        recommendations: [],
        unverified_recommendations: [],
        shown_place_ids: [],
        messages: [
          {
            id: "schedule-1",
            type: "schedule_result",
            elapsed_ms: 120,
            schedule: {
              items: [
                {
                  order: 1,
                  place_id: "place-1",
                  place_name: "역삼 아트뮤지엄",
                  estimated_arrival: "15:02",
                  estimated_duration_min: 60,
                  travel_to_next_min: 12,
                  travel_to_next_mode: "transit",
                  travel_to_next_measured: true,
                  reason: "실내라 비를 피하며 둘러보기 좋아요",
                },
                {
                  order: 2,
                  place_id: "place-2",
                  place_name: "대림창고",
                  estimated_arrival: "16:14",
                  estimated_duration_min: 45,
                  travel_to_next_min: null,
                  reason: "천장이 높아 사진 찍기 좋은 공간이에요",
                },
              ],
              total_duration_min: 105,
              route_summary: "역삼 아트뮤지엄을 둘러본 후 대림창고로 이동하는 동선이에요.",
              basis_note: "이 정보는 계산 당시 시각 기준이에요.",
              elapsed_ms: 120,
            },
          },
        ],
        auditTurns: [],
        phase: "ready",
        error: null,
        session_id: null,
        awaiting_clarification: false,
        saved_places: [],
        agentProgress: null,
        streamingIntent: null,
      },
    }),
  );
}

/*
 * **"지금 일정" 줄을 없앤 뒤의 계약이다(2026-09-16).** 저장하지 않은 일정은 이
 * 화면에 오지 않는다. 없앨 때 **대부분이 보이지 않게 되는 것을 알고** 내린
 * 결정이라(실측: 편성 100건 대 저장 14건, D 결정) 이 테스트가 깨지면 그 결정이
 * 되돌아온 것이다. 자동 저장은 두지 않기로 했으므로 채팅의 저장 버튼이 유일한
 * 경로다.
 */
test("저장하지 않은 일정은 이 화면에 오지 않는다", async () => {
  seedScheduleState();
  render(
    <AuthProvider>
      <MemoryRouter initialEntries={["/schedule"]}>
        <AppShellProvider>
          <TripProvider>
            <SchedulePage />
          </TripProvider>
        </AppShellProvider>
      </MemoryRouter>
    </AuthProvider>,
  );

  /* 세션에 일정이 있어도 목록은 비어 있으므로 안내가 뜬다. */
  expect(await screen.findByText("저장한 일정이 없어요. 채팅에서 일정을 저장하면 여기에 모여요.")).toBeInTheDocument();
  expect(screen.queryByText(/지금 일정/)).not.toBeInTheDocument();
  /* 상세가 열릴 길 자체가 없다. */
  expect(screen.queryByText("역삼 아트뮤지엄")).not.toBeInTheDocument();
});

test("목록↔상세를 오가면 PageTransition의 떠오르는 페이드가 다시 걸린다", async () => {
  const user = userEvent.setup();
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) =>
      String(input).includes("/schedules/")
        ? Response.json(SAVED_TWO_STOP)
        : Response.json({
            items: [
              {
                id: SAVED_TWO_STOP.id,
                title: SAVED_TWO_STOP.title,
                session_id: null,
                created_at: SAVED_TWO_STOP.created_at,
                updated_at: SAVED_TWO_STOP.updated_at,
              },
            ],
          }),
    ),
  );
  const { container } = render(
    <AuthProvider>
      <MemoryRouter initialEntries={["/schedule"]}>
        <AppShellProvider>
          <TripProvider>
            <SchedulePage />
          </TripProvider>
        </AppShellProvider>
      </MemoryRouter>
    </AuthProvider>,
  );

  /* AppShell 바깥에서 단독으로 띄운 화면이라 라우팅에 걸리는 PageTransition은
     안 탄다 — 이 화면이 안에 한 겹 더 두는 것을 직접 확인한다. */
  expect(container.querySelector(".tb-page-enter")).toBeInTheDocument();

  await user.click(await screen.findByRole("button", { name: "성수 저녁 코스 일정 열기" }));
  expect(container.querySelector(".tb-page-enter")).toBeInTheDocument();

  await user.click(await screen.findByRole("button", { name: "목록으로" }));
  expect(container.querySelector(".tb-page-enter")).toBeInTheDocument();
});

/*
 * 저장한 일정 열기. (SCHEDULE 카드 2)
 *
 * **이 화면을 재사용하는 이유**는 사이드바 "일정"이 이미 여기를 열기 때문이다.
 * 목록에서 고른 일정이 다른 모양으로 열리면 같은 것을 두 가지로 그리게 된다.
 */

const SAVED_DETAIL = {
  id: "11111111-2222-4333-8444-555555555555",
  title: "종로 반나절",
  session_id: "sess_1",
  created_at: "2026-08-31T14:30:00+09:00",
  updated_at: "2026-08-31T14:30:00+09:00",
  payload: {
    items: [
      {
        order: 1,
        place_id: "p1",
        place_name: "경복궁",
        estimated_arrival: "14:30",
        estimated_duration_min: 90,
        reason: "조용히 걷기 좋아요",
        travel_to_next_min: null,
        travel_to_next_mode: null,
        travel_to_next_measured: null,
        warnings: [],
      },
    ],
    total_duration_min: 90,
    route_summary: "경복궁 한 바퀴",
    basis_note: "8월 31일 14:30 기준",
    elapsed_ms: 1200,
  },
};

/*
 * 두 정류장짜리 저장 일정. 이동수단 표기와 피드백 토글은 원래 "지금 일정"
 * 상세에서 검증했는데, 그 줄을 없애면서(2026-09-16) 저장 경로로 옮겼다 —
 * 같은 ScheduleRoute를 쓰므로 검증 대상은 그대로다.
 */
/* 목록의 날짜 띠가 이번 주만 보여주고 날짜 필터가 걸리므로, 목록에 뜨려면
   created_at이 오늘이어야 한다 — 고정 날짜를 쓰면 주가 바뀌는 순간 행이 사라진다. */
const TODAY_ISO = new Date().toISOString();

const SAVED_TWO_STOP = {
  id: "aaaaaaaa-1111-4222-8333-444444444444",
  title: "성수 저녁 코스",
  session_id: "sess_2",
  created_at: TODAY_ISO,
  updated_at: TODAY_ISO,
  payload: {
    items: [
      {
        order: 1,
        place_id: "place-1",
        place_name: "역삼 아트뮤지엄",
        estimated_arrival: "15:02",
        estimated_duration_min: 60,
        travel_to_next_min: 12,
        travel_to_next_mode: "transit",
        travel_to_next_measured: true,
        reason: "실내라 비를 피하며 둘러보기 좋아요",
        warnings: [],
      },
      {
        order: 2,
        place_id: "place-2",
        place_name: "대림창고",
        estimated_arrival: "16:14",
        estimated_duration_min: 45,
        travel_to_next_min: null,
        travel_to_next_mode: null,
        travel_to_next_measured: null,
        reason: "천장이 높아 사진 찍기 좋은 공간이에요",
        warnings: [],
      },
    ],
    total_duration_min: 105,
    route_summary: "역삼 아트뮤지엄을 둘러본 후 대림창고로 이동하는 동선이에요.",
    basis_note: "이 정보는 계산 당시 시각 기준이에요.",
    elapsed_ms: 120,
  },
};

function renderSaved(id: string) {
  return render(
    <AuthProvider>
      <MemoryRouter initialEntries={[`/schedule?saved=${id}`]}>
        <AppShellProvider>
          <TripProvider>
            <SchedulePage />
          </TripProvider>
        </AppShellProvider>
      </MemoryRouter>
    </AuthProvider>,
  );
}

test("저장한 일정을 열면 그때 편성이 그대로 보인다", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json(SAVED_DETAIL)),
  );

  renderSaved(SAVED_DETAIL.id);

  expect((await screen.findAllByText("경복궁")).length).toBeGreaterThan(0);
  expect(screen.getByText("경복궁 한 바퀴")).toBeInTheDocument();
});

/*
 * 저장한 일정에는 시간 띠의 "지금"을 얹지 않는다.
 *
 * **시계를 고정해야 의미가 있는 테스트다.** 저장 일정은 14:30~16:00 인데, 그
 * 바깥 시각에 돌면 "지금"은 어차피 안 뜬다 — 그러면 이 테스트는 배선이 끊겨도
 * 통과한다(2026-09-06 되돌림 확인에서 실제로 그랬다). 일정 한가운데로 시계를
 * 맞춰, 넘기기만 하면 뜨는 상태에서 안 뜨는 것을 본다.
 */
test("저장한 일정 상세가 정류장 타임라인과 피드백 토글을 보여준다", async () => {
  const user = userEvent.setup();
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json(SAVED_TWO_STOP)),
  );

  renderSaved(SAVED_TWO_STOP.id);

  /* 장소 이름은 두 곳에 나온다 — 시간 띠의 범례와 정류장 카드. 범례는 aria-hidden
     이라 소리로는 한 번만 읽히지만, 화면 질의에는 둘 다 걸린다. */
  expect((await screen.findAllByText("역삼 아트뮤지엄")).length).toBeGreaterThan(0);
  expect(screen.getAllByText("대림창고").length).toBeGreaterThan(0);
  // 서버가 내려준 이동수단을 그대로 쓴다 — 예전에는 전 구간을 도보로 고정 표기했다(TP-216).
  expect(screen.getByText("대중교통으로 12분")).toBeInTheDocument();
  // 마지막 정류장은 다음 이동이 없다(travel_to_next_min === null) — 구간 표기는 한 줄뿐이다.
  expect(screen.queryAllByText(/으로 \d+분$/)).toHaveLength(1);

  const helpful = screen.getByRole("button", { name: "도움이 됐어요" });
  expect(helpful).toHaveAttribute("aria-pressed", "false");
  await user.click(helpful);
  expect(helpful).toHaveAttribute("aria-pressed", "true");
});

test("저장한 일정에는 지금 표시가 뜨지 않는다", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json(SAVED_DETAIL)),
  );
  vi.useFakeTimers({ shouldAdvanceTime: true });
  vi.setSystemTime(new Date(2026, 7, 31, 15, 15));

  try {
    renderSaved(SAVED_DETAIL.id);
    await screen.findAllByText("경복궁");

    expect(screen.queryByText("지금")).not.toBeInTheDocument();
  } finally {
    vi.useRealTimers();
  }
});

/*
 * **여기가 이 기능에서 제일 틀리기 쉬운 곳이다.** 도착 시각·이동 시간은 저장
 * 시점 값이라, 화면이 지금 시각을 얹으면 사흘 전 일정이 방금 짠 것처럼 보인다.
 */
test("저장한 일정에는 지금 시각이 아니라 저장한 시각을 밝힌다", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json(SAVED_DETAIL)),
  );

  renderSaved(SAVED_DETAIL.id);

  expect(await screen.findByText(/저장한 일정이에요/)).toBeInTheDocument();
  expect(screen.queryByText(/기준으로 짠 동선이에요/)).not.toBeInTheDocument();
});

test("저장한 일정을 못 불러오면 그 사실을 알린다", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json({ error: { message: "not found" } }, { status: 404 })),
  );

  renderSaved(SAVED_DETAIL.id);

  expect(await screen.findByText(/불러오지 못했어요/)).toBeInTheDocument();
  /* 빈 목록 안내로 뭉뚱그리면 사용자는 저장이 안 된 줄 안다 — 오류는 오류라고 말한다. */
  expect(screen.queryByText("저장한 일정이 없어요. 채팅에서 일정을 저장하면 여기에 모여요.")).not.toBeInTheDocument();
});

/*
 * 저장한 일정 목록을 사이드바에서 여기로 옮겼다(2026-09-04). 저장한 것이 있을
 * 때 목록 화면(짠 일정 없음/있음)에는 있어야 한다.
 *
 * **불러오기 실패는 목록이 아니라 상세 자리에 뜬다**(2026-09-07). 목록과 상세를
 * 한 화면에 같이 두지 않기로 하면서, 다른 일정을 고르는 입구는 목록 자체가
 * 아니라 상세 화면 위의 "목록으로" 버튼이 맡는다 — `savedError` 테스트가 잠근다.
 */
test("저장한 일정이 있으면 세션 일정 유무와 무관하게 목록만 보인다", async () => {
  const saved = {
    items: [
      {
        id: "aaaaaaaa-1111-4222-8333-444444444444",
        title: "성수 저녁 코스",
        session_id: null,
        created_at: "2026-09-01T18:00:00+09:00",
        updated_at: "2026-09-01T18:00:00+09:00",
      },
    ],
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json(saved)),
  );

  /* 목록 구획이 떴는지는 검색창으로 본다 — 상단 "저장한 일정" 표제는
     2026-09-07에 없앴다. */
  const listed = () => screen.findByRole("textbox", { name: "저장한 일정 검색" });

  // ① 짠 일정이 없을 때
  const empty = render(
    <AuthProvider>
      <MemoryRouter initialEntries={["/schedule"]}>
        <AppShellProvider>
          <TripProvider>
            <SchedulePage />
          </TripProvider>
        </AppShellProvider>
      </MemoryRouter>
    </AuthProvider>,
  );
  expect(await listed()).toBeInTheDocument();
  /* 목록이 있으면 빈 안내는 뜨지 않는다. */
  expect(screen.queryByText("저장한 일정이 없어요. 채팅에서 일정을 저장하면 여기에 모여요.")).not.toBeInTheDocument();
  empty.unmount();

  // ② 저장하지 않은 일정이 세션에 있어도 목록만 보인다 — 줄이 늘지 않는다.
  seedScheduleState();
  const filled = render(
    <AuthProvider>
      <MemoryRouter initialEntries={["/schedule"]}>
        <AppShellProvider>
          <TripProvider>
            <SchedulePage />
          </TripProvider>
        </AppShellProvider>
      </MemoryRouter>
    </AuthProvider>,
  );
  expect(await listed()).toBeInTheDocument();
  expect(screen.queryByText(/지금 일정/)).not.toBeInTheDocument();
  filled.unmount();
});

/*
 * 저장한 일정을 못 불러오면 상세 자리에 오류가 뜨고, 목록은 같이 그리지 않는다
 * — "목록으로" 버튼이 다른 일정을 고르는 유일한 입구다.
 */
test("저장한 일정을 못 불러오면 목록 없이 오류와 '목록으로' 버튼만 뜬다", async () => {
  const saved = {
    items: [
      {
        id: "aaaaaaaa-1111-4222-8333-444444444444",
        title: "성수 저녁 코스",
        session_id: null,
        created_at: "2026-09-01T18:00:00+09:00",
        updated_at: "2026-09-01T18:00:00+09:00",
      },
    ],
  };
  const user = userEvent.setup();
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) =>
      String(input).includes("/schedules/")
        ? new Response(null, { status: 404 })
        : Response.json(saved),
    ),
  );

  renderSaved("gone");
  expect(
    await screen.findByText("이미 지워졌거나 접근 권한이 없을 수 있어요."),
  ).toBeInTheDocument();
  expect(screen.queryByRole("textbox", { name: "저장한 일정 검색" })).not.toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: "목록으로" }));
  expect(await screen.findByRole("textbox", { name: "저장한 일정 검색" })).toBeInTheDocument();
});
