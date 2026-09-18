/*
 * 역할: 실시간 지하철 도착 카드가 공유하는 표시 로직 — 호선 색, 도착 안내 문구
 *   분해, 상행/하행처럼 같은 역·같은 호선을 방면별로 묶기.
 * 입력: C가 내려주는 InfoPlaceCard.realtime_detail_items(question_type=
 *   realtime_subway). 컴팩트 카드(PlaceInfoCard)와 상세 모달
 *   (RecommendationDetailPreviewModal) 양쪽이 이 파일을 함께 쓴다.
 */

import type { RealtimeInfoDetailItem } from "../types";

const SUBWAY_LINE_COLORS: Record<string, string> = {
  "1호선": "#0052A4",
  "2호선": "#00A84D",
  "3호선": "#EF7C1C",
  "4호선": "#00A5DE",
  "5호선": "#996CAC",
  "6호선": "#CD7C2F",
  "7호선": "#747F00",
  "8호선": "#E6186C",
  "9호선": "#BDB092",
};

const FALLBACK_LINE_COLOR = "#64748B";

export function subwayLineColor(stationTitle: string): string {
  const matched = stationTitle.match(/\d+호선/);
  if (!matched) return FALLBACK_LINE_COLOR;
  return SUBWAY_LINE_COLORS[matched[0]] ?? FALLBACK_LINE_COLOR;
}

/**
 * "목적지행 · 방면 · 약 3분 후"(_format_subway_arrival) 형태에서 맨 끝 도착
 * 안내만 떼어 강조 칩으로 쓴다. 나머지(행선지·방면)는 칩 아래 잔글씨로 남긴다.
 */
export function parseSubwayArrival(value: string): { meta: string | null; arrival: string | null } {
  const parts = value.split(" · ").filter(Boolean);
  if (parts.length === 0) return { meta: null, arrival: null };
  const arrival = parts[parts.length - 1];
  const meta = parts.slice(0, -1).join(" · ") || null;
  return { meta, arrival };
}

export interface SubwayDirectionGroup {
  direction: string;
  items: RealtimeInfoDetailItem[];
}

export interface SubwayLineGroup {
  /** 역명+호선. 도착 목록 항목의 title 그대로다("종로3가 3호선"). */
  stationLine: string;
  directions: SubwayDirectionGroup[];
}

const NO_DIRECTION_LABEL = "방면 정보 없음";

/**
 * 같은 역·같은 호선이라도 상행/하행(또는 내선/외선)은 완전히 다른 방향이다.
 * 나열 순서만으로는 구분이 안 돼(2026-09-02 실사용 지적) 역+호선 → 방면
 * 순으로 묶어, 실제 렌더링에서 방면마다 별도 묶음으로 보여줄 수 있게 한다.
 *
 * 방면 값은 서울시 API 원문을 그대로 쓴다("상행"/"하행"뿐 아니라 2호선의
 * "내선"/"외선"도 있어 상행·하행으로 고정하지 않는다).
 */
export function groupSubwayArrivals(items: RealtimeInfoDetailItem[]): SubwayLineGroup[] {
  const lineOrder: string[] = [];
  const byLine = new Map<string, Map<string, RealtimeInfoDetailItem[]>>();

  for (const item of items) {
    const direction = item.details["방면"] || NO_DIRECTION_LABEL;
    if (!byLine.has(item.title)) {
      lineOrder.push(item.title);
      byLine.set(item.title, new Map());
    }
    const byDirection = byLine.get(item.title) as Map<string, RealtimeInfoDetailItem[]>;
    if (!byDirection.has(direction)) byDirection.set(direction, []);
    byDirection.get(direction)?.push(item);
  }

  return lineOrder.map((stationLine) => ({
    stationLine,
    directions: [...(byLine.get(stationLine) as Map<string, RealtimeInfoDetailItem[]>).entries()].map(
      ([direction, directionItems]) => ({ direction, items: directionItems }),
    ),
  }));
}
