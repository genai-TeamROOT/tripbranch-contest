/*
 * 역할: 즐겨찾기 목록을 저장소와 묶어 화면에 내려준다.
 * 입력: 없음(state/sidebarStorage).
 * 출력: 목록과 갱신 함수. 갱신하면 저장소에 쓰고 구독 중인 화면이 함께 바뀐다.
 * 호출 시점: 사이드바와 위치 설정 화면.
 *
 * **화면마다 사본을 두지 않는다.** 예전에는 두 화면이 각자 useState로 목록을 들고
 * useEffect로 저장소에 되썼다. 그러면 한쪽에서 담아도 다른 쪽은 새로고침해야
 * 보이고, 같은 목록이 두 군데서 다르게 보인다.
 *
 * 저장소에 쓰는 일도 여기서만 한다 — 화면이 "바뀌면 저장"하는 효과를 따로 두면
 * 구독으로 받은 값을 다시 저장하면서 서로 되쓰기가 오간다.
 *
 * 마운트할 때 계정과 맞춘다(favoritesSync). localStorage는 이제 정본이 아니라
 * 거울이라, 다른 기기에서 담은 것도 여기서 따라온다.
 */

import { useCallback, useEffect, useState } from "react";
import { pushFavorites, syncFavorites } from "../state/favoritesSync";
import { loadFavorites, subscribeFavorites, type FavoritePlace } from "../state/sidebarStorage";

type Updater = FavoritePlace[] | ((previous: FavoritePlace[]) => FavoritePlace[]);

export function useFavorites(): [FavoritePlace[], (next: Updater) => void] {
  const [favorites, setFavorites] = useState<FavoritePlace[]>(() => loadFavorites());

  useEffect(() => {
    /* 구독 사이에 바뀐 값이 있을 수 있다(마운트 직전에 다른 화면이 고친 경우). */
    setFavorites(loadFavorites());
    const unsubscribe = subscribeFavorites(setFavorites);

    /* 계정과 맞춘다. 서버가 정본이고, 결과는 저장소에 쓰이며 구독으로 돌아온다.
       페이지 로드당 한 번만 실제 요청이 나간다. */
    let active = true;
    void syncFavorites().then((synced) => {
      if (active) setFavorites(synced);
    });

    return () => {
      active = false;
      unsubscribe();
    };
  }, []);

  const update = useCallback((next: Updater) => {
    /* 저장소를 기준으로 계산한다 — 이 화면의 사본이 낡았을 수 있다. */
    const current = loadFavorites();
    void pushFavorites(typeof next === "function" ? next(current) : next);
  }, []);

  return [favorites, update];
}
