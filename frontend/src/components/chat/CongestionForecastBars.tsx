/*
 * 역할: 실시간 인구 혼잡도·관광지 집중률 예측을 시각적으로 보여준다.
 * 입력: InfoPlaceCard의 population_* / concentration_* 필드.
 * 출력: 현재 단계 게이지 + 색상이 단계별로 다른 예측 막대그래프.
 * 호출 시점: PlaceInfoCard(요약 카드)와 RecommendationDetailPreviewModal(상세 모달) 양쪽.
 */

import { useState } from "react";
import type {
  InfoPlaceCard as InfoPlaceCardData,
  RoadIncidentCategoryCount,
} from "../../types";
import {
  AXIS_TICK_COUNT,
  axisCeiling,
  axisLabel,
  formatPopulationRange,
  populationMidpoint,
} from "../../utils/seoulRealtimeDisplay";


/*
 * 서울시 인구 혼잡도 원문 단계(한글) → 팔레트 색상.
 * amber-400/orange-500을 쓰면 가운데 두 단계가 거의 같은 색으로 보인다 — 토큰
 * 팔레트가 amber-500과 orange-500을 둘 다 gold(#e0a83e)로 재정의하기 때문이다.
 * 옅은 앰버(300)와 진한 앰버(500)로 간격을 벌려 네 단계가 서로 구분되게 한다.
 */
const POPULATION_LEVEL_COLOR: Record<string, { bar: string; track: string }> = {
  여유: { bar: "bg-emerald-500", track: "bg-emerald-50 dark:bg-emerald-950/30" },
  보통: { bar: "bg-amber-300", track: "bg-amber-50 dark:bg-amber-950/30" },
  "약간 붐빔": { bar: "bg-amber-500", track: "bg-amber-100 dark:bg-amber-950/30" },
  붐빔: { bar: "bg-red-500", track: "bg-red-50 dark:bg-red-950/30" },
};

/** app/concentration_policy.py의 ConcentrationLevel 영문 코드 → 팔레트 색상. */
const CONCENTRATION_LEVEL_COLOR: Record<string, { bar: string; track: string }> = {
  quiet: { bar: "bg-emerald-500", track: "bg-emerald-50 dark:bg-emerald-950/30" },
  normal: { bar: "bg-amber-300", track: "bg-amber-50 dark:bg-amber-950/30" },
  slightly_crowded: { bar: "bg-amber-500", track: "bg-amber-100 dark:bg-amber-950/30" },
  crowded: { bar: "bg-red-500", track: "bg-red-50 dark:bg-red-950/30" },
};

const UNKNOWN_LEVEL_COLOR = { bar: "bg-gray-400", track: "bg-gray-50 dark:bg-gray-800/60" };

/**
 * 안내 아이콘을 누르면(또는 커서를 올리면) 짧은 설명이 뜨는 작은 툴팁. 전체
 * 폭 캡션 대신 아이콘 하나로 둬서, 평소엔 자리를 차지하지 않는다.
 *
 * `text`는 항상 뜨고, `extraText`는 있을 때만(예: 막대 높이·색이 실제로 어긋난
 * 경우) 그 아래 이어 붙는다 — 이 그래프를 읽을 때 늘 알아둘 것과, 이번에만
 * 해당하는 주의사항을 한 툴팁 안에서 문단으로 나눈다.
 */
function InfoTooltipIcon({
  label,
  text,
  extraText,
}: {
  label: string;
  text: string;
  extraText?: string;
}) {
  const [open, setOpen] = useState(false);
  return (
    <span className="relative inline-flex shrink-0">
      <button
        type="button"
        aria-label={label}
        onMouseEnter={() => setOpen(true)}
        onMouseLeave={() => setOpen(false)}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onClick={() => setOpen((value) => !value)}
        className="flex h-4 w-4 items-center justify-center rounded-full text-[11px] font-bold leading-none text-muted hover:text-label focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand"
      >
        ⓘ
      </button>
      {open && (
        <div
          role="tooltip"
          // 이 아이콘은 항상 카드 왼쪽 제목 옆에 있다 — right-0으로 두면 카드
          // 폭이 좁을 때 툴팁이 왼쪽 밖으로 삐져나간다(실측으로 확인).
          className="absolute left-0 top-full z-10 mt-1 w-60 rounded-lg border border-border bg-surface px-2.5 py-2 text-[11px] text-muted shadow-card"
        >
          <p>{text}</p>
          {extraText && <p className="mt-1.5">{extraText}</p>}
        </div>
      )}
    </span>
  );
}

