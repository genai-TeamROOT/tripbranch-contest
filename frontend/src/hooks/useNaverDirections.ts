/*
 * 역할: 길찾기를 열 때 어디서 출발할지 정하고 네이버지도 딥링크를 연다.
 * 입력: 없음. 출발지는 위치 설정(useLocationSettings)에서 읽는다.
 * 출력: 길찾기를 열 수 있는지, 출발점을 푸는 중인지, 그리고 여는 함수.
 * 호출 시점: 길찾기 버튼이 있는 카드·모달이 렌더링할 때.
 *
 * **출발점을 정하는 규칙은 여기 한 곳에만 둔다.** 부르는 곳이 셋(상세 모달·비교 카드·
 * 장소 정보 카드)인데 각자 "출발점이 있으면 연다"를 따로 계산하면 한 곳만 고쳐지는
 * 일이 생긴다.
 *
 *   1. 사용자가 위치 설정에서 정한 출발지 → 그 이름을 좌표로 풀어 쓴다
 *   2. 출발지가 없으면 검색 위치 → 같은 방식으로 풀어 쓴다
 *   3. 둘 다 없으면 열지 않는다(화면이 출발지 정하기를 안내한다)
 *
 * 이 버전은 브라우저 위치 권한을 쓰지 않는다 — 위치는 이름으로만 정한다. 추천 카드의
 * 거리와 이동시간은 출발지에서, 출발지가 없으면 검색 위치에서 잰 값이고(D-067,
 * domain/ranking_origin.py) 상단 위치 칩도 그때 검색 위치 한 칸만 보여주므로, 길찾기도
 * 같은 지점에서 출발해야 한 화면이 한 가지를 말한다. 2를 빼면 추천을 받은 뒤(= 검색
 * 위치가 이미 있는데도) 길찾기마다 출발지를 정하라는 안내가 뜬다.
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

export function useNaverDirections(): UseNaverDirections {
  const settings = useLocationSettings();
  const [isResolvingOrigin, setIsResolvingOrigin] = useState(false);

  const selectedOrigin = settings.origin ?? settings.center;
  const canRoute = Boolean(selectedOrigin);

  const openDirections = useCallback(
    async (destination: DirectionsDestination): Promise<boolean> => {
      if (!selectedOrigin) return false;

      let origin: NaverDirectionsOrigin | null;
      setIsResolvingOrigin(true);
      try {
        origin = await resolveNamedOrigin(selectedOrigin);
      } finally {
        setIsResolvingOrigin(false);
      }

      /* 저장되는 이름은 위치 설정 화면의 장소 검색 결과나 대화에서 해석된 위치라 못 푸는
         일이 거의 없다. 못 풀었으면 열지 않는다 — 대신 쓸 다른 출발점이 없고, 엉뚱한
         곳에서 출발하는 길찾기보다 안 열리는 편이 낫다. */
      if (!origin) return false;

      return openNaverDirections({ origin, ...destination });
    },
    [selectedOrigin],
  );

  return { canRoute, isResolvingOrigin, openDirections };
}
