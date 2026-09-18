/*
 * 역할: 일정 상세에서 "다녀왔어요" 체크 상태를 관리한다.
 * 입력: 일정을 구분하는 키(저장한 일정 id 또는 세션 메시지 id).
 * 출력: 체크한 정류장 인덱스 집합과 켜고 끄는 함수.
 * 호출 시점: SchedulePage가 시간 띠(ScheduleRibbon)와 정류장 카드(ScheduleRoute)
 *   양쪽에 같은 체크 상태를 나눠 쓸 때.
 *
 * **원래 ScheduleRoute 안에만 있었다**(2026-09-07). 시간 띠도 체크 기준으로
 * 다시 그리기로 하면서, 두 컴포넌트가 같은 상태를 봐야 해 이 훅으로 뺐다.
 */

import { useEffect, useState } from "react";
import { loadVisitedIndices, saveVisitedIndices } from "../state/scheduleProgress";

export function useScheduleVisited(scheduleKey: string): [Set<number>, (index: number) => void] {
  const [visited, setVisited] = useState<Set<number>>(
    () => new Set(loadVisitedIndices(scheduleKey)),
  );

  /* 다른 일정으로 갈아 끼우면(scheduleKey가 바뀌면) 그 일정의 체크를 새로 읽는다. */
  useEffect(() => {
    setVisited(new Set(loadVisitedIndices(scheduleKey)));
  }, [scheduleKey]);

  function toggle(index: number) {
    setVisited((previous) => {
      const next = new Set(previous);
      if (next.has(index)) {
        next.delete(index);
      } else {
        next.add(index);
      }
      saveVisitedIndices(scheduleKey, [...next]);
      return next;
    });
  }

  return [visited, toggle];
}
