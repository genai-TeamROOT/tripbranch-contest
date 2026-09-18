/*
 * 역할: 위치 설정 화면의 장소 검색과 검색 위치 지정, "현재 위치 사용" 동작,
 *   즐겨찾기·최근 검색 목록을 검증한다.
 * 입력: mocked navigator.geolocation, mocked searchPlaces().
 * 출력: 검색 결과 목록과 빈 결과 문구, 고른 장소가 검색 위치·최근 검색·즐겨찾기로
 *   이어지는지, 좌표 갱신, 실패 시 오류 문구, 즐겨찾기 추가/삭제.
 * 호출 시점: vitest 실행 시.
 */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { AppShellProvider } from "../components/layout/AppShellContext";
import { TripProvider } from "../state/TripContext";
import { LocationPage } from "./LocationPage";
import { searchPlaces } from "../api/trip";
import { ApiError } from "../api/client";
import type { PlaceSearchResponse } from "../types";
import { resetFavoritesSync } from "../state/favoritesSync";
import {
  loadLocationSettings,
  setLocationCenter,
  setLocationOrigin,
} from "../state/locationSettings";
import { loadRecentSearches } from "../state/recentSearchesStorage";
import { loadFavorites, saveFavorites } from "../state/sidebarStorage";

vi.mock("../api/trip", () => ({
  searchPlaces: vi.fn(),
  /* 이 화면은 즐겨찾기를 계정과 맞춘다(favoritesSync). 서버가 없으면 이 기기의
     값으로 도는 것이 정상 동작이라, 실패로 두고 로컬 경로를 그대로 본다. */
  fetchFavorites: vi.fn(() => Promise.reject(new Error("no server"))),
  replaceFavorites: vi.fn(() => Promise.reject(new Error("no server"))),
}));

const searchPlacesMock = vi.mocked(searchPlaces);

/* 서버가 돌려주는 후보 한 건. 여러 테스트가 같은 장소를 쓴다. */
const ANGUK = {
  name: "안국역",
  address: "서울특별시 종로구 견지동",
  road_address: "서울특별시 종로구 율곡로 62",
  category: "교통,지하철",
  latitude: 37.5765389,
  longitude: 126.9856,
};

