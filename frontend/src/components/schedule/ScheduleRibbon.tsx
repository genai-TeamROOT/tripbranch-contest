/*
 * 역할: 일정 진행을 게이지 하나로 보여준다. 가장 멀리 체크한 곳까지 찬다.
 * 입력: 일정 항목들, 언어, 체크한 정류장들.
 * 출력: 시작·종료 시각, 다녀온 정류장 수, 채워지는 막대.
 * 호출 시점: SchedulePage가 일정 본문 맨 위에 그린다.
 *
 * **한때 정류장마다 칸을 나눠 폭을 분(分)에 비례시켰다.** 그 아래 정류장
 * 카드가 체크 하나로 "어디까지 왔는지"를 이미 말해주게 되면서(2026-09-07),
 * 위 띠까지 칸을 나눌 이유가 옅어졌다.
 *
 * **"칸이 여러 개로 보인다"는 되돌림을 네 번 받았다**(2026-09-07). 왼쪽부터
 * 채우는 막대(순서를 가정해 건너뛰면 틀렸다) → 정류장마다 칸을 나눠 각자
 * 채우기(칸 사이 실금) → 막대 두 개를 겹치기(합쳐질 때 하나가 사라져
 * 보임) → 칸을 절대좌표로 고정하기 → 건너뛴 곳에 얇은 눈금 찍기, 전부
 * "이 막대 위에서 건너뛴 곳까지 구분"하려다 생긴 문제였다. **구분 자체를
 * 이 막대에서 걷어냈다.** 막대는 가장 멀리 체크한 곳까지 하나의 색으로만
 * 찬다. 어떤 곳을 건너뛰었는지는 바로 아래 정류장 카드(ScheduleRoute)의
 * "건너뛰었어요" 표시가 맡는다 — 카드는 애초에 정류장마다 독립된 요소라
 * "칸처럼 보인다"는 문제 자체가 없다.
 *
 * 시간 계산은 utils/scheduleTimeline.ts 를 그대로 쓴다 — 시작·종료 시각
 * 표기에 자정을 넘는 일정이 섞여 있어 화면 없이 값으로 확인해야 했다.
 */

import { buildScheduleTimeline, clockLabel } from "../../utils/scheduleTimeline";
import type { ScheduleItem } from "../../types";

interface ScheduleRibbonProps {
  items: ScheduleItem[];
  isEn: boolean;
  /** 체크한 정류장 인덱스 집합(`useScheduleVisited`). 안 넘기면 게이지가 비어
      있다. */
  visited?: Set<number>;
}

export function ScheduleRibbon({ items, isEn, visited }: ScheduleRibbonProps) {
  const timeline = buildScheduleTimeline(items);
  /* 도착 시각을 못 읽으면 띠를 통째로 그리지 않는다 — 시작·종료 시각을
     지어낼 수 없다. */
  if (timeline === null || timeline.totalMinutes <= 0) return null;

  const visitedCount = visited ? items.filter((_, index) => visited.has(index)).length : 0;
  /* 게이지는 "가장 멀리 체크한 곳"까지 찬다. 건너뛰어 체크해도(예: 두 번째만
     체크) 그 위치만큼 찬 것으로 본다 — 순서를 가정하지 않는다. */
  const furthestVisited = visited && visited.size > 0 ? Math.max(...visited) : -1;
  const progress = items.length > 0 ? (furthestVisited + 1) / items.length : 0;

  return (
    <section className="flex flex-col gap-1.5" aria-label={isEn ? "Progress" : "일정 진행"}>
      <div className="flex items-center justify-between text-xs tabular-nums text-muted">
        <span>{clockLabel(timeline.startMinutes, isEn)}</span>
        <span className="font-bold text-ink">
          {isEn
            ? `${visitedCount}/${items.length} visited`
            : `${visitedCount}/${items.length} 다녀왔어요`}
        </span>
        <span>{clockLabel(timeline.endMinutes, isEn)}</span>
      </div>

      <div
        role="progressbar"
        aria-valuenow={Math.round(progress * 100)}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label={isEn ? "Furthest visited stop" : "가장 멀리 다녀온 곳"}
        className="h-3 w-full overflow-hidden rounded-full bg-chip"
      >
        <div
          className="h-full rounded-full bg-brand transition-[width] duration-500 ease-out"
          style={{ width: `${progress * 100}%` }}
        />
      </div>
    </section>
  );
}
