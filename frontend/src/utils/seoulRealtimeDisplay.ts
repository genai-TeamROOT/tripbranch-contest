/*
 * 역할: 서울시 실시간 도시데이터의 숫자를 사람이 읽는 문구로 바꾼다 — 인구 구간,
 *   결제 금액 구간, 인구 그래프의 세로축 눈금.
 * 입력: InfoPlaceCard의 population_forecasts / seoul_realtime_summary 값.
 * 서울시는 인구도 결제 금액도 단일 값이 아니라 구간으로만 준다. 여기서 중간값
 *   하나로 접지 않고 범위를 그대로 보여주는 게 원칙이다 — 막대 높이 계산에만
 *   중간값을 쓴다.
 */

export const AXIS_TICK_COUNT = 4;

/**
 * 눈금이 4칸으로 떨어지도록 축 상한을 올린다. 최대 8만이면 2만 간격(0~8만),
 * 최대 3.2만이면 1만 간격(0~4만)이 된다 — 서울시 앱 그래프와 같은 읽는 방식이다.
 */
export function axisCeiling(maxValue: number) {
  if (maxValue <= 0) return 0;
  const rawStep = maxValue / AXIS_TICK_COUNT;
  const exponent = 10 ** Math.floor(Math.log10(rawStep));
  const step = [1, 2, 2.5, 5, 10]
    .map((factor) => factor * exponent)
    .find((value) => value >= rawStep);
  return (step ?? 10 * exponent) * AXIS_TICK_COUNT;
}

/** 축 눈금 라벨. 만 단위가 넘으면 "4만명"으로 접어 좁은 폭에서도 읽힌다. */
export function axisLabel(value: number) {
  if (value === 0) return "0명";
  if (value >= 10000) {
    const man = value / 10000;
    return `${Number.isInteger(man) ? man : man.toFixed(1)}만명`;
  }
  return `${value.toLocaleString("ko-KR")}명`;
}

/** 만 단위 축약값. 3만은 "3", 3.2만은 "3.2"로 — 축 눈금과 같은 방식이다. */
function manwon(value: number) {
  const man = value / 10000;
  return Number.isInteger(man) ? String(man) : man.toFixed(1);
}

/** 서울시가 구간으로 주는 인구를 자릿수 그대로 — "78,000~80,000명". */
export function formatPopulationRange(
  min: number | null | undefined,
  max: number | null | undefined,
) {
  if (min == null && max == null) return null;
  if (min == null || max == null || min === max) {
    return `${(max ?? min ?? 0).toLocaleString("ko-KR")}명`;
  }
  return `${min.toLocaleString("ko-KR")}~${max.toLocaleString("ko-KR")}명`;
}

/**
 * 같은 값을 만 단위로 접은 것 — "7.8~8만명". 좁은 타일에서는 자릿수를 다 쓰면
 * `30,000~32...`처럼 잘려서 정작 숫자가 안 읽힌다. 툴팁처럼 폭이 넉넉한 자리에서는
 * 접지 않은 `formatPopulationRange`를 쓴다.
 */
export function formatPopulationRangeCompact(
  min: number | null | undefined,
  max: number | null | undefined,
) {
  if (min == null && max == null) return null;
  if (min == null || max == null || min === max) {
    const only = max ?? min ?? 0;
    return only >= 10000 ? `${manwon(only)}만명` : `${only.toLocaleString("ko-KR")}명`;
  }
  if (min >= 10000) return `${manwon(min)}~${manwon(max)}만명`;
  return `${min.toLocaleString("ko-KR")}~${max.toLocaleString("ko-KR")}명`;
}

/**
 * 원 단위 결제 금액을 만원으로 접는다. 상권 금액은 수백만원대라 원 단위로는
 * 자릿수를 세게 된다. 1만원 미만이 0으로 접히지 않도록 소수 첫째 자리를 남긴다.
 */
export function formatPaymentAmountRange(
  min: number | null | undefined,
  max: number | null | undefined,
) {
  if (min == null && max == null) return null;
  const toManwon = (value: number) => {
    const manwon = value / 10000;
    return manwon >= 10 ? Math.round(manwon).toLocaleString("ko-KR") : manwon.toFixed(1);
  };
  if (min == null || max == null || min === max) {
    return `${toManwon(max ?? min ?? 0)}만원`;
  }
  return `${toManwon(min)}~${toManwon(max)}만원`;
}

/** 막대 높이 기준값. 구간의 중간값을 쓰고, 한쪽만 있으면 그 값을 쓴다. */
export function populationMidpoint(
  min: number | null | undefined,
  max: number | null | undefined,
) {
  if (min == null && max == null) return null;
  if (min == null || max == null) return max ?? min ?? null;
  return (min + max) / 2;
}
