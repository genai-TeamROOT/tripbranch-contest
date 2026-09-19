/*
 * 역할: 추천 장소 하나를 가로 스크롤 카드로 렌더링한다(DESIGN_SYSTEM.md §6.5).
 * 입력: RecommendationItem 데이터.
 * 출력: 이미지, 순위, 장소명, 거리, 남은 운영시간, 주의사항, 담기 토글.
 *
 * **추천 사유를 카드에 쓰지 않는다**(2026-09-08). 사유는 D가
 * `_recommendation_reason()`에서 만드는 한 가지 형식뿐이다 — "<축들> 조건을 종합한
 * N순위 추천이에요."(recommendation_pipeline.py). 축 이름만 바뀌고 문장은 같아서
 * 카드마다 두 줄을 먹으면서 하는 말이 "N순위"인데, 그 순위는 카드 제목에 이미
 * `N위`로 붙어 있다. 사유 자체는 남아 있고(`recommendation_reason`) 상세
 * 미리보기의 "AI가 추천하는 이유" 섹션과 개발자 패널이 그대로 쓴다.
 * 호출 시점: PlaceCardRow가 추천/검증 불가 목록을 한 줄씩 그릴 때 호출된다.
 * 담기/빼기 액션은 onToggleSave가 주어질 때만 노출한다(SCHEDULE-12 카드 3).
 * TODO: 지도 링크, 제외 액션, 실시간 영업 정보가 생기면 하위 UI를 확장한다.
 */

import { ChevronRight, Heart, MapPin } from "lucide-react";
import type { Language, RecommendationItem } from "../types";
import { PlaceThumbnail } from "./PlaceThumbnail";
import { isAlwaysOpen } from "../utils/operatingHours";
import { travelValue } from "../utils/travelDisplay";

interface PlaceCardProps {
  item: RecommendationItem;
  /** 1위부터 매기는 순위. 검증 불가 목록처럼 순위 의미가 없으면 생략한다. */
  rank?: number;
  /** 추천 결과의 현재 정보만으로 여는 1차 상세 미리보기. */
  onOpenDetail?: (item: RecommendationItem) => void;
  /*
   * 있을 때만 담기/빼기 토글을 노출한다(SCHEDULE-12). 보관함을 안 쓰는 화면
   * (사진 유사 검색 결과 등)은 이 prop을 주지 않으면 기존과 동일하게 그려진다.
   */
  onToggleSave?: (item: RecommendationItem) => void;
  /** 이 장소가 지금 보관함에 담겨 있는지. onToggleSave가 있을 때만 의미가 있다. */
  isSaved?: boolean;
  language?: Language;
}

function formatRemainingDuration(remainingMinutes: number, language: Language): string {
  // 카드에서는 분 단위 정밀도보다 빠른 비교가 중요하므로, 가장 가까운 시간으로
  // 반올림한다. 운영 종료가 임박한 경우 0시간으로 보이지 않도록 최소 1시간이다.
  const hours = Math.max(1, Math.round(remainingMinutes / 60));
  return language === "en" ? `${hours} hr remaining` : `${hours}시간 남음`;
}

