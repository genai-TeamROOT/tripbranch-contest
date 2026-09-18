/*
 * 역할: 위치 설정(출발지·검색 기준)을 화면이 구독한다.
 * 입력: 없음(state/locationSettings).
 * 출력: 지금 정해져 있는 두 값.
 * 호출 시점: 상단 위치 pill을 그리는 화면(HomePage·ChatPage)과 위치 설정 화면.
 *
 * 저장소를 읽기만 하면 값이 바뀌어도 다시 그려지지 않는다 — 위치 설정 화면이
 * 시트로 열려 있는 동안 그 뒤의 홈은 계속 마운트된 채이기 때문이다. 구독해서
 * 정한 즉시 헤더가 바뀌게 한다.
 */

import { useEffect, useState } from "react";
import {
  loadLocationSettings,
  subscribeLocationSettings,
  type LocationSettings,
} from "../state/locationSettings";

export function useLocationSettings(): LocationSettings {
  const [settings, setSettings] = useState<LocationSettings>(() => loadLocationSettings());

  useEffect(() => {
    /* 구독 사이에 바뀐 값이 있을 수 있다(마운트 직전에 다른 화면이 고친 경우). */
    setSettings(loadLocationSettings());
    return subscribeLocationSettings(setSettings);
  }, []);

  return settings;
}
