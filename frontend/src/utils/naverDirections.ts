/*
 * 역할: 네이버지도 길찾기 딥링크를 만들고 연다(출발=지정한 지점, 도착=장소).
 *       수단은 대중교통(기본)·도보·자동차 중 고른다.
 * 입력: 출발 지점(좌표와 표시 이름), 목적지 좌표·이름, 수단(생략 시 대중교통).
 * 출력: nmap:// 앱 딥링크를 열고, 앱이 없으면 네이버지도 웹 길찾기로 폴백한다.
 * 참고: API가 아니라 딥링크다 — 경로 계산은 네이버지도가 한다(키·비용 없음).
 *       appname은 검증되는 키가 아니라 "돌아가기"용 호출자 식별자라 현재 호스트명을
 *       그대로 쓴다.
 *
 * **출발점을 밖에서 받는다.** 예전에는 기기 좌표 문자열만 받고 표시 이름을
 * "내 위치"로 박아 뒀다. 그래서 사용자가 위치 설정에서 출발지를 안국역으로 정해도
 * 길찾기는 GPS에서 출발했다 — 같은 화면의 추천 카드는 안국역 기준으로 잰 거리와
 * 이동시간을 보여주는데 버튼만 다른 곳에서 출발하는 어긋남이었다(TP-256).
 * 출발점을 고르는 사다리는 hooks/useNaverDirections가 맡고, 이 파일은 받은 지점을
 * 링크로 옮기기만 한다.
 */

/** 길찾기 수단. 화장실처럼 걸어서 가는 목적지는 "walk"를 쓴다. */
export type NaverDirectionsMode = "public" | "walk" | "car";

export interface NaverDirectionsOrigin {
  lat: number;
  lng: number;
  /**
   * 네이버 화면의 출발지 자리에 적힐 이름. 기기 좌표면 "내 위치", 사용자가 정한
   * 출발지면 그 장소 이름이다. **좌표와 함께 바뀌어야 한다** — 안국역 좌표로
   * 출발하면서 "내 위치"라고 적으면 사용자가 어디서 출발하는지 잘못 읽는다.
   */
  name: string;
}

export interface NaverDirectionsArgs {
  origin: NaverDirectionsOrigin;
  destLat: number;
  destLng: number;
  destName: string;
  /** 생략하면 대중교통. 기존 호출부(추천·비교 카드)의 동작을 바꾸지 않기 위한 기본값이다. */
  mode?: NaverDirectionsMode;
}

// 웹 길찾기 URL의 마지막 경로 조각. 앱 딥링크는 route/{키}를 그대로 쓰는데
// 웹은 대중교통만 이름이 다르다(public → transit).
const _WEB_MODE_SEGMENT: Record<NaverDirectionsMode, string> = {
  public: "transit",
  walk: "walk",
  car: "car",
};

/** 주소만 가진 항목을 네이버지도에서 바로 찾는다.
 *
 * 실시간 주차장 목록은 일부 민영 주차장에 좌표가 없고 주소만 있다. 이름을 함께
 * 넘기면(예: "탑골공원 관광버스전용 주차장") 네이버지도가 그 이름으로 등록된
 * 장소를 찾다가 실패해 검색 자체가 안 되는 경우가 있었다(2026-09-02 실사용).
 * 주소만 검색어로 넘긴다 — 지번·도로명 주소는 이름 없이도 특정된다.
 */
export function openNaverMapSearch(destinationAddress: string): boolean {
  const address = destinationAddress.trim();
  if (!address) return false;

  const query = encodeURIComponent(address);
  const webUrl = `https://map.naver.com/p/search/${query}`;
  window.open(webUrl, "_blank", "noopener");
  return true;
}

function parseLatLng(value: string): { lat: number; lng: number } | null {
  const parts = value.split(",");
  if (parts.length !== 2) return null;
  const lat = Number(parts[0]);
  const lng = Number(parts[1]);
  if (!Number.isFinite(lat) || !Number.isFinite(lng)) return null;
  return { lat, lng };
}

