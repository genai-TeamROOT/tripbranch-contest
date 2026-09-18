/*
 * 역할: Ops에서 구별 TourAPI 대·중·소분류의 활성 장소 수와 예시를 계층으로 보여준다.
 * 입력: /api/dev/db-status의 overall/districts category_coverage.
 * 출력: 구 선택 탭, 대·중분류 아코디언, 소분류별 장소 수·예시 최대 2개.
 * 호출 시점: /dev-ops?tab=categories 화면.
 *
 * 원본 장소 전체를 프론트로 보내지 않는다. 서버가 집계한 트리만 받으므로, 장소가
 * 수천 건으로 늘어도 이 화면의 응답과 렌더링 규모는 분류 개수에 비례한다.
 */

import { useState } from "react";

import type {
  CategoryCoverage,
  CategoryLargeCoverage,
  CategoryMiddleCoverage,
  DbStatus,
  DistrictPlaceSummary,
  PlaceSummary,
} from "../../api/dev";

function districtKey(summary: DistrictPlaceSummary): string {
  return `${summary.area_code}-${summary.district_code}`;
}

function countLabel(value: number): string {
  return `${new Intl.NumberFormat("ko-KR").format(value)}개`;
}

function categoryName(label: string, code: string | null): string {
  return code ? `${label} · ${code}` : label;
}

function Bar({ count, total }: { count: number; total: number }) {
  const percent = total > 0 ? (count / total) * 100 : 0;
  return (
    <span
      aria-hidden="true"
      className="h-1.5 w-20 overflow-hidden rounded-full bg-gray-100 dark:bg-gray-800 sm:w-28"
    >
      <span
        className="block h-full rounded-full bg-blue-500"
        style={{ width: `${Math.max(percent, 2)}%` }}
      />
    </span>
  );
}

function SmallCategoryRow({
  small,
  total,
}: {
  small: CategoryMiddleCoverage["smalls"][number];
  total: number;
}) {
  return (
    <li className="rounded-lg border border-gray-100 bg-gray-50 px-3 py-2.5 dark:border-gray-800 dark:bg-gray-950/40">
      <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
        <div className="flex min-w-0 items-center gap-2">
          <span className="truncate text-sm font-medium text-gray-900 dark:text-gray-100">
            {categoryName(small.label, small.code)}
          </span>
          <Bar count={small.count} total={total} />
        </div>
        <span className="shrink-0 text-sm font-semibold tabular-nums text-gray-800 dark:text-gray-200">
          {countLabel(small.count)}
        </span>
      </div>
      {small.examples.length > 0 && (
        <p className="mt-1.5 text-xs text-gray-500 dark:text-gray-400">
          <span className="mr-1 font-medium text-gray-600 dark:text-gray-300">예시</span>
          {small.examples.join(" · ")}
        </p>
      )}
    </li>
  );
}

function MiddleCategory({
  middle,
  total,
}: {
  middle: CategoryMiddleCoverage;
  total: number;
}) {
  return (
    <details className="group rounded-xl border border-gray-200 bg-white dark:border-gray-800 dark:bg-gray-900">
      <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-3 py-3 hover:bg-gray-50 dark:hover:bg-gray-800/60">
        <span className="flex min-w-0 items-center gap-2">
          <span className="text-gray-400 transition-transform group-open:rotate-90" aria-hidden="true">
            ›
          </span>
          <span className="truncate text-sm font-semibold text-gray-800 dark:text-gray-200">
            {categoryName(middle.label, middle.code)}
          </span>
          <Bar count={middle.count} total={total} />
        </span>
        <span className="shrink-0 text-sm font-semibold tabular-nums text-gray-800 dark:text-gray-200">
          {countLabel(middle.count)}
        </span>
      </summary>
      <ul className="space-y-2 border-t border-gray-100 p-3 dark:border-gray-800">
        {middle.smalls.map((small) => (
          <SmallCategoryRow key={small.code ?? "unclassified"} small={small} total={total} />
        ))}
      </ul>
    </details>
  );
}

function LargeCategory({
  group,
  total,
  initiallyOpen,
}: {
  group: CategoryLargeCoverage;
  total: number;
  initiallyOpen: boolean;
}) {
  return (
    <details
      className="group rounded-xl border border-gray-200 bg-white shadow-sm dark:border-gray-800 dark:bg-gray-900"
      open={initiallyOpen}
    >
      <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-4 py-3.5 hover:bg-gray-50 dark:hover:bg-gray-800/60">
        <span className="flex min-w-0 items-center gap-2">
          <span className="text-lg text-gray-400 transition-transform group-open:rotate-90" aria-hidden="true">
            ›
          </span>
          <span className="truncate font-semibold text-gray-950 dark:text-gray-50">
            {categoryName(group.label, group.code)}
          </span>
          <Bar count={group.count} total={total} />
        </span>
        <span className="shrink-0 font-bold tabular-nums text-gray-900 dark:text-gray-100">
          {countLabel(group.count)}
        </span>
      </summary>
      <div className="space-y-2 border-t border-gray-100 p-3 dark:border-gray-800">
        {group.middles.map((middle) => (
          <MiddleCategory key={middle.code ?? "unclassified"} middle={middle} total={total} />
        ))}
      </div>
    </details>
  );
}

