/*
 * 역할: 저장한 일정 목록의 열기·이름 바꾸기·삭제를 잠근다.
 *
 * **원래 `SideDrawerContent.test.tsx`에 있던 테스트를 옮긴 것이다**(2026-09-04).
 * 목록이 사이드바에서 일정 화면으로 옮겨갔으므로 테스트도 함께 왔다.
 *
 * 저쪽은 `<App/>`을 통째로 띄우는 하네스였는데(사이드바가 앱 셸 안에만 있어서),
 * 이 컴포넌트는 자기 데이터를 직접 받아오므로 컴포넌트만 띄운다 — 대화 목록·
 * 즐겨찾기·로그아웃을 함께 세울 이유가 없다.
 *
 * 옮기면서 **테스트 하나를 지웠다**: "대화와 저장한 일정의 id가 겹쳐도 메뉴는
 * 하나만 뜬다". 그것은 두 목록이 메뉴 상태를 한 벌로 나눠 쓰며 `{ kind, id }`로
 * 구분할 때만 성립하던 가드다. 목록이 분리돼 각자 자기 id만 들게 됐으므로 겹칠
 * 대상 자체가 없어졌다.
 */

import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test, vi } from "vitest";
import { MemoryRouter, useLocation } from "react-router-dom";
import { AuthProvider } from "../../auth/AuthContext";
import { TripProvider } from "../../state/TripContext";
import { refreshSavedSchedules, resetSavedSchedulesCache } from "../../state/savedSchedules";
import { GUEST_SESSION, resetSupabaseMock, setMockSession } from "../../test/supabaseMock";
import { resetSupabaseClient } from "../../auth/supabaseClient";
import { SavedScheduleList } from "./SavedScheduleList";

const server = vi.hoisted(() => ({
  schedules: [] as {
    id: string;
    title: string;
    session_id: string | null;
    created_at: string;
    updated_at: string;
  }[],
  renamed: [] as { id: string; title: string }[],
  deleted: [] as string[],
}));

vi.mock("../../api/trip", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api/trip")>();
  return {
    ...actual,
    fetchSavedSchedules: async () => ({ items: server.schedules }),
    renameSavedSchedule: async (scheduleId: string, title: string) => {
      server.renamed.push({ id: scheduleId, title });
      server.schedules = server.schedules.map((item) =>
        item.id === scheduleId ? { ...item, title } : item,
      );
      return { ...server.schedules[0], title };
    },
    deleteSavedSchedule: async (scheduleId: string) => {
      server.deleted.push(scheduleId);
      server.schedules = server.schedules.filter((item) => item.id !== scheduleId);
      return { id: scheduleId, deleted: true };
    },
  };
});

/*
 * **저장 시각을 "오늘"로 만든다**(2026-09-08). 목록이 오늘 날짜로 필터된 채
 * 열리게 바뀌어서(SavedScheduleList), 고정 날짜를 쓰면 아래 테스트 대부분이
 * "저장한 일정이 하나도 없는" 화면을 보게 된다.
 *
 * 시각을 정오·오전으로 박아 두 항목이 항상 **같은 날**에 떨어지게 한다 —
 * `new Date()`에서 몇 시간을 빼는 식으로 만들면 자정 무렵에 돌릴 때 둘이 다른
 * 날로 갈려 필터에 하나만 걸린다.
 */
function todayAt(hour: number, minute: number): string {
  const at = new Date();
  at.setHours(hour, minute, 0, 0);
  return at.toISOString();
}

const SEED = [
  {
    id: "sched-1",
    title: "종로 반나절",
    session_id: "chat-1",
    created_at: todayAt(14, 30),
    updated_at: todayAt(14, 30),
  },
  {
    id: "sched-2",
    title: "성수 저녁 코스",
    session_id: "chat-2",
    created_at: todayAt(18, 0),
    updated_at: todayAt(18, 0),
  },
];

/* 달력 필터는 날짜가 갈려야 확인된다 — 그 테스트만 고정 날짜를 쓴다. */
const WEEK_SEED = [
  { ...SEED[0], created_at: "2026-08-31T14:30:00+09:00", updated_at: "2026-08-31T14:30:00+09:00" },
  { ...SEED[1], created_at: "2026-09-01T18:00:00+09:00", updated_at: "2026-09-01T18:00:00+09:00" },
];