function renderPage() {
  render(
    <MemoryRouter>
      <AppShellProvider>
        <TripProvider>
          <LocationPage />
        </TripProvider>
      </AppShellProvider>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  sessionStorage.clear();
  localStorage.clear();
  // 로컬 .env의 Codex 테스트 좌표가 이 테스트의 mocked navigator 응답을 가리지
  // 않게 한다(App.test.tsx와 같은 이유 — utils/geolocation.ts의 개발 전용 우회).
  vi.stubEnv("VITE_TEST_DEVICE_LOCATION", "");
  searchPlacesMock.mockReset();
  /* 계정 동기화 결과는 페이지 로드당 한 번만 계산된다 — 테스트마다 그 경계를 만든다. */
  resetFavoritesSync();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

test('현재 위치 사용을 누르면 좌표 대신 "현재 위치"로 표시하고, 고른 검색 위치를 내려놓는다', async () => {
  const user = userEvent.setup();
  vi.stubGlobal("navigator", {
    geolocation: {
      getCurrentPosition: vi.fn((success: PositionCallback) =>
        success({
          coords: { latitude: 37.5, longitude: 127.0 },
          timestamp: Date.now(),
        } as GeolocationPosition),
      ),
    },
  });
  renderPage();

  await user.click(screen.getByRole("button", { name: "현재 위치 사용" }));

  /* 좌표는 사용자에게 숫자 두 개일 뿐이라 화면에 싣지 않는다. */
  expect(await screen.findByText(/^현재 위치 · /)).toBeInTheDocument();
  expect(screen.queryByText(/37.5,127/)).not.toBeInTheDocument();
  // getLocationAgeMinutes는 최소 1분으로 올림한다(utils/locationRefresh.ts) —
  // 방금 받아온 위치도 "1분 전"으로 보인다.
  expect(screen.getByText(/1분 전에 확인했어요/)).toBeInTheDocument();
  /* 내 위치로 찾겠다는 뜻이므로 골라둔 검색 위치는 함께 풀린다. */
  expect(loadLocationSettings().center).toBeNull();
});

test("위치를 가져오지 못하면 오류 문구를 보여준다", async () => {
  const user = userEvent.setup();
  vi.stubGlobal("navigator", {
    geolocation: {
      getCurrentPosition: vi.fn((_success: PositionCallback, error: PositionErrorCallback) =>
        error({
          code: 1,
          PERMISSION_DENIED: 1,
          POSITION_UNAVAILABLE: 2,
          TIMEOUT: 3,
          message: "denied",
        } as GeolocationPositionError),
      ),
    },
  });
  renderPage();

  await user.click(screen.getByRole("button", { name: "현재 위치 사용" }));

  await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
});

test("즐겨찾기가 없으면 안내 문구와 0/10을, 별을 눌러 담으면 목록과 개수를 함께 보여준다", async () => {
  const user = userEvent.setup();
  searchPlacesMock.mockResolvedValue({ places: [ANGUK], outside_service_area_count: 0 });
  renderPage();

  expect(screen.getByText("등록된 즐겨찾기가 없어요")).toBeInTheDocument();
  expect(screen.getByText("0/10")).toBeInTheDocument();

  await user.type(screen.getByLabelText("장소 검색"), "안국역");
  await user.click(screen.getByRole("button", { name: "검색" }));
  await user.click(await screen.findByRole("button", { name: "안국역 즐겨찾기 추가" }));

  expect(await screen.findByRole("button", { name: "안국역 이름 바꾸기" })).toBeInTheDocument();
  expect(screen.getByText("1/10")).toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: "안국역 즐겨찾기 삭제" }));

  expect(screen.queryByRole("button", { name: "안국역 이름 바꾸기" })).not.toBeInTheDocument();
  expect(screen.getByText("등록된 즐겨찾기가 없어요")).toBeInTheDocument();
  expect(screen.getByText("0/10")).toBeInTheDocument();
});

test("즐겨찾기가 10개면 별을 눌러도 담기지 않고 모달로 알린다", async () => {
  const user = userEvent.setup();
  saveFavorites(
    Array.from({ length: 10 }, (_, index) => ({
      id: `fav-${index}`,
      label: `장소${index}`,
      searchCenterName: `장소${index}`,
    })),
  );
  searchPlacesMock.mockResolvedValue({ places: [ANGUK], outside_service_area_count: 0 });
  renderPage();

  expect(screen.getByText("10/10")).toBeInTheDocument();

  await user.type(screen.getByLabelText("장소 검색"), "안국역");
  await user.click(screen.getByRole("button", { name: "검색" }));
  await user.click(await screen.findByRole("button", { name: "안국역 즐겨찾기 추가" }));

  const dialog = await screen.findByRole("dialog");
  expect(dialog).toHaveTextContent("즐겨찾기가 가득 찼어요");
  // 한도 숫자는 모달이 따로 갖지 않고 화면에서 받는다 — 두 곳이 어긋나지 않게.
  expect(dialog).toHaveTextContent("즐겨찾기는 10개까지 담을 수 있어요");
  expect(loadFavorites()).toHaveLength(10);
  expect(screen.getByText("10/10")).toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: "확인" }));

  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});

