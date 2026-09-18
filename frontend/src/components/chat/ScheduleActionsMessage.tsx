/*
 * 역할: 일정 결과에 딸린 재편성 버튼(다른 코스 보기·검색 범위 넓히기)을 그린다.
 * 입력: 그 턴이 일정을 못 짰는지와 로딩 상태.
 * 출력: 버튼 줄. 일정을 못 짠 턴이면 범위를 넓히는 버튼만 낸다.
 * 호출 시점: ChatMessageList가 schedule_actions 메시지를 그릴 때.
 *
 * **일정 카드와 갈라 둔 이유는 수명이다.** 이 메시지는 다음 발화가 나가는 순간
 * 걷어내지고(TripContext의 isPastTurnControl) 일정 카드는 기록으로 남는다.
 * 지난 턴의 "다른 코스 보기"를 누르면 그때 조건으로 다시 짜서 결과가 어긋난다.
 *
 * "일정 저장하기"는 여기 없다 — ScheduleResultMessage에 남는다. 그건 새 요청이
 * 아니라 그 턴의 일정을 run_id로 저장하는 것이라, 지난 일정을 나중에 저장하는
 * 것도 정상적인 사용이기 때문이다.
 */

interface ScheduleActionsMessageProps {
  /** 일정을 못 짠 턴인가. 버튼 구성이 갈린다. */
  hasNoSchedule: boolean;
  isLoading: boolean;
  onRequestMore: () => void;
  onRelaxRadius: () => void;
}

export function ScheduleActionsMessage({
  hasNoSchedule,
  isLoading,
  onRequestMore,
  onRelaxRadius,
}: ScheduleActionsMessageProps) {
  if (hasNoSchedule) {
    return (
      <div className="mr-auto flex w-full flex-col gap-3 text-sm">
        <button
          type="button"
          disabled={isLoading}
          onClick={onRelaxRadius}
          className="w-fit rounded-full bg-rust px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-rust/90 active:scale-[0.98] disabled:opacity-50"
        >
          검색 범위 넓혀서 다시 찾기
        </button>
      </div>
    );
  }

  return (
    <div className="mr-auto flex w-full flex-wrap items-center gap-2">
      <button
        type="button"
        disabled={isLoading}
        onClick={onRequestMore}
        className="rounded-full border border-border bg-white px-4 py-2.5 text-sm font-medium text-ink transition-colors hover:border-brand hover:text-brand disabled:opacity-50"
      >
        {isLoading ? "불러오는 중..." : "다른 코스 보기"}
      </button>
      <span className="text-xs text-muted">
        다른 조건이 있으면 아래 입력창에 이어서 적어주세요.
      </span>
    </div>
  );
}
