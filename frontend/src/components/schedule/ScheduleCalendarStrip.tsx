/*
 * 역할: 저장한 일정 목록 위에 두는 주간 달력 띠. 날짜를 고르면 그 날 저장한
 *   일정만 남긴다.
 * 입력: 저장한 일정들의 날짜(점으로 표시), 지금 고른 날짜.
 * 출력: 날짜 선택·해제, 주 이동.
 * 호출 시점: SavedScheduleList가 검색창 아래에 그린다.
 *
 * **일요일 시작 7칸 고정 폭이다.** 월 전체를 보여주는 달력이 아니라 "이번 주
 * 어느 요일에 저장했더라"를 훑는 용도라, 좌우 화살표로 주 단위만 넘긴다.
 *
 * **스와이프로도 넘긴다**(2026-09-07). 화살표는 폭이 좁아 모바일에서 누르기
 * 빠듯하다 — 날짜 칸이 놓인 자리 전체를 터치 시작·끝 x좌표 차이로 가로채
 * `SWIPE_THRESHOLD_PX`를 넘기면 한 주 이동으로 본다. 라이브러리 없이 두 값의
 * 차이만 보므로 세로 스크롤을 막지 않는다(`preventDefault`를 부르지 않는다).
 */

import { useRef } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { startOfWeek, toDateKey, weekMonthLabel } from "../../utils/scheduleDates";

/* 실수로 살짝 스친 것과 실제로 밀어 넘긴 것을 가르는 문턱. 요일 칸 하나
   너비(대략 40px)보다 조금 크게 잡아 탭이 스와이프로 오인되지 않게 한다. */
const SWIPE_THRESHOLD_PX = 40;

const WEEKDAY_KO = ["일", "월", "화", "수", "목", "금", "토"];
const WEEKDAY_EN = ["S", "M", "T", "W", "T", "F", "S"];

interface ScheduleCalendarStripProps {
  weekStart: Date;
  onWeekChange: (weekStart: Date) => void;
  selectedDateKey: string | null;
  onSelectDate: (dateKey: string | null) => void;
  markedDateKeys: Set<string>;
  isEn: boolean;
}

export function ScheduleCalendarStrip({
  weekStart,
  onWeekChange,
  selectedDateKey,
  onSelectDate,
  markedDateKeys,
  isEn,
}: ScheduleCalendarStripProps) {
  const labels = isEn ? WEEKDAY_EN : WEEKDAY_KO;
  const todayKey = toDateKey(new Date());
  const days = Array.from({ length: 7 }, (_, index) => {
    const day = new Date(weekStart);
    day.setDate(day.getDate() + index);
    return day;
  });

  function shiftWeek(deltaDays: number) {
    const next = new Date(weekStart);
    next.setDate(next.getDate() + deltaDays);
    onWeekChange(next);
  }

  const isCurrentWeek = toDateKey(weekStart) === toDateKey(startOfWeek(new Date()));

  const touchStartX = useRef<number | null>(null);

  function handleTouchStart(event: React.TouchEvent<HTMLDivElement>) {
    touchStartX.current = event.touches[0]?.clientX ?? null;
  }

  function handleTouchEnd(event: React.TouchEvent<HTMLDivElement>) {
    const startX = touchStartX.current;
    touchStartX.current = null;
    if (startX === null) return;
    const endX = event.changedTouches[0]?.clientX;
    if (endX === undefined) return;
    const deltaX = endX - startX;
    if (Math.abs(deltaX) < SWIPE_THRESHOLD_PX) return;
    /* 왼쪽으로 밀면(화면이 왼쪽으로 넘어가는 방향) 다음 주, 오른쪽으로 밀면
       지난 주 — 책장을 넘기는 방향과 같다. */
    shiftWeek(deltaX < 0 ? 7 : -7);
  }

  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-center justify-between px-1">
        <span className="text-sm font-bold text-ink">{weekMonthLabel(weekStart, isEn)}</span>
        {/* 오늘이 이미 보이는 주면 눌러도 할 일이 없다 — 자리만 차지하지 않게 뺀다. */}
        {!isCurrentWeek && (
          <button
            type="button"
            onClick={() => onWeekChange(startOfWeek(new Date()))}
            className="text-xs font-semibold text-brand"
          >
            {isEn ? "Today" : "오늘"}
          </button>
        )}
      </div>
      <div className="flex items-center gap-1">
        <button
          type="button"
          aria-label={isEn ? "Previous week" : "지난 주"}
          onClick={() => shiftWeek(-7)}
          className="flex h-8 w-6 shrink-0 items-center justify-center text-muted hover:text-ink"
        >
          <ChevronLeft size={16} />
        </button>
        <div
          className="grid flex-1 grid-cols-7 gap-1"
          onTouchStart={handleTouchStart}
          onTouchEnd={handleTouchEnd}
        >
          {days.map((day, index) => {
            const dateKey = toDateKey(day);
            const isSelected = selectedDateKey === dateKey;
            const isToday = dateKey === todayKey;
            const hasSaved = markedDateKeys.has(dateKey);
            return (
              <button
                type="button"
                key={dateKey}
                aria-pressed={isSelected}
                aria-label={
                  isEn
                    ? `${day.toLocaleDateString("en-US", { month: "long", day: "numeric" })}${hasSaved ? ", has saved schedules" : ""}`
                    : `${day.toLocaleDateString("ko-KR", { month: "long", day: "numeric" })}${hasSaved ? ", 저장한 일정 있음" : ""}`
                }
                onClick={() => onSelectDate(isSelected ? null : dateKey)}
                className="flex flex-col items-center gap-1 rounded-xl py-1.5 transition-colors hover:bg-chip"
              >
                <span className="text-[11px] font-medium text-muted">{labels[index]}</span>
                <span
                  className={`flex h-7 w-7 items-center justify-center rounded-full text-sm tabular-nums transition-colors ${
                    isSelected
                      ? "bg-brand font-bold text-white"
                      : isToday
                        ? "font-bold text-brand"
                        : "text-ink"
                  }`}
                >
                  {day.getDate()}
                </span>
                <span
                  className={`h-1 w-1 rounded-full ${hasSaved && !isSelected ? "bg-brand" : "bg-transparent"}`}
                />
              </button>
            );
          })}
        </div>
        <button
          type="button"
          aria-label={isEn ? "Next week" : "다음 주"}
          onClick={() => shiftWeek(7)}
          className="flex h-8 w-6 shrink-0 items-center justify-center text-muted hover:text-ink"
        >
          <ChevronRight size={16} />
        </button>
      </div>
    </div>
  );
}