test("검색하면 찾은 장소의 이름과 주소를 목록으로 보여준다", async () => {
  const user = userEvent.setup();
  searchPlacesMock.mockResolvedValue({
    places: [
      {
        name: "안국역",
        address: "서울특별시 종로구 견지동",
        road_address: "서울특별시 종로구 율곡로 62",
        category: "교통,지하철",
        latitude: 37.5765389,
        longitude: 126.9856,
      },
    ],
    outside_service_area_count: 0,
  });
  renderPage();

  await user.type(screen.getByLabelText("장소 검색"), "안국역");
  await user.click(screen.getByRole("button", { name: "검색" }));

  /* 검색어("안국역")가 최근 검색에도 같은 글자로 남으므로 결과 줄은 그 줄의
     라벨로 집는다. */
  expect(
    await screen.findByRole("button", { name: "안국역 검색 위치로 설정" }),
  ).toBeInTheDocument();
  expect(screen.getByText("서울특별시 종로구 율곡로 62")).toBeInTheDocument();
  expect(searchPlacesMock).toHaveBeenCalledWith("안국역");
});

test("검색 결과를 고르면 검색 위치로 잡히고 결과 목록이 닫힌다", async () => {
  const user = userEvent.setup();
  searchPlacesMock.mockResolvedValue({
    places: [
      {
        name: "안국역",
        address: "서울특별시 종로구 견지동",
        road_address: "서울특별시 종로구 율곡로 62",
        category: "교통,지하철",
        latitude: 37.5765389,
        longitude: 126.9856,
      },
    ],
    outside_service_area_count: 0,
  });
  renderPage();

  await user.type(screen.getByLabelText("장소 검색"), "안국역");
  await user.click(screen.getByRole("button", { name: "검색" }));
  await user.click(await screen.findByRole("button", { name: "안국역 검색 위치로 설정" }));
  /* 고른 장소를 출발지로 쓸지 검색 기준으로 쓸지 이어서 묻는다(D-067). */
  await user.click(screen.getByRole("button", { name: /안국역 주변에서 찾아주세요/ }));

  // 고르고 나면 결과 목록은 닫힌다 — 이미 정했으니 계속 띄워 둘 이유가 없다.
  expect(screen.queryByText("검색 결과")).not.toBeInTheDocument();

  /* 저장소가 진실이다 — 발화를 보낼 때 HomePage·ChatPage가 여기서 읽어 가고,
     상단 위치 pill도 같은 값을 구독한다. 이 화면에는 따로 표시하지 않는다. */
  expect(loadLocationSettings().center).toBe("안국역");
  expect(screen.queryByRole("button", { name: "안국역 검색 위치로 설정" })).not.toBeInTheDocument();
});

test("친 검색어가 최근 검색에 남고, 다시 누르면 그 검색어로 재검색한다", async () => {
  const user = userEvent.setup();
  searchPlacesMock.mockResolvedValue({ places: [], outside_service_area_count: 0 });
  renderPage();

  expect(screen.getByText("아직 검색한 장소가 없어요")).toBeInTheDocument();

  await user.type(screen.getByLabelText("장소 검색"), "익선동 골목");
  await user.click(screen.getByRole("button", { name: "검색" }));

  /* 못 찾은 검색어도 남는다 - 오히려 그런 검색어를 다시 꺼내 고쳐 쓰게 된다. */
  await waitFor(() => expect(loadRecentSearches()).toEqual(["익선동 골목"]));

  searchPlacesMock.mockClear();
  searchPlacesMock.mockResolvedValue({ places: [ANGUK], outside_service_area_count: 0 });
  await user.click(screen.getByRole("button", { name: "익선동 골목 다시 검색" }));

  expect(searchPlacesMock).toHaveBeenCalledWith("익선동 골목");
  expect(await screen.findByText("안국역")).toBeInTheDocument();
});

