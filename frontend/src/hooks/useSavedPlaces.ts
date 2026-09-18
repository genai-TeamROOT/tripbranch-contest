/*
 * 역할: 장소 보관함(SCHEDULE-12)의 조회·담기·빼기를 한 곳에서 다룬다.
 * 입력: TripContext의 session_id와 saved_places.
 * 출력: 담긴 place_id 집합, 담기/빼기 토글, 서버 재조회 함수.
 * 호출 시점: 추천 카드의 담기 버튼과 하단 보관함 바가 사용한다.
 *
 * 진실의 원천은 항상 서버다. 화면은 먼저 낙관적으로 바꾸고 응답이 준 목록으로
 * 통째로 확정하며, 실패하면 직전 목록으로 되돌린다 — 세 REST가 전부 담긴 목록
 * 전체를 반환하므로 별도 재조회가 필요 없다.
 */

import { useCallback, useMemo } from "react";
import { ApiError } from "../api/client";
import { fetchSavedPlaces, removeSavedPlace, savePlace } from "../api/trip";
import { useTripDispatch, useTripState } from "../state/TripContext";
import type { SavedPlaceItem, SavedPlacesResponse } from "../types";

/** 매 렌더 새 배열을 만들지 않도록 고정한다 — useMemo 의존성이 흔들린다. */
const EMPTY_ITEMS: SavedPlaceItem[] = [];

/*
 * 담기와 빼기를 갈라 말한다(TP-250). 하나로 뭉치면 방금 무엇을 눌렀는지와 문구가
 * 어긋난다 — 빼기에 실패했는데 "담지 못했어요"가 뜨는 식이다.
 *
 * 실패하면 카드가 원래대로 되돌아가므로(아래 낙관적 갱신), 문구가 그 되돌아감이
 * 무엇이었는지를 설명하는 자리가 된다.
 */
const FAILURE_TEXT = {
  save: {
    ko: "보관함에 담지 못했어요.",
    en: "We couldn't save this place.",
  },
  remove: {
    ko: "보관함에서 빼지 못했어요.",
    en: "We couldn't remove this place.",
  },
} as const;

type SavedPlacesAction = keyof typeof FAILURE_TEXT;

export function useSavedPlaces() {
  const state = useTripState();
  const dispatch = useTripDispatch();
  const sessionId = state.session_id;
  /*
   * 구버전 저장본에서 복원되면 이 필드가 없을 수 있다. 보관함 하나 때문에
   * 채팅 화면 전체가 죽지 않도록 여기서 막는다.
   */
  const items = state.saved_places ?? EMPTY_ITEMS;
  const language = state.language;

  const savedPlaceIds = useMemo(
    () => new Set(items.map((item) => item.place_id)),
    [items],
  );

  /*
   * 서버에서 보관함을 다시 읽는다.
   *
   * 두 곳에서 쓴다 — 새로고침 직후(저장본이 아니라 서버가 기준이다)와 채팅 턴이
   * 끝난 뒤다. 후자가 필요한 이유는 record_rejected()가 거절된 place_id를 서버에서
   * 보관함에서 빼기 때문이다(saved ∩ rejected = ∅). 그 결과는 채팅 응답에 실려
   * 오지 않는다 — AgentResponse.state는 StateApplyResponse(계약 6.2절)이고 거기엔
   * saved_places가 없다. 계약을 넓히는 대신 여기서 다시 읽는 쪽을 택했다.
   *
   * 조회 실패는 삼킨다. 보관함을 못 읽었다고 대화를 막을 이유가 없고, 화면은
   * 직전 목록을 그대로 들고 있으면 된다.
   */
  const refresh = useCallback(async () => {
    if (!sessionId) return;
    try {
      const response = await fetchSavedPlaces(sessionId);
      dispatch({ type: "SET_SAVED_PLACES", payload: { items: response.items } });
    } catch {
      // 의도적으로 무시한다(위 주석 참고).
    }
  }, [dispatch, sessionId]);

  /** 담긴 것이 있을 때만 다시 읽는다 — 빈 보관함에서는 사라질 것이 없다. */
  const refreshIfAny = useCallback(async () => {
    if (items.length === 0) return;
    await refresh();
  }, [items.length, refresh]);

  const commit = useCallback(
    async (
      action: SavedPlacesAction,
      optimistic: SavedPlaceItem[],
      call: () => Promise<SavedPlacesResponse>,
    ) => {
      const previous = items;
      dispatch({ type: "SET_SAVED_PLACES", payload: { items: optimistic } });
      try {
        const response = await call();
        dispatch({ type: "SET_SAVED_PLACES", payload: { items: response.items } });
      } catch (error) {
        dispatch({ type: "SET_SAVED_PLACES", payload: { items: previous } });
        dispatch({
          type: "FAIL_TURN",
          payload: {
            message: error instanceof ApiError ? error.message : FAILURE_TEXT[action][language],
          },
        });
      }
    },
    [dispatch, items, language],
  );

  const toggleSaved = useCallback(
    async (place: { place_id: string; name: string }) => {
      if (!sessionId) return;
      if (savedPlaceIds.has(place.place_id)) {
        await commit(
          "remove",
          items.filter((item) => item.place_id !== place.place_id),
          () => removeSavedPlace(sessionId, place.place_id),
        );
        return;
      }
      /*
       * 새 항목은 목록 끝에 붙인다 — items의 순서가 담은 순서이고, 개수 상한을
       * 넘을 때 무엇을 남길지 이 순서로 정해진다. 서버 응답으로 곧 통째로
       * 교체되므로 saved_from_run_id는 낙관적 항목에서 비워둔다.
       */
      const optimistic: SavedPlaceItem[] = [
        ...items,
        {
          place_id: place.place_id,
          name: place.name,
          saved_from_run_id: "",
          saved_at: new Date().toISOString(),
        },
      ];
      await commit("save", optimistic, () => savePlace(sessionId, place.place_id));
    },
    [commit, items, savedPlaceIds, sessionId],
  );

  const removeSaved = useCallback(
    async (placeId: string) => {
      if (!sessionId) return;
      await commit(
        "remove",
        items.filter((item) => item.place_id !== placeId),
        () => removeSavedPlace(sessionId, placeId),
      );
    },
    [commit, items, sessionId],
  );

  return {
    savedPlaces: items,
    savedPlaceIds,
    toggleSaved,
    removeSaved,
    refresh,
    refreshIfAny,
  };
}
