/*
 * 역할: 추천 결과에 딸린 동작 버튼(다른 장소 보기·반경 넓히기·기준 전환)을 그린다.
 * 입력: 그 턴이 빈손이었는지와 기준 전환 제안.
 * 출력: 버튼 줄. 결과가 없던 턴이면 "반경 넓혀 다시 찾기"를, 있으면 "다른 장소
 *   보기"를 낸다.
 * 호출 시점: ChatMessageList가 recommendation_actions 메시지를 그릴 때.
 *
 * **카드와 갈라 둔 이유는 수명이다.** 이 메시지는 다음 발화가 나가는 순간
 * 걷어내지고(TripContext의 isPastTurnControl) 카드와 취향 표만 기록으로 남는다.
 * 한 메시지 안에 있을 때는 지난 턴의 버튼이 계속 눌렸고, 그러면 그때 기준의
 * 요청이 지금 맥락으로 나가 결과가 어긋났다.
 */

import type { Language, TravelOriginToggle } from "../../types";

const RADIUS_RELAXATION_STEP_KM = 0.5;

interface RecommendationActionsMessageProps {
  /** 그 턴이 빈손이었는가. 버튼 구성이 갈린다. */
  hasNoResults: boolean;
  /** 있을 때만 "OO 기준으로 다시 보기" 버튼을 노출한다(D-071). */
  travelOriginToggle?: TravelOriginToggle | null;
  isLoading: boolean;
  onRequestMore: () => void;
  onRelaxRadius: () => void;
  /** travelOriginToggle이 있을 때만 호출 가능. 버튼 클릭 시 그 값 그대로 넘어온다. */
  onToggleTravelOrigin?: (toggle: TravelOriginToggle) => void;
  language?: Language;
}

export function RecommendationActionsMessage({
  hasNoResults,
  travelOriginToggle,
  isLoading,
  onRequestMore,
  onRelaxRadius,
  onToggleTravelOrigin,
  language = "ko",
}: RecommendationActionsMessageProps) {
  const text =
    language === "en"
      ? {
          widen: `Search a wider area (+${RADIUS_RELAXATION_STEP_KM} km)`,
          basedOn: (name: string) => `View results based on ${name}`,
          currentLocation: "View results based on my current location",
          loading: "Loading...",
          more: "Show more places",
          hint: "Add another condition in the message box below.",
        }
      : {
          widen: `검색 반경 넓혀서 다시 찾기 (+${RADIUS_RELAXATION_STEP_KM}km)`,
          basedOn: (name: string) => `${name} 기준으로 다시 보기`,
          currentLocation: "현재 위치 기준으로 다시 보기",
          loading: "불러오는 중...",
          more: "다른 장소 보기",
          hint: "다른 조건이 있으면 아래 입력창에 이어서 적어주세요.",
        };

  const originToggleButton = travelOriginToggle && onToggleTravelOrigin && (
    <button
      type="button"
      disabled={isLoading}
      onClick={() => onToggleTravelOrigin(travelOriginToggle)}
      className="w-fit rounded-full border border-border bg-white px-4 py-2.5 text-sm font-medium text-ink transition-colors hover:border-brand hover:text-brand disabled:opacity-50"
    >
      {travelOriginToggle.alternative_origin === "search_center"
        ? text.basedOn(travelOriginToggle.alternative_origin_name)
        : text.currentLocation}
    </button>
  );

  if (hasNoResults) {
    return (
      <div className="mr-auto flex w-full flex-wrap gap-2">
        <button
          type="button"
          disabled={isLoading}
          onClick={onRelaxRadius}
          className="w-fit rounded-full bg-rust px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-rust/90 active:scale-[0.98] disabled:opacity-50"
        >
          {text.widen}
        </button>
        {originToggleButton}
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
        {isLoading ? text.loading : text.more}
      </button>
      {originToggleButton}
      <span className="text-xs text-muted">{text.hint}</span>
    </div>
  );
}