test("최근 검색을 휴지통으로 한 줄씩 지운다", async () => {
  /* 2026-09-08에 추가했다. 즐겨찾기와 달리 연필(이름 수정)은 없다 — 사용자가
     친 말 그대로가 값이라 고칠 것이 없다. */
  const user = userEvent.setup();
  searchPlacesMock.mockResolvedValue({ places: [], outside_service_area_count: 0 });
  renderPage();

  await user.type(screen.getByLabelText("장소 검색"), "익선동 골목");
  await user.click(screen.getByRole("button", { name: "검색" }));
  await waitFor(() => expect(loadRecentSearches()).toEqual(["익선동 골목"]));

  await user.click(screen.getByRole("button", { name: "익선동 골목 최근 검색에서 삭제" }));

  expect(loadRecentSearches()).toEqual([]);
  expect(screen.getByText("아직 검색한 장소가 없어요")).toBeInTheDocument();
  /* 지우는 것이 재검색으로 새지 않는다 — 줄 전체가 "다시 검색" 버튼이라
     stopPropagation이 빠지면 지우면서 검색까지 나간다. */
  expect(searchPlacesMock).toHaveBeenCalledTimes(1);
});

test("칩이 출발지와 검색 기준을 각각 보여주고, 하나만 되돌린다", async () => {
  const user = userEvent.setup();
  setLocationOrigin("혜화역");
  setLocationCenter("안국역");
  renderPage();

  expect(screen.getByText(/혜화역에서 출발/)).toBeInTheDocument();
  expect(screen.getByText(/안국역 주변/)).toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: "검색 기준 되돌리기" }));

  /* 검색 기준만 풀리고 출발지는 그대로다 - 서로 다른 질문의 답이라서다. */
  expect(loadLocationSettings()).toEqual({ origin: "혜화역", center: null });
  /* 검색 기준을 비우면 출발지가 그 자리를 이어받는다(agent_context의 사다리).
     "없음"이 아니라 실제로 뒤지는 곳을 그대로 말한다. */
  expect(screen.getByText(/혜화역 주변/)).toBeInTheDocument();
  expect(screen.getByText(/혜화역에서 출발/)).toBeInTheDocument();
});

test("아무것도 정하지 않았으면 둘 다 현재 위치라고 말한다", () => {
  renderPage();

  expect(screen.getByText(/현재 위치에서 출발/)).toBeInTheDocument();
  expect(screen.getByText(/현재 위치 주변/)).toBeInTheDocument();
});

test("같은 장소라도 출발지로 고르면 검색 기준은 그대로 둔다", async () => {
  /* 출발지("어디서 출발하나")와 검색 기준("어디 주변을 찾나")은 다른 질문이라
     한쪽을 정해도 다른 쪽이 따라 바뀌면 안 된다(D-067). */
  const user = userEvent.setup();
  searchPlacesMock.mockResolvedValue({ places: [ANGUK], outside_service_area_count: 0 });
  renderPage();

  await user.type(screen.getByLabelText("장소 검색"), "안국역");
  await user.click(screen.getByRole("button", { name: "검색" }));
  await user.click(await screen.findByRole("button", { name: "안국역 검색 위치로 설정" }));
  await user.click(screen.getByRole("button", { name: /안국역에서 출발할게요/ }));

  expect(loadLocationSettings()).toEqual({ origin: "안국역", center: null });
});

test("현재 위치 사용은 되묻지 않고 출발지만 기기 좌표로 되돌린다", async () => {
  /* 이 버튼의 뜻은 "내 위치는 기기 좌표다" 하나뿐이라 쓰임새를 물을 것이 없다.
     검색 기준으로 잡아둔 곳은 그대로 남는다 - 그건 다른 질문의 답이다. */
  const user = userEvent.setup();
  setLocationOrigin("혜화역");
  setLocationCenter("안국역");
  vi.stubGlobal("navigator", {
    geolocation: {
      getCurrentPosition: vi.fn((success: PositionCallback) =>
        success({
          coords: { latitude: 37.5, longitude: 127.0 },
          timestamp: Date.now(),
        } as GeolocationPosition),
      ),
    },
  });
  renderPage();

  await user.click(screen.getByRole("button", { name: "현재 위치 사용" }));

  await waitFor(() => expect(loadLocationSettings()).toEqual({ origin: null, center: "안국역" }));
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(await screen.findByText(/^현재 위치 · /)).toBeInTheDocument();
  expect(screen.getByText(/현재 위치에서 출발/)).toBeInTheDocument();
  expect(screen.getByText(/안국역 주변/)).toBeInTheDocument();
});

