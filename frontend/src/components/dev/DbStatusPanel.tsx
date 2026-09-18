/*
 * 역할: 장소 DB(places / place_enrichments / place_sync_runs)의 현재 상태를 보여준다.
 * 입력: /api/dev/db-status 응답.
 * 출력: 구별 탭으로 나눈 장소 요약·상태 분포와, 탭 밖의 최근 동기화 이력·잠금.
 * 호출 시점: DeveloperOpsPage가 렌더링될 때, 그리고 새로고침 시.
 *
 * places는 구별로 나눠 보여준다. 한 구만 세던 시절에는 같은 화면의 동기화 이력이
 * 전 구를 보여주고 있어서, "용산구 486건 신규"와 "활성 844"가 나란히 놓인 채
 * 어느 쪽이 어느 범위인지 화면에서 알 수 없었다.
 *
 * 반대로 동기화 이력과 잠금은 탭 밖에 둔다 — 어느 구를 언제 돌렸는지는 구를
 * 오가며 보는 것보다 한 목록으로 보는 편이 읽힌다. place_enrichments와 집중률
 * 매핑은 테이블에 구 열이 없어(둘 다 content_id 기준) 전체 탭에만 둔다.
 *
 * 상세조회 TTL은 구별 값이 아니라 서버 설정값이라 머리말에 한 번만 쓴다.
 *
 * 조회 전용이다. 동기화 실행(대조·반영)은 별도 패널이 담당한다.
 */

import { useState } from "react";

import { releaseSyncLock } from "../../api/dev";
import type {
  DbStatus,
  DistrictPlaceSummary,
  PlaceSummary,
  SyncLockRow,
  SyncRunRow,
} from "../../api/dev";

const RUN_STATUS_TONES: Record<string, string> = {
  success: "bg-emerald-100 text-emerald-900 dark:bg-emerald-950/50 dark:text-emerald-100",
  partial_failure: "bg-amber-100 text-amber-900 dark:bg-amber-950/50 dark:text-amber-100",
  failed: "bg-red-100 text-red-900 dark:bg-red-950/50 dark:text-red-100",
  running: "bg-blue-100 text-blue-900 dark:bg-blue-950/50 dark:text-blue-100",
};

function formatDateTime(value: string | null | undefined) {
  if (!value) return "-";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString("ko-KR", { hour12: false });
}

function Distribution({ counts }: { counts: Record<string, number> }) {
  const entries = Object.entries(counts).sort((a, b) => b[1] - a[1]);
  if (entries.length === 0) return <span className="text-xs text-gray-400">없음</span>;
  return (
    <div className="flex flex-wrap gap-1.5">
      {entries.map(([key, value]) => (
        <span
          key={key}
          className="rounded bg-gray-100 px-1.5 py-0.5 font-mono text-[11px] dark:bg-gray-800"
        >
          {key} {value}
        </span>
      ))}
    </div>
  );
}

/** 이 실행이 실제로 쓴 테이블. place_sync_runs 행에는 이 정보가 없어 카운트에서 파생한다.
 *
 * 동기화가 건드리는 테이블은 세 개뿐이다(리포지토리 쓰기 경로 전수 확인).
 * place_enrichments와 place_concentration_mappings는 별도 스크립트 소관이라
 * 여기 절대 나타나지 않는 게 정상이다.
 */
