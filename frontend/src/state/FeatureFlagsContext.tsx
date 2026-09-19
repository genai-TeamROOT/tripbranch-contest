/* eslint-disable react-refresh/only-export-components */
/*
 * 역할: 서버가 알려준 기능 스위치(GET /api/features)를 앱 전체에 나눠 준다.
 * 입력: 앱이 뜰 때 한 번 받아 오는 FeaturesResponse.
 * 출력: useTasteEnabled() — 후기·블로그 데이터 기능(취향)이 켜져 있는지.
 *   useFeatureFlagsLoaded() — 서버 답(성공이든 실패든)을 받았는지.
 * 호출 시점: App.tsx가 최상위에서 FeatureFlagsProvider로 감싼다. 메뉴(사이드바·
 *   드로어), /preferences 라우트, 추천 결과 캡션, 상세 카드의 AI 추천 이유 절이 읽는다.
 *
 * **받기 전(로딩 중)과 실패했을 때는 "꺼짐"으로 본다.** 켜짐으로 두면 서버가
 * 취향을 끈 배포에서 메뉴가 잠깐 떴다 사라지거나, 조회가 실패한 채로 계속 남는다 —
 * 눌러 봐야 저장한 취향이 순위에 아무 영향도 주지 않는 화면이다. 켜진 배포에서
 * 메뉴가 잠깐 늦게 나타나는 편이, 동작하지 않는 기능을 보여주는 것보다 안전하다.
 * 같은 이유로 Provider 밖에서 읽어도(테스트에서 컴포넌트만 렌더할 때) 꺼짐이다.
 *
 * **받았는지(loaded)를 따로 둔 것은 /preferences 하나 때문이다.** 꺼짐이면 그
 * 주소를 홈으로 돌려보내는데(AppRoutes), 로딩 중의 꺼짐으로 돌려보내면 켜진
 * 서버에서도 그 화면에서 새로고침할 때마다 홈으로 튕긴다. 로딩 중에는 화면을
 * 비워 두고(기능을 보여주지 않는다는 점은 같다) 답을 받은 뒤에 판정한다.
 */

import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { fetchFeatures } from "../api/features";

type FeatureFlags = {
  tasteEnabled: boolean;
  loaded: boolean;
};

const LOADING: FeatureFlags = { tasteEnabled: false, loaded: false };

/* Provider 밖(컴포넌트 단위 테스트)은 "받았고 꺼짐"이다 — 영영 로딩으로 남지 않게. */
const FeatureFlagsContext = createContext<FeatureFlags>({ tasteEnabled: false, loaded: true });

export function FeatureFlagsProvider({ children }: { children: ReactNode }) {
  const [flags, setFlags] = useState<FeatureFlags>(LOADING);

  useEffect(() => {
    let cancelled = false;
    void fetchFeatures()
      .then((response) => {
        /* 서버가 불리언이 아닌 값을 주면(구버전 서버·프록시 오류 페이지) 켜지 않는다. */
        if (!cancelled) {
          setFlags({ tasteEnabled: response?.taste_enabled === true, loaded: true });
        }
      })
      .catch(() => {
        /* 실패는 꺼짐이다(위 주석). */
        if (!cancelled) setFlags({ tasteEnabled: false, loaded: true });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return <FeatureFlagsContext.Provider value={flags}>{children}</FeatureFlagsContext.Provider>;
}

/** 후기·블로그 데이터 기능(취향)이 켜져 있는지. 받기 전·실패 시에는 false다. */
export function useTasteEnabled(): boolean {
  return useContext(FeatureFlagsContext).tasteEnabled;
}

/** 서버 답을 받았는지(실패 포함). 꺼짐을 "되돌려 보내기"로 쓸 때만 필요하다. */
export function useFeatureFlagsLoaded(): boolean {
  return useContext(FeatureFlagsContext).loaded;
}