const CONGESTION_HEIGHT: Record<string, number> = {
  여유: 25,
  보통: 45,
  "약간 붐빔": 70,
  붐빔: 92,
};

/** 인구 혼잡도 게이지의 4단계 순서. 마커 위치도 이 순서 기준 인덱스로 계산한다. */
const POPULATION_GAUGE_LEVELS: Array<{ label: string; color: string }> = [
  { label: "여유", color: "bg-emerald-500" },
  { label: "보통", color: "bg-amber-300" },
  { label: "약간 붐빔", color: "bg-amber-500" },
  { label: "붐빔", color: "bg-red-500" },
];

/** 도로소통 게이지의 3단계 순서(ROAD_TRAFFIC_IDX 원문 그대로). */
const TRAFFIC_GAUGE_LEVELS: Array<{ label: string; color: string }> = [
  { label: "원활", color: "bg-emerald-500" },
  { label: "서행", color: "bg-amber-500" },
  { label: "정체", color: "bg-red-500" },
];

function hourLabel(value: string) {
  const match = value.match(/(\d{2}):(\d{2})/);
  return match ? `${Number(match[1])}시` : value;
}

function dateLabel(value: string) {
  const match = value.match(/(\d{4})-(\d{2})-(\d{2})/);
  return match ? `${Number(match[2])}/${Number(match[3])}` : value;
}

/*
 * 단계 칩 색. 토큰 팔레트 안에서 "옅은 초록 → 옅은 앰버 → 진한 앰버 → 옅은 레드"로
 * 올라가게 짰다 — 막대 색(emerald→amber→orange→red)과 같은 계열이면서, 배경만
 * 옅게 깔던 기존 muted 캡션보다 글자 대비가 확실히 높다.
 */
const LEVEL_CHIP_CALM = "bg-emerald-50 text-emerald-700";
const LEVEL_CHIP_SOFT = "bg-amber-50 text-amber-700";
const LEVEL_CHIP_WARM = "bg-amber-100 text-amber-900";
const LEVEL_CHIP_BUSY = "bg-rust-tint text-rust";
const LEVEL_CHIP_NEUTRAL = "bg-chip text-label";

/**
 * 인구 혼잡도 4단계와 상권 활동 4단계를 한 표에서 본다 — 두 척도가 쓰는 낱말은
 * 다르지만(붐빔 vs 분주한) 읽는 방향은 같아서 같은 색 사다리를 태운다.
 * 서울시 원문은 "바쁜 시간대"처럼 접미사가 붙어 오기도 한다.
 */
const LEVEL_CHIP_STYLE: Record<string, string> = {
  여유: LEVEL_CHIP_CALM,
  보통: LEVEL_CHIP_SOFT,
  "약간 붐빔": LEVEL_CHIP_WARM,
  붐빔: LEVEL_CHIP_BUSY,
  한산한: LEVEL_CHIP_CALM,
  바쁜: LEVEL_CHIP_WARM,
  분주한: LEVEL_CHIP_BUSY,
  원활: LEVEL_CHIP_CALM,
  서행: LEVEL_CHIP_WARM,
  정체: LEVEL_CHIP_BUSY,
};

/** 혼잡도·상권 단계를 눈에 띄는 칩으로 보여준다. 모르는 단계는 중립색으로 둔다. */
export function CongestionLevelChip({
  level,
  prefix,
  size = "sm",
  className = "",
}: {
  level: string | null | undefined;
  prefix?: string;
  size?: "sm" | "md";
  className?: string;
}) {
  if (!level) return null;
  // "바쁜 시간대"·"보통 시간대"처럼 접미사가 붙어도 같은 단계로 읽는다.
  const normalized = level.replace(/\s*시간대$/, "");
  const style = LEVEL_CHIP_STYLE[normalized] ?? LEVEL_CHIP_NEUTRAL;
  const sizeClass = size === "md" ? "px-2.5 py-1 text-sm" : "px-2 py-0.5 text-[11px]";
  return (
    <span
      className={`inline-flex shrink-0 items-center rounded-full font-semibold ${sizeClass} ${style} ${className}`}
    >
      {prefix ? `${prefix} ${level}` : level}
    </span>
  );
}

