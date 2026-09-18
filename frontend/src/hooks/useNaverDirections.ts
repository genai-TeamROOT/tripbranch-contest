/*
 * 역할: 길찾기를 열 때 어디서 출발할지 정하고 네이버지도 딥링크를 연다.
 * 입력: 이 화면이 들고 있는 기기 좌표("위도,경도" 문자열 또는 없음).
 * 출력: 길찾기를 열 수 있는지, 출발점을 푸는 중인지, 그리고 여는 함수.
 * 호출 시점: 길찾기 버튼이 있는 카드·모달이 렌더링할 때.
 *
 * **출발점 사다리는 여기 한 곳에만 둔다.** 부르는 곳이 셋(상세 모달·비교 카드·장소
 * 정보 카드)인데 각자 "좌표가 있으면 연다"를 따로 계산하고 있었다. 사다리가 세 칸으로
 * 늘어나는 지금 그대로 두면 한 곳만 고쳐지는 일이 생긴다.
 *
 *   1. 사용자가 위치 설정에서 정한 출발지 → 그 이름을 좌표로 풀어 쓴다
 *   2. 없으면 기기 좌표
 *   3. 둘 다 없으면 열지 않는다(화면이 현재 위치 받기를 안내한다)
 *
 * **1번이 먼저인 이유는 화면이 이미 그렇게 말하고 있기 때문이다.** 추천 카드의 거리와
 * 이동시간은 사용자가 정한 출발지에서 잰 값이고(D-067) 상단 위치 칩도 그 이름을
 * 보여준다. 그런데 길찾기만 GPS에서 출발하면 같은 화면이 두 가지를 말하게 된다.
 * 백엔드도 같은 순서를 쓴다(agent_context/service.py::_resolve_user_location —
 * 발화·설정이 기기 GPS보다 앞선다).
 *
 * **좌표를 저장소에 넣지 않는다.** 위치 설정은 이름만 저장하고 이름을 좌표로 바꾸는
 * 일은 그때그때 한다 — 좌표를 두 군데서 만들면 어느 쪽이 맞는지 알 수 없게 된다는
 * 기존 판단(LocationPage의 handleSelect 주석)을 그대로 따른다. 대신 같은 이름을 매번
 * 다시 조회하지 않도록 이 모듈의 메모리에만 캐시한다. 새로고침하면 사라지는데,
 * 그래도 되는 값이라 그렇게 둔다.
 */

import { useCallback, useState } from "react";
import { searchPlaces } from "../api/trip";
import { useLocationSettings } from "./useLocationSettings";
import {
  deviceLocationToOrigin,
  openNaverDirections,
  type NaverDirectionsMode,
  type NaverDirectionsOrigin,
} from "../utils/naverDirections";

/*
 * 이름 → 좌표 캐시. 탭이 살아 있는 동안만 유효하다.
 *
 * 값이 아니라 Promise를 담는다 — 같은 이름으로 버튼을 연달아 누르면 조회가 두 번
 * 나가는 것을 막는다. 실패한 조회는 아래에서 지워 다음 시도가 다시 나가게 한다.
 */
const originCache = new Map<string, Promise<NaverDirectionsOrigin | null>>();

/** 테스트에서 캐시가 케이스 사이에 새지 않게 한다. */
export function clearDirectionsOriginCache(): void {
  originCache.clear();
}

async function resolveNamedOrigin(name: string): Promise<NaverDirectionsOrigin | null> {
  const cached = originCache.get(name);
  if (cached) return cached;

  const pending = (async () => {
    const response = await searchPlaces(name);
    const first = response.places[0];
    if (!first) return null;
    /* 검색 결과의 이름이 아니라 사용자가 정한 이름을 쓴다. 네이버가 돌려주는 이름은
       "안국역 3번출구"처럼 더 길 때가 있는데, 출발지 자리에는 사용자가 화면에서
       보고 있는 이름이 떠야 같은 곳을 말한다고 읽힌다. */
    return { lat: first.latitude, lng: first.longitude, name };
  })();

  originCache.set(name, pending);
  try {
    return await pending;
  } catch {
    /* 조회가 실패한 것은 "이 이름에 좌표가 없다"가 아니라 "이번에 못 받았다"이다.
       캐시에 남겨두면 탭이 살아 있는 동안 계속 실패한 채로 굳는다. */
    originCache.delete(name);
    return null;
  }
}

export interface DirectionsDestination {
  destLat: number;
  destLng: number;
  destName: string;
  mode?: NaverDirectionsMode;
}

export interface UseNaverDirections {
  /**
   * 출발점을 하나라도 정할 수 있는가. 목적지 좌표가 있는지는 부르는 쪽이 따로 본다 —
   * 그건 카드마다 다른 사실이라 이 훅이 알 수 없다.
   */
  canRoute: boolean;
  /** 출발지 이름을 좌표로 푸는 중. 버튼을 누른 뒤 잠깐 걸릴 수 있다. */
  isResolvingOrigin: boolean;
  /** 길찾기를 연다. 출발점을 못 정하면 아무것도 안 하고 false. */
  openDirections: (destination: DirectionsDestination) => Promise<boolean>;
}

export function useNaverDirections(
  deviceLocation: string | null | undefined,
): UseNaverDirections {
  const settings = useLocationSettings();
  const [isResolvingOrigin, setIsResolvingOrigin] = useState(false);

  const selectedOrigin = settings.origin;
  const deviceOrigin = deviceLocationToOrigin(deviceLocation);
  const canRoute = Boolean(selectedOrigin) || deviceOrigin !== null;

  const openDirections = useCallback(
    async (destination: DirectionsDestination): Promise<boolean> => {
      let origin: NaverDirectionsOrigin | null = null;

      if (selectedOrigin) {
        setIsResolvingOrigin(true);
        try {
          origin = await resolveNamedOrigin(selectedOrigin);
        } finally {
          setIsResolvingOrigin(false);
        }
      }

      /* 이름을 못 풀었으면 기기 좌표로 내려간다. 저장되는 출발지 이름은 위치 설정
         화면의 장소 검색 결과에서 온 것이라 못 푸는 일이 거의 없지만, 대비가 없으면
         그날 길찾기가 조용히 안 열린다.

         위에서 만든 deviceOrigin을 쓰지 않고 여기서 다시 만든다 — 저 값은 매 렌더
         새 객체라 의존성에 넣으면 이 콜백이 렌더마다 새로 만들어진다. 문자열
         하나만 보면 된다. */
      const resolved = origin ?? deviceLocationToOrigin(deviceLocation);
      if (!resolved) return false;

      return openNaverDirections({ origin: resolved, ...destination });
    },
    [selectedOrigin, deviceLocation],
  );

  return { canRoute, isResolvingOrigin, openDirections };
}
