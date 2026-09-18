/*
 * 역할: 길찾기 출발점 사다리(설정한 출발지 → 기기 좌표 → 없음)를 검증한다.
 * 입력: 위치 설정(sessionStorage)과 기기 좌표 문자열, mocked 장소 검색.
 * 출력: openNaverDirections에 넘어간 출발점에 대한 assertion.
 * 호출 시점: vitest 실행 시 호출된다.
 */

import { renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { searchPlaces } from "../api/trip";
import { clearLocationSettings, setLocationOrigin } from "../state/locationSettings";
import { openNaverDirections } from "../utils/naverDirections";
import { clearDirectionsOriginCache, useNaverDirections } from "./useNaverDirections";

vi.mock("../api/trip", () => ({ searchPlaces: vi.fn() }));

/* 링크를 여는 함수만 가로채고 좌표 파싱은 진짜를 쓴다 — 가짜를 씌우면 사다리 판단이
   통째로 무력해진다. */
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
    /* 이게 이 훅의 존재 이유다. 예전에는 기기 좌표만 써서, 추천 카드의 거리는 안국역
       기준으로 재놓고 길찾기 버튼만 GPS에서 출발했다(TP-256). */
    setLocationOrigin("안국역");
    mockSearchResult(37.5765, 126.9856);

    const { result } = renderHook(() => useNaverDirections("37.4979,127.0276"));
    await result.current.openDirections(destination);

    await waitFor(() =>
      expect(openNaverDirections).toHaveBeenCalledWith({
        origin: { lat: 37.5765, lng: 126.9856, name: "안국역" },
        ...destination,
      }),
    );
    expect(searchPlaces).toHaveBeenCalledWith("안국역");
  });

  it("출발지를 정하지 않았으면 기기 좌표로 연다", async () => {
    const { result } = renderHook(() => useNaverDirections("37.4979,127.0276"));
    await result.current.openDirections(destination);

    expect(openNaverDirections).toHaveBeenCalledWith({
      origin: { lat: 37.4979, lng: 127.0276, name: "내 위치" },
      ...destination,
    });
    /* 이름을 안 정했으면 풀 것도 없다 — 여기서 검색이 나가면 매 클릭마다 불필요한
       외부 호출이 붙는다. */
    expect(searchPlaces).not.toHaveBeenCalled();
  });

  it("이름을 못 풀면 기기 좌표로 내려간다", async () => {
    setLocationOrigin("있을 리 없는 장소");
    vi.mocked(searchPlaces).mockResolvedValue({ places: [], outside_service_area_count: 0 });

    const { result } = renderHook(() => useNaverDirections("37.4979,127.0276"));
    await result.current.openDirections(destination);

    await waitFor(() =>
      expect(openNaverDirections).toHaveBeenCalledWith({
        origin: { lat: 37.4979, lng: 127.0276, name: "내 위치" },
        ...destination,
      }),
    );
  });

  it("조회가 실패해도 기기 좌표로 내려간다 — 오류를 밖으로 던지지 않는다", async () => {
    setLocationOrigin("안국역");
    vi.mocked(searchPlaces).mockRejectedValue(new Error("network"));

    const { result } = renderHook(() => useNaverDirections("37.4979,127.0276"));
    await expect(result.current.openDirections(destination)).resolves.toBe(true);

    expect(openNaverDirections).toHaveBeenCalledWith({
      origin: { lat: 37.4979, lng: 127.0276, name: "내 위치" },
      ...destination,
    });
  });

  it("출발지도 기기 좌표도 없으면 열지 않는다", async () => {
    const { result } = renderHook(() => useNaverDirections(null));

    expect(result.current.canRoute).toBe(false);
    await expect(result.current.openDirections(destination)).resolves.toBe(false);
    expect(openNaverDirections).not.toHaveBeenCalled();
  });

  it("출발지만 있고 기기 좌표가 없어도 열 수 있다", async () => {
    /* 새 대화는 좌표를 지우지만 출발지 이름은 남긴다. 그때 좌표만 보고 판단하면 열 수
       있는데도 "현재 위치를 받으세요"라고 말하게 된다. */
    setLocationOrigin("안국역");
    mockSearchResult(37.5765, 126.9856);

    const { result } = renderHook(() => useNaverDirections(null));

    expect(result.current.canRoute).toBe(true);
    await result.current.openDirections(destination);

    await waitFor(() =>
      expect(openNaverDirections).toHaveBeenCalledWith({
        origin: { lat: 37.5765, lng: 126.9856, name: "안국역" },
        ...destination,
      }),
    );
  });

  it("같은 출발지를 두 번 열면 좌표를 한 번만 조회한다", async () => {
    setLocationOrigin("안국역");
    mockSearchResult(37.5765, 126.9856);

    const { result } = renderHook(() => useNaverDirections(null));
    await result.current.openDirections(destination);
    await result.current.openDirections(destination);

    await waitFor(() => expect(openNaverDirections).toHaveBeenCalledTimes(2));
    expect(searchPlaces).toHaveBeenCalledTimes(1);
  });
});