/** 기기 좌표를 쓸 때 붙는 출발지 이름. 위치 설정 화면과 같은 말을 쓴다. */
export const DEVICE_ORIGIN_LABEL = "내 위치";

/**
 * geolocation.ts가 만드는 "위도,경도" 문자열을 출발점으로 바꾼다.
 * 형식이 깨졌거나 값이 없으면 null — 호출부가 다음 칸으로 내려갈 수 있어야 한다.
 */
export function deviceLocationToOrigin(
  deviceLocation: string | null | undefined,
): NaverDirectionsOrigin | null {
  if (!deviceLocation) return null;
  const parsed = parseLatLng(deviceLocation);
  if (!parsed) return null;
  return { ...parsed, name: DEVICE_ORIGIN_LABEL };
}

function callerAppName(): string {
  if (typeof window !== "undefined" && window.location.hostname) {
    return window.location.hostname;
  }
  return "tripbranch";
}

/**
 * 앱 딥링크와 웹 폴백 URL을 만든다. 출발 좌표가 없거나 형식이 깨졌으면 null.
 * 장소명·appname은 URL 인코딩해 값이 링크를 깨거나 뒤 파라미터를 자르지 않게 한다.
 */
export function buildNaverDirections(
  args: NaverDirectionsArgs,
): { appUrl: string; webUrl: string } | null {
  const origin = args.origin;
  if (!Number.isFinite(origin.lat) || !Number.isFinite(origin.lng)) return null;

  const appname = encodeURIComponent(callerAppName());
  const dname = encodeURIComponent(args.destName);
  /* 출발점 라벨. 네이버 화면의 출발지 자리에 그대로 뜬다. 경로 계산은 아래 slat·slng로
     하므로 이 값은 표시 전용이지만, 좌표와 어긋나면 사용자가 어디서 출발하는지
     잘못 읽는다. */
  const sname = encodeURIComponent(origin.name.trim() || DEVICE_ORIGIN_LABEL);

  const mode = args.mode ?? "public";

  // route/public = 대중교통, route/walk = 도보, route/car = 자동차.
  const appUrl =
    `nmap://route/${mode}?slat=${origin.lat}&slng=${origin.lng}&sname=${sname}` +
    `&dlat=${args.destLat}&dlng=${args.destLng}&dname=${dname}&appname=${appname}`;

  // 네이버지도 웹 길찾기. 형식: /출발/도착/-/{수단} (경도,위도,이름 순).
  const webUrl =
    `https://map.naver.com/p/directions/` +
    `${origin.lng},${origin.lat},${sname},,/${args.destLng},${args.destLat},${dname},,/-/` +
    `${_WEB_MODE_SEGMENT[mode]}`;

  return { appUrl, webUrl };
}

function isMobileBrowser(): boolean {
  if (typeof navigator === "undefined") return false;
  return /Android|iPhone|iPad|iPod/i.test(navigator.userAgent);
}

/**
 * 길찾기를 연다(수단은 args.mode, 생략 시 대중교통). 우리 서비스는 웹이므로 플랫폼에 맞춘다.
 * - 데스크톱: 네이버지도 앱이 없으니 웹 길찾기를 새 탭으로 바로 연다(지연 없음).
 * - 모바일: 앱 딥링크를 시도하고, 앱으로 전환되지 않으면(미설치) 웹으로 폴백한다.
 * 열 수 없으면(출발 좌표 없음) false.
 */
export function openNaverDirections(args: NaverDirectionsArgs): boolean {
  const urls = buildNaverDirections(args);
  if (!urls) return false;

  if (!isMobileBrowser()) {
    window.open(urls.webUrl, "_blank", "noopener");
    return true;
  }

  const fallbackTimer = window.setTimeout(() => {
    window.location.href = urls.webUrl;
  }, 1200);

  // 앱이 열리면 탭이 백그라운드로 가므로 폴백을 취소한다.
  const onHidden = () => {
    if (document.visibilityState === "hidden") {
      window.clearTimeout(fallbackTimer);
    }
  };
  document.addEventListener("visibilitychange", onHidden, { once: true });

  window.location.href = urls.appUrl;
  return true;
}