test("검색 결과의 별을 누르면 즐겨찾기에 담기고 다시 누르면 빠진다", async () => {
  const user = userEvent.setup();
  searchPlacesMock.mockResolvedValue({ places: [ANGUK], outside_service_area_count: 0 });
  renderPage();

  await user.type(screen.getByLabelText("장소 검색"), "안국역");
  await user.click(screen.getByRole("button", { name: "검색" }));
  await user.click(await screen.findByRole("button", { name: "안국역 즐겨찾기 추가" }));

  // 사이드바와 같은 저장소를 쓴다 - 여기서 담으면 사이드바 목록에도 함께 보인다.
  expect(loadFavorites()).toEqual([
    {
      id: expect.any(String),
      label: "안국역",
      searchCenterName: "안국역",
      address: "서울특별시 종로구 율곡로 62",
    },
  ]);
  expect(screen.queryByText("등록된 즐겨찾기가 없어요")).not.toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: "안국역 즐겨찾기 해제" }));

  expect(loadFavorites()).toEqual([]);
});

test("즐겨찾기 줄을 누르면 검색 기준이 되고 주소·칩에 함께 드러난다", async () => {
  const user = userEvent.setup();
  saveFavorites([
    {
      id: "fav-1",
      label: "회사 (역삼동)",
      searchCenterName: "역삼역",
      address: "서울특별시 강남구 테헤란로 152",
    },
  ]);
  renderPage();

  expect(screen.getByText("서울특별시 강남구 테헤란로 152")).toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: "회사 (역삼동)을 검색 위치로 설정" }));
  await user.click(screen.getByRole("button", { name: /역삼역 주변에서 찾아주세요/ }));

  // 라벨이 아니라 담을 때의 장소 이름을 보낸다 - "회사 (역삼동)"은 위치로 안 풀린다.
  expect(loadLocationSettings().center).toBe("역삼역");
  /* 지금 잡힌 곳은 아이콘이 바뀌어 목록에서 바로 구분된다. */
  /* 목록에서도, 위쪽 칩에서도 지금 무엇이 검색 기준인지 보인다. */
  expect(
    screen.getByRole("button", { name: "회사 (역삼동)이 지금 검색 기준이에요" }),
  ).toHaveAttribute("aria-pressed", "true");
  expect(screen.getByText(/역삼역 주변/)).toBeInTheDocument();
});

test("이름 글자를 눌러도 이름 편집이 아니라 장소 선택 모달이 열린다", async () => {
  /* 2026-09-08에 바꾼 동작이다. 전에는 글자가 이름 바꾸기 버튼이라, 줄에서
     이름을 뺀 빈 자리만 모달을 열었다. 이름 편집은 연필 아이콘으로 옮겼다. */
  const user = userEvent.setup();
  saveFavorites([{ id: "fav-1", label: "역삼역", searchCenterName: "역삼역" }]);
  renderPage();

  await user.click(screen.getByText("역삼역"));

  expect(await screen.findByRole("dialog")).toBeInTheDocument();
  expect(screen.queryByRole("textbox", { name: "역삼역 이름 바꾸기" })).not.toBeInTheDocument();
});

test("이름을 고치는 중에는 연필·휴지통이 사라지고 저장만 남는다", async () => {
  /* 이름을 다 쓰고 오른쪽 아이콘을 누르려다 휴지통을 눌러 지우는 사고를 막는다. */
  const user = userEvent.setup();
  saveFavorites([{ id: "fav-1", label: "역삼역", searchCenterName: "역삼역" }]);
  renderPage();

  await user.click(screen.getByRole("button", { name: "역삼역 이름 바꾸기" }));

  expect(screen.getByRole("button", { name: "이름 저장" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "역삼역 즐겨찾기 삭제" })).not.toBeInTheDocument();
});