beforeEach(() => {
  localStorage.clear();
  server.schedules = [];
  server.renamed = [];
  server.deleted = [];
  resetSavedSchedulesCache();
  resetSupabaseMock();
  resetSupabaseClient();
  setMockSession(GUEST_SESSION);
});

/* MemoryRouter는 window.location을 건드리지 않는다(원본 테스트는 <App/>과 실제
   라우터를 썼다). 이동 결과를 보려면 라우터 안에서 위치를 읽어 내보내야 한다. */
function LocationProbe() {
  return <output data-testid="search">{useLocation().search}</output>;
}

function renderList() {
  return render(
    <AuthProvider>
      <MemoryRouter initialEntries={["/schedule"]}>
        <TripProvider>
          <SavedScheduleList />
          <LocationProbe />
        </TripProvider>
      </MemoryRouter>
    </AuthProvider>,
  );
}

/*
 * 저장한 일정이 없어도 **검색바와 달력은 그린다**(2026-09-16). 저장이 하나 생기는
 * 순간 이 요소들이 갑자기 나타나면 화면이 다른 구조로 바뀐다 — 틀은 늘 두고
 * 내용만 비운다.
 *
 * **같이 지키는 것이 더 중요하다**: 이때 "조건에 맞는 …이 없어요"는 내지 않는다.
 * 저장이 0건이라는 안내는 화면(SchedulePage)이 "홈에서 일정 짜기"와 함께 내므로,
 * 여기서 또 내면 **비었다는 안내가 두 개 겹쳐 보인다** — 예전에 실제로 그랬다.
 */
test("저장한 일정이 없어도 검색바와 달력은 보이고, 빈 안내는 겹치지 않는다", async () => {
  renderList();

  expect(
    await screen.findByRole("textbox", { name: "저장한 일정 검색" }),
  ).toBeInTheDocument();
  /* 달력 띠가 함께 있는지는 주 이동 버튼으로 본다. */
  expect(screen.getByRole("button", { name: "지난 주" })).toBeInTheDocument();

  /* 필터 안내는 저장이 있을 때만 낸다. */
  expect(screen.queryByText("조건에 맞는 저장한 일정이 없어요.")).not.toBeInTheDocument();
  expect(screen.queryAllByRole("listitem")).toHaveLength(0);
});

test("저장한 일정이 목록에 뜨고 누르면 그 일정이 열린다", async () => {
  server.schedules = [SEED[0]];
  renderList();

  /* 한 줄에 "열기"와 "메뉴" 두 버튼이 있다 — 정규식으로 찾으면 둘 다 걸린다. */
  const entry = await screen.findByRole("button", { name: "종로 반나절 일정 열기" });

  /* 카드 아이콘은 고정 아이콘 대신 계정 아바타다(2026-09-07) — 기본 세션은
     게스트라 이니셜이 "게"다. */
  expect(screen.getByText("게")).toBeInTheDocument();

  await userEvent.click(entry);

  /* SchedulePage가 ?saved=로 받아 같은 화면에서 갈아 끼운다. */
  await waitFor(() => expect(screen.getByTestId("search").textContent).toContain("saved=sched-1"));
});

test("저장한 일정 이름을 바꾸면 새 이름이 남는다", async () => {
  server.schedules = [...SEED];
  const user = userEvent.setup();
  renderList();

  await user.click(await screen.findByRole("button", { name: "종로 반나절 메뉴" }));
  await user.click(screen.getByRole("menuitem", { name: "이름 바꾸기" }));
  const input = screen.getByRole("textbox", { name: "일정 이름" });
  await user.clear(input);
  await user.type(input, "종로 반나절 (수정){Enter}");

  await waitFor(() =>
    expect(server.renamed).toEqual([{ id: "sched-1", title: "종로 반나절 (수정)" }]),
  );
  expect(await screen.findByText("종로 반나절 (수정)")).toBeInTheDocument();
});

