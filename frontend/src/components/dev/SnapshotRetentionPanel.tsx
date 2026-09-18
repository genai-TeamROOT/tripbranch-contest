/*
 * 역할: supabase/data에 쌓인 스냅샷·대조 CSV를 구별 최근 N개만 남기고 정리한다.
 * 입력: /api/dev/place-sync/snapshots(구별 보관 상태와 지울 후보), .../snapshots/prune.
 * 출력: 구별 개수 표, 지울 파일 미리보기, 확인 다이얼로그, 정리 결과.
 * 호출 시점: DeveloperOpsPage가 렌더링될 때.
 *
 * 자동이 아니라 버튼인 이유가 있다. 삭제를 대조에 걸면 반영하지 않은 구의 변경분이
 * 사라진다 — 대조는 DB를 바꾸지 않으므로, 옛 스냅샷을 지우면 다음 대조가 오늘
 * 스냅샷을 기준으로 삼아 오늘 잡힌 신규가 다시는 안 나온다. 반영에 걸어도
 * 마찬가지 문제가 있다: 상세조회 상한이 걸린 실행은 비활성화를 건너뛰므로,
 * 사라진 장소가 DB에 활성인 채 남는데 스냅샷에는 그 장소가 없다. 그 스냅샷을
 * 기준으로 삼으면 그 장소는 다음 대조에서 "삭제"로도 잡히지 않는다.
 *
 * 지울 후보는 서버가 준다. 화면이 따로 세면 미리보기와 실제 정리가 갈라져,
 * 보여준 것과 다른 파일이 지워진다.
 */

import { useMemo, useState } from "react";
import type { SnapshotPruneResult, SnapshotRetention } from "../../api/dev";
import { PRUNE_CONFIRM, pruneTotals } from "./snapshotRetention";

function districtLabel(district: {
  district_name: string | null;
  area_code: string;
  district_code: string;
}) {
  const name = district.district_name ?? `구 ${district.district_code}`;
  return `${name} ${district.area_code}-${district.district_code}`;
}

