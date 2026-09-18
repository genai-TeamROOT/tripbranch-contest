/*
 * 역할: 화면에 어느 기관의 데이터가 실제로 실렸는지 판정한다.
 * 입력: INFO 장소 카드(상세 조회 결과 포함).
 * 출력: 관광공사·서울시 출처 문구를 각각 붙여야 하는지 여부.
 * 호출 시점: 답변 카드·상세 모달이 출처 줄을 그릴지 정할 때.
 *
 * **있을 때만 붙인다.** 출처는 "이 값이 어디서 왔다"는 말이라, 그 기관 값이
 * 하나도 없는 카드에 붙이면 틀린 표기가 된다. 한 카드에 둘 다 실릴 수도 있어
 * (관광공사 상세 + 서울시 실시간 혼잡도) 두 판정은 서로 배타적이지 않다.
 */

import type { InfoPlaceCard } from "../types";

/*
 * 관광공사에서 오는 값. 개요·운영정보·요금·주차는 KorService2, 무장애 아홉
 * 항목은 KorWithService2, 사진은 같은 API의 대표/상세 이미지, 집중률 예측은
 * TatsCnctrRateService에서 온다. 백엔드 Provider 구성이 바뀌면 이 목록도 같이
 * 바꿔야 한다.
 */
const TOUR_API_FIELDS: Array<keyof InfoPlaceCard> = [
  "overview",
  "operating_hours",
  "rest_date",
  "parking",
  "parking_fee",
  "fee",
  "baby_carriage",
  "pet",
  "credit_card",
  "restroom",
  "homepage",
  "accessible_restroom",
  "accessible_parking",
  "elevator",
  "visual_guide",
  "wheelchair_rental",
  "nursing_room",
  "seating",
  "stroller_rental",
  "guide_dog",
];

/* 답변 필드는 자유 키(상권 지역 등)도 섞여 오므로 관광공사 계약 키만 센다.
   주소·행사도 관광공사 상세/축제 API에서 온다. */
const TOUR_API_ANSWER_KEYS = new Set([
  ...TOUR_API_FIELDS.map(String),
  "address",
  "telephone",
  "event",
  "wheelchair_access",
  "public_transport",
  "infant_family_etc",
  "disability_etc",
  "braille_block",
  "braille_promotion",
  "audio_guide",
]);

/* 서울시 실시간 도시데이터만 실린 카드. 같은 이름의 값이라도 출처가 달라서,
   여기에 걸리면 관광공사 표기를 붙이지 않는다. */
const SEOUL_ONLY_QUESTION_TYPES = new Set([
  "realtime_parking",
  "realtime_public_parking",
  "realtime_subway",
  "realtime_bus",
  "realtime_event",
  "realtime_traffic",
  "realtime_commercial",
  "public_toilet",
]);

export function hasTourApiContent(card: InfoPlaceCard | null | undefined): boolean {
  if (!card) return false;
  if (SEOUL_ONLY_QUESTION_TYPES.has(card.question_type)) return false;

  const hasField = TOUR_API_FIELDS.some((key) => {
    const value = card[key];
    return typeof value === "string" && value.trim() !== "";
  });
  const hasAnswerField = Object.keys(card.answer_fields).some((key) =>
    TOUR_API_ANSWER_KEYS.has(key),
  );
  const hasPhoto = Boolean(card.thumbnail_url) || (card.photos?.length ?? 0) > 0;
  /* 실시간 혼잡도(서울시)가 아니라 집중률 예측(관광공사)만 센다. */
  const hasConcentration = (card.concentration_forecasts?.length ?? 0) > 0;

  return hasField || hasAnswerField || hasPhoto || hasConcentration;
}

/* 서울시 실시간 도시데이터가 실렸다는 표시. 실시간 항목·지도·요약 중 하나라도
   있으면 그 카드는 서울시 값을 보여주고 있다. 출처 링크(realtime_source_url)는
   응답에 따라 비기도 해서 판정 근거로 쓰지 않는다 — 링크가 없다고 출처가 없는
   것은 아니다. */
export function hasSeoulRealtimeContent(card: InfoPlaceCard | null | undefined): boolean {
  if (!card) return false;
  if (SEOUL_ONLY_QUESTION_TYPES.has(card.question_type)) return true;
  return Boolean(
    card.realtime_source_url ||
      card.realtime_map_url ||
      (card.realtime_detail_items?.length ?? 0) > 0 ||
      card.seoul_realtime_summary ||
      (card.population_forecasts?.length ?? 0) > 0 ||
      card.population_current_level ||
      (card.road_incident_counts?.length ?? 0) > 0,
  );
}
