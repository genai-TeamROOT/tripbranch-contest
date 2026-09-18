/*
 * 역할: 이 백엔드 프로세스가 기동 이후 보낸 외부 API 호출량을 오퍼레이션 단위로 보여준다.
 * 입력: /api/dev/api-usage 스냅샷.
 * 출력: provider 모드 경고, 오늘 한도 게이지, 오퍼레이션별 호출·실패·지연 표.
 * 호출 시점: DeveloperOpsPage가 렌더링될 때, 그리고 자동 새로고침 주기마다.
 *
 * 집계는 프로세스 메모리라 서버를 재시작하면 0으로 돌아간다. 일일 한도의 실제
 * 소진량이 아니라 "지금 띄운 서버가 무엇을 얼마나 부르고 있는가"를 본다.
 */

import type { ApiUsageEntry, ApiUsageSnapshot } from "../../api/dev";

const PROVIDER_LABELS: Record<string, string> = {
  tour_api: "TourAPI",
  concentration: "집중률",
  kma_weather: "기상청",
  kasi_holiday: "공휴일(KASI)",
  // 도보(/v2/routing/walk)와 대중교통(/v2/routing/publictraffic)이 같은 호스트라
  // provider가 하나로 묶인다. 어느 쪽인지는 operation 열이 가른다.
  kakao_map: "카카오맵 길찾기",
  naver_driving: "네이버 자동차 경로",
  naver_geocoding: "네이버 지오코딩",
  naver_local_search: "네이버 지역검색",
  supabase: "Supabase",
  gemini: "Gemini",
  unknown: "미분류",
};

const MODE_LABELS: Record<string, string> = {
  llm: "LLM",
  place: "장소",
  geocoding: "지오코딩",
  local_search: "지역검색",
  weather: "날씨",
  concentration: "집중률",
  holiday: "공휴일",
  travel_route: "경로 조회",
};

function formatTime(value: string | null) {
  if (!value) return "-";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleTimeString("ko-KR", { hour12: false });
}

function formatLatency(value: number | null) {
  if (value === null) return "-";
  return value >= 1000 ? `${(value / 1000).toFixed(1)}초` : `${Math.round(value)}ms`;
}

function LimitGauge({ entry }: { entry: ApiUsageEntry }) {
  if (entry.daily_limit === null) {
    return <span className="text-xs text-gray-400">한도 미설정</span>;
  }
  const ratio = Math.min(1, entry.today_count / entry.daily_limit);
  const tone =
    ratio >= 0.9
      ? "bg-red-500"
      : ratio >= 0.7
        ? "bg-amber-500"
        : "bg-emerald-500";
  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 w-24 overflow-hidden rounded-full bg-gray-200 dark:bg-gray-700">
        <div className={`h-full ${tone}`} style={{ width: `${ratio * 100}%` }} />
      </div>
      <span className="text-xs tabular-nums text-gray-600 dark:text-gray-300">
        {entry.today_count} / {entry.daily_limit}
      </span>
    </div>
  );
}

