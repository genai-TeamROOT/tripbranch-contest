/*
 * 역할: 응답 피드백(좋아요/싫어요)을 rating·reason_code·intent 기준으로 집계해 보여준다.
 * 입력: /api/feedback/stats 응답.
 * 출력: rating 요약 카드, reason_code 분포 표(dislike만), intent 상위 N 표.
 * 호출 시점: DeveloperOpsPage가 렌더링될 때, 새로고침 버튼 클릭 시.
 *
 * 이 패널이 부르는 /api/feedback/stats는 이 페이지의 다른 패널과 달리
 * APP_ENV=local이 아니어도 등록돼 있다(routes/feedback.py는 무조건 include) —
 * 그래도 화면 위치는 여기가 맞다. 개발자가 답변 품질을 점검하는 운영 도구라는
 * 성격이 같고, GET /feedback/dislikes(개별 나쁜 답변 조회)와 짝을 이룬다.
 */

import type { FeedbackReasonCode, FeedbackStatsResponse } from "../../types";

type ReasonCodeKey = FeedbackReasonCode | "unclassified";

const REASON_CODE_LABELS: Record<ReasonCodeKey, string> = {
  intent_mismatch: "의도 파악 실패",
  clarification_unhelpful: "되묻기가 도움 안 됨",
  context_not_preserved: "맥락 유지 실패",
  location_misunderstood: "위치 오인식",
  conditions_not_applied: "조건 미반영",
  recommendation_not_suitable: "추천 부적절",
  other: "기타(직접 서술)",
  unclassified: "사유 미입력",
};

// 화면에 항상 이 순서로 보여준다 — 응답 dict의 키 순서에 기대지 않는다.
const REASON_CODE_ORDER: ReasonCodeKey[] = [
  "intent_mismatch",
  "clarification_unhelpful",
  "context_not_preserved",
  "location_misunderstood",
  "conditions_not_applied",
  "recommendation_not_suitable",
  "other",
  "unclassified",
];

function formatPercent(count: number, denominator: number) {
  if (denominator === 0) return "-";
  return `${((count / denominator) * 100).toFixed(1)}%`;
}

export function FeedbackStatsPanel({
  stats,
  error,
  loading,
  onRefresh,
}: {
  stats: FeedbackStatsResponse | null;
  error: string | null;
  loading: boolean;
  onRefresh: () => void;
}) {
  const dislikeTotal = stats?.rating_counts.dislike ?? 0;
  const intentTotal = stats
    ? stats.top_intents.reduce((sum, item) => sum + item.count, 0) +
      stats.other_intent_count +
      stats.missing_intent_count
    : 0;

  return (
    <section className="rounded-lg border border-gray-200 bg-white p-4 dark:border-gray-800 dark:bg-gray-900">
      <header className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="text-base font-bold text-gray-950 dark:text-gray-50">
            피드백 통계
          </h2>
          <p className="mt-0.5 text-xs text-gray-500 dark:text-gray-400">
            좋아요/싫어요 전체 집계 (TP-146) · reason_code는 싫어요에만 붙는다
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
              <dt className="text-[11px] text-gray-500 dark:text-gray-400">전체</dt>
              <dd className="text-lg font-bold tabular-nums">{stats.total}</dd>
            </div>
            <div className="rounded-md bg-gray-100 p-2.5 dark:bg-gray-800">
              <dt className="text-[11px] text-gray-500 dark:text-gray-400">좋아요</dt>
              <dd className="text-lg font-bold tabular-nums text-emerald-600 dark:text-emerald-400">
                {stats.rating_counts.like}
              </dd>
            </div>
            <div className="rounded-md bg-gray-100 p-2.5 dark:bg-gray-800">
              <dt className="text-[11px] text-gray-500 dark:text-gray-400">싫어요</dt>
              <dd className="text-lg font-bold tabular-nums text-red-600 dark:text-red-400">
                {stats.rating_counts.dislike}
              </dd>
            </div>
          </dl>

          {stats.total === 0 ? (
            <p className="mt-3 rounded-md border border-dashed border-gray-300 p-4 text-sm text-gray-500 dark:border-gray-700">
              아직 기록된 피드백이 없습니다.
            </p>
          ) : (
            <div className="mt-4 grid gap-4 md:grid-cols-2">
              <div>
                <h3 className="text-xs font-semibold text-gray-700 dark:text-gray-300">
                  싫어요 사유별 분포
                </h3>
                <table className="mt-2 w-full text-left text-xs">
                  <tbody>
                    {REASON_CODE_ORDER.map((code) => {
                      const count = stats.reason_code_counts[code] ?? 0;
                      return (
                        <tr
                          key={code}
                          className="border-b border-gray-100 dark:border-gray-800/60"
                        >
                          <td className="py-1.5 pr-2">
                            {REASON_CODE_LABELS[code] ?? code}
                          </td>
                          <td className="py-1.5 pr-2 text-right tabular-nums">
                            {count}
                          </td>
                          <td className="py-1.5 text-right tabular-nums text-gray-500">
                            {formatPercent(count, dislikeTotal)}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>

              <div>
                <h3 className="text-xs font-semibold text-gray-700 dark:text-gray-300">
                  intent별 분포 (상위 {stats.top_intents.length}개)
                </h3>
                {stats.top_intents.length === 0 ? (
                  <p className="mt-2 text-xs text-gray-500 dark:text-gray-400">
                    intent가 남은 피드백이 없습니다.
                  </p>
                ) : (
                  <table className="mt-2 w-full text-left text-xs">
                    <tbody>
                      {stats.top_intents.map((item) => (
                        <tr
                          key={item.intent}
                          className="border-b border-gray-100 dark:border-gray-800/60"
                        >
                          <td className="py-1.5 pr-2 font-mono text-[11px]">
                            {item.intent}
                          </td>
                          <td className="py-1.5 pr-2 text-right tabular-nums">
                            {item.count}
                          </td>
                          <td className="py-1.5 text-right tabular-nums text-gray-500">
                            {formatPercent(item.count, intentTotal)}
                          </td>
                        </tr>
                      ))}
                      {stats.other_intent_count > 0 && (
                        <tr className="border-b border-gray-100 dark:border-gray-800/60 text-gray-500">
                          <td className="py-1.5 pr-2">기타(롱테일)</td>
                          <td className="py-1.5 pr-2 text-right tabular-nums">
                            {stats.other_intent_count}
                          </td>
                          <td className="py-1.5 text-right tabular-nums">
                            {formatPercent(stats.other_intent_count, intentTotal)}
                          </td>
                        </tr>
                      )}
                    </tbody>
                  </table>
                )}
                {stats.missing_intent_count > 0 && (
                  <p className="mt-2 text-[11px] text-gray-500 dark:text-gray-400">
                    intent가 기록되지 않은 피드백 {stats.missing_intent_count}건은 위 집계에서
                    빠져 있습니다.
                  </p>
                )}
              </div>
            </div>
          )}
        </>
      )}
    </section>
  );
}
