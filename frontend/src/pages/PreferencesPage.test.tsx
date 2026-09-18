/*
 * 역할: 취향 설정 화면의 선택 개수 제한(최소 3·최대 5)과 초기화·직접 입력을 검증한다.
 * 호출 시점: vitest 실행 시.
 */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test, vi } from "vitest";
import { MemoryRouter, useLocation } from "react-router-dom";
import { AppShellProvider } from "../components/layout/AppShellContext";
import { TripProvider } from "../state/TripContext";
import { loadPreferences, savePreferences } from "../state/preferenceStorage";
import { resetPreferenceSync } from "../state/preferenceSync";
import { PreferencesPage } from "./PreferencesPage";
import { PREFERENCE_GROUPS } from "./preferenceOptions";

/*
 * 계정 저장소를 인메모리로 흉내 낸다. 취향은 이제 이 기기와 계정 양쪽에 남고,
 * 저장이 계정까지 닿아야 화면이 홈으로 넘어간다 — 실제 fetch를 그대로 두면 매번
 * 실패 경로만 타서 성공 흐름을 한 번도 안 밟는다.
 */
const server = vi.hoisted(() => ({
  items: [] as { label: string; source: string; codes: readonly string[] }[],
  updatedAt: null as string | null,
  failNext: false,
  calls: 0,
}));

vi.mock("../api/trip", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/trip")>();
  return {
    ...actual,
    fetchPreferences: async () => ({ items: server.items, updated_at: server.updatedAt }),
    replacePreferences: async (items: { label: string; source: string; codes: readonly string[] }[]) => {
      server.calls += 1;
      if (server.failNext) throw new Error("네트워크 실패");
      server.items = [...items];
      server.updatedAt = "2026-09-03T00:00:00+09:00";
      return { items: server.items, updated_at: server.updatedAt };
    },
  };
});

beforeEach(() => {
  localStorage.clear();
  resetPreferenceSync();
  server.items = [];
  server.updatedAt = null;
  server.failNext = false;
  server.calls = 0;
});

/** 저장 뒤 어디로 갔는지 보려고 현재 경로를 화면에 흘려둔다. */
function LocationProbe() {
  return <span data-testid="probe">{useLocation().pathname}</span>;
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/preferences"]}>
      <TripProvider>
        <AppShellProvider>
          <PreferencesPage />
          <LocationProbe />
        </AppShellProvider>
      </TripProvider>
    </MemoryRouter>,
  );
}

test("3개 미만이면 저장 버튼이 남은 개수를 안내하며 비활성 상태다", async () => {
  const user = userEvent.setup();
  renderPage();

  const saveButton = screen.getByRole("button", { name: "3개 더 골라주세요" });
  expect(saveButton).toBeDisabled();

  await user.click(screen.getByRole("button", { name: "조용한 곳" }));
  await user.click(screen.getByRole("button", { name: "카페" }));

  expect(screen.getByRole("button", { name: "1개 더 골라주세요" })).toBeDisabled();

  await user.click(screen.getByRole("button", { name: "아이와 함께" }));

  const enabled = screen.getByRole("button", { name: "저장하기" });
  expect(enabled).not.toBeDisabled();
  expect(screen.getByText("3–5개 중 3개 선택됨")).toBeInTheDocument();
});

/*
 * 세는 칩의 채움이 저장 가능 여부를 말한다(2026-09-17). 색만 보는 테스트가
 * 아니라 **경계에서 뒤집히는지**를 본다 — 2개까지는 비어 있고 3개째에 찬다.
 */
test("세는 칩은 3개째를 고르는 순간 브랜드 색으로 찬다", async () => {
  const user = userEvent.setup();
  renderPage();

  expect(screen.getByText("3–5개 중 0개 선택됨")).toHaveClass("bg-white", "text-brand");

  await user.click(screen.getByRole("button", { name: "조용한 곳" }));
  await user.click(screen.getByRole("button", { name: "카페" }));
  expect(screen.getByText("3–5개 중 2개 선택됨")).toHaveClass("bg-white", "text-brand");

  await user.click(screen.getByRole("button", { name: "아이와 함께" }));
  expect(screen.getByText("3–5개 중 3개 선택됨")).toHaveClass("bg-brand", "text-white");

  /* 하나 빼면 다시 비워진다 — 저장하기가 닫히는 것과 같은 경계다. */
  await user.click(screen.getByRole("button", { name: "카페" }));
  expect(screen.getByText("3–5개 중 2개 선택됨")).toHaveClass("bg-white", "text-brand");
});

