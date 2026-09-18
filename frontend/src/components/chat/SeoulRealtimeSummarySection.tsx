/*
 * 역할: 서울시 실시간 도시데이터의 인구·상권 요약을 카드 공통 블록으로 보여준다.
 * 입력: InfoPlaceCard.seoul_realtime_summary(+ 현재 단계·기준 시각은 population_* 필드).
 * 출력: "실시간 인구"와 "실시간 인기 상권" 두 구획. 값이 없는 구획은 통째로 감춘다.
 * 호출 시점: 실시간 혼잡도(concentration)·실시간 상권(realtime_commercial) 카드에서만 —
 *   이 두 유형만 서울시 citydata를 이미 호출하므로 추가 호출 없이 채울 수 있다.
 *   단, "실시간 인기 상권" 구획은 realtime_commercial 카드에만 싣는다 — 같은
 *   응답에 상권 값이 실려 와도 concentration(혼잡도) 질문에는 보여주지 않는다.
 */

import type { InfoPlaceCard as InfoPlaceCardData } from "../../types";
import { formatPopulationRangeCompact } from "../../utils/seoulRealtimeDisplay";
import { CongestionLevelChip } from "./CongestionForecastBars";

/** 이 블록을 싣는 질문 유형. 나머지 INFO는 서울시 데이터를 조회하지 않는다. */
const SUPPORTED_QUESTION_TYPES = new Set(["concentration", "realtime_commercial"]);

/**
 * "대분류 · 소분류" 원문(예: "음식·음료 · 한식")에서 소분류만 남긴다. 상권 활동
 * 단계 칩과 Top 3를 한 줄에 같이 넣어야 해서, 뜻이 크게 안 달라지는 대분류 접두어를
 * 뺀다 — 서울시 앱 원본도 "한식"처럼 소분류만 보여준다. 구분자가 없으면 원문 그대로.
 */
function shortCategoryLabel(label: string) {
  const idx = label.lastIndexOf(" · ");
  return idx === -1 ? label : label.slice(idx + 3);
}

/*
 * 값 아래 보조 정보는 두 종류다 — 혼잡도·상권 "단계"는 색 칩으로(옅은 회색 글씨로
 * 두면 정작 제일 읽히길 바라는 값이 가장 안 보인다), "329건"·"29.0%" 같은 수치는
 * 진한 글씨로 둔다.
 */
function SummaryTile({
  label,
  value,
  caption,
  levelCaption,
}: {
  label: string;
  value: string;
  caption?: string | null;
  levelCaption?: string | null;
}) {
  return (
    /*
      바탕을 깔지 않고 **테두리로만** 구분한다(2026-09-16). 전에는 bg-chip으로
      채웠는데, 안에 든 단계 칩(보통·여유)도 채워진 모양이라 채움이 두 겹으로
      쌓여 정작 눈에 걸려야 할 단계 칩이 묻혔다. 바탕을 걷으면 이 구획에서
      채워진 것은 단계 칩뿐이다.

      테두리는 옅게(70%) 두지 않고 그대로 쓴다 — 이제 칸을 규정하는 것이
      테두리뿐이라서다. 같은 카드의 이웃 블록들도 border-border를 쓴다
      (PlaceInfoCard).
    */
    <div className="min-w-0 flex-1 rounded-xl border border-border px-3 py-2.5">
      <p className="text-[11px] font-medium text-muted">{label}</p>
      <p className="mt-1 truncate text-[15px] font-bold leading-tight text-ink" title={value}>
        {value}
      </p>
      {levelCaption ? (
        <CongestionLevelChip level={levelCaption} className="mt-1.5" />
      ) : (
        caption && <p className="mt-1 truncate text-[11px] font-semibold text-label">{caption}</p>
      )}
    </div>
  );
}

