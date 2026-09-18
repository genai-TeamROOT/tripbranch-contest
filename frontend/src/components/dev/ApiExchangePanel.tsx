/*
 * 역할: /dev-chat 왼쪽에서 외부 API 요청·응답 원문을 호출 단위로 보여준다.
 * 입력: /api/dev/exchanges 스냅샷.
 * 출력: 접힌 호출 목록. 펼치면 요청 → 응답 순서로 원문이 나온다.
 * 호출 시점: DeveloperChatPage가 렌더링될 때, 캡처가 켜져 있으면 주기적으로.
 *
 * 자격증명은 서버에서 이미 마스킹된 상태로 온다(값이 `***`). 프론트에서 다시
 * 가리지 않는다 — 마스킹 지점이 둘이면 어느 쪽이 실제로 막고 있는지 흐려진다.
 */

import { useState } from "react";
import type { ApiExchange, ApiExchangeSnapshot } from "../../api/dev";

const PROVIDER_LABELS: Record<string, string> = {
  tour_api: "TourAPI",
  concentration: "집중률",
  kma_weather: "기상청",
  kasi_holiday: "공휴일",
  // 도보와 대중교통이 같은 호스트라 provider가 하나로 묶인다(operation이 가른다).
  kakao_map: "카카오맵 길찾기",
  naver_driving: "자동차 경로",
  naver_geocoding: "지오코딩",
  naver_local_search: "지역검색",
  supabase: "Supabase",
  unknown: "미분류",
};

function formatTime(value: string) {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleTimeString("ko-KR", { hour12: false });
}

