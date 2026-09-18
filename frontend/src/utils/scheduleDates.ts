/*
 * 역할: 저장한 일정을 날짜로 훑어볼 때 쓰는 순수 날짜 계산.
 * 호출 시점: ScheduleCalendarStrip(달력 띠 렌더)과 SavedScheduleList(날짜 필터).
 *
 * 시간 띠 계산(scheduleTimeline.ts)과는 다른 관심사다 — 그쪽은 하루 안 분 단위
 * 위치를, 이건 달력 위 날짜 하나를 다룬다.
 */

export function toDateKey(date: Date): string {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

export function startOfWeek(date: Date): Date {
  const start = new Date(date);
  start.setHours(0, 0, 0, 0);
  start.setDate(start.getDate() - start.getDay());
  return start;
}

/* 주 단위 띠 위에 적을 월 라벨. 일요일 시작 7칸이라 월 경계에 걸치는 주가
   흔하다(예: 8/31~9/6) — 그때는 "8월 – 9월"처럼 두 달을 함께 적는다. */
export function weekMonthLabel(weekStart: Date, isEn: boolean): string {
  const weekEnd = new Date(weekStart);
  weekEnd.setDate(weekEnd.getDate() + 6);
  const sameMonth =
    weekStart.getFullYear() === weekEnd.getFullYear() &&
    weekStart.getMonth() === weekEnd.getMonth();

  if (sameMonth) {
    return isEn
      ? weekStart.toLocaleDateString("en-US", { month: "long", year: "numeric" })
      : `${weekStart.getFullYear()}년 ${weekStart.getMonth() + 1}월`;
  }

  const sameYear = weekStart.getFullYear() === weekEnd.getFullYear();
  const startLabel = isEn
    ? weekStart.toLocaleDateString("en-US", { month: "short" })
    : `${weekStart.getMonth() + 1}월`;
  const endLabel = isEn
    ? weekEnd.toLocaleDateString("en-US", {
        month: "short",
        year: sameYear ? undefined : "numeric",
      })
    : `${weekEnd.getMonth() + 1}월`;
  return `${startLabel} – ${endLabel}`;
}