function formatClosingTime(remainingMinutes: number, language: Language): string {
  const closesAt = new Date(Date.now() + remainingMinutes * 60 * 1000);
  const time = new Intl.DateTimeFormat("ko-KR", {
    timeZone: "Asia/Seoul",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(closesAt);
  return language === "en"
    ? `Closes at ${time} (${formatRemainingDuration(remainingMinutes, language)})`
    : `운영 종료 예정 ${time} (${formatRemainingDuration(remainingMinutes, language)})`;
}

function hoursRemainingLabel(item: RecommendationItem, language: Language): string {
  // D가 제공하는 당일 적용 운영 구간을 우선 표시한다. 이전 응답 또는 구간을
  // 판별할 수 없는 후보는 기존의 종료 예정 시각 표기로 자연스럽게 폴백한다.
  // 운영시간 무시로 폐점 후보를 함께 보여주는 경우에도 이 값은 남아 있다. 이때
  // remaining_minutes만 null이라는 이유로 "확인 불가"로 덮어쓰면 실제 운영시간을
  // 알고도 숨기게 되므로, 구간과 현재 폐점 상태를 함께 표시한다.
  if (item.operating_hours_display) {
    if (isAlwaysOpen(item.operating_hours_display)) {
      return item.operating_hours_display;
    }
    if (item.remaining_minutes === null) {
      return `${item.operating_hours_display} (${language === "en" ? "Currently closed" : "현재 운영시간 아님"})`;
    }
    return `${item.operating_hours_display} (${formatRemainingDuration(item.remaining_minutes, language)})`;
  }

  if (item.remaining_minutes === null) {
    return language === "en" ? "Unavailable" : "확인 불가";
  }

  return formatClosingTime(item.remaining_minutes, language);
}

export function PlaceCard({
  item,
  rank,
  onOpenDetail,
  onToggleSave,
  isSaved = false,
  language = "ko",
}: PlaceCardProps) {
  const saveText =
    language === "en"
      ? { toggle: (n: string) => `Save ${n} for later` }
      : { toggle: (n: string) => `${n} 보관함에 담기` };

  return (
    <li className="w-40 shrink-0">
      <div
        className={`relative w-full text-left${onOpenDetail ? " cursor-pointer" : ""}`}
        role={onOpenDetail ? "button" : undefined}
        tabIndex={onOpenDetail ? 0 : undefined}
        aria-label={
          onOpenDetail
            ? language === "en"
              ? `View details for ${item.name}`
              : `${item.name} 장소 정보 미리 보기`
            : undefined
        }
        onClick={onOpenDetail ? () => onOpenDetail(item) : undefined}
        onKeyDown={
          onOpenDetail
            ? (event) => {
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault();
                  onOpenDetail(item);
                }
              }
            : undefined
        }
      >
        <div className="group relative overflow-hidden rounded-2xl">
          {/* 배치 조회에서 못 찾은 장소는 image_url이 null/undefined로 온다. 주소가
              있어도 원본이 사라졌을 수 있어(D-087과 같은 종류) 자리표시는 같다.
              작은 썸네일만 죽은 장소는 image_url_fallback으로 갈아탄다. */}
          <PlaceThumbnail src={item.image_url} fallbackSrc={item.image_url_fallback} />

          {onToggleSave && (
            <button
              type="button"
              aria-pressed={isSaved}
              aria-label={saveText.toggle(item.name)}
              className={`absolute right-1.5 top-1.5 flex h-7 w-7 items-center justify-center rounded-full bg-white/80 backdrop-blur-sm transition-colors ${
                isSaved ? "text-rust" : "text-muted hover:text-rust"
              }`}
              onClick={(event) => {
                event.stopPropagation();
                onToggleSave(item);
              }}
              onKeyDown={(event) => event.stopPropagation()}
            >
              <Heart size={14} className={isSaved ? "fill-current" : undefined} />
            </button>
          )}
        </div>
        <div className="pt-2">
          <p className="truncate text-sm font-bold text-ink">
            {typeof rank === "number" && <span className="text-brand">{rank}위 </span>}
            {item.name}
          </p>
          <p className="mt-1 flex items-center gap-1 text-[11px] text-muted">
            <MapPin size={10} /> {travelValue(item, language)}
          </p>
          <p className="mt-1 text-[11px] text-muted">{hoursRemainingLabel(item, language)}</p>
          {item.warnings.length > 0 && (
            <p className="mt-1 truncate text-[11px] text-gold">{item.warnings[0]}</p>
          )}
          {onOpenDetail && (
            <p className="mt-1.5 flex items-center gap-0.5 text-[11px] font-semibold text-brand">
              {language === "en" ? "Preview" : "장소 미리보기"} <ChevronRight size={11} />
            </p>
          )}
        </div>
      </div>
    </li>
  );
}
