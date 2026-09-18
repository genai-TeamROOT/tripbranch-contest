import { describe, expect, it, vi } from "vitest";

import { buildNaverDirections, openNaverDirections, openNaverMapSearch } from "./naverDirections";

/** 위치 설정에서 정한 출발지. 대부분의 케이스가 이 모양이다. */
const cityHallOrigin = { lat: 37.5665, lng: 126.978, name: "서울시청" };

describe("buildNaverDirections", () => {
  it("출발·도착 좌표로 대중교통 앱 딥링크와 웹 폴백을 만든다", () => {
    const urls = buildNaverDirections({
      origin: cityHallOrigin,
      destLat: 37.5796,
      destLng: 126.977,
      destName: "경복궁",
    });

    expect(urls).not.toBeNull();
    expect(urls?.appUrl).toContain("nmap://route/public");
    expect(urls?.appUrl).toContain("slat=37.5665");
    expect(urls?.appUrl).toContain("slng=126.978");
    expect(urls?.appUrl).toContain("dlat=37.5796");
    expect(urls?.appUrl).toContain("dlng=126.977");
    // 출발점 라벨(인코딩)
    expect(urls?.appUrl).toContain(`sname=${encodeURIComponent("서울시청")}`);
    // appname은 호출 호스트명(jsdom: localhost)
    expect(urls?.appUrl).toContain("appname=localhost");
    // 웹 폴백은 경도,위도 순 + 대중교통
    expect(urls?.webUrl).toContain("map.naver.com/p/directions");
    expect(urls?.webUrl).toContain("/-/transit");
  });

  it("사용자가 정한 출발지는 그 좌표와 그 이름으로 나간다", () => {
    /* 예전에는 출발 라벨이 "내 위치"로 박혀 있었다. 안국역 좌표로 출발하면서 화면에는
       "내 위치"라고 뜨면 사용자가 어디서 출발하는지 잘못 읽는다 — 좌표와 이름이 함께
       바뀌어야 한다(TP-256). */
    const urls = buildNaverDirections({
      origin: { lat: 37.5765, lng: 126.9856, name: "안국역" },
      destLat: 37.5796,
      destLng: 126.977,
      destName: "경복궁",
    });

    expect(urls?.appUrl).toContain("slat=37.5765");
    expect(urls?.appUrl).toContain("slng=126.9856");
    expect(urls?.appUrl).toContain(`sname=${encodeURIComponent("안국역")}`);
    expect(urls?.appUrl).not.toContain(encodeURIComponent("내 위치"));
    expect(urls?.webUrl).toContain(encodeURIComponent("안국역"));
  });

  it("출발지 이름이 비어 있으면 기본 라벨을 쓴다 — 링크의 sname이 비지 않게", () => {
    const urls = buildNaverDirections({
      origin: { lat: 37.5, lng: 127.0, name: "   " },
      destLat: 37.6,
      destLng: 127.1,
      destName: "장소",
    });

    expect(urls?.appUrl).toContain(`sname=${encodeURIComponent("출발지")}`);
  });

  it("도보 모드는 앱·웹 모두 walk로 연다 — 화장실처럼 걸어가는 목적지용", () => {
    const urls = buildNaverDirections({
      origin: { lat: 37.5739, lng: 126.9852, name: "서울시청" },
      destLat: 37.5743,
      destLng: 126.9856,
      destName: "인사동마루 신관 개방화장실",
      mode: "walk",
    });

    expect(urls?.appUrl).toContain("nmap://route/walk");
    expect(urls?.webUrl).toContain("/-/walk");
    expect(urls?.webUrl).not.toContain("/-/transit");
  });

  it("mode를 생략하면 기존 동작인 대중교통을 유지한다", () => {
    const urls = buildNaverDirections({
      origin: { lat: 37.5, lng: 127.0, name: "서울시청" },
      destLat: 37.6,
      destLng: 127.1,
      destName: "장소",
    });

    expect(urls?.appUrl).toContain("nmap://route/public");
    expect(urls?.webUrl).toContain("/-/transit");
  });

  it("장소명을 URL 인코딩해 링크가 깨지지 않게 한다", () => {
    const urls = buildNaverDirections({
      origin: { lat: 37.5, lng: 127.0, name: "서울시청" },
      destLat: 37.6,
      destLng: 127.1,
      destName: "카페 & 정원",
    });

    // 공백·&가 그대로 들어가면 뒤 파라미터가 잘린다 → 인코딩 확인
    expect(urls?.appUrl).toContain(encodeURIComponent("카페 & 정원"));
    expect(urls?.appUrl).not.toContain("dname=카페 & 정원");
  });

  it("출발 좌표가 숫자가 아니면 null", () => {
    expect(
      buildNaverDirections({
        origin: { lat: Number.NaN, lng: 127.1, name: "서울시청" },
        destLat: 37.6,
        destLng: 127.1,
        destName: "장소",
      }),
    ).toBeNull();
  });
});

describe("openNaverDirections (데스크톱)", () => {
  it("데스크톱(jsdom)에서는 웹 길찾기를 새 탭으로 연다", () => {
    const openSpy = vi.spyOn(window, "open").mockImplementation(() => null);

    const ok = openNaverDirections({
      origin: { lat: 37.5, lng: 127.0, name: "서울시청" },
      destLat: 37.6,
      destLng: 127.1,
      destName: "장소",
    });

    expect(ok).toBe(true);
    expect(openSpy).toHaveBeenCalledWith(
      expect.stringContaining("map.naver.com/p/directions"),
      "_blank",
      "noopener",
    );
    openSpy.mockRestore();
  });

  it("출발 좌표가 없으면 아무것도 열지 않고 false", () => {
    const openSpy = vi.spyOn(window, "open").mockImplementation(() => null);

    expect(
      openNaverDirections({
        origin: { lat: Number.NaN, lng: Number.NaN, name: "서울시청" },
        destLat: 37.6,
        destLng: 127.1,
        destName: "장소",
      }),
    ).toBe(false);
    expect(openSpy).not.toHaveBeenCalled();
    openSpy.mockRestore();
  });
});

describe("openNaverMapSearch", () => {
  it("주소만으로 네이버지도 검색을 연다 — 이름을 함께 넘기면 검색이 안 된다(2026-09-02 실사용)", () => {
    const openSpy = vi.spyOn(window, "open").mockImplementation(() => null);

    expect(openNaverMapSearch("서울특별시 종로구 세종대로 189")).toBe(true);
    expect(openSpy).toHaveBeenCalledWith(
      expect.stringContaining("map.naver.com/p/search/"),
      "_blank",
      "noopener",
    );
    expect(openSpy.mock.calls[0]?.[0]).toBe(
      `https://map.naver.com/p/search/${encodeURIComponent("서울특별시 종로구 세종대로 189")}`,
    );
    openSpy.mockRestore();
  });

  it("주소가 없으면 지도를 열지 않는다", () => {
    const openSpy = vi.spyOn(window, "open").mockImplementation(() => null);

    expect(openNaverMapSearch("   ")).toBe(false);
    expect(openSpy).not.toHaveBeenCalled();
    openSpy.mockRestore();
  });
});
