/*
 * 역할: 일정 항목들에서 시간 띠(가로 막대)와 "지금" 위치를 계산한다.
 * 입력: ScheduleItem 배열, 그리고 "지금"을 물을 때는 현재 시각.
 * 출력: 구간 목록(머무름/이동)과 각 구간의 분, 총 분, 시작·종료 시각.
 * 호출 시점: SchedulePage가 화면 위쪽 시간 띠를 그릴 때.
 *
 * **왜 유틸로 떼어냈나.** 이 계산에는 자정을 넘는 일정과 "지금이 일정 밖"인
 * 경우가 섞여 있어 화면 코드 안에서는 검증하기 어렵다. 화면 없이 값만 재는
 * 테스트를 붙이려고 순수 함수로 뺐다.
 *
 * `estimated_arrival`은 백엔드가 `arrival_at.strftime("%H:%M")`으로 만든 24시간
 * 문자열이다(app/schedule/planner.py). 날짜가 없으므로 자정을 넘는 일정은
 * 문자열만으로는 순서가 뒤집혀 보인다 — 아래 buildScheduleTimeline이 그것을 편다.
 */

import type { ScheduleItem } from "../types";

const MINUTES_PER_DAY = 24 * 60;

export interface TimelineSegment {
  kind: "stay" | "move";
  minutes: number;
  /** 머무는 구간일 때만: items 배열에서의 위치. */
  itemIndex?: number;
}

export interface ScheduleTimeline {
  segments: TimelineSegment[];
  totalMinutes: number;
  /** 자정 기준 분. 자정을 넘긴 일정은 1440을 넘는 값이 된다. */
  startMinutes: number;
  endMinutes: number;
  /** 각 정류장의 도착 시각(자정 기준 분). items와 같은 순서·길이다. */
  arrivals: number[];
}

/** "14:10" → 850. 형식이 어긋나면 null — 화면은 띠를 그리지 않는다. */
export function parseClock(value: string): number | null {
  const match = /^(\d{1,2}):(\d{2})$/.exec(value.trim());
  if (!match) return null;
  const hour = Number(match[1]);
  const minute = Number(match[2]);
  if (hour > 23 || minute > 59) return null;
  return hour * 60 + minute;
}

/** 자정 기준 분 → "오후 2:10". 앱의 다른 시각 표기와 같은 방식을 쓴다. */
export function clockLabel(totalMinutes: number, isEn: boolean): string {
  const wrapped = ((totalMinutes % MINUTES_PER_DAY) + MINUTES_PER_DAY) % MINUTES_PER_DAY;
  const date = new Date(2000, 0, 1, Math.floor(wrapped / 60), wrapped % 60);
  return date.toLocaleTimeString(isEn ? "en-US" : "ko-KR", {
    hour: "2-digit",
    minute: "2-digit",
  });
}

/**
 * 항목들을 머무름/이동 구간으로 편다.
 *
 * **자정을 넘는 일정을 편다.** 도착 시각에 날짜가 없어서 23:30 다음의 00:40은
 * 숫자로는 뒤로 간다 — 앞 항목보다 이른 시각이 나오면 하루를 더한다. 그래야
 * 띠의 폭과 "지금" 위치가 맞는다.
 *
 * 마지막 항목의 `travel_to_next_min`은 갈 곳이 없으므로 무시한다. 서버가 값을
 * 채워 보내도 마찬가지다 — 일정은 마지막 장소에서 끝난다.
 */
export function buildScheduleTimeline(items: ScheduleItem[]): ScheduleTimeline | null {
  if (items.length === 0) return null;

  const arrivals: number[] = [];
  let previous = -1;
  let dayOffset = 0;
  for (const item of items) {
    const parsed = parseClock(item.estimated_arrival);
    if (parsed === null) return null;
    if (previous >= 0 && parsed + dayOffset < previous) dayOffset += MINUTES_PER_DAY;
    const value = parsed + dayOffset;
    arrivals.push(value);
    previous = value;
  }

  const segments: TimelineSegment[] = [];
  items.forEach((item, index) => {
    segments.push({ kind: "stay", minutes: Math.max(0, item.estimated_duration_min), itemIndex: index });
    const isLast = index === items.length - 1;
    const travel = item.travel_to_next_min;
    if (!isLast && travel !== null && travel > 0) {
      segments.push({ kind: "move", minutes: travel });
    }
  });

  const totalMinutes = segments.reduce((sum, segment) => sum + segment.minutes, 0);
  const startMinutes = arrivals[0];
  /*
   * 종료 시각을 마지막 도착 + 마지막 체류로 잡는다. 구간 합(startMinutes +
   * totalMinutes)으로 잡지 않는 이유는 **둘이 어긋날 수 있기 때문**이다 —
   * 도착 시각은 서버가 10분 단위로 올려 보내고(planner._round_up_arrival)
   * 체류·이동 값은 올리지 않는다. 화면에 적는 시각은 서버가 실제로 보낸
   * 도착 시각을 따라야 한다.
   */
  const endMinutes = arrivals[arrivals.length - 1] + Math.max(0, items[items.length - 1].estimated_duration_min);

  return { segments, totalMinutes, startMinutes, endMinutes, arrivals };
}

export interface NowPosition {
  /** 띠 위 위치(0~1). 띠는 구간 합을 100%로 그리므로 그 축에 맞춘 값이다. */
  ratio: number;
  /** 지금 머물고 있는 정류장. 이동 중이면 null. */
  itemIndex: number | null;
  /** 지금 있는 곳을 떠날 때까지 남은 분. 이동 중이면 null. */
  minutesLeftHere: number | null;
}

/**
 * 지금이 일정의 어디쯤인지. 일정이 시작 전이거나 이미 끝났으면 null —
 * 그때는 "지금"을 그리지 않는다(없는 위치를 0%나 100%에 붙이면 거짓말이 된다).
 *
 * **저장한 일정에는 쓰지 않는다.** 저장된 도착 시각은 저장 시점 기준이라 오늘
 * 시계를 얹으면 사흘 전 일정이 방금 짠 것처럼 보인다(SchedulePage의 basisAt과
 * 같은 이유).
 */
export function locateNow(timeline: ScheduleTimeline, now: Date): NowPosition | null {
  const nowMinutes = now.getHours() * 60 + now.getMinutes();
  /* 자정을 넘긴 일정이면 지금도 다음 날일 수 있다 — 시작보다 이르면 하루를 더해 본다. */
  const candidates =
    timeline.endMinutes >= MINUTES_PER_DAY
      ? [nowMinutes, nowMinutes + MINUTES_PER_DAY]
      : [nowMinutes];
  const current = candidates.find(
    (value) => value >= timeline.startMinutes && value <= timeline.endMinutes,
  );
  if (current === undefined) return null;

  const elapsed = current - timeline.startMinutes;
  const ratio = timeline.totalMinutes > 0 ? Math.min(1, elapsed / timeline.totalMinutes) : 0;

  let cursor = 0;
  for (const segment of timeline.segments) {
    const end = cursor + segment.minutes;
    if (elapsed < end || (elapsed === end && segment === timeline.segments[timeline.segments.length - 1])) {
      if (segment.kind === "stay" && segment.itemIndex !== undefined) {
        return { ratio, itemIndex: segment.itemIndex, minutesLeftHere: Math.max(0, end - elapsed) };
      }
      return { ratio, itemIndex: null, minutesLeftHere: null };
    }
    cursor = end;
  }
  return { ratio, itemIndex: null, minutesLeftHere: null };
}
