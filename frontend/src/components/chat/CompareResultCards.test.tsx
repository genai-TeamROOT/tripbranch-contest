/* COMPARE(TRAVEL_TIME) 카드가 장소별 거리·수단별 소요시간과 최단 수단 배지를 보여주는지,
 * 그리고 카드를 누르면 네이버 지도 길찾기 딥링크가 열리는지(TP-120) 검증한다. */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, vi } from "vitest";
import { searchPlaces } from "../../api/trip";
import { clearDirectionsOriginCache } from "../../hooks/useNaverDirections";
import { clearLocationSettings, setLocationOrigin } from "../../state/locationSettings";
import type { ComparisonItem, ComparisonResult } from "../../types";
import { CompareResultCards } from "./CompareResultCards";
import { openNaverDirections } from "../../utils/naverDirections";

// 길찾기 훅이 위치 설정의 출발지 이름을 좌표로 풀 때 부른다.
vi.mock("../../api/trip", () => ({ searchPlaces: vi.fn() }));

afterEach(() => {
  clearLocationSettings();
  clearDirectionsOriginCache();
});

/* 출발지를 정해 두고 그 이름이 풀릴 좌표를 심는다. */
function seedOrigin(name: string, latitude: number, longitude: number): void {
  setLocationOrigin(name);
  vi.mocked(searchPlaces).mockResolvedValue({
    places: [{ name, address: null, road_address: null, category: null, latitude, longitude }],
    outside_service_area_count: 0,
  });
}

/* 링크를 여는 두 함수만 가로채고 나머지는 진짜를 쓴다. */
vi.mock("../../utils/naverDirections", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../utils/naverDirections")>()),
  openNaverMapSearch: vi.fn(),
  openNaverDirections: vi.fn(),
}));

function makeItem(overrides: Partial<ComparisonItem>): ComparisonItem {
  return {
    place_id: "1",
    place_name: "장소",
    rank: 1,
    distance_km: null,
    remaining_minutes: null,
    environment_type: null,
    latitude: null,
    longitude: null,
    travel_distance_km: null,
    travel_walking_minutes: null,
    travel_driving_minutes: null,
    travel_transit_minutes: null,
    ...overrides,
  };
}

it("travel_time 기준이면 장소별 거리·수단별 소요시간 카드를 보여주고, 가장 빠른 곳에 배지를 단다", () => {
  const comparison: ComparisonResult = {
    criteria: "travel_time",
    items: [
      makeItem({
        place_id: "a",
        place_name: "서울공예박물관",
        travel_distance_km: 1.92,
        travel_walking_minutes: 12,
        travel_driving_minutes: 10,
        travel_transit_minutes: 2,
      }),
      makeItem({
        place_id: "b",
        place_name: "가회민화박물관",
        travel_distance_km: 1.97,
        travel_walking_minutes: 21,
        travel_driving_minutes: 11,
        travel_transit_minutes: 3,
      }),
    ],
  };

  render(<CompareResultCards comparison={comparison} />);

  expect(screen.getByText("서울공예박물관")).toBeInTheDocument();
  expect(screen.getByText("가회민화박물관")).toBeInTheDocument();
  expect(screen.getByText("약 1.92km")).toBeInTheDocument();
  expect(screen.getAllByText("2분")).toHaveLength(1);
  expect(screen.getByText("가장 빠름")).toBeInTheDocument();
});

it("travel_time이 아니면 아무것도 렌더링하지 않는다", () => {
  const comparison: ComparisonResult = {
    criteria: "overall",
    items: [makeItem({ place_id: "a", place_name: "장소 A" })],
  };

  const { container } = render(<CompareResultCards comparison={comparison} />);
  expect(container).toBeEmptyDOMElement();
});

it("이동 경로를 확인하지 못한 장소는 안내 문구만 보여준다", () => {
  const comparison: ComparisonResult = {
    criteria: "travel_time",
    items: [makeItem({ place_id: "a", place_name: "장소 A" })],
  };

  render(<CompareResultCards comparison={comparison} />);
  expect(screen.getByText("이동 경로를 확인하지 못했어요.")).toBeInTheDocument();
});

it("카드를 누르면 정한 출발지에서 그 장소까지 네이버 지도 길찾기를 연다", async () => {
  const comparison: ComparisonResult = {
    criteria: "travel_time",
    items: [
      makeItem({
        place_id: "a",
        place_name: "서울공예박물관",
        latitude: 37.5758,
        longitude: 126.9843,
        travel_walking_minutes: 12,
      }),
    ],
  };

  seedOrigin("경복궁역", 37.5788, 126.977);
  render(<CompareResultCards comparison={comparison} />);

  const card = screen.getByRole("button", { name: "서울공예박물관까지 네이버 지도로 길찾기" });
  expect(screen.getByText("네이버 지도로 길찾기")).toBeInTheDocument();

  fireEvent.click(card);

  /* 출발점은 훅이 정한다 — 위치 설정의 출발지 이름을 좌표로 풀어 그 이름과 함께
     넣는다. 여는 함수가 비동기라 waitFor로 기다린다. */
  await waitFor(() =>
    expect(openNaverDirections).toHaveBeenCalledWith({
      origin: { lat: 37.5788, lng: 126.977, name: "경복궁역" },
      destLat: 37.5758,
      destLng: 126.9843,
      destName: "서울공예박물관",
    }),
  );
});

it("좌표가 없는 장소는 클릭할 수 없다", () => {
  const comparison: ComparisonResult = {
    criteria: "travel_time",
    items: [makeItem({ place_id: "a", place_name: "장소 A", travel_walking_minutes: 5 })],
  };

  seedOrigin("경복궁역", 37.5788, 126.977);
  render(<CompareResultCards comparison={comparison} />);

  expect(screen.queryByRole("button")).not.toBeInTheDocument();
  expect(screen.queryByText("네이버 지도로 길찾기")).not.toBeInTheDocument();
});

it("출발지를 정하지 않았으면 좌표가 있어도 클릭할 수 없다", () => {
  const comparison: ComparisonResult = {
    criteria: "travel_time",
    items: [
      makeItem({
        place_id: "a",
        place_name: "장소 A",
        latitude: 37.5758,
        longitude: 126.9843,
        travel_walking_minutes: 5,
      }),
    ],
  };

  render(<CompareResultCards comparison={comparison} />);

  expect(screen.queryByRole("button")).not.toBeInTheDocument();
});