/*
 * 테두리는 두 상태 모두에 있어야 한다. 한쪽에만 두면 3개째에서 칩이 1px씩
 * 커졌다 작아지며 옆 안내 문구를 민다.
 */
test("세는 칩의 테두리는 채움과 무관하게 유지된다", async () => {
  const user = userEvent.setup();
  renderPage();

  expect(screen.getByText("3–5개 중 0개 선택됨")).toHaveClass("border-brand");

  for (const label of ["조용한 곳", "카페", "아이와 함께"]) {
    await user.click(screen.getByRole("button", { name: label }));
  }
  expect(screen.getByText("3–5개 중 3개 선택됨")).toHaveClass("border-brand");
});

test("6번째 칩은 선택되지 않고, 초기화하면 전부 풀린다", async () => {
  const user = userEvent.setup();
  renderPage();

  const options = ["조용한 곳", "아늑한 공간", "야경 명소", "사진 명소", "힐링하기 좋은"];
  for (const label of options) {
    await user.click(screen.getByRole("button", { name: label }));
  }
  expect(screen.getByText("3–5개 중 5개 선택됨")).toBeInTheDocument();

  /* 6번째는 눌러도 아무 일이 안 나는 게 아니라 **아예 못 누른다**(2026-09-06).
     눌러도 반응이 없는 것과 고장은 화면에서 구분되지 않는다. */
  const sixth = screen.getByRole("button", { name: "넓고 쾌적한" });
  expect(sixth).toBeDisabled();
  await user.click(sixth);
  expect(screen.getByText("3–5개 중 5개 선택됨")).toBeInTheDocument();
  expect(sixth).toHaveAttribute("aria-pressed", "false");

  /* 이미 고른 칩은 빼는 동작이라 상한과 무관하게 눌린다. */
  expect(screen.getByRole("button", { name: "조용한 곳" })).toBeEnabled();

  await user.click(screen.getByRole("button", { name: "선택 초기화" }));
  expect(screen.getByText("3–5개 중 0개 선택됨")).toBeInTheDocument();
});

/*
 * **키워드를 직접 만드는 길을 없앴다**(2026-09-06). 칩 목록은 DB에 대응이 있는
 * 것만 남긴 것인데(아래 테스트), 직접 입력한 키워드는 `codes: []`라 어디에도
 * 대응하지 않는다 — 고를 수 있는 5칸 중 하나를 아무 데도 안 걸리는 값이 차지했다.
 */
test("키워드를 직접 만드는 입구가 없다", () => {
  renderPage();

  expect(screen.queryByRole("button", { name: "키워드 직접 입력" })).not.toBeInTheDocument();
});

/*
 * 이 화면의 칩은 예시 문구가 아니라 DB에 대응이 있는 것만 남긴 목록이다.
 * 근거 없는 문구가 다시 섞여 들어오는 것을 여기서 막는다 — 예전 목록에는
 * 대응이 0건인 칩이 5개 있었다(반려동물 동반·감성 인테리어·브런치 등).
 */
test("모든 칩이 대응하는 DB 코드를 하나 이상 갖는다", () => {
  const options = PREFERENCE_GROUPS.flat();
  expect(options.length).toBeGreaterThan(0);

  for (const option of options) {
    expect(option.codes.length, `${option.label}에 코드가 없다`).toBeGreaterThan(0);
    for (const code of option.codes) {
      expect(code.trim(), `${option.label}의 코드가 비었다`).not.toBe("");
    }
  }
});

test("칩 라벨이 축을 넘어 중복되지 않는다", () => {
  // 선택 상태를 label로 들고 있어서, 라벨이 겹치면 두 칩이 같이 눌린다.
  const labels = PREFERENCE_GROUPS.flat().map((option) => option.label);
  expect(new Set(labels).size).toBe(labels.length);
});