test("이름을 고치는 중에는 스페이스·엔터가 모달을 열지 않는다", async () => {
  /* 줄 전체가 "이 장소를 쓰겠다" 버튼이라, 안에서 누른 키가 위로 올라가면 이름에
     띄어쓰기 한 번 넣을 때마다 모달이 뜬다. */
  const user = userEvent.setup();
  saveFavorites([{ id: "fav-1", label: "역삼역", searchCenterName: "역삼역" }]);
  renderPage();

  await user.click(screen.getByRole("button", { name: "역삼역 이름 바꾸기" }));
  await user.clear(screen.getByRole("textbox", { name: "역삼역 이름 바꾸기" }));
  await user.type(screen.getByRole("textbox", { name: "역삼역 이름 바꾸기" }), "회사 앞{Enter}");

  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(loadFavorites()[0].label).toBe("회사 앞");
});

test("예전에 자유 입력으로 만든 즐겨찾기는 라벨을 그대로 쓴다", async () => {
  const user = userEvent.setup();
  saveFavorites([{ id: "fav-1", label: "안국역" }]);
  renderPage();

  await user.click(screen.getByRole("button", { name: "안국역을 검색 위치로 설정" }));
  await user.click(screen.getByRole("button", { name: /안국역 주변에서 찾아주세요/ }));

  expect(loadLocationSettings().center).toBe("안국역");
});

test("연필 아이콘을 눌러 즐겨찾기 이름을 원하는 이름으로 바꾼다", async () => {
  const user = userEvent.setup();
  saveFavorites([{ id: "fav-1", label: "역삼역", searchCenterName: "역삼역" }]);
  renderPage();

  await user.click(screen.getByRole("button", { name: "역삼역 이름 바꾸기" }));
  await user.clear(screen.getByRole("textbox", { name: "역삼역 이름 바꾸기" }));
  await user.type(screen.getByRole("textbox", { name: "역삼역 이름 바꾸기" }), "회사");
  await user.click(screen.getByRole("button", { name: "이름 저장" }));

  expect(screen.getByText("회사")).toBeInTheDocument();
  expect(loadFavorites()).toEqual([{ id: "fav-1", label: "회사", searchCenterName: "역삼역" }]);

  /* 이름을 바꿔도 검색 위치로 보내는 값은 담을 때의 장소 이름 그대로다. */
  await user.click(screen.getByRole("button", { name: "회사을 검색 위치로 설정" }));
  await user.click(screen.getByRole("button", { name: /역삼역 주변에서 찾아주세요/ }));

  expect(loadLocationSettings().center).toBe("역삼역");
});

test("서울 밖 결과만 걸러졌으면 지역 제한을 알려준다", async () => {
  const user = userEvent.setup();
  searchPlacesMock.mockResolvedValue({ places: [], outside_service_area_count: 3 });
  renderPage();

  await user.type(screen.getByLabelText("장소 검색"), "해운대");
  await user.click(screen.getByRole("button", { name: "검색" }));

  expect(await screen.findByText("서울 지역만 검색할 수 있어요")).toBeInTheDocument();
  expect(screen.queryByText("찾은 장소가 없어요")).not.toBeInTheDocument();
});

test("서울 밖도 아니고 찾지도 못했으면 검색어 문제로 안내한다", async () => {
  const user = userEvent.setup();
  searchPlacesMock.mockResolvedValue({ places: [], outside_service_area_count: 0 });
  renderPage();

  await user.type(screen.getByLabelText("장소 검색"), "ㅁㄴㅇㄹ");
  await user.click(screen.getByRole("button", { name: "검색" }));

  expect(await screen.findByText("찾은 장소가 없어요")).toBeInTheDocument();
});

/*
 * 결과 목록은 검색창 아래에 떠 있다(문서 흐름을 차지하지 않는다). 그래서 스스로
 * 닫히지 않으므로 바깥 클릭과 Esc로 닫는 길을 둔다 - 열어둔 채로 아래 즐겨찾기를
 * 누르려다 가려지면 곤란하다.
 */