export function ApiUsagePanel({
  snapshot,
  error,
  autoRefresh,
  onToggleAutoRefresh,
  onRefresh,
  onReset,
}: {
  snapshot: ApiUsageSnapshot | null;
  error: string | null;
  autoRefresh: boolean;
  onToggleAutoRefresh: (next: boolean) => void;
  onRefresh: () => void;
  onReset: () => void;
}) {
  const fakeProviders = snapshot
    ? Object.entries(snapshot.provider_modes).filter(([, mode]) => mode === "fake")
    : [];

  return (
    <section className="rounded-lg border border-gray-200 bg-white p-4 dark:border-gray-800 dark:bg-gray-900">
      <header className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="text-base font-bold text-gray-950 dark:text-gray-50">
            외부 API 호출량
          </h2>
          <p className="mt-0.5 text-xs text-gray-500 dark:text-gray-400">
            프로세스 기동 {formatTime(snapshot?.process_started_at ?? null)} 이후 누적 ·
            한도 게이지는 오늘({snapshot?.today ?? "-"}, KST) 기준
          </p>
        </div>
        <div className="flex items-center gap-2">
          <label className="flex items-center gap-1.5 text-xs text-gray-600 dark:text-gray-300">
            <input
              type="checkbox"
              checked={autoRefresh}
              onChange={(event) => onToggleAutoRefresh(event.target.checked)}
            />
            자동 새로고침
          </label>
          <button
            type="button"
            onClick={onRefresh}
            className="rounded-md border border-gray-300 px-2.5 py-1 text-xs dark:border-gray-700"
          >
            새로고침
          </button>
          <button
            type="button"
            onClick={onReset}
            className="rounded-md border border-gray-300 px-2.5 py-1 text-xs dark:border-gray-700"
          >
            카운터 초기화
          </button>
        </div>
      </header>

      {error && (
        <p className="mt-3 rounded-md bg-red-50 p-3 text-xs text-red-900 dark:bg-red-950/40 dark:text-red-100">
          {error}
        </p>
      )}

      {snapshot && (
        <>
          <dl className="mt-3 grid grid-cols-3 gap-2">
            <div className="rounded-md bg-gray-100 p-2.5 dark:bg-gray-800">
              <dt className="text-[11px] text-gray-500 dark:text-gray-400">누적 호출</dt>
              <dd className="text-lg font-bold tabular-nums">{snapshot.totals.count}</dd>
            </div>
            <div className="rounded-md bg-gray-100 p-2.5 dark:bg-gray-800">
              <dt className="text-[11px] text-gray-500 dark:text-gray-400">오늘 호출</dt>
              <dd className="text-lg font-bold tabular-nums">
                {snapshot.today_totals.count}
              </dd>
            </div>
            <div className="rounded-md bg-gray-100 p-2.5 dark:bg-gray-800">
              <dt className="text-[11px] text-gray-500 dark:text-gray-400">실패</dt>
              <dd className="text-lg font-bold tabular-nums text-red-600 dark:text-red-400">
                {snapshot.totals.error}
              </dd>
            </div>
          </dl>

          {fakeProviders.length > 0 && (
            <p className="mt-3 rounded-md bg-amber-50 p-3 text-xs text-amber-900 dark:bg-amber-950/30 dark:text-amber-100">
              Fake Provider: {fakeProviders.map(([key]) => MODE_LABELS[key] ?? key).join(", ")}.
              Fake는 외부 HTTP를 아예 보내지 않으므로 아래 표가 비어 있는 것이 정상이에요 —
              실데이터로 띄운 줄 알았다면 backend/.env의 PROVIDER_MODE를 확인하세요.
            </p>
          )}

          {snapshot.entries.length === 0 ? (
            <p className="mt-3 rounded-md border border-dashed border-gray-300 p-4 text-sm text-gray-500 dark:border-gray-700">
              아직 기록된 외부 호출이 없습니다.
            </p>
          ) : (
            <div className="mt-3 overflow-x-auto">
              <table className="w-full min-w-180 text-left text-xs">
                <thead className="text-gray-500 dark:text-gray-400">
                  <tr className="border-b border-gray-200 dark:border-gray-800">
                    <th className="py-1.5 pr-2 font-medium">Provider</th>
                    <th className="py-1.5 pr-2 font-medium">오퍼레이션</th>
                    <th className="py-1.5 pr-2 text-right font-medium">누적</th>
                    <th className="py-1.5 pr-2 text-right font-medium">실패</th>
                    <th className="py-1.5 pr-2 font-medium">오늘 관측 / 한도</th>
                    <th className="py-1.5 pr-2 text-right font-medium">평균</th>
                    <th className="py-1.5 pr-2 text-right font-medium">최대</th>
                    <th className="py-1.5 pr-2 font-medium">마지막</th>
                  </tr>
                </thead>
                <tbody>
                  {snapshot.entries.map((entry) => (
                    <tr
                      key={`${entry.provider}:${entry.operation}`}
                      className="border-b border-gray-100 dark:border-gray-800/60"
                    >
                      <td className="py-1.5 pr-2">
                        {PROVIDER_LABELS[entry.provider] ?? entry.provider}
                      </td>
                      <td className="py-1.5 pr-2 font-mono text-[11px]">{entry.operation}</td>
                      <td className="py-1.5 pr-2 text-right tabular-nums">{entry.count}</td>
                      <td
                        className={`py-1.5 pr-2 text-right tabular-nums ${
                          entry.error > 0 ? "font-semibold text-red-600 dark:text-red-400" : ""
                        }`}
                      >
                        {entry.error}
                      </td>
                      <td className="py-1.5 pr-2">
                        <LimitGauge entry={entry} />
                      </td>
                      <td className="py-1.5 pr-2 text-right tabular-nums">
                        {formatLatency(entry.avg_latency_ms)}
                      </td>
                      <td className="py-1.5 pr-2 text-right tabular-nums">
                        {formatLatency(entry.max_latency_ms)}
                      </td>
                      <td className="py-1.5 pr-2 text-gray-500">
                        {formatTime(entry.last_called_at)}
                        {entry.last_status ? ` (${entry.last_status})` : ""}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          <p className="mt-2 text-[11px] text-gray-500 dark:text-gray-400">
            이 서버 프로세스가 보낸 호출만 셉니다. backend/scripts/* 는 별도
            프로세스라 여기 안 잡히지만 일일 한도는 똑같이 소모해요. 서버를
            재시작하면 카운터가 0으로 돌아갑니다. 따라서 한도 게이지는 실제 소진량이
            아니라 <strong>하한</strong>이에요 — 남은 호출은 표시된 것보다 적을 수
            있습니다.
          </p>
        </>
      )}
    </section>
  );
}