test("저장하면 이 기기에 남고, 다시 열면 고른 채로 시작한다", async () => {
  const user = userEvent.setup();
  const { unmount } = renderPage();

  for (const label of ["조용한 곳", "카페", "데이트 코스"]) {
    await user.click(screen.getByRole("button", { name: label }));
  }
  await user.click(screen.getByRole("button", { name: "저장하기" }));

  expect(loadPreferences()).toEqual([
    { label: "조용한 곳", source: "preference", codes: ["quiet"] },
    { label: "카페", source: "place_tag", codes: ["카페", "찻집"] },
    { label: "데이트 코스", source: "preference", codes: ["date"] },
  ]);

  unmount();
  renderPage();
  expect(screen.getByText("3–5개 중 3개 선택됨")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "카페" })).toHaveAttribute("aria-pressed", "true");
});

/*
 * 입구는 없앴지만 **전에 저장해 둔 키워드는 계속 보여야 한다.** 안 그리면 선택
 * 개수(N/5)에는 잡히는데 화면에 없는 칩이 생겨서, 5개를 다 못 고르는데 그 이유가
 * 어디에도 안 보인다. 여기 있으면 눌러서 빼고 저장하는 것으로 정리된다.
 */
test("전에 직접 넣어둔 키워드는 남아 있고 눌러서 뺄 수 있다", async () => {
  const user = userEvent.setup();
  savePreferences([
    { label: "조용한 서점", source: "custom", codes: [] },
    { label: "조용한 곳", source: "preference", codes: ["quiet"] },
    { label: "카페", source: "place_tag", codes: ["카페", "찻집"] },
  ]);
  renderPage();

  const custom = screen.getByRole("button", { name: "조용한 서점" });
  expect(custom).toHaveAttribute("aria-pressed", "true");
  expect(screen.getByText("3–5개 중 3개 선택됨")).toBeInTheDocument();

  await user.click(custom);
  expect(custom).toHaveAttribute("aria-pressed", "false");
  expect(screen.getByText("3–5개 중 2개 선택됨")).toBeInTheDocument();
});

/*
 * 저장 버튼은 3개 미만이면 눌리지 않아서 "다 빼고 저장"이라는 경로가 없다.
 * 초기화가 저장값까지 지우지 않으면 한번 저장한 취향을 되돌릴 방법이 사라진다.
 */
test("선택 초기화는 저장해 둔 값까지 지운다", async () => {
  const user = userEvent.setup();
  renderPage();

  for (const label of ["조용한 곳", "카페", "데이트 코스"]) {
    await user.click(screen.getByRole("button", { name: label }));
  }
  await user.click(screen.getByRole("button", { name: "저장하기" }));
  expect(loadPreferences()).toHaveLength(3);

  await user.click(screen.getByRole("button", { name: "선택 초기화" }));

  expect(loadPreferences()).toEqual([]);
  /* 문구를 통째로 본다. "홈 화면에서도 사라져요"가 되살아나면 여기서 걸린다 —
     홈에는 2026-09-07부터 취향이 안 보이므로 그 문장은 거짓이다(2026-09-17). */
  expect(screen.getByRole("status")).toHaveTextContent(/^저장해 둔 취향을 지웠어요\.$/);
});

test("저장하면 홈 화면으로 보낸다", async () => {
  const user = userEvent.setup();
  renderPage();

  expect(screen.getByTestId("probe")).toHaveTextContent("/preferences");
  for (const label of ["조용한 곳", "카페", "데이트 코스"]) {
    await user.click(screen.getByRole("button", { name: label }));
  }
  await user.click(screen.getByRole("button", { name: "저장하기" }));

  /* 계정까지 저장된 뒤에 넘어간다. 결과를 보려고 사용자가 한 번 더 홈으로
     이동하게 두지 않는다. */
  await waitFor(() => expect(screen.getByTestId("probe").textContent).toBe("/"));
});

/*
 * 저장하면 추천 순위에 반영된다(SCORING_VERSION 1.5.0). 부제는 이 사실만
 * 말한다(2026-09-07) — 홈 화면에 안 보인다는 부수적인 사실은 뺐다.
 */
