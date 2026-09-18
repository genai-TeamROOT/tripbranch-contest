/*
 * 역할: LLMOps 실행 Trace를 step 기준으로 집계해 보여준다.
 * 입력: /api/trace/stats 응답.
 * 출력: 전체 건수 요약, step별 건수/평균·최대 latency/에러 건수 표, 최근 에러 목록.
 * 호출 시점: DeveloperOpsPage가 렌더링될 때, 새로고침 버튼 클릭 시.
 *
 * 이 패널이 부르는 /api/trace/stats는 이 페이지의 다른 패널과 달리
 * APP_ENV=local이 아니어도 등록돼 있다(routes/trace.py는 무조건 include) —
 * FeedbackStatsPanel과 같은 이유다. step_stats는 reason_code_counts처럼
 * 고정된 값 집합이 아니라 실제 등장한 step만 담기므로, 값이 비어 있으면
 * "아직 등장하지 않은 step"이 아니라 "trace 자체가 없다"는 뜻이다.
 */

import type { TraceStatsResponse } from "../../types";

function formatLatency(value: number | null) {
  if (value === null) return "-";
  return `${Math.round(value).toLocaleString()}ms`;
}

function formatPercent(count: number, denominator: number) {
  if (denominator === 0) return "-";
  return `${((count / denominator) * 100).toFixed(1)}%`;
}

function formatRecordedAt(value: string) {
  try {
    return new Date(value).toLocaleString("ko-KR");
  } catch {
    return value;
  }
}

export function TracePanel({
  stats,
  error,
  loading,
  onRefresh,
}: {
  stats: TraceStatsResponse | null;
  error: string | null;
  loading: boolean;
  onRefresh: () => void;
}) {
  const totalErrors = stats
    ? stats.step_stats.reduce((sum, item) => sum + item.error_count, 0)
    : 0;

  return (
    <section className="rounded-lg border border-gray-200 bg-white p-4 dark:border-gray-800 dark:bg-gray-900">
      <header className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="text-base font-bold text-gray-950 dark:text-gray-50">
            Trace 통계
          </h2>
          <p className="mt-0.5 text-xs text-gray-500 dark:text-gray-400">
            실행 단계(step)별 집계 (TP-157) · 세션을 가리지 않고 전체를 본다
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

      {stats && (
        <>
          <dl className="mt-3 grid grid-cols-3 gap-2">
            <div className="rounded-md bg-gray-100 p-2.5 dark:bg-gray-800">
              <dt className="text-[11px] text-gray-500 dark:text-gray-400">전체 실행</dt>
              <dd className="text-lg font-bold tabular-nums">{stats.total}</dd>
            </div>
            <div className="rounded-md bg-gray-100 p-2.5 dark:bg-gray-800">
              <dt className="text-[11px] text-gray-500 dark:text-gray-400">step 종류</dt>
              <dd className="text-lg font-bold tabular-nums">{stats.step_stats.length}</dd>
            </div>
            <div className="rounded-md bg-gray-100 p-2.5 dark:bg-gray-800">
              <dt className="text-[11px] text-gray-500 dark:text-gray-400">에러</dt>
              <dd className="text-lg font-bold tabular-nums text-red-600 dark:text-red-400">
                {totalErrors}
              </dd>
            </div>
          </dl>

          {stats.total === 0 ? (
            <p className="mt-3 rounded-md border border-dashed border-gray-300 p-4 text-sm text-gray-500 dark:border-gray-700">
              아직 기록된 trace가 없습니다.
            </p>
          ) : (
            <div className="mt-4 grid gap-4 md:grid-cols-2">
              <div>
                <h3 className="text-xs font-semibold text-gray-700 dark:text-gray-300">
                  step별 집계
                </h3>
                <table className="mt-2 w-full text-left text-xs">
                  <thead>
                    <tr className="text-[11px] text-gray-500 dark:text-gray-400">
                      <th className="pb-1 pr-2 font-normal">step</th>
                      <th className="pb-1 pr-2 text-right font-normal">건수</th>
                      <th className="pb-1 pr-2 text-right font-normal">평균</th>
                      <th className="pb-1 pr-2 text-right font-normal">최대</th>
                      <th className="pb-1 text-right font-normal">에러</th>
                    </tr>
                  </thead>
                  <tbody>
                    {stats.step_stats.map((item) => (
                      <tr
                        key={item.step}
                        className="border-b border-gray-100 dark:border-gray-800/60"
                      >
                        <td className="py-1.5 pr-2 font-mono text-[11px]">{item.step}</td>
                        <td className="py-1.5 pr-2 text-right tabular-nums">{item.count}</td>
                        <td className="py-1.5 pr-2 text-right tabular-nums text-gray-500">
                          {formatLatency(item.avg_latency_ms)}
                        </td>
                        <td className="py-1.5 pr-2 text-right tabular-nums text-gray-500">
                          {formatLatency(item.max_latency_ms)}
                        </td>
                        <td className="py-1.5 text-right tabular-nums">
                          {item.error_count > 0 ? (
                            <span className="text-red-600 dark:text-red-400">
                              {item.error_count} ({formatPercent(item.error_count, item.count)})
                            </span>
                          ) : (
                            "0"
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              <div>
                <h3 className="text-xs font-semibold text-gray-700 dark:text-gray-300">
                  최근 에러 (상위 {stats.recent_errors.length}건)
                </h3>
                {stats.recent_errors.length === 0 ? (
                  <p className="mt-2 text-xs text-gray-500 dark:text-gray-400">
                    최근 에러가 없습니다.
                  </p>
                ) : (
                  <table className="mt-2 w-full text-left text-xs">
                    <tbody>
                      {stats.recent_errors.map((item) => (
                        <tr
                          key={`${item.session_id}-${item.run_id}-${item.step}-${item.recorded_at}`}
                          className="border-b border-gray-100 dark:border-gray-800/60 align-top"
                        >
                          <td className="py-1.5 pr-2">
                            <div className="font-mono text-[11px]">{item.step}</div>
                            <div className="text-[11px] text-red-600 dark:text-red-400">
                              {item.error_type}
                            </div>
                          </td>
                          <td className="py-1.5 text-right text-[11px] text-gray-500">
                            {formatRecordedAt(item.recorded_at)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
            </div>
          )}
        </>
      )}
    </section>
  );
}