function syncedTables(run: SyncRunRow): { table: string; detail: string }[] {
  const tables: { table: string; detail: string }[] = [];
  const processed = run.processed_count ?? 0;
  if (processed > 0) {
    const parts: string[] = [];
    if (run.new_count) parts.push(`신규 ${run.new_count}`);
    // updated_count는 "값이 바뀐 수"가 아니라 "목록에 있던 장소 중 DB에 이미
    // 있던 수"다(place_sync.py의 len(places) - new_count). 갱신이라고 쓰면 그만큼
    // 바뀐 것으로 읽힌다.
    if (run.updated_count) parts.push(`기존 ${run.updated_count}`);
    if (run.deactivated_count) parts.push(`비활성 ${run.deactivated_count}`);
    tables.push({
      table: "places",
      detail: parts.length > 0 ? parts.join(" · ") : `${processed}건 변경 없음`,
    });
  }
  // 상태는 왼쪽 배지에 이미 있으므로 여기서는 되풀이하지 않는다.
  tables.push({ table: "place_sync_runs", detail: "이력 1건" });
  tables.push({
    table: "place_sync_locks",
    detail: run.status === "running" ? "잠금 보유 중" : "획득·해제",
  });
  return tables;
}

function SyncedTables({ run }: { run: SyncRunRow }) {
  return (
    <div className="flex flex-col gap-0.5">
      {syncedTables(run).map(({ table, detail }) => (
        <span key={table} className="whitespace-nowrap">
          <span className="rounded bg-gray-100 px-1.5 py-0.5 font-mono text-[11px] dark:bg-gray-800">
            {table}
          </span>
          <span className="ml-1.5 text-[11px] text-gray-500">{detail}</span>
        </span>
      ))}
    </div>
  );
}

/* 잠금을 만든 실행이 얼마나 오래 running으로 있었는지. 살아 있는 동기화인지
 * 죽은 프로세스가 남긴 유령인지는 이 값으로만 짐작할 수 있다 — 진행 상황은
 * 서버 메모리에만 있어 DB에는 시작·종료 시점만 남는다. */
function formatElapsed(startedAt: string | null | undefined) {
  if (!startedAt) return null;
  const started = new Date(startedAt);
  if (Number.isNaN(started.getTime())) return null;
  const minutes = Math.floor((Date.now() - started.getTime()) / 60000);
  if (minutes < 1) return "1분 미만";
  if (minutes < 60) return `${minutes}분`;
  return `${Math.floor(minutes / 60)}시간 ${minutes % 60}분`;
}

function LockRow({
  lock,
  onRelease,
  releasing,
}: {
  lock: SyncLockRow;
  onRelease: (lock: SyncLockRow, force: boolean) => void;
  releasing: boolean;
}) {
  const expiresAt = lock.expires_at ? new Date(lock.expires_at) : null;
  // 만료 판정은 저장소가 아니라 화면에서 한다 — 저장소가 걸러버리면 "잠금이 남아
  // 실행이 막힌다"와 "잠금이 없다"가 구분되지 않는다.
  const expired = expiresAt !== null && expiresAt.getTime() <= Date.now();
  // 실행이 끝났으면(success/failed) 잠금은 확실히 유령이라 바로 풀어도 된다.
  // running이면 살아 있을 수도 죽었을 수도 있어 한 번 더 확인받는다.
  const stillRunning = lock.run_status === "running";
  const elapsed = formatElapsed(lock.run_started_at);
  const canRelease = Boolean(lock.area_code && lock.district_code && lock.sync_run_id);

  return (
    <li className="flex flex-wrap items-center gap-2 py-1 text-xs">
      <span
        className={`rounded px-1.5 py-0.5 font-semibold ${
          expired
            ? "bg-gray-200 text-gray-700 dark:bg-gray-800 dark:text-gray-300"
            : "bg-blue-100 text-blue-900 dark:bg-blue-950/50 dark:text-blue-100"
        }`}
      >
        {expired ? "만료됨" : "잠금 중"}
      </span>
      <span>
        {lock.area_code}-{lock.district_code}
      </span>
      <span className="text-gray-500">
        획득 {formatDateTime(lock.acquired_at)} · 만료 {formatDateTime(lock.expires_at)}
      </span>
      <span
        className={`rounded px-1.5 py-0.5 ${
          stillRunning
            ? "bg-amber-100 text-amber-900 dark:bg-amber-950/50 dark:text-amber-100"
            : "bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-300"
        }`}
      >
        {lock.run_status
          ? `실행 ${lock.run_status}${elapsed ? ` · ${elapsed} 경과` : ""}`
          : "실행 기록 없음"}
      </span>
      {typeof lock.run_processed_count === "number" && (
        <span className="text-gray-500">처리 {lock.run_processed_count}건</span>
      )}
      <span className="font-mono text-[11px] text-gray-400">{lock.sync_run_id}</span>
      {canRelease && (
        <button
          type="button"
          onClick={() => onRelease(lock, stillRunning)}
          disabled={releasing}
          className="rounded border border-gray-300 px-2 py-0.5 text-[11px] font-semibold text-gray-700 hover:bg-gray-100 disabled:opacity-50 dark:border-gray-600 dark:text-gray-200 dark:hover:bg-gray-800"
        >
          {releasing ? "해제 중…" : stillRunning ? "강제 해제" : "잠금 해제"}
        </button>
      )}
    </li>
  );
}