test("부제가 추천에 반영된다는 사실을 밝힌다", () => {
  renderPage();

  expect(screen.getByText(/고르신 취향이 추천 결과에 반영돼요/)).toBeInTheDocument();
  expect(screen.queryByText(/홈 화면/)).not.toBeInTheDocument();
});


/* ------------------------------------------------------------ 계정 연결 */

test("저장하면 계정에도 올라간다", async () => {
  const user = userEvent.setup();
  renderPage();

  for (const label of ["조용한 곳", "카페", "데이트 코스"]) {
    await user.click(screen.getByRole("button", { name: label }));
  }
  await user.click(screen.getByRole("button", { name: "저장하기" }));

  await waitFor(() => expect(server.items.map((item) => item.label)).toEqual([
    "조용한 곳",
    "카페",
    "데이트 코스",
  ]));
});

/*
 * 이 파일에서 가장 중요한 테스트다. 계정에 못 올렸는데 조용히 넘어가면
 * 사용자는 다른 기기에서도 취향이 따라올 거라고 믿는다.
 */
test("계정에 저장하지 못하면 알리고 화면에 머문다", async () => {
  const user = userEvent.setup();
  server.failNext = true;
  renderPage();

  for (const label of ["조용한 곳", "카페", "데이트 코스"]) {
    await user.click(screen.getByRole("button", { name: label }));
  }
  await user.click(screen.getByRole("button", { name: "저장하기" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("계정에 저장하지 못했어요");
  expect(screen.getByTestId("probe").textContent).toBe("/preferences");
  /* 고른 값 자체는 잃지 않는다 — 이 기기에는 남아 있어야 한다. */
  expect(loadPreferences()).toHaveLength(3);
});

test("계정에 저장된 취향이 있으면 그 상태로 열린다", async () => {
  server.items = [
    { label: "조용한 곳", source: "preference", codes: ["quiet"] },
    { label: "카페", source: "place_tag", codes: ["카페", "찻집"] },
    { label: "야경 명소", source: "preference", codes: ["night_view"] },
  ];
  server.updatedAt = "2026-09-03T00:00:00+09:00";

  renderPage();

  await waitFor(() =>
    expect(screen.getByRole("button", { name: "야경 명소" })).toHaveAttribute("aria-pressed", "true"),
  );
  expect(screen.getByText("3–5개 중 3개 선택됨")).toBeInTheDocument();
});

/* 다른 기기에서 전부 해제한 사람의 계정은 "빈 목록"이 정본이다. 이 기기의 낡은
   값을 되살리면 안 된다. */
test("계정이 비어 있으면 이 기기 값을 계정으로 올린다", async () => {
  localStorage.setItem(
    "tb_preferences",
    JSON.stringify([{ label: "조용한 곳", source: "preference", codes: ["quiet"] }]),
  );

  renderPage();

  await waitFor(() => expect(server.items.map((item) => item.label)).toEqual(["조용한 곳"]));
});

/*
 * **동행은 하나만 고른다**(2026-09-06 사용자 결정). 다른 동행을 누르면 막지 않고
 * 바꾼다 — 막으면 먼저 빼고 다시 눌러야 해서 한 번에 될 일이 두 번 걸린다.
 */
test("동행을 다시 고르면 앞서 고른 동행이 빠진다", async () => {
  const user = userEvent.setup();
  renderPage();

  await user.click(screen.getByRole("button", { name: "데이트 코스" }));
  await user.click(screen.getByRole("button", { name: "친구와 함께" }));

  expect(screen.getByRole("button", { name: "데이트 코스" })).toHaveAttribute(
    "aria-pressed",
    "false",
  );
  expect(screen.getByRole("button", { name: "친구와 함께" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  /* 바꾼 것이지 더한 것이 아니다 — 개수가 늘면 안 된다. */
  expect(screen.getByText("3–5개 중 1개 선택됨")).toBeInTheDocument();
});

/* 동행은 필수가 아니다. 하나 고른 뒤 다시 누르면 그냥 빠진다. */
test("동행은 안 골라도 된다", async () => {
  const user = userEvent.setup();
  renderPage();

  await user.click(screen.getByRole("button", { name: "데이트 코스" }));
  await user.click(screen.getByRole("button", { name: "데이트 코스" }));

  expect(screen.getByText("3–5개 중 0개 선택됨")).toBeInTheDocument();
});

/*
 * 상한 판정과 동행 교체가 따로 놀면 여기가 깨진다 — 5개를 다 고른 사람은 동행을
 * 영영 못 바꾸게 된다. 교체는 개수가 늘지 않으므로 허용해야 한다.
 */
test("5개를 다 골랐어도 동행은 바꿀 수 있다", async () => {
  const user = userEvent.setup();
  renderPage();

  for (const label of ["조용한 곳", "아늑한 공간", "야경 명소", "사진 명소", "데이트 코스"]) {
    await user.click(screen.getByRole("button", { name: label }));
  }
  expect(screen.getByText("3–5개 중 5개 선택됨")).toBeInTheDocument();
  /* 분위기 칩은 잠겼는데 동행은 열려 있어야 한다. */
  expect(screen.getByRole("button", { name: "넓고 쾌적한" })).toBeDisabled();

  const friends = screen.getByRole("button", { name: "친구와 함께" });
  expect(friends).toBeEnabled();
  await user.click(friends);

  expect(friends).toHaveAttribute("aria-pressed", "true");
  expect(screen.getByRole("button", { name: "데이트 코스" })).toHaveAttribute(
    "aria-pressed",
    "false",
  );
  expect(screen.getByText("3–5개 중 5개 선택됨")).toBeInTheDocument();
});

/* 동행을 아직 안 골랐으면 5개가 찬 순간 동행도 함께 잠긴다 — 그때는 교체가
   아니라 6번째를 더하는 것이다. */
test("동행을 안 고른 채 5개가 차면 동행도 잠긴다", async () => {
  const user = userEvent.setup();
  renderPage();

  for (const label of ["조용한 곳", "아늑한 공간", "야경 명소", "사진 명소", "힐링하기 좋은"]) {
    await user.click(screen.getByRole("button", { name: label }));
  }

  expect(screen.getByRole("button", { name: "데이트 코스" })).toBeDisabled();
});

/*
 * 예전에 동행을 둘 이상 저장해 둔 값은 열 때 손대지 않는다 — 저장한 것을 말없이
 * 지우지 않는다. 동행 칩을 한 번 누르면 그때 하나로 정리된다.
 */
test("전에 저장된 동행 2개는 그대로 열리고, 한 번 누르면 하나로 정리된다", async () => {
  const user = userEvent.setup();
  savePreferences([
    { label: "데이트 코스", source: "preference", codes: ["date"] },
    { label: "친구와 함께", source: "preference", codes: ["with_friends"] },
    { label: "조용한 곳", source: "preference", codes: ["quiet"] },
  ]);
  renderPage();

  expect(screen.getByText("3–5개 중 3개 선택됨")).toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: "혼자 가기 좋은" }));

  expect(screen.getByRole("button", { name: "데이트 코스" })).toHaveAttribute(
    "aria-pressed",
    "false",
  );
  expect(screen.getByRole("button", { name: "친구와 함께" })).toHaveAttribute(
    "aria-pressed",
    "false",
  );
  expect(screen.getByText("3–5개 중 2개 선택됨")).toBeInTheDocument();
});

/*
 * 개수 규칙이 화면에 보여야 한다. 카운터는 최소·최대를 함께 내고, 그 옆 한 줄이
 * 지금 무엇을 해야 하는지 말한다 — 아래 저장 버튼이 "몇 개 더"를 세는 것과 역할이
 * 다르다.
 */
test("개수 안내가 상태에 따라 세 갈래로 바뀐다", async () => {
  const user = userEvent.setup();
  renderPage();

  expect(screen.getByText("저장하려면 3개는 골라야 해요")).toBeInTheDocument();

  for (const label of ["조용한 곳", "아늑한 공간", "야경 명소"]) {
    await user.click(screen.getByRole("button", { name: label }));
  }
  expect(screen.getByText("2개 더 고를 수 있어요")).toBeInTheDocument();

  for (const label of ["사진 명소", "힐링하기 좋은"]) {
    await user.click(screen.getByRole("button", { name: label }));
  }
  expect(screen.getByText("다 골랐어요. 바꾸려면 하나를 빼주세요")).toBeInTheDocument();
});
