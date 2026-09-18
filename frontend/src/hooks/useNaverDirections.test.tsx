/*
 * 역할: 길찾기 출발점 규칙(설정한 출발지 → 검색 위치 → 없으면 열지 않음)을 검증한다.
 * 입력: 위치 설정(sessionStorage), mocked 장소 검색.
 * 출력: openNaverDirections에 넘어간 출발점에 대한 assertion.
 * 호출 시점: vitest 실행 시 호출된다.
 */

import { renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { searchPlaces } from "../api/trip";
import {
  clearLocationSettings,
  setLocationCenter,
  setLocationOrigin,
} from "../state/locationSettings";
import { openNaverDirections } from "../utils/naverDirections";
import { clearDirectionsOriginCache, useNaverDirections } from "./useNaverDirections";

vi.mock("../api/trip", () => ({ searchPlaces: vi.fn() }));

/* 링크를 여는 함수만 가로채고 나머지는 진짜를 쓴다. */
vi.mock("../utils/naverDirections", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../utils/naverDirections")>()),
  openNaverDirections: vi.fn(() => true),
}));

const destination = { destLat: 37.5796, destLng: 126.977, destName: "경복궁" };

function mockSearchResult(latitude: number, longitude: number, name = "안국역") {
  vi.mocked(searchPlaces).mockResolvedValue({
    places: [{ name, address: null, road_address: null, category: null, latitude, longitude }],
    outside_service_area_count: 0,
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  clearLocationSettings();
  clearDirectionsOriginCache();
});

describe("useNaverDirections", () => {
  it("사용자가 정한 출발지가 있으면 그 좌표와 이름으로 연다", async () => {
    /* 추천 카드의 거리는 정한 출발지 기준으로 재므로 길찾기도 같은 곳에서 출발해야
       한다(TP-256). */
    setLocationOrigin("안국역");
    mockSearchResult(37.5765, 126.9856);

    const { result } = renderHook(() => useNaverDirections());
    expect(result.current.canRoute).toBe(true);
    await result.current.openDirections(destination);

    await waitFor(() =>
      expect(openNaverDirections).toHaveBeenCalledWith({
        origin: { lat: 37.5765, lng: 126.9856, name: "안국역" },
        ...destination,
      }),
    );
    expect(searchPlaces).toHaveBeenCalledWith("안국역");
  });

  it("출발지가 없으면 검색 위치에서 출발한다", async () => {
    /* 추천을 받았다는 것은 검색 위치가 있다는 뜻이고, 그때 거리는 검색 위치에서 잰다
       (domain/ranking_origin.py). 이 폴백이 없으면 추천 카드마다 길찾기를 누를 때
       출발지를 정하라는 안내가 뜬다. */
    setLocationCenter("인사동");
    mockSearchResult(37.574, 126.985, "인사동");

    const { result } = renderHook(() => useNaverDirections());
    expect(result.current.canRoute).toBe(true);
    await result.current.openDirections(destination);

    await waitFor(() =>
      expect(openNaverDirections).toHaveBeenCalledWith({
        origin: { lat: 37.574, lng: 126.985, name: "인사동" },
        ...destination,
      }),
    );
    expect(searchPlaces).toHaveBeenCalledWith("인사동");
  });

  it("출발지와 검색 위치가 둘 다 있으면 출발지에서 출발한다", async () => {
    setLocationOrigin("안국역");
    setLocationCenter("인사동");
    mockSearchResult(37.5765, 126.9856);

    const { result } = renderHook(() => useNaverDirections());
    await result.current.openDirections(destination);

    await waitFor(() => expect(openNaverDirections).toHaveBeenCalledTimes(1));
    expect(searchPlaces).toHaveBeenCalledWith("안국역");
    expect(searchPlaces).not.toHaveBeenCalledWith("인사동");
  });

  it("출발지와 검색 위치를 둘 다 정하지 않았으면 열지 않는다", async () => {
    const { result } = renderHook(() => useNaverDirections());

    expect(result.current.canRoute).toBe(false);
    await expect(result.current.openDirections(destination)).resolves.toBe(false);
    expect(openNaverDirections).not.toHaveBeenCalled();
    /* 이름을 안 정했으면 풀 것도 없다 — 여기서 검색이 나가면 매 클릭마다 불필요한
       외부 호출이 붙는다. */
    expect(searchPlaces).not.toHaveBeenCalled();
  });

  it("이름을 못 풀면 열지 않는다", async () => {
    setLocationOrigin("있을 리 없는 장소");
    vi.mocked(searchPlaces).mockResolvedValue({ places: [], outside_service_area_count: 0 });

    const { result } = renderHook(() => useNaverDirections());
    await expect(result.current.openDirections(destination)).resolves.toBe(false);

    expect(openNaverDirections).not.toHaveBeenCalled();
  });

  it("조회가 실패해도 오류를 밖으로 던지지 않고 열지 않는다", async () => {
    setLocationOrigin("안국역");
    vi.mocked(searchPlaces).mockRejectedValue(new Error("network"));

    const { result } = renderHook(() => useNaverDirections());
    await expect(result.current.openDirections(destination)).resolves.toBe(false);

    expect(openNaverDirections).not.toHaveBeenCalled();
  });

  it("같은 출발지를 두 번 열면 좌표를 한 번만 조회한다", async () => {
    setLocationOrigin("안국역");
    mockSearchResult(37.5765, 126.9856);

    const { result } = renderHook(() => useNaverDirections());
    await result.current.openDirections(destination);
    await result.current.openDirections(destination);

    await waitFor(() => expect(openNaverDirections).toHaveBeenCalledTimes(2));
    expect(searchPlaces).toHaveBeenCalledTimes(1);
  });
});