function formatLatency(ms: number) {
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}초` : `${Math.round(ms)}ms`;
}

function formatBytes(bytes: number) {
  if (bytes < 1024) return `${bytes}B`;
  return `${(bytes / 1024).toFixed(1)}KB`;
}

/** JSON이면 들여쓰기해서, 아니면 원문 그대로. */
function prettify(body: string) {
  try {
    return JSON.stringify(JSON.parse(body), null, 2);
  } catch {
    return body;
  }
}

function KeyValues({ values }: { values: Record<string, string> }) {
  const entries = Object.entries(values);
  if (entries.length === 0) return <p className="text-[11px] text-gray-400">없음</p>;
  return (
    <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-2 gap-y-0.5">
      {entries.map(([key, value]) => (
        <div key={key} className="contents">
          <dt className="font-mono text-[11px] text-gray-500">{key}</dt>
          <dd
            className={`break-all font-mono text-[11px] ${
              value === "***"
                ? "text-gray-400"
                : "text-gray-800 dark:text-gray-200"
            }`}
          >
            {value}
          </dd>
        </div>
      ))}
    </dl>
  );
}

function BodyBlock({
  body,
  truncated,
  bytes,
}: {
  body: string | null;
  truncated: boolean;
  bytes?: number;
}) {
  if (body === null) return <p className="text-[11px] text-gray-400">본문 없음</p>;
  return (
    <>
      <pre className="max-h-72 overflow-auto rounded bg-gray-100 p-2 font-mono text-[11px] leading-relaxed dark:bg-gray-950">
        {prettify(body)}
      </pre>
      {truncated && (
        <p className="mt-1 text-[11px] text-amber-700 dark:text-amber-300">
          본문이 길어 앞부분만 보관했어요
          {bytes !== undefined ? ` (원본 ${formatBytes(bytes)})` : ""}.
        </p>
      )}
    </>
  );
}

function ExchangeRow({ exchange }: { exchange: ApiExchange }) {
  const [open, setOpen] = useState(false);
  return (
    <li className="border-b border-gray-200 last:border-0 dark:border-gray-800">
      <button
        type="button"
        onClick={() => setOpen((previous) => !previous)}
        aria-expanded={open}
        className="flex w-full items-center gap-2 px-2 py-1.5 text-left hover:bg-gray-100 dark:hover:bg-gray-800"
      >
        <span className="text-[11px] text-gray-400">{open ? "▾" : "▸"}</span>
        <span className="text-[11px] tabular-nums text-gray-500">
          {formatTime(exchange.started_at)}
        </span>
        <span className="min-w-0 flex-1 truncate text-xs">
          <span className="text-gray-500">
            {PROVIDER_LABELS[exchange.provider] ?? exchange.provider}
          </span>{" "}
          <span className="font-mono text-[11px]">{exchange.operation}</span>
        </span>
        <span
          className={`rounded px-1.5 py-0.5 text-[11px] font-semibold ${
            exchange.ok
              ? "bg-emerald-100 text-emerald-900 dark:bg-emerald-950/50 dark:text-emerald-100"
              : "bg-red-100 text-red-900 dark:bg-red-950/50 dark:text-red-100"
          }`}
        >
          {exchange.status}
        </span>
        <span className="text-[11px] tabular-nums text-gray-500">
          {formatLatency(exchange.latency_ms)}
        </span>
      </button>

      {open && (
        <div className="flex flex-col gap-3 bg-gray-50 px-3 pb-3 pt-1 dark:bg-gray-900/60">
          <section>
            <h4 className="text-[11px] font-bold text-gray-700 dark:text-gray-300">
              요청
            </h4>
            <p className="mt-1 break-all font-mono text-[11px] text-gray-700 dark:text-gray-300">
              {exchange.method} {exchange.url}
            </p>
            <p className="mt-2 text-[11px] font-medium text-gray-500">쿼리</p>
            <KeyValues values={exchange.query} />
            <p className="mt-2 text-[11px] font-medium text-gray-500">헤더</p>
            <KeyValues values={exchange.request_headers} />
            {exchange.request_body !== null && (
              <>
                <p className="mt-2 text-[11px] font-medium text-gray-500">본문</p>
                <BodyBlock
                  body={exchange.request_body}
                  truncated={exchange.request_body_truncated}
                />
              </>
            )}
          </section>

          <section>
            <h4 className="text-[11px] font-bold text-gray-700 dark:text-gray-300">
              응답
            </h4>
            {exchange.error ? (
              <p className="mt-1 rounded bg-red-50 p-2 text-[11px] text-red-900 dark:bg-red-950/40 dark:text-red-100">
                응답을 받지 못했어요: {exchange.error}
              </p>
            ) : (
              <>
                <p className="mt-1 text-[11px] text-gray-500">
                  {exchange.status} · {formatLatency(exchange.latency_ms)} ·{" "}
                  {formatBytes(exchange.response_bytes)}
                </p>
                <p className="mt-2 text-[11px] font-medium text-gray-500">헤더</p>
                <KeyValues values={exchange.response_headers} />
                <p className="mt-2 text-[11px] font-medium text-gray-500">본문</p>
                <BodyBlock
                  body={exchange.response_body}
                  truncated={exchange.response_body_truncated}
                  bytes={exchange.response_bytes}
                />
              </>
            )}
          </section>
        </div>
      )}
    </li>
  );
}

export function ApiExchangePanel({
  snapshot,
  error,
  onToggleCapture,
  onClear,
  onRefresh,
}: {
  snapshot: ApiExchangeSnapshot | null;
  error: string | null;
  onToggleCapture: (enabled: boolean) => void;
  onClear: () => void;
  onRefresh: () => void;
}) {
  const enabled = snapshot?.enabled ?? false;

  return (
    <aside className="flex min-h-0 min-w-0 flex-col border-r border-gray-200 bg-white dark:border-gray-800 dark:bg-gray-900">
      <header className="border-b border-gray-200 px-3 py-3 dark:border-gray-800">
        <div className="flex items-center justify-between gap-2">
          <h2 className="text-sm font-bold">API 요청·응답</h2>
          <label className="flex items-center gap-1.5 text-xs">
            <input
              type="checkbox"
              checked={enabled}
              onChange={(event) => onToggleCapture(event.target.checked)}
            />
            캡처
          </label>
        </div>
        <div className="mt-2 flex items-center gap-2">
          <button
            type="button"
            onClick={onRefresh}
            className="rounded-md border border-gray-300 px-2 py-1 text-[11px] dark:border-gray-700"
          >
            새로고침
          </button>
          <button
            type="button"
            onClick={onClear}
            disabled={!enabled}
            className="rounded-md border border-gray-300 px-2 py-1 text-[11px] disabled:opacity-50 dark:border-gray-700"
          >
            비우기
          </button>
          <span className="ml-auto text-[11px] text-gray-400">
            최근 {snapshot?.capacity ?? 0}건
          </span>
        </div>
      </header>

      {error && (
        <p className="m-3 rounded-md bg-red-50 p-3 text-xs text-red-900 dark:bg-red-950/40 dark:text-red-100">
          {error}
        </p>
      )}

      {!enabled && (
        <p className="m-3 rounded-md border border-dashed border-gray-300 p-3 text-xs text-gray-500 dark:border-gray-700">
          캡처가 꺼져 있어요. 켜면 이후 발생하는 외부 API 호출의 요청·응답 원문을
          모아서 보여줘요. 켜기 전에 오간 호출은 남아 있지 않아요.
        </p>
      )}

      <div className="min-h-0 flex-1 overflow-y-auto">
        {enabled && snapshot?.items.length === 0 && (
          <p className="m-3 rounded-md border border-dashed border-gray-300 p-3 text-xs text-gray-500 dark:border-gray-700">
            아직 기록된 호출이 없어요. 발화를 보내면 여기에 쌓여요.
          </p>
        )}
        <ul>
          {snapshot?.items.map((exchange) => (
            <ExchangeRow key={exchange.id} exchange={exchange} />
          ))}
        </ul>
      </div>

      <p className="border-t border-gray-200 px-3 py-2 text-[11px] text-gray-500 dark:border-gray-800 dark:text-gray-400">
        인증 값은 서버에서 <code>***</code>로 가린 뒤 내려와요. Gemini는 자체 전송
        계층을 써서 여기 잡히지 않아요.
      </p>
    </aside>
  );
}