/** 현재 단계가 주어진 순서형 척도(예: 여유~붐빔, 원활~정체) 중 어디인지 보여주는 가로 게이지. */
export function CongestionLevelGauge({
  level,
  levels = POPULATION_GAUGE_LEVELS,
  ariaLabelPrefix = "현재 인구 혼잡도",
}: {
  level: string | null | undefined;
  levels?: Array<{ label: string; color: string }>;
  ariaLabelPrefix?: string;
}) {
  if (!level) return null;
  const activeIndex = levels.findIndex((entry) => entry.label === level);

  return (
    <div className="mt-2" aria-label={`${ariaLabelPrefix} ${level}`}>
      {activeIndex >= 0 && (
        <div
          className="flex text-rust transition-transform"
          style={{
            transform: `translateX(calc(${activeIndex} * 100%))`,
            width: `${100 / levels.length}%`,
          }}
        >
          <span className="mx-auto text-xs" aria-hidden="true">
            ▼
          </span>
        </div>
      )}
      <div className="flex overflow-hidden rounded-full">
        {levels.map((entry) => (
          <div key={entry.label} className={`h-2 flex-1 ${entry.color}`} />
        ))}
      </div>
      <div className="mt-1 flex text-[10px] text-muted">
        {levels.map((entry) => (
          <span
            key={entry.label}
            className={`flex-1 text-center ${
              entry.label === level ? "font-semibold text-ink" : ""
            }`}
          >
            {entry.label}
          </span>
        ))}
      </div>
    </div>
  );
}

/** 도로소통 단계·평균속도·안내문구를 카드에 시각적으로 보여준다. */
/**
 * 도로 위 돌발상황 4분류(사고/고장 · 공사/집회 · 기상/화재 · 기타) 진행 건수 카드.
 * 값이 있는(1건 이상) 분류만 rust로 강조한다 — 전부 0건이면 눈에 띌 이유가 없고,
 * 서울시 지도 화면도 평상시엔 네 칸이 전부 조용한 회색이다.
 */
function RoadIncidentCountGrid({ counts }: { counts: RoadIncidentCategoryCount[] }) {
  if (counts.length === 0) return null;
  return (
    <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
      {counts.map((item) => (
        /* 바탕 없이 테두리로만 구분한다(2026-09-16) — 같은 카드의 실시간 인구
           칸(SeoulRealtimeSummarySection)과 같은 규칙이다. 값이 있는 분류는
           숫자를 rust로 물들여 알리므로, 칸까지 채우면 그 강조가 묻힌다. */
        <div key={item.label} className="rounded-xl border border-border px-3 py-2.5">
          <p className="truncate text-[11px] font-medium text-muted">{item.label}</p>
          <p
            className={`mt-1 text-lg font-bold leading-tight ${
              item.count > 0 ? "text-rust" : "text-ink"
            }`}
          >
            {item.count}
            <span className="ml-0.5 text-xs font-semibold">건</span>
          </p>
        </div>
      ))}
    </div>
  );
}

export function RoadTrafficStatusSection({ card }: { card: InfoPlaceCardData }) {
  if (card.question_type !== "realtime_traffic") return null;
  const level = card.answer_fields["도로소통 단계"];
  if (!level) return null;
  const speed = card.answer_fields["평균 주행속도"];
  const message = card.answer_fields["안내"];
  const incidentCounts = card.road_incident_counts ?? [];

  return (
    <section className="border-t border-border px-4 py-3">
      <div className="flex items-baseline justify-between gap-2">
        <h3 className="text-sm font-bold text-ink">도로소통 현황</h3>
        <div className="flex shrink-0 items-center gap-1.5">
          {speed && <span className="text-xs text-muted">평균 {speed}</span>}
          <CongestionLevelChip level={level} />
        </div>
      </div>
      <CongestionLevelGauge
        level={level}
        levels={TRAFFIC_GAUGE_LEVELS}
        ariaLabelPrefix="현재 도로소통 단계"
      />
      {message && <p className="mt-2 text-xs text-muted">{message}</p>}
      <RoadIncidentCountGrid counts={incidentCounts} />
      {incidentCounts.length > 0 && (
        <p className="mt-2 text-[11px] text-muted">
          진행 중인 돌발상황 · 사고/고장·공사/집회·기상/화재·기타로 분류
        </p>
      )}
    </section>
  );
}