export function SeoulRealtimeSummarySection({ card }: { card: InfoPlaceCardData }) {
  if (!SUPPORTED_QUESTION_TYPES.has(card.question_type)) return null;
  const summary = card.seoul_realtime_summary;
  if (!summary) return null;

  const populationRange = formatPopulationRangeCompact(
    summary.population_min,
    summary.population_max,
  );
  const topCategories = summary.top_payment_categories ?? [];
  // 서울시 원문(AREA_CMRCL_LVL)은 "한산한"처럼 접미사 없이 오는 경우와 이미
  // "한산한 시간대"로 오는 경우가 둘 다 있어(관광공사 앱 표기 기준) 중복으로
  // 안 붙게 방어한다.
  const commercialLevelLabel = summary.commercial_level
    ? summary.commercial_level.endsWith("시간대")
      ? summary.commercial_level
      : `${summary.commercial_level} 시간대`
    : null;

  const hasPopulation = Boolean(
    populationRange || summary.peak_forecast_hour_label || summary.top_age_label,
  );
  // 실시간 혼잡도(concentration) 질문에는 상권 값이 같은 citydata 응답에 실려
  // 와도 보여주지 않는다 — 물어본 것은 인구 혼잡도이지 상권이 아니다. 반대로
  // 실시간 상권 질문의 카드에는 인구 값을 계속 함께 싣는다(그쪽은 요청 범위 밖).
  const hasCommercial =
    card.question_type === "realtime_commercial" &&
    Boolean(commercialLevelLabel || topCategories.length);
  if (!hasPopulation && !hasCommercial) return null;

  return (
    <>
      {hasPopulation && (
        <section className="border-t border-border px-4 py-3">
          <div className="flex items-baseline justify-between gap-2">
            <h3 className="text-sm font-bold text-ink">실시간 인구</h3>
            {card.population_observed_at && (
              <span className="text-[10px] text-muted">{card.population_observed_at} 기준</span>
            )}
          </div>
          <div className="mt-2 flex gap-2">
            {populationRange && (
              <SummaryTile
                label="현재 인구"
                value={populationRange}
                levelCaption={card.population_current_level}
              />
            )}
            {summary.peak_forecast_hour_label && (
              <SummaryTile
                // 서울시 앱의 "오늘의 인기 시간대"와 다르다 — 원본이 과거 추이를
                // 주지 않아 앞으로의 예측만 말할 수 있다.
                label="가장 붐빌 시간대"
                value={summary.peak_forecast_hour_label}
                levelCaption={summary.peak_forecast_level}
              />
            )}
            {summary.top_age_label && (
              <SummaryTile
                label="가장 많은 연령대"
                value={summary.top_age_label}
                caption={
                  summary.top_age_rate != null ? `${summary.top_age_rate.toFixed(1)}%` : null
                }
              />
            )}
          </div>
        </section>
      )}
      {hasCommercial && (
        <section className="border-t border-border px-4 py-3">
          <div className="flex items-baseline justify-between gap-2">
            <h3 className="text-sm font-bold text-ink">실시간 인기 상권</h3>
            {summary.commercial_observed_at && (
              <span className="text-[10px] text-muted">{summary.commercial_observed_at} 기준</span>
            )}
          </div>
          {(commercialLevelLabel || topCategories.length > 0) && (
            // 상권 활동 단계와 Top 3를 두 줄로 나누지 않고 한 줄에 같이 둔다 —
            // 칩은 폭 고정(shrink-0), Top 3는 남는 폭을 균등히 나눠(flex-1)
            // 길면 그 칸 안에서만 말줄임하고 전체 업종명은 title 툴팁으로 남긴다.
            <div className="mt-2 flex items-center gap-2 overflow-hidden">
              {commercialLevelLabel && (
                <CongestionLevelChip level={commercialLevelLabel} className="shrink-0" />
              )}
              {topCategories.length > 0 && (
                <ol className="flex min-w-0 flex-1 items-center gap-x-2 text-[11px]">
                  {topCategories.map((category, index) => (
                    <li
                      key={category.label}
                      className={`min-w-0 flex-1 truncate ${
                        index > 0 ? "border-l border-border pl-2" : ""
                      }`}
                      title={category.label}
                    >
                      <span className="font-bold text-ink">{index + 1}위</span>{" "}
                      <span className="text-muted">{shortCategoryLabel(category.label)}</span>
                    </li>
                  ))}
                </ol>
              )}
            </div>
          )}
        </section>
      )}
    </>
  );
}