test("저장한 일정을 삭제하면 목록에서 빠진다", async () => {
  server.schedules = [...SEED];
  const user = userEvent.setup();
  renderList();

  await user.click(await screen.findByRole("button", { name: "성수 저녁 코스 메뉴" }));
  await user.click(screen.getByRole("menuitem", { name: "삭제" }));

  await waitFor(() => expect(server.deleted).toEqual(["sched-2"]));
  await waitFor(() => expect(screen.queryByText("성수 저녁 코스")).not.toBeInTheDocument());
  // 다른 줄은 그대로 있다.
  expect(screen.getByText("종로 반나절")).toBeInTheDocument();
});

/*
 * 삭제 직후 화면에서 빠지는 것은 이 컴포넌트의 로컬 state(setSchedules) 덕분이고,
 * `state/savedSchedules.ts`의 캐시(cached 프라미스)는 그것과 별개다. 삭제 성공 뒤
 * refreshSavedSchedules()를 부르지 않으면 캐시는 삭제 전 목록을 계속 들고 있다가,
 * 다른 화면에 다녀와 이 컴포넌트가 다시 마운트될 때(useSavedSchedules의
 * loadSavedSchedules() 호출) 그 캐시를 그대로 돌려줘 지운 일정이 되살아난다
 * (2026-09-10 실사용 보고).
 *
 * resetSavedSchedulesCache()를 부르지 않는 것이 이 테스트의 핵심이다 — 그 함수는
 * "페이지를 새로고침한 경계"를 흉내 낼 때 쓰는 것이라, 부르면 이 버그가 가려진다.
 * 여기서는 페이지 이동 뒤 돌아오는 것(같은 로드 안에서의 재마운트)을 흉내 낸다.
 */
test("삭제한 일정은 다른 화면에 다녀와도 되살아나지 않는다", async () => {
  server.schedules = [...SEED];
  const user = userEvent.setup();
  const first = renderList();

  await user.click(await screen.findByRole("button", { name: "성수 저녁 코스 메뉴" }));
  await user.click(screen.getByRole("menuitem", { name: "삭제" }));
  await waitFor(() => expect(server.deleted).toEqual(["sched-2"]));

  // 일정 화면을 벗어났다가 돌아온다 — 컴포넌트가 새로 마운트되지만 페이지는
  // 새로고침되지 않는다.
  first.unmount();
  renderList();

  await screen.findByText("종로 반나절");
  expect(screen.queryByText("성수 저녁 코스")).not.toBeInTheDocument();
});

/* 대화 목록과 별도 저장소다. 세션이 30일 뒤 정리돼도 저장한 일정은 남는다 —
   사이드바에 있을 때는 "대화가 없어도 보인다"로 잠갔던 것을, 목록이 분리된 뒤에는
   대화와 무관하다는 사실 자체로 잠근다(대화 목록을 세우지 않고도 그려진다). */
test("대화 목록 없이도 저장한 일정만으로 그려진다", async () => {
  server.schedules = [{ ...SEED[0], session_id: null }];
  renderList();

  /* 빈 목록일 때도 <ul>은 그려지므로(2026-09-16) findByRole("list")를 로딩
     대기로 쓸 수 없다 — 데이터가 온 것을 텍스트로 먼저 확인한다. */
  await screen.findByText("종로 반나절");
  const list = screen.getByRole("list");
  expect(within(list).getByText("종로 반나절")).toBeInTheDocument();
});

/*
 * 일정을 저장하면 목록이 **바로** 바뀌어야 한다. 새로고침해야 보이면 사용자는
 * 저장이 안 된 줄 안다(`savedSchedules.refreshSavedSchedules` 주석).
 *
 * 발신 측(저장 뒤 refresh를 부르는 것)은 `ScheduleResultMessage.test.tsx`가
 * 잠갔다. 여기서는 **수신 측** — 이 컴포넌트가 그 알림을 받아 다시 그리는지를
 * 본다. 구독을 끊어도 다른 테스트는 전부 통과했다(2026-09-04 되돌림 확인).
 */
test("목록이 갱신되면 다시 그린다", async () => {
  server.schedules = [SEED[0]];
  renderList();
  expect(await screen.findByText("종로 반나절")).toBeInTheDocument();

  server.schedules = [...SEED];
  await act(async () => {
    await refreshSavedSchedules();
  });

  expect(await screen.findByText("성수 저녁 코스")).toBeInTheDocument();
});