export function ConcentrationForecastBars({ card }: { card: InfoPlaceCardData }) {
  const forecasts = card.concentration_forecasts ?? [];
  if (forecasts.length === 0) return null;
  return (
    <section className="border-t border-border px-4 py-3">
      <div className="flex items-baseline justify-between gap-2">
        <h3 className="text-sm font-bold text-ink">관광지 혼잡도 예측</h3>
        <span className="text-xs text-muted">방문 예정일 포함 {forecasts.length}일</span>
      </div>
      <div className="mt-3 flex h-28 items-end gap-1.5" aria-label="관광지 혼잡도 7일 예측">
        {forecasts.map((forecast) => {
          const height = Math.max(14, Math.min(100, forecast.concentration_rate));
          const color =
            CONCENTRATION_LEVEL_COLOR[forecast.concentration_level] ?? UNKNOWN_LEVEL_COLOR;
          return (
            <div
              key={forecast.forecast_date}
              className="flex min-w-0 flex-1 flex-col items-center gap-1"
            >
              <div className={`flex h-16 w-full items-end rounded-t px-0.5 ${color.track}`}>
                <div
                  className={`w-full rounded-t ${color.bar}`}
                  style={{ height: `${height}%` }}
                  title={`${dateLabel(forecast.forecast_date)} ${forecast.concentration_label}`}
                />
              </div>
              <span className="text-[10px] font-medium text-muted">
                {dateLabel(forecast.forecast_date)}
              </span>
              <span className="truncate text-[10px] text-muted">
                {forecast.concentration_label}
              </span>
            </div>
          );
        })}
      </div>
      <p className="mt-2 text-xs text-muted">관광지 집중률 예측 · 한국관광공사 데이터 기반</p>
    </section>
  );
}

/**
 * "현재" 막대를 예측과 갈라 보이는 점선 구분선. 막대 행과 라벨 행에 같이 걸어야
 * 두 행의 칸 폭이 어긋나지 않는다(border-box라 폭은 그대로 유지된다).
 */
const CURRENT_COLUMN_CLASS = "border-r-2 border-dashed border-border pr-1.5";

/** 막대에 커서를 올렸을 때 뜨는 말풍선. 시각·혼잡도·인구수를 그대로 보여준다. */
function PopulationBarTooltip({
  column,
  index,
  total,
  barColor,
}: {
  column: PopulationBarColumn;
  index: number;
  total: number;
  barColor: string;
}) {
  const range = formatPopulationRange(column.populationMin, column.populationMax);
  // 양 끝 막대는 가운데 정렬하면 카드 밖으로 삐져나간다 — 끝에 붙여 세운다.
  const edge =
    index === 0
      ? "left-0"
      : index === total - 1
        ? "right-0"
        : "left-1/2 -translate-x-1/2";
  return (
    <div
      role="tooltip"
      className={`absolute bottom-full z-10 mb-1.5 w-max max-w-[11rem] rounded-lg border border-border bg-surface px-2.5 py-1.5 shadow-card ${edge}`}
    >
      <p className="text-[11px] font-bold text-ink">{column.label}</p>
      {column.level && (
        <p className="mt-0.5 flex items-center gap-1 text-[11px] text-muted">
          혼잡도
          <span className={`h-1.5 w-1.5 rounded-full ${barColor}`} aria-hidden="true" />
          <span className="font-semibold text-label">{column.level}</span>
        </p>
      )}
      {range && (
        <p className="mt-0.5 text-[11px] text-muted">
          인구수 <span className="font-semibold text-label">{range}</span>
        </p>
      )}
    </div>
  );
}