function SyncRunTable({ runs }: { runs: SyncRunRow[] }) {
  if (runs.length === 0) {
    return (
      <p className="rounded-md border border-dashed border-gray-300 p-4 text-sm text-gray-500 dark:border-gray-700">
        동기화 이력이 없습니다.
      </p>
    );
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-240 text-left text-xs">
        <thead className="text-gray-500 dark:text-gray-400">
          <tr className="border-b border-gray-200 dark:border-gray-800">
            <th className="py-1.5 pr-2 font-medium">상태</th>
            <th className="py-1.5 pr-2 font-medium">지역</th>
            <th className="py-1.5 pr-2 font-medium">반영 테이블</th>
            <th className="py-1.5 pr-2 font-medium">시작</th>
            <th className="py-1.5 pr-2 font-medium">완료</th>
            <th className="py-1.5 pr-2 text-right font-medium">처리</th>
            <th className="py-1.5 pr-2 text-right font-medium">신규</th>
            <th className="py-1.5 pr-2 text-right font-medium">기존</th>
            <th className="py-1.5 pr-2 text-right font-medium">비활성</th>
            <th className="py-1.5 pr-2 text-right font-medium">실패</th>
            <th className="py-1.5 pr-2 font-medium">오류</th>
          </tr>
        </thead>
        <tbody>
          {runs.map((run, index) => (
            <tr
              key={run.id ?? index}
              className="border-b border-gray-100 dark:border-gray-800/60"
            >
              <td className="py-1.5 pr-2">
                <span
                  className={`rounded px-1.5 py-0.5 font-semibold ${
                    RUN_STATUS_TONES[run.status ?? ""] ?? "bg-gray-100 dark:bg-gray-800"
                  }`}
                >
                  {run.status ?? "-"}
                </span>
              </td>
              <td className="py-1.5 pr-2">
                {run.area_code}-{run.district_code}
              </td>
              <td className="py-1.5 pr-2">
                <SyncedTables run={run} />
              </td>
              <td className="py-1.5 pr-2 text-gray-500">{formatDateTime(run.started_at)}</td>
              <td className="py-1.5 pr-2 text-gray-500">{formatDateTime(run.completed_at)}</td>
              <td className="py-1.5 pr-2 text-right tabular-nums">{run.processed_count ?? 0}</td>
              <td className="py-1.5 pr-2 text-right tabular-nums">{run.new_count ?? 0}</td>
              <td className="py-1.5 pr-2 text-right tabular-nums">{run.updated_count ?? 0}</td>
              <td className="py-1.5 pr-2 text-right tabular-nums">
                {run.deactivated_count ?? 0}
              </td>
              <td
                className={`py-1.5 pr-2 text-right tabular-nums ${
                  (run.failed_count ?? 0) > 0
                    ? "font-semibold text-red-600 dark:text-red-400"
                    : ""
                }`}
              >
                {run.failed_count ?? 0}
              </td>
              <td className="py-1.5 pr-2">
                {run.error_summary && Object.keys(run.error_summary).length > 0 ? (
                  <Distribution counts={run.error_summary} />
                ) : (
                  <span className="text-gray-400">-</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** 탭을 가리키는 키. 지역 코드까지 붙여야 다른 시도의 같은 구 코드와 안 겹친다. */
function districtKey(summary: DistrictPlaceSummary) {
  return `${summary.area_code}-${summary.district_code}`;
}

function DistrictTabs({
  districts,
  selectedKey,
  onSelect,
}: {
  districts: DistrictPlaceSummary[];
  selectedKey: string | null;
  onSelect: (key: string | null) => void;
}) {
  const tabClass = (active: boolean) =>
    `-mb-px border-b-2 px-3 py-1.5 text-xs ${
      active
        ? "border-gray-900 font-semibold text-gray-950 dark:border-gray-100 dark:text-gray-50"
        : "border-transparent text-gray-500 hover:text-gray-800 dark:hover:text-gray-200"
    }`;
  return (
    <div
      role="tablist"
      className="mt-3 flex flex-wrap items-end gap-1 border-b border-gray-200 dark:border-gray-800"
    >
      <button
        type="button"
        role="tab"
        aria-selected={selectedKey === null}
        onClick={() => onSelect(null)}
        className={tabClass(selectedKey === null)}
      >
        전체
        <span className="ml-1.5 font-normal text-gray-400">{districts.length}개 구</span>
      </button>
      {districts.map((district) => {
        const key = districtKey(district);
        return (
          <button
            key={key}
            type="button"
            role="tab"
            aria-selected={selectedKey === key}
            onClick={() => onSelect(key)}
            className={tabClass(selectedKey === key)}
          >
            {/* 이름을 못 찾은 구는 코드로만 부른다 — 빈 라벨로 두면 어느 구인지 사라진다. */}
            {district.district_name ?? `구 ${district.district_code}`}
            <span className="ml-1.5 font-mono font-normal text-gray-400">{key}</span>
          </button>
        );
      })}
    </div>
  );
}

function PlaceSummaryCards({
  summary,
  /** 전체 탭에서만 쓸 전 구 카운트. 구 탭에서는 null을 받아 카드를 그리지 않는다. */
  tableCounts,
}: {
  summary: PlaceSummary;
  tableCounts: { enrichments: number; concentrationMappings: number } | null;
}) {
  return (
    <dl
      className={`mt-3 grid grid-cols-2 gap-2 ${
        tableCounts ? "sm:grid-cols-5" : "sm:grid-cols-3"
      }`}
    >
      <div className="rounded-md bg-gray-100 p-2.5 dark:bg-gray-800">
        <dt className="text-[11px] text-gray-500 dark:text-gray-400">places 활성</dt>
        <dd className="text-lg font-bold tabular-nums">{summary.active}</dd>
        <dd className="text-[11px] text-gray-500">
          전체 {summary.total} · 비활성 {summary.inactive}
        </dd>
      </div>
      {/* 무장애 정보를 확인한 장소 수. places보다 훨씬 적은 게 정상이에요 —
       * 무장애 목록에 있는 장소만 행이 되고, 4개 구 실측으로 19%였습니다. */}
      <div className="rounded-md bg-gray-100 p-2.5 dark:bg-gray-800">
        <dt className="text-[11px] text-gray-500 dark:text-gray-400">
          place_barrier_free 활성
        </dt>
        <dd className="text-lg font-bold tabular-nums">{summary.barrier_free_active}</dd>
        <dd className="text-[11px] text-gray-500">전체 {summary.barrier_free_total}</dd>
      </div>
      {tableCounts && (
        <>
          <div className="rounded-md bg-gray-100 p-2.5 dark:bg-gray-800">
            <dt className="text-[11px] text-gray-500 dark:text-gray-400">
              place_enrichments
            </dt>
            <dd className="text-lg font-bold tabular-nums">{tableCounts.enrichments}</dd>
            <dd className="text-[11px] text-gray-500">전 구 합계</dd>
          </div>
          <div className="rounded-md bg-gray-100 p-2.5 dark:bg-gray-800">
            <dt className="text-[11px] text-gray-500 dark:text-gray-400">집중률 매핑</dt>
            <dd className="text-lg font-bold tabular-nums">
              {tableCounts.concentrationMappings}
            </dd>
            <dd className="text-[11px] text-gray-500">전 구 합계</dd>
          </div>
        </>
      )}
      <div className="rounded-md bg-gray-100 p-2.5 dark:bg-gray-800">
        <dt className="text-[11px] text-gray-500 dark:text-gray-400">최근 상세조회 시각</dt>
        <dd className="text-sm font-semibold">
          {formatDateTime(summary.latest_detail_fetched_at)}
        </dd>
      </div>
    </dl>
  );
}

export function DbStatusPanel({
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
  const [releasingRunId, setReleasingRunId] = useState<string | null>(null);
  const [releaseMessage, setReleaseMessage] = useState<string | null>(null);

  /* 잠금 해제. 실행이 아직 running이면 한 번 더 확인받는다. 서버도 force 없이는
   * 거부하므로 확인은 두 겹이다.
   *
   * 해제해도 돌고 있는 동기화가 멈추지는 않는다 — 잠금은 시작할 때 한 번 잡고
   * 도는 동안 다시 확인하지 않는다. 그래서 위험은 "실행이 끊기는 것"이 아니라
   * "같은 구를 두 곳에서 동시에 적재하는 것"이다. 문구도 그렇게 적는다. */
  const handleRelease = async (lock: SyncLockRow, stillRunning: boolean) => {
    const { area_code: areaCode, district_code: districtCode, sync_run_id: syncRunId } = lock;
    if (!areaCode || !districtCode || !syncRunId) return;
    if (
      stillRunning &&
      !window.confirm(
        "이 잠금을 만든 동기화가 아직 진행 중으로 기록돼 있어요.\n\n" +
          "해제해도 그 실행이 멈추지는 않습니다 — 잠금만 풀려서, 다른 곳에서 " +
          "실제로 돌고 있다면 같은 구를 두 곳에서 동시에 적재하게 돼요.\n\n" +
          "서버가 강제 종료돼 남은 잠금인 것이 확실할 때만 진행하세요.",
      )
    ) {
      return;
    }
    setReleasingRunId(syncRunId);
    setReleaseMessage(null);
    try {
      const result = await releaseSyncLock({
        areaCode,
        districtCode,
        syncRunId,
        force: stillRunning,
      });
      if (result.released) {
        setReleaseMessage(
          result.run_abandoned
            ? "잠금을 풀고 진행 중이던 실행을 실패로 마감했어요."
            : "잠금을 풀었어요.",
        );
        onRefresh();
      } else {
        setReleaseMessage(result.message ?? "잠금을 풀지 못했어요.");
      }
    } catch (caught) {
      setReleaseMessage(caught instanceof Error ? caught.message : "잠금 해제에 실패했어요.");
    } finally {
      setReleasingRunId(null);
    }
  };

  const districts = status?.districts ?? [];
  // 고른 구가 새 응답에 없으면(전량 삭제 등) 전체로 되돌린다 — 빈 화면을 남기지 않는다.
  const selected = districts.find((district) => districtKey(district) === selectedKey) ?? null;
  const summary = selected ?? status?.overall ?? null;

  return (
    <section className="rounded-lg border border-gray-200 bg-white p-4 dark:border-gray-800 dark:bg-gray-900">
      <header className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="text-base font-bold text-gray-950 dark:text-gray-50">장소 DB 상태</h2>
          <p className="mt-0.5 text-xs text-gray-500 dark:text-gray-400">
            {/* 구별 값이 아니라 서버 설정값이라 탭 밖에 한 번만 쓴다. */}
            상세조회 TTL {status?.detail_ttl_days ?? "-"}일
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

      {status && summary && (
        <>
          <DistrictTabs
            districts={districts}
            selectedKey={selected === null ? null : selectedKey}
            onSelect={setSelectedKey}
          />

          <PlaceSummaryCards
            summary={summary}
            tableCounts={
              selected === null
                ? {
                    enrichments: status.place_enrichments_count,
                    concentrationMappings: status.place_concentration_mappings_count,
                  }
                : null
            }
          />

          <dl className="mt-3 grid gap-2 sm:grid-cols-3">
            <div>
              <dt className="text-[11px] font-medium text-gray-500 dark:text-gray-400">
                상세조회 상태
              </dt>
              <dd className="mt-1">
                <Distribution counts={summary.detail_fetch_status} />
              </dd>
            </div>
            <div>
              <dt className="text-[11px] font-medium text-gray-500 dark:text-gray-400">
                운영시간 파싱 상태
              </dt>
              <dd className="mt-1">
                <Distribution counts={summary.operating_parse_status} />
              </dd>
            </div>
            <div>
              <dt className="text-[11px] font-medium text-gray-500 dark:text-gray-400">
                파서 버전
              </dt>
              <dd className="mt-1">
                <Distribution counts={summary.operating_parser_version} />
              </dd>
            </div>
          </dl>

          <h3 className="mt-4 text-sm font-semibold text-gray-900 dark:text-gray-100">
            동기화 잠금
          </h3>
          {status.sync_locks.length === 0 ? (
            <p className="mt-1 text-xs text-gray-500">잠금 없음 — 실행 가능한 상태예요.</p>
          ) : (
            <ul className="mt-1">
              {status.sync_locks.map((lock) => (
                <LockRow
                  key={lock.sync_run_id ?? `${lock.area_code}-${lock.district_code}`}
                  lock={lock}
                  onRelease={handleRelease}
                  releasing={releasingRunId === lock.sync_run_id}
                />
              ))}
            </ul>
          )}
          {releaseMessage && (
            <p className="mt-1 text-xs text-gray-600 dark:text-gray-300">{releaseMessage}</p>
          )}
          <p className="mt-1 text-[11px] text-gray-500 dark:text-gray-400">
            서버가 강제 종료되면 잠금이 남아 최대 2시간 동안 그 구의 동기화가 막혀요 —
            재시작해도 안 풀립니다. 실행이 이미 끝났으면 바로 풀어도 되고, 아직
            진행 중으로 보이면 경과 시간을 보고 판단하세요. 해제해도 돌고 있는
            동기화가 멈추지는 않습니다 — 잠금만 풀려서 같은 구를 두 곳에서 동시에
            적재할 수 있어요.
          </p>

          <h3 className="mt-4 text-sm font-semibold text-gray-900 dark:text-gray-100">
            최근 동기화 이력
          </h3>
          <div className="mt-2">
            <SyncRunTable runs={status.sync_runs} />
          </div>
          <p className="mt-2 text-[11px] text-gray-500 dark:text-gray-400">
            <strong>기존</strong>은 값이 바뀐 수가 아니라 목록에 있던 장소 중 DB에
            이미 있던 수예요(처리 = 신규 + 기존). 무엇이 실제로 바뀌었는지는 대조
            결과에만 남아요.
            <br />
            잠금과 이력은 탭과 무관하게 전 구를 함께 보여줘요. 장소 동기화가 쓰는
            테이블은 places · place_sync_runs · place_sync_locks 셋뿐이에요.
            place_enrichments와 집중률 매핑은 이 동기화가 건드리지 않아요 (각각 별도
            스크립트 소관).
          </p>
        </>
      )}
    </section>
  );
}
