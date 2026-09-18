import type { Language, RecommendationItem, TravelMode } from "../types";

/**
 * 추천 카드의 "이동" 행 표기. 서버 실측값만 쓰고, 프론트에서 시간을 만들지 않는다.
 *
 * 이전에는 distance_km에 3.6km/h를 곱해 도보 시간을 자체 추정했다. 서버가 카카오
 * 실측으로 근거 문장을 만들기 시작한 뒤로는 같은 카드에 두 개의 도보 시간이 동시에
 * 떴다 — 1.4km 후보에서 근거 문장은 41분, "도보 이동" 행은 24분이었다. 실측이 없으면
 * 시간을 말하지 않고 직선거리만 말한다.
 */

const MODE_LABEL: Record<TravelMode, string> = {
  walking: "도보 이동",
  transit: "대중교통 이동",
  driving: "자동차 이동",
};

const MODE_LABEL_EN: Record<TravelMode, string> = {
  walking: "Walking",
  transit: "Public transit",
  driving: "Driving",
};

/** 실측이 없을 때 쓰는 행 제목. 값이 직선거리라는 것을 제목에서 밝힌다. */
const STRAIGHT_LINE_LABEL = "직선거리";

export function travelLabel(item: RecommendationItem, language: Language = "ko"): string {
  const measured = measuredMinutes(item);
  if (measured === null || !item.travel_mode) {
    return language === "en" ? "Straight-line distance" : STRAIGHT_LINE_LABEL;
  }
  const labels = language === "en" ? MODE_LABEL_EN : MODE_LABEL;
  return (
    labels[item.travel_mode] ?? (language === "en" ? "Straight-line distance" : STRAIGHT_LINE_LABEL)
  );
}

export function travelValue(item: RecommendationItem, language: Language = "ko"): string {
  const measured = measuredMinutes(item);
  if (measured === null) {
    return formatDistance(item.distance_km, language);
  }
  return language === "en"
    ? `About ${formatMinutes(measured, language)}`
    : `약 ${formatMinutes(measured, language)}`;
}

const MODE_SHORT_LABEL: Record<TravelMode, string> = {
  walking: "도보",
  transit: "대중교통",
  driving: "자동차",
};

const MODE_SHORT_LABEL_EN: Record<TravelMode, string> = {
  walking: "Walking",
  transit: "Transit",
  driving: "Driving",
};

/** 상세 시트의 한 줄짜리 이동 표기(예: "도보 9분"). 실측이 없으면 직선거리만 말한다. */
export function travelShortLabel(item: RecommendationItem, language: Language = "ko"): string {
  const measured = measuredMinutes(item);
  if (measured === null || !item.travel_mode) {
    return formatDistance(item.distance_km, language);
  }
  const labels = language === "en" ? MODE_SHORT_LABEL_EN : MODE_SHORT_LABEL;
  return `${labels[item.travel_mode]} ${formatMinutes(measured, language)}`;
}

function measuredMinutes(item: RecommendationItem): number | null {
  const seconds = item.travel_duration_seconds;
  if (seconds === null || seconds === undefined) {
    return null;
  }
  return Math.max(1, Math.round(seconds / 60));
}

function formatMinutes(minutes: number, language: Language): string {
  const hours = Math.floor(minutes / 60);
  const remainder = minutes % 60;
  if (language === "en") {
    if (hours > 0 && remainder > 0) return `${hours} hr ${remainder} min`;
    if (hours > 0) return `${hours} hr`;
    return `${minutes} min`;
  }
  if (hours > 0 && remainder > 0) {
    return `${hours}시간 ${remainder}분`;
  }
  if (hours > 0) {
    return `${hours}시간`;
  }
  return `${minutes}분`;
}

function formatDistance(distanceKm: number, language: Language): string {
  if (distanceKm < 1) {
    return language === "en"
      ? `About ${Math.round(distanceKm * 1000)}m`
      : `약 ${Math.round(distanceKm * 1000)}m`;
  }
  const rounded = Math.round(distanceKm * 10) / 10;
  const value = `${Number.isInteger(rounded) ? rounded : rounded.toFixed(1)}km`;
  return language === "en" ? `About ${value}` : `약 ${value}`;
}
