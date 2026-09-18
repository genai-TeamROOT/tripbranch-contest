/*
 * 역할: 계정에 저장한 일정 목록을 받아오고, 목록이 바뀌면 다시 알려준다.
 * 입력: 없다(로그인한 신원이 바뀌면 다시 받아온다).
 * 출력: 저장한 일정 배열. 아직 못 받아온 동안은 null이다.
 * 호출 시점: 일정 화면과 그 안의 저장 목록.
 *
 * **"아직 모름"과 "없음"을 구분한다.** 화면이 이 값으로 무엇을 그릴지 정하는데,
 * 받아오기 전을 빈 배열로 두면 저장한 일정이 있는 사람도 첫 순간에 "아직 짠 일정이
 * 없어요"를 보게 된다 — 목록이 도착하면서 화면이 갈아끼워진다.
 */

import { useEffect, useState } from "react";
import { useAuth } from "../auth/AuthContext";
import {
  loadSavedSchedules,
  subscribeSavedSchedules,
  type SavedScheduleEntry,
} from "../state/savedSchedules";

export function useSavedSchedules(): SavedScheduleEntry[] | null {
  const { session } = useAuth();
  const [entries, setEntries] = useState<SavedScheduleEntry[] | null>(null);

  useEffect(() => {
    let active = true;
    /* 신원을 함께 넘기는 이유는 SideDrawerContent의 대화 목록과 같다. */
    void loadSavedSchedules(session?.user?.id ?? null).then((loaded) => {
      if (active) setEntries(loaded);
    });
    /* 일정을 저장하면 목록이 바로 바뀐다. TripContext 상태를 볼 수 없는 이유는
       state/savedSchedules.subscribeSavedSchedules 주석에 있다. */
    const unsubscribe = subscribeSavedSchedules(setEntries);
    return () => {
      active = false;
      unsubscribe();
    };
  }, [session?.user?.id]);

  return entries;
}