interface PopulationBarColumn {
  key: string;
  label: string;
  /** 축 아래에 시각을 적을지. 잘리지 않게 솎아낸다 — 라벨 칸 자체는 그대로 둔다. */
  labelShown: boolean;
  level: string;
  populationMin: number | null;
  populationMax: number | null;
  isCurrent: boolean;
}

/**
 * 바로 옆 막대끼리 인구 수 순서와 혼잡도 단계 순서가 실제로 어긋나는지 본다.
 *
 * 서울시는 "현재" 단계를 인구 수(과거 28일 대비 백분율) → 밀집도(면적당 인구)
 * 보정 → 표준점수·사분위·대중교통 승하차 실측 보정까지 5단계를 거쳐 매기지만,
 * "예측" 단계는 이 중 인구 수 백분율만으로 매길 수밖에 없다 — 밀집도·승하차
 * 보정에 쓰는 실측값이 미래 시점엔 존재하지 않기 때문이다(서울시 매뉴얼 V8.5,
 * 3장 5·6절). 그래서 드물게 인구가 더 적은 슬롯이 더 붐비는 색으로 나온다.
 *
 * 항상 안내를 띄우면 실제로는 안 어긋난 경우에도 뜬다 — 2026-09-07 121곳
 * 실측에서 세로축이 서는 곳(99%) 중 바로 옆 막대끼리 어긋난 곳은 6%뿐이었다.
 * 그래서 이 함수가 참일 때만 안내 아이콘을 보여준다.
 */
function hasAdjacentLevelPopulationMismatch(
  columns: PopulationBarColumn[],
  midpoints: Array<number | null>,
) {
  for (let index = 0; index < columns.length - 1; index++) {
    const levelRankA = CONGESTION_HEIGHT[columns[index].level];
    const levelRankB = CONGESTION_HEIGHT[columns[index + 1].level];
    const populationA = midpoints[index];
    const populationB = midpoints[index + 1];
    if (levelRankA == null || levelRankB == null || populationA == null || populationB == null) {
      continue;
    }
    if ((levelRankA - levelRankB) * (populationA - populationB) < 0) return true;
  }
  return false;
}