function CoverageSummary({ coverage }: { coverage: CategoryCoverage }) {
  const metrics = [
    ["활성 장소", coverage.active_place_count],
    ["대분류", coverage.large_category_count],
    ["중분류", coverage.middle_category_count],
    ["소분류", coverage.small_category_count],
  ] as const;
  return (
    <dl className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-4">
      {metrics.map(([label, value]) => (
        <div key={label} className="rounded-lg bg-gray-100 px-3 py-2.5 dark:bg-gray-800">
          <dt className="text-[11px] text-gray-500 dark:text-gray-400">{label}</dt>
          <dd className="mt-0.5 text-lg font-bold tabular-nums text-gray-950 dark:text-gray-50">
            {countLabel(value)}
          </dd>
        </div>
      ))}
    </dl>
  );
}

export function CategoryCoveragePanel({
  status,
  error,
  loading,
  onRefresh,
}: {
  status: DbStatus | null;
  error: string | null;
  loading: boolean;
  onRefresh: () => void;
}) {
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const districts = status?.districts ?? [];
  const selected = districts.find((district) => districtKey(district) === selectedKey) ?? null;
  const summary: PlaceSummary | null = selected ?? status?.overall ?? null;
  const coverage = summary?.category_coverage;

  return (
    <section className="rounded-lg border border-gray-200 bg-white p-4 dark:border-gray-800 dark:bg-gray-900">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-base font-bold text-gray-950 dark:text-gray-50">구별 카테고리 현황</h2>
          <p className="mt-0.5 text-xs text-gray-500 dark:text-gray-400">
            활성 장소 기준 · TourAPI 대분류 → 중분류 → 소분류 · 소분류별 예시 최대 2개
          </p>
        </div>
        <button
          type="button"
          onClick={onRefresh}
          disabled={loading}
          className="rounded-md border border-gray-300 px-2.5 py-1 text-xs disabled:opacity-50 dark:border-gray-700"
        >
          {loading ? "불러오는 중…" : "새로고침"}
        </button>
      </header>

      {error && (
        <p className="mt-3 rounded-md bg-red-50 p-3 text-xs text-red-900 dark:bg-red-950/40 dark:text-red-100">
          {error}
        </p>
      )}

      {status && (
        <div role="tablist" className="mt-4 flex flex-wrap gap-1 border-b border-gray-200 pb-2 dark:border-gray-800">
          <button
            type="button"
            role="tab"
            aria-selected={selected === null}
            onClick={() => setSelectedKey(null)}
            className={`rounded-md px-3 py-1.5 text-xs font-medium ${
              selected === null
                ? "bg-gray-900 text-white dark:bg-gray-100 dark:text-gray-900"
                : "text-gray-600 hover:bg-gray-100 dark:text-gray-300 dark:hover:bg-gray-800"
            }`}
          >
            전체
          </button>
          {districts.map((district) => {
            const key = districtKey(district);
            const active = selectedKey === key;
            return (
              <button
                key={key}
                type="button"
                role="tab"
                aria-selected={active}
                onClick={() => setSelectedKey(key)}
                className={`rounded-md px-3 py-1.5 text-xs font-medium ${
                  active
                    ? "bg-gray-900 text-white dark:bg-gray-100 dark:text-gray-900"
                    : "text-gray-600 hover:bg-gray-100 dark:text-gray-300 dark:hover:bg-gray-800"
                }`}
              >
                {district.district_name ?? `구 ${district.district_code}`}
              </button>
            );
          })}
        </div>
      )}

      {coverage ? (
        <>
          <CoverageSummary coverage={coverage} />
          <div className="mt-4 space-y-2">
            {coverage.groups.map((group, index) => (
              <LargeCategory
                key={group.code ?? "unclassified"}
                group={group}
                total={coverage.active_place_count}
                initiallyOpen={index === 0}
              />
            ))}
          </div>
        </>
      ) : status && !error ? (
        <p className="mt-4 rounded-md border border-dashed border-gray-300 p-4 text-sm text-gray-500 dark:border-gray-700">
          카테고리 집계를 아직 받지 못했어요. 백엔드를 최신 버전으로 다시 실행한 뒤 새로고침하세요.
        </p>
      ) : null}
    </section>
  );
}