test("결과 목록은 바깥을 누르거나 Esc를 누르면 닫힌다", async () => {
  const user = userEvent.setup();
  searchPlacesMock.mockResolvedValue({ places: [ANGUK], outside_service_area_count: 0 });
  renderPage();

  await user.type(screen.getByLabelText("장소 검색"), "안국역");
  await user.click(screen.getByRole("button", { name: "검색" }));
  expect(
    await screen.findByRole("button", { name: "안국역 검색 위치로 설정" }),
  ).toBeInTheDocument();

  await user.click(screen.getByText("현재 서울 지역 장소만 추천해 드리고 있어요"));

  expect(screen.queryByRole("button", { name: "안국역 검색 위치로 설정" })).not.toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: "검색" }));
  expect(
    await screen.findByRole("button", { name: "안국역 검색 위치로 설정" }),
  ).toBeInTheDocument();

  await user.keyboard("{Escape}");

  expect(screen.queryByRole("button", { name: "안국역 검색 위치로 설정" })).not.toBeInTheDocument();
});

test("검색 전에는 결과 영역 자체를 보여주지 않는다", () => {
  renderPage();

  expect(screen.queryByText("검색 결과")).not.toBeInTheDocument();
  expect(screen.queryByText("찾은 장소가 없어요")).not.toBeInTheDocument();
});

/* TP-240. 예전에는 응답이 안 오면 isSearching이 true로 굳어 버튼이 잠기고, 다시
   눌러도 가드가 조용히 삼켰다. 새로고침 말고는 빠져나갈 길이 없었다. */
test("검색이 끊겨도 새로고침 없이 같은 화면에서 다시 검색할 수 있다", async () => {
  const user = userEvent.setup();
  searchPlacesMock.mockRejectedValueOnce(
    new ApiError({
      code: "request_timeout",
      message: "서버가 응답하지 않아 요청을 끊었어요. 다시 시도해주세요.",
      retryable: true,
      details: null,
    }),
  );
  renderPage();

  await user.type(screen.getByLabelText("장소 검색"), "안국역");
  await user.click(screen.getByRole("button", { name: "검색" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("서버가 응답하지 않아");
  /* 잠금이 풀렸다는 것 자체가 확인 대상이다. */
  expect(screen.getByRole("button", { name: "검색" })).toBeEnabled();

  searchPlacesMock.mockResolvedValueOnce({ places: [ANGUK], outside_service_area_count: 0 });
  await user.click(screen.getByRole("button", { name: "검색" }));

  expect(
    await screen.findByRole("button", { name: "안국역 검색 위치로 설정" }),
  ).toBeInTheDocument();
});

/* 검색 버튼은 검색 중에 disabled라 브라우저가 Enter 제출까지 막는다. 반면 최근
   검색 줄은 잠기지 않아서, 눌러도 가드에 걸려 아무 일도 안 일어나는 자리가 된다. */
test("검색 중에 최근 검색을 누르면 조용히 무시하지 않고 진행 중임을 알린다", async () => {
  const user = userEvent.setup();
  let release: (value: PlaceSearchResponse) => void = () => {};
  searchPlacesMock.mockReturnValueOnce(
    new Promise<PlaceSearchResponse>((resolve) => {
      release = resolve;
    }),
  );
  renderPage();

  await user.type(screen.getByLabelText("장소 검색"), "안국역");
  await user.click(screen.getByRole("button", { name: "검색" }));
  expect(await screen.findByRole("button", { name: "검색 중" })).toBeDisabled();

  /* 검색어는 결과가 오기 전에 최근 검색으로 남는다 — 그 줄이 아직 잠기지 않았다. */
  await user.click(screen.getByRole("button", { name: "안국역 다시 검색" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("아직 검색 중이에요");

  release({ places: [ANGUK], outside_service_area_count: 0 });
  expect(
    await screen.findByRole("button", { name: "안국역 검색 위치로 설정" }),
  ).toBeInTheDocument();
});