export function SnapshotRetentionPanel({
  retention,
  result,
  error,
  loading,
  pruning,
  keep,
  busy,
  onChangeKeep,
  onRefresh,
  onPrune,
}: {
  retention: SnapshotRetention | null;
  result: SnapshotPruneResult | null;
  error: string | null;
  loading: boolean;
  pruning: boolean;
  keep: number;
  /** 대조·반영이 돌고 있으면 참. 그 사이 파일을 지우면 반영이 쓰는 스냅샷이 사라진다. */
  busy: boolean;
  onChangeKeep: (keep: number) => void;
  onRefresh: () => void;
  onPrune: (input: { includeReconciliations: boolean }) => void;
}) {
  const [includeReconciliations, setIncludeReconciliations] = useState(true);
  const [confirm, setConfirm] = useState("");
  const [showDialog, setShowDialog] = useState(false);

  const totals = useMemo(
    () => pruneTotals(retention, includeReconciliations),
    [retention, includeReconciliations],
  );
  const nothingToPrune = totals.snapshots + totals.reconciliations === 0;

  return (
    <section className="rounded-lg border border-gray-200 bg-white p-4 dark:border-gray-800 dark:bg-gray-900">
      <header className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="text-base font-bold text-gray-950 dark:text-gray-50">스냅샷 보관</h2>
          <p className="mt-0.5 text-xs text-gray-500 dark:text-gray-400">
            구별로 최근 몇 개만 남기고 옛 파일을 지워요. 지운 파일명은{" "}
            <code>snapshot-history.md</code>에 남고, git 이력에도 그대로 있어요. 대조 CSV는 반영이
            끝나면 자동으로 지워지므로 여기서는 그 잔여분만 다뤄요.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <label className="flex items-center gap-1.5 text-xs">
            <span className="text-gray-500 dark:text-gray-400">유지 개수</span>
            <input
              aria-label="유지 개수"
              value={keep}
              inputMode="numeric"
              onChange={(event) => {
                const next = Number(event.target.value.trim());
                // 0은 받지 않는다. 스냅샷이 0개가 되면 다음 대조가 기준을 잃고
                // 전량을 신규로 잡아 detailIntro2를 그만큼 낭비한다.
                if (Number.isFinite(next) && next >= 1) onChangeKeep(Math.floor(next));
              }}
              className="w-14 rounded-md border border-gray-300 px-2 py-1 text-xs dark:border-gray-700 dark:bg-gray-950"
            />
          </label>
          <button
            type="button"
            onClick={onRefresh}
            disabled={loading}
            className="rounded-md border border-gray-300 px-3 py-1.5 text-sm disabled:opacity-50 dark:border-gray-700"
          >
            {loading ? "불러오는 중…" : "다시 읽기"}
          </button>
        </div>
      </header>

      {error && (
        <p className="mt-3 rounded-md bg-red-50 p-3 text-xs text-red-900 dark:bg-red-950/40 dark:text-red-100">
          {error}
        </p>
      )}

      {retention && (
        <>
          <div className="mt-3 max-h-72 overflow-y-auto rounded-md border border-gray-200 dark:border-gray-800">
            <table className="w-full text-left text-xs">
              <thead className="sticky top-0 bg-gray-50 dark:bg-gray-950">
                <tr className="border-b border-gray-200 dark:border-gray-800">
                  <th className="py-1.5 pl-2 font-medium">구</th>
                  <th className="py-1.5 pr-2 font-medium">스냅샷</th>
                  <th className="py-1.5 pr-2 font-medium">대조 CSV</th>
                  <th className="py-1.5 pr-2 font-medium">지울 파일</th>
                </tr>
              </thead>
              <tbody>
                {retention.districts.map((district) => {
                  const targets = [
                    ...district.prunable_snapshots,
                    ...(includeReconciliations ? district.prunable_reconciliations : []),
                  ];
                  return (
                    <tr
                      key={`${district.area_code}-${district.district_code}`}
                      className="border-b border-gray-100 last:border-0 dark:border-gray-800/60"
                    >
                      <td className="py-1.5 pl-2">{districtLabel(district)}</td>
                      <td className="py-1.5 pr-2 tabular-nums">{district.snapshot_count}</td>
                      <td className="py-1.5 pr-2 tabular-nums">{district.reconciliation_count}</td>
                      <td className="py-1.5 pr-2">
                        {targets.length === 0 ? (
                          <span className="text-gray-400">—</span>
                        ) : (
                          <span className="font-mono text-[11px] text-amber-700 dark:text-amber-300">
                            {targets.join(", ")}
                          </span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          <label className="mt-2 flex items-start gap-2 text-xs text-gray-600 dark:text-gray-300">
            <input
              type="checkbox"
              checked={includeReconciliations}
              onChange={(event) => setIncludeReconciliations(event.target.checked)}
              className="mt-0.5"
            />
            <span>
              대조 결과 CSV도 같은 개수로 정리 — 반영이 끝나면 그 구의 대조 CSV는 자동으로
              지워지니까, 여기 남은 건 <strong>대조만 하고 반영하지 않은 구</strong>의 것이에요.
              스냅샷 두 개만 있으면 다시 만들 수 있는 파생물이라 지워도 돼요.
            </span>
          </label>

          <div className="mt-3 flex flex-wrap items-center gap-3 border-t border-gray-200 pt-3 dark:border-gray-800">
            <span className="text-xs text-gray-500 dark:text-gray-400">
              구별 {keep}개 유지 · 지울 파일 스냅샷 {totals.snapshots}개
              {includeReconciliations && ` + 대조 CSV ${totals.reconciliations}개`} (
              {totals.districts}개 구)
            </span>
            <button
              type="button"
              onClick={() => {
                setConfirm("");
                setShowDialog(true);
              }}
              disabled={busy || pruning || nothingToPrune}
              className="ml-auto rounded-md bg-gray-900 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50 dark:bg-gray-100 dark:text-gray-900"
            >
              정리 실행
            </button>
          </div>
        </>
      )}

      {busy && (
        <p className="mt-2 rounded-md bg-amber-50 p-3 text-xs text-amber-900 dark:bg-amber-950/30 dark:text-amber-100">
          대조나 반영이 도는 중에는 정리할 수 없어요. 반영은 대조가 남긴 스냅샷 파일을 읽으므로, 그
          사이 지우면 돌고 있는 동기화가 읽을 파일이 사라져요.
        </p>
      )}

      {result && (
        <p className="mt-3 rounded-md bg-emerald-50 p-3 text-xs text-emerald-900 dark:bg-emerald-950/30 dark:text-emerald-100">
          {result.deleted.length}개 파일을 지웠어요 (구별 {result.keep}개 유지). 지운 파일명은{" "}
          <code>{result.history_file}</code>에 남겼어요.
          {result.failed.length > 0 && (
            <>
              {" "}
              <strong>{result.failed.length}개는 지우지 못했어요:</strong>{" "}
              {result.failed.map((entry) => entry.file).join(", ")}. 이 파일들은 이력에도 적지
              않았어요.
            </>
          )}
        </p>
      )}

      {showDialog && retention && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
          <div className="w-full max-w-md rounded-lg bg-white p-5 dark:bg-gray-900">
            <h3 className="text-base font-bold">
              {totals.snapshots + totals.reconciliations}개 파일을 지웁니다
            </h3>
            <p className="mt-2 text-sm text-gray-600 dark:text-gray-300">
              구별로 최근 {keep}개만 남겨요. 지운 파일은 git 이력에 있어 <code>git show</code>로
              되찾을 수 있어요.
            </p>
            <ul className="mt-3 space-y-1 text-xs text-gray-600 dark:text-gray-300">
              <li>· 스냅샷 {totals.snapshots}개</li>
              <li>
                · 대조 CSV {includeReconciliations ? `${totals.reconciliations}개` : "정리 안 함"}
              </li>
              <li>· 대상 구 {totals.districts}개</li>
            </ul>
            <p className="mt-3 rounded bg-amber-50 p-2 text-xs text-amber-900 dark:bg-amber-950/30 dark:text-amber-100">
              남은 스냅샷이 다음 대조의 기준이 돼요.{" "}
              {keep === 1 && (
                <>
                  <strong>1개만 남기면 같은 날 두 번째 대조가 기준을 잃어요</strong> — 파일명이
                  날짜라 첫 대조가 만든 파일을 덮어쓰거든요.
                </>
              )}
            </p>
            <label className="mt-4 block text-xs text-gray-600 dark:text-gray-300">
              확인을 위해 <span className="font-mono font-bold">{PRUNE_CONFIRM}</span> 를
              입력하세요.
              <input
                aria-label="정리 확인 문자열"
                value={confirm}
                onChange={(event) => setConfirm(event.target.value)}
                className="mt-1 w-full rounded-md border border-gray-300 px-2 py-1.5 text-sm dark:border-gray-700 dark:bg-gray-950"
              />
            </label>
            <div className="mt-4 flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setShowDialog(false)}
                className="rounded-md border border-gray-300 px-3 py-1.5 text-sm dark:border-gray-700"
              >
                취소
              </button>
              <button
                type="button"
                disabled={confirm.trim() !== PRUNE_CONFIRM}
                onClick={() => {
                  setShowDialog(false);
                  onPrune({ includeReconciliations });
                }}
                className="rounded-md bg-gray-900 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50 dark:bg-gray-100 dark:text-gray-900"
              >
                실행
              </button>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