export function PopulationForecastBars({ card }: { card: InfoPlaceCardData }) {
  // 커서를 올린(또는 탭·포커스한) 막대. 터치 기기에는 hover가 없어 클릭으로도 연다.
  const [activeIndex, setActiveIndex] = useState<number | null>(null);
  const forecasts = card.population_forecasts ?? [];
  if (forecasts.length === 0) return null;

  const summary = card.seoul_realtime_summary;
  const columns: PopulationBarColumn[] = [
    ...(card.population_current_level
      ? [
          {
            key: "current",
            label: "현재",
            level: card.population_current_level,
            populationMin: summary?.population_min ?? null,
            populationMax: summary?.population_max ?? null,
            labelShown: true,
            isCurrent: true,
          },
        ]
      : []),
    ...forecasts.map((forecast, index) => ({
      key: forecast.forecast_at,
      label: hourLabel(forecast.forecast_at),
      // 12슬롯을 다 적으면 라벨끼리 너무 붙어 보인다. 세 칸에 하나만 적어
      // 눈금처럼 쓰고, 정확한 시각은 막대 말풍선이 알려준다. "현재" 칸이 앞에
      // 하나 더 있다는 걸 셈에 넣어야 한다 — 안 넣으면 예측 배열의 0번째가
      // 전체 열에서는 1번째라, "현재"와 첫 라벨이 한 칸만 떨어져 붙어 보인다
      // (2026-09-07 모바일 실측에서 라벨 겹침으로 드러남).
      labelShown: (index + (card.population_current_level ? 1 : 0)) % 3 === 0,
      level: forecast.congestion_level ?? "",
      populationMin: forecast.population_min ?? null,
      populationMax: forecast.population_max ?? null,
      isCurrent: false,
    })),
  ];

  // 막대 하나라도 인구 수가 비면 세로축을 세우지 않는다. 일부만 실제 수치이고
  // 나머지는 단계로 어림한 높이면, 눈금이 붙은 순간 전부 실측치처럼 읽힌다.
  const midpoints = columns.map((column) =>
    populationMidpoint(column.populationMin, column.populationMax),
  );
  const hasFullScale = midpoints.every((value) => value != null && value > 0);
  const ceiling = hasFullScale ? axisCeiling(Math.max(...(midpoints as number[]))) : 0;
  const ticks =
    ceiling > 0
      ? Array.from({ length: AXIS_TICK_COUNT + 1 }, (_, index) => ({
          ratio: index / AXIS_TICK_COUNT,
          value: (ceiling / AXIS_TICK_COUNT) * index,
        }))
      : [];
  // 세로축이 서야(모든 슬롯에 인구 수가 있어야) 공정하게 비교할 수 있고, 그
  // 중에서도 바로 옆 막대끼리 실제로 순서가 어긋날 때만 그 설명을 덧붙인다.
  const hasLevelPopulationMismatch =
    ceiling > 0 && hasAdjacentLevelPopulationMismatch(columns, midpoints);
  // 그래프 설명 아이콘은 항상 뜬다 — 무엇을 보여주는 데이터인지·출처·기준
  // 시각은 매번 알아둘 만하다. 어긋남 설명만 실제로 어긋났을 때 덧붙는다.
  const populationInfoText = `향후 12시간 인구 혼잡도 예측이에요 (${
    card.population_observed_at ? `${card.population_observed_at} 기준, ` : ""
  }통신 데이터 기반 · 서울시 실시간 도시데이터).`;

  return (
    <section className="border-t border-border px-4 py-3">
      <div className="flex items-baseline justify-between gap-2">
        <h3 className="flex items-center gap-1 text-sm font-bold text-ink">
          인구 혼잡도 예측
          <InfoTooltipIcon
            label="인구 혼잡도 예측 안내"
            text={populationInfoText}
            extraText={
              hasLevelPopulationMismatch
                ? "'현재'와 '예측' 혼잡도는 계산 기준이 달라서, 막대 높이(인구 수)와 색(혼잡도 단계)이 안 맞아 보일 때가 있어요."
                : undefined
            }
          />
        </h3>
        <CongestionLevelChip level={card.population_current_level} prefix="현재" />
      </div>
      <CongestionLevelGauge level={card.population_current_level} />
      <div
        className="mt-3 flex gap-1"
        aria-label="현재부터 향후 12시간 인구 혼잡도 예측"
      >
        {ticks.length > 0 && (
          <div className="relative h-16 w-12 shrink-0" aria-hidden="true">
            {ticks.map((tick) => (
              <span
                key={tick.ratio}
                className="absolute right-1 translate-y-1/2 text-[10px] leading-none text-muted"
                style={{ bottom: `${tick.ratio * 100}%` }}
              >
                {axisLabel(tick.value)}
              </span>
            ))}
          </div>
        )}
        <div
          // 막대 사이 간격을 여기 한 곳(CSS 변수)에만 두고 막대 행·라벨 위치
          // 계산이 같은 값을 참조하게 한다 — 웹은 기존 그대로(6px), 좁은 화면은
          // 더 좁혀(3px) 막대 자체가 간격 대비 더 두꺼워 보이게 한다(2026-09-07:
          // 모서리를 각지게 하는 대신 간격을 줄이는 쪽으로 다시 잡았다).
          className="relative min-w-0 flex-1 [--pf-bar-gap:3px] sm:[--pf-bar-gap:6px]"
        >
          {ticks.length > 0 && (
            <div className="pointer-events-none absolute inset-x-0 top-0 h-16" aria-hidden="true">
              {ticks.map((tick) => (
                <div
                  key={tick.ratio}
                  className="absolute inset-x-0 border-t border-border/60"
                  style={{ bottom: `${tick.ratio * 100}%` }}
                />
              ))}
            </div>
          )}
          {/* 격자선 레이어가 absolute라 static 형제보다 위에 그려진다 — 막대 행도
              positioned로 만들어 격자선이 막대 뒤로 가게 한다. */}
          <div className="relative flex h-16 items-end gap-[var(--pf-bar-gap)]">
            {columns.map((column, index) => {
              const color = POPULATION_LEVEL_COLOR[column.level] ?? UNKNOWN_LEVEL_COLOR;
              const midpoint = midpoints[index];
              const height =
                ceiling > 0 && midpoint != null
                  ? Math.max(2, Math.min(100, (midpoint / ceiling) * 100))
                  : (CONGESTION_HEIGHT[column.level] ?? 18);
              const range = formatPopulationRange(column.populationMin, column.populationMax);
              return (
                <button
                  type="button"
                  key={column.key}
                  // 커서·포커스로 열고, hover가 없는 터치 기기에서는 탭으로 연다.
                  onMouseEnter={() => setActiveIndex(index)}
                  onMouseLeave={() => setActiveIndex(null)}
                  onFocus={() => setActiveIndex(index)}
                  onBlur={() => setActiveIndex(null)}
                  onClick={() => setActiveIndex(activeIndex === index ? null : index)}
                  // 말풍선은 커서를 올려야 보이니, 읽어주는 화면에는 같은 내용을 붙인다.
                  aria-label={[column.label, column.level, range].filter(Boolean).join(", ")}
                  className={`relative flex h-full min-w-0 flex-1 cursor-default flex-col items-center rounded-t focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand ${
                    column.isCurrent ? CURRENT_COLUMN_CLASS : ""
                  }`}
                >
                  {activeIndex === index && (
                    <PopulationBarTooltip
                      column={column}
                      index={index}
                      total={columns.length}
                      barColor={color.bar}
                    />
                  )}
                  {/* 눈금이 서면 칸 배경(track)을 지운다 — 막대 위로 옅은 색 블록이
                      남으면 격자선과 겹쳐 "저기까지 찼다"고 읽힌다. */}
                  <div
                    className={`flex h-full w-full items-end rounded-t px-0.5 ${
                      ceiling > 0 ? "" : color.track
                    }`}
                  >
                    <div
                      data-population-bar={column.key}
                      // "현재" 강조는 칸이 아니라 막대에 건다. 칸에 걸면 축이 선
                      // 뒤에는 테두리가 최고 눈금까지 올라가 실제보다 많아 보인다.
                      className={`w-full rounded-t ${color.bar} ${
                        column.isCurrent ? "ring-2 ring-inset ring-ink" : ""
                      }`}
                      style={{ height: `${height}%` }}
                    />
                  </div>
                </button>
              );
            })}
          </div>
          {/*
           * 라벨을 막대와 같은 flex-1 칸에 가두지 않는다 — 그 칸 폭은 화면 폭에
           * 비례해 줄어들어서, 모바일(카드 폭 ~340px)에서는 칸 하나가 20px 안팎이라
           * "현재"(2글자)조차 "현..."으로 잘렸다(2026-09-07 모바일 실측 재현).
           * 대신 막대 중심 좌표 위에 절대 위치로 얹어 글자가 자기 칸 폭과
           * 무관해지게 한다 — 3칸마다 하나만 보여주는 솎아내기(labelShown)는
           * 그대로라 라벨 사이 간격은 어느 화면 폭에서도 충분하다. 막대 행 간격이
           * 화면 폭에 따라 3px/6px로 바뀌므로(위 --pf-bar-gap), 하드코딩한 px 대신
           * 그 변수를 그대로 참조해 라벨 중심이 항상 실제 막대 중심과 맞게 한다.
           */}
          <div className="relative mt-1 h-3.5">
            {columns.map((column, index) =>
              column.labelShown ? (
                <span
                  key={column.key}
                  className={`absolute top-0 -translate-x-1/2 whitespace-nowrap text-[10px] leading-none ${
                    column.isCurrent ? "font-semibold text-ink" : "text-muted"
                  }`}
                  style={{
                    left: `calc((100% - (${columns.length - 1}) * var(--pf-bar-gap)) / ${
                      columns.length
                    } * ${index + 0.5} + ${index} * var(--pf-bar-gap))`,
                  }}
                >
                  {column.label}
                </span>
              ) : null,
            )}
          </div>
        </div>
      </div>
      {/* 이 그래프가 뭘 보여주는지·출처·기준 시각은 이제 제목 옆 안내 아이콘
          하나로 옮겼다 — 같은 내용을 캡션으로 또 반복하지 않는다. */}
      {card.population_peak_forecast_summary && (
        <p className="mt-2 text-xs font-semibold text-label">
          {card.population_peak_forecast_summary}
        </p>
      )}
    </section>
  );
}