/*
 * 검색·달력 필터(2026-09-07)는 목록을 지우지 않고 걸러낸다 — 실제로 지우면
 * 삭제와 구분이 안 된다.
 */
test("검색어를 넣으면 이름이 안 맞는 일정은 숨는다", async () => {
  server.schedules = [...SEED];
  const user = userEvent.setup();
  renderList();
  await screen.findByText("종로 반나절");

  await user.type(screen.getByRole("textbox", { name: "저장한 일정 검색" }), "성수");

  expect(screen.queryByText("종로 반나절")).not.toBeInTheDocument();
  expect(screen.getByText("성수 저녁 코스")).toBeInTheDocument();

  await user.clear(screen.getByRole("textbox", { name: "저장한 일정 검색" }));
  expect(await screen.findByText("종로 반나절")).toBeInTheDocument();
});

test("검색어에 맞는 일정이 없으면 안내만 뜨고 목록은 비운다", async () => {
  server.schedules = [...SEED];
  const user = userEvent.setup();
  renderList();
  await screen.findByText("종로 반나절");

  await user.type(screen.getByRole("textbox", { name: "저장한 일정 검색" }), "존재하지않음");

  expect(screen.getByText("조건에 맞는 저장한 일정이 없어요.")).toBeInTheDocument();
  expect(screen.queryAllByRole("listitem")).toHaveLength(0);
});

/*
 * 달력 띠에서 날짜를 고르면 그 날 저장한 일정만 남는다. `WEEK_SEED`의 두 날짜가
 * 같은 주(8/30 일~9/5 토)에 들도록 "지금"을 그 주 안으로 고정한다 — 실제
 * 오늘 기준이면 기본 화면이 다른 주를 보여줘 두 점이 안 보인다.
 *
 * 목록은 오늘(=8/31로 고정한 날) 것만 보인 채 열리므로, 8/31을 누르는 첫 단언은
 * 이미 그 상태를 확인하는 셈이고 두 번째(선택 해제)가 전체 보기를 확인한다.
 */
test("달력에서 날짜를 고르면 그 날 저장한 일정만 남는다", async () => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-08-31T09:00:00+09:00"));
  server.schedules = [...WEEK_SEED];
  const user = userEvent.setup();
  renderList();
  await screen.findByText("종로 반나절");

  const aug31 = screen.getByRole("button", { name: "8월 31일, 저장한 일정 있음" });

  /* 열자마자 오늘(고정한 8/31) 것만 보이고, 그 날짜가 눌린 상태로 시작한다. */
  expect(aug31).toHaveAttribute("aria-pressed", "true");
  expect(screen.queryByText("성수 저녁 코스")).not.toBeInTheDocument();

  /* 눌린 날짜를 다시 누르면 선택이 풀려 다른 날 것도 보인다. */
  await user.click(aug31);
  expect(aug31).toHaveAttribute("aria-pressed", "false");
  expect(screen.getByText("성수 저녁 코스")).toBeInTheDocument();

  /* 다시 고르면 그 날 저장한 것만 남는다. */
  await user.click(aug31);
  expect(screen.getByText("종로 반나절")).toBeInTheDocument();
  expect(screen.queryByText("성수 저녁 코스")).not.toBeInTheDocument();

  vi.useRealTimers();
});

/*
 * 대화 목록과 같은 사고가 여기에도 있었다. 게스트로 처음 들어와 받은 목록이
 * 신원이 바뀐 뒤에도 그대로 나와, 로그인해도 새로고침해야 자기 일정이 보였다
 * (state/savedSchedules.ts의 cachedUserId).
 */
test("로그인해서 신원이 바뀌면 그 계정의 저장 일정으로 갈아탄다", async () => {
  server.schedules = [{ ...SEED[0], title: "게스트가 저장한 일정" }];
  renderList();
  await screen.findByText("게스트가 저장한 일정");

  server.schedules = [{ ...SEED[1], title: "로그인한 계정의 일정" }];
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

  await screen.findByText("로그인한 계정의 일정");
  expect(screen.queryByText("게스트가 저장한 일정")).not.toBeInTheDocument();
});
