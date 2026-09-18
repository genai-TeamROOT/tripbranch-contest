/*
 * 역할: 서울 25개 구를 한 번에 대조하고 반영하는 전 구 갱신 패널.
 * 입력: DeveloperOpsPage가 순회하며 채우는 구별 대조·반영 상태.
 * 출력: 구별 변경 건수 표, 예상 상세조회 합계, 남은 한도, 확인 다이얼로그, 진행 표시.
 * 호출 시점: DeveloperOpsPage가 렌더링될 때.
 *
 * 구 단위 패널(PlaceSyncPanel)과 같은 순서를 전 구 규모로 한 벌 더 얹는다 —
 * 1단계 대조는 구마다 목록 API를 1회씩만 써서 스냅샷을 남기고(DB 쓰기 없음),
 * 2단계 반영은 그 대조가 정한 대상에만 상세조회를 보낸다. 한 버튼으로 합치지
 * 않는 이유는 구 단위와 같다 — 무엇이 바뀌는지 모르는 채로 운영 DB에 쓰게 되고,
 * 25개 구 합계가 하루 한도를 넘는지도 누르기 전에는 알 수 없다.
 *
 * 스냅샷 CSV는 구마다 따로 남긴다. 25개 구를 한 파일로 합치면
 * `_require_snapshot_region()`이 "이 스냅샷의 구가 반영 대상 구와 같은가"를
 * 검사할 수 없고, 대조가 중간에 끊겼을 때 아직 담기지 않은 구가 "장소 0건"으로
 * 보여 반영 시 통째로 비활성화된다.
 *
 * 순회 중 한도를 넘길 구는 건너뛰고 다음 구로 간다. 거기서 멈추면 뒤쪽 구가
 * 영영 돌지 못한다 — 강남구는 상세 미완이 814건이라 혼자서 하루 한도를 다 쓴다.
 */

import { useMemo, useState } from "react";
import type { DbStatus, SyncDistrict } from "../../api/dev";
import {
  backfillUncheckedCount,
  barrierFreeCell,
  barrierFreeTotals,
  barrierFreeUncheckedCount,
  buildMappingCommand,
  plannedDetailCalls,
  remainingDetailBudget,
  reusedSnapshotDates,
  unmappedDistricts,
  type AllSyncEntry,
  type AllSyncOutcome,
  type AllSyncState,
} from "./allDistrictSync";

const OUTCOME_LABELS: Record<AllSyncOutcome, string> = {
  pending: "대기",
  reconciled: "대조 완료",
  running: "반영 중",
  success: "반영 완료",
  skipped: "건너뜀",
  failed: "실패",
};

const OUTCOME_TONES: Record<AllSyncOutcome, string> = {
  pending: "bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-300",
  reconciled: "bg-blue-100 text-blue-900 dark:bg-blue-950/50 dark:text-blue-100",
  running: "bg-blue-100 text-blue-900 dark:bg-blue-950/50 dark:text-blue-100",
  success: "bg-emerald-100 text-emerald-900 dark:bg-emerald-950/50 dark:text-emerald-100",
  skipped: "bg-amber-100 text-amber-900 dark:bg-amber-950/50 dark:text-amber-100",
  failed: "bg-red-100 text-red-900 dark:bg-red-950/50 dark:text-red-100",
};

function entryLabel(entry: AllSyncEntry) {
  return entry.districtName ?? `구 ${entry.districtCode}`;
}

/** 그 구가 실제로 쓴 상세조회 수. 아직 안 돌았으면 예상치를 보여준다. */
function detailCell(entry: AllSyncEntry): string {
  const attempted = entry.job?.result?.detail_attempted_count;
  if (typeof attempted === "number") return `${attempted}`;
  if (entry.reconcile === null) return "—";
  return `${plannedDetailCalls(entry.reconcile)} (예상)`;
}

export function AllDistrictSyncPanel({
  districts,
  state,
  detailCallsToday,
  busy,
  onReconcileAll,
  onReuseSnapshots,
  onApplyAll,
  onCancel,
}: {
  districts: SyncDistrict[];
  state: AllSyncState;
  /** 오늘 detailIntro2 사용량. 예산을 여기서 뺀다. */
  detailCallsToday: DbStatus["detail_calls_today"] | null;
  /** 구 단위 패널이 대조·반영 중이면 참. 동기화 job은 서버에서 한 번에 하나만 돈다. */
  busy: boolean;
  onReconcileAll: () => void;
  /** 저장된 스냅샷으로 대조만 다시 계산한다. 외부 호출이 0회다. */
  onReuseSnapshots: () => void;
  onApplyAll: () => void;
  onCancel: () => void;
}) {
  const [confirm, setConfirm] = useState("");
  const [showDialog, setShowDialog] = useState(false);

  const areaCode = districts[0]?.area_code ?? "11";
  const expectedConfirm = `${areaCode}-ALL`;

  const reconciled = useMemo(
    () => state.entries.filter((entry) => entry.reconcile !== null),
    [state.entries],
  );

  const totals = useMemo(() => {
    let added = 0;
    let removed = 0;
    let updated = 0;
    let detail = 0;
    let barrierFree = 0;
    let excluded = 0;
    for (const entry of reconciled) {
      const result = entry.reconcile;
      if (result === null) continue;
      added += result.counts.added;
      removed += result.counts.removed;
      updated += result.counts.updated;
      detail += plannedDetailCalls(result);
      // 확인하지 못한 구는 합계에 넣지 않는다. 0을 더하면 "부를 게 없다"와
      // "안 봤다"가 같은 수로 뭉개진다.
      if (result.barrier_free_checked) barrierFree += result.barrier_free_detail_count;
      excluded += result.detail_excluded_ids.length;
    }
    return { added, removed, updated, detail, barrierFree, excluded };
  }, [reconciled]);

  const budget = remainingDetailBudget(detailCallsToday);
  const listCalls = districts.reduce((sum, district) => sum + district.list_call_estimate, 0);
  const running = state.phase === "reconciling" || state.phase === "applying";
  const skipped = state.entries.filter((entry) => entry.outcome === "skipped");
  const failed = state.entries.filter((entry) => entry.outcome === "failed");
  const succeeded = state.entries.filter((entry) => entry.outcome === "success");
  const unmapped = useMemo(() => unmappedDistricts(state.entries), [state.entries]);
  const barrierFreeUnchecked = barrierFreeUncheckedCount(state.entries);
  const backfillUnchecked = backfillUncheckedCount(state.entries);
  const barrierFreeDone = barrierFreeTotals(state.entries);
  const reusedDates = useMemo(() => reusedSnapshotDates(state.entries), [state.entries]);
  const unmappedCount = unmapped.reduce((sum, district) => sum + district.count, 0);

  return (
    <section className="rounded-lg border border-gray-200 bg-white p-4 dark:border-gray-800 dark:bg-gray-900">
      <header className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="text-base font-bold text-gray-950 dark:text-gray-50">전 구 갱신</h2>
          <p className="mt-0.5 text-xs text-gray-500 dark:text-gray-400">
            적재된 {districts.length}개 구를 구 코드 순으로 하나씩 대조하고 반영해요. 갱신 대상은
            전체 장소가 아니라 변경분과 지난 실행에서 상세를 못 채운 장소예요.
          </p>
        </div>
        <div className="flex gap-2">
          {running ? (
            <button
              type="button"
              onClick={onCancel}
              className="rounded-md border border-red-300 px-3 py-1.5 text-sm text-red-700 dark:border-red-900 dark:text-red-300"
            >
              중단
            </button>
          ) : (
            <>
              {/* 저장된 스냅샷이 있으면 목록을 다시 받을 이유가 없다. 대조 결과는
                  스냅샷 두 장에서 순수하게 계산되므로 외부 호출이 0회다. 오늘
                  상세조회 한도가 없어 반영을 못 하고 다음 날 이어서 할 때 쓴다. */}
              <button
                type="button"
                onClick={onReuseSnapshots}
                disabled={busy || districts.length === 0}
                className="rounded-md border border-gray-300 px-3 py-1.5 text-sm disabled:opacity-50 dark:border-gray-700"
              >
                저장된 스냅샷 쓰기
              </button>
              <button
                type="button"
                onClick={onReconcileAll}
                disabled={busy || districts.length === 0}
                className="rounded-md border border-gray-300 px-3 py-1.5 text-sm disabled:opacity-50 dark:border-gray-700"
              >
                1. 전 구 대조
              </button>
            </>
          )}
        </div>
      </header>

      <p className="mt-3 rounded-md border border-amber-300 bg-amber-50 p-3 text-xs text-amber-900 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-100">
        <strong>대조는 DB를 바꾸지 않아요.</strong> 구마다 목록 API를 부르고(합계 약 {listCalls}회)
        무장애 목록을 1회씩 불러 스냅샷 CSV를 남길 뿐이에요. 상세조회는 2단계 반영에서만 나갑니다.
      </p>

      {reusedDates.length > 0 && (
        <p className="mt-3 rounded-md border border-blue-300 bg-blue-50 p-3 text-xs text-blue-900 dark:border-blue-900 dark:bg-blue-950/30 dark:text-blue-100">
          <strong>저장된 스냅샷을 다시 읽었어요 — 외부 호출 0회.</strong> 목록 날짜는{" "}
          {reusedDates.join(", ")}예요. 그 뒤에 생기거나 사라진 장소는 이번 반영에 들어가지 않고{" "}
          <strong>다음 대조로 넘어가요</strong> — 놓치는 게 아니라 밀리는 거예요. 무장애 예상
          호출수는 목록을 불러야 셀 수 있어서 세지 않았어요.
        </p>
      )}

      {state.error && (
        <p className="mt-3 rounded-md bg-red-50 p-3 text-xs text-red-900 dark:bg-red-950/40 dark:text-red-100">
          {state.error}
        </p>
      )}

      {running && (
        <div className="mt-3 rounded-md border border-gray-200 p-3 dark:border-gray-800">
          <div className="flex items-center justify-between text-xs">
            <span className="font-semibold">
              {state.phase === "reconciling" ? "대조 중" : "반영 중"} ·{" "}
              {entryLabel(
                state.entries[Math.min(state.cursor, state.entries.length - 1)] ?? state.entries[0],
              )}
            </span>
            <span className="tabular-nums text-gray-500">
              {state.cursor} / {state.entries.length}
            </span>
          </div>
          <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-gray-200 dark:bg-gray-700">
            <div
              className="h-full bg-blue-500"
              style={{
                width: `${
                  state.entries.length > 0 ? (state.cursor / state.entries.length) * 100 : 0
                }%`,
              }}
            />
          </div>
        </div>
      )}

      {state.entries.length > 0 && (
        <div className="mt-3 max-h-96 overflow-y-auto rounded-md border border-gray-200 dark:border-gray-800">
          <table className="w-full text-left text-xs">
            <thead className="sticky top-0 bg-gray-50 dark:bg-gray-950">
              <tr className="border-b border-gray-200 dark:border-gray-800">
                <th className="py-1.5 pl-2 font-medium">구</th>
                <th className="py-1.5 pr-2 font-medium">신규</th>
                <th className="py-1.5 pr-2 font-medium">수정</th>
                <th className="py-1.5 pr-2 font-medium">삭제</th>
                <th className="py-1.5 pr-2 font-medium">상세조회</th>
                <th className="py-1.5 pr-2 font-medium">무장애</th>
                {/* 집중률 매핑이 없는 신규 장소. 건수만 보여준다 — 25줄짜리 표에
                    content_id를 늘어놓으면 표를 읽을 수 없다. 어느 구에 무슨
                    명령을 돌려야 하는지는 순회가 끝난 뒤 아래에서 알린다. */}
                <th className="py-1.5 pr-2 font-medium">미매핑</th>
                <th className="py-1.5 pr-2 font-medium">상태</th>
              </tr>
            </thead>
            <tbody>
              {state.entries.map((entry) => (
                <tr
                  key={`${entry.areaCode}-${entry.districtCode}`}
                  className="border-b border-gray-100 last:border-0 dark:border-gray-800/60"
                >
                  <td className="py-1.5 pl-2">
                    {entryLabel(entry)}{" "}
                    <span className="font-mono text-[11px] text-gray-400">
                      {entry.areaCode}-{entry.districtCode}
                    </span>
                  </td>
                  <td className="py-1.5 pr-2 tabular-nums">
                    {entry.reconcile?.counts.added ?? "—"}
                  </td>
                  <td className="py-1.5 pr-2 tabular-nums">
                    {entry.reconcile?.counts.updated ?? "—"}
                  </td>
                  <td className="py-1.5 pr-2 tabular-nums">
                    {entry.reconcile?.counts.removed ?? "—"}
                  </td>
                  <td className="py-1.5 pr-2 tabular-nums">{detailCell(entry)}</td>
                  <td className="py-1.5 pr-2 tabular-nums">{barrierFreeCell(entry)}</td>
                  <td className="py-1.5 pr-2 tabular-nums">
                    {entry.job === null ? "—" : entry.job.unmapped_new_place_ids.length || "—"}
                  </td>
                  <td className="py-1.5 pr-2">
                    <span
                      className={`rounded px-1.5 py-0.5 font-semibold ${OUTCOME_TONES[entry.outcome]}`}
                    >
                      {OUTCOME_LABELS[entry.outcome]}
                    </span>
                    {(entry.skipReason ?? entry.reconcileError ?? entry.applyError) && (
                      <span className="ml-1.5 text-[11px] text-gray-500 dark:text-gray-400">
                        {entry.skipReason ?? entry.reconcileError ?? entry.applyError}
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {reconciled.length > 0 && (
        <>
          <dl className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
            {[
              ["신규", totals.added],
              ["수정", totals.updated],
              ["삭제", totals.removed],
              ["상세조회 합계", totals.detail],
            ].map(([label, value]) => (
              <div key={String(label)} className="rounded-md bg-gray-100 p-2.5 dark:bg-gray-800">
                <dt className="text-[11px] text-gray-500 dark:text-gray-400">{label}</dt>
                <dd className="text-lg font-bold tabular-nums">{value}</dd>
              </div>
            ))}
          </dl>

          <p className="mt-2 text-xs text-gray-500 dark:text-gray-400">
            무장애 상세(detailWithTour2)는 별도 오퍼레이션이라 한도도 따로예요 — 합계{" "}
            {totals.barrierFree}회.
            {barrierFreeUnchecked > 0 && (
              <>
                {" "}
                <strong>
                  {barrierFreeUnchecked}개 구는 목록을 확인하지 못해 이 합계에 안 들어갔어요
                </strong>{" "}
                — 저장된 스냅샷을 재사용하면 무장애 목록을 부르지 않아요. 반영할 때는 정상적으로
                조회하고 채웁니다.
              </>
            )}
            {totals.excluded > 0 &&
              ` 수정시각이 그대로라 상세조회에서 제외한 장소가 ${totals.excluded}건 있어요 (구 단위 패널에서 구별로 포함시킬 수 있어요).`}
            {backfillUnchecked > 0 && (
              <>
                {" "}
                <strong>
                  {backfillUnchecked}개 구는 지난 실행에서 못 채운 건을 확인하지 못했어요
                </strong>{" "}
                — 상세조회 합계가 실제보다 적을 수 있어요.
              </>
            )}
          </p>

          {/* 남은 한도는 어림이다. 빼는 쪽 사용량이 하한이라 실제 잔여는 이보다
           * 적을 수 있고, 그래서 예산이 남았는데도 서버가 한도 소진을 돌려줄 수
           * 있다. 그 경우는 순회가 quotaExhausted로 갈아타 남은 구를 건너뛴다. */}
          <p
            className={`mt-2 rounded-md p-3 text-xs ${
              budget !== null && totals.detail > budget
                ? "border border-amber-300 bg-amber-50 text-amber-900 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-100"
                : "bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-300"
            }`}
          >
            {budget === null ? (
              <>일일 한도 설정을 읽지 못했어요. 예산 없이 전 구를 순회합니다.</>
            ) : (
              <>
                오늘 남은 상세조회 한도는 <strong>약 {budget}회</strong>예요 (한도{" "}
                {detailCallsToday?.daily_limit} · 기록된 사용량 {detailCallsToday?.count}). 재시도는
                세지 않아 실제 잔여는 이보다 적을 수 있어요.
                {totals.detail > budget && (
                  <>
                    {" "}
                    <strong>합계가 남은 한도를 넘어요.</strong> 순회는 한도를 넘길 구를 건너뛰고
                    다음 구로 가며, 건너뛴 구는 표에 "건너뜀"으로 남아 다음날 다시 실행하면 돼요.
                  </>
                )}
              </>
            )}
          </p>

          {state.phase !== "applying" && (
            <div className="mt-3 flex items-center justify-end border-t border-gray-200 pt-3 dark:border-gray-800">
              <button
                type="button"
                onClick={() => {
                  setConfirm("");
                  setShowDialog(true);
                }}
                disabled={busy || running}
                className="rounded-md bg-gray-900 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50 dark:bg-gray-100 dark:text-gray-900"
              >
                2. 전 구 반영
              </button>
            </div>
          )}
        </>
      )}

      {state.phase === "done" && (
        <p className="mt-3 rounded-md bg-emerald-50 p-3 text-xs text-emerald-900 dark:bg-emerald-950/30 dark:text-emerald-100">
          순회를 마쳤어요. 반영 {succeeded.length}개 구 · 건너뜀 {skipped.length}개 구 · 실패{" "}
          {failed.length}개 구. 이번 순회가 쓴 상세조회는 {state.spentDetailCalls}회예요.
          {barrierFreeDone.attempted > 0 && (
            <>
              {" "}
              무장애 정보는 {barrierFreeDone.attempted}회 불러 {barrierFreeDone.stored}건을 채웠어요
              — 목록에 있어도 항목이 전부 빈 장소가 있어 두 수가 달라요.
            </>
          )}
          {state.quotaExhausted &&
            " 도중에 오늘 한도가 소진돼 남은 구는 상세조회 없이 건너뛰었어요."}
          {skipped.length > 0 && (
            <>
              {" "}
              <strong>건너뛴 구는 대조부터 다시 하세요.</strong> 지금 표에 있는 스냅샷은 오늘
              목록이라, 날이 바뀐 뒤 그대로 반영하면 그 사이의 변경이 빠진 목록으로 DB를 맞추게
              돼요.
            </>
          )}
        </p>
      )}

      {state.phase === "done" && unmapped.length > 0 && (
        <div className="mt-3 rounded-md border border-amber-300 bg-amber-50 p-3 text-xs text-amber-900 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-100">
          <p>
            <strong>
              집중률 매핑이 없는 신규 장소가 {unmapped.length}개 구에 {unmappedCount}건 생겼어요.
            </strong>{" "}
            매핑이 없으면 그 장소는 혼잡도 조회를 <strong>통째로 건너뜁니다</strong> — 오류 없이 그
            장소만 판정에서 빠져요. 이 동기화는 매핑 테이블을 건드리지 않으니 아래를 실행하세요.
          </p>
          {/* 구 코드를 5자리로 붙여서 낸다. 집중률 API는 11110을 쓰고 places는 뒤
              3자리만 담아서, 표에 보이는 11-110을 그대로 치면 스크립트가 받지 않는다. */}
          <pre className="mt-2 overflow-x-auto rounded bg-amber-100/60 p-2 font-mono text-[11px] dark:bg-amber-950/40">
            {unmapped
              .map(
                (district) =>
                  `# ${district.label} (${district.count}건)\n${buildMappingCommand(district)}`,
              )
              .join("\n")}
          </pre>
          <p className="mt-2">
            만든 CSV는 <code>python -m scripts.import_concentration_mappings</code> 로 적재해야
            테이블에 들어가요.
          </p>
        </div>
      )}

      {showDialog && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
          <div className="w-full max-w-md rounded-lg bg-white p-5 dark:bg-gray-900">
            <h3 className="text-base font-bold">{reconciled.length}개 구를 반영합니다</h3>
            <p className="mt-2 text-sm text-gray-600 dark:text-gray-300">
              운영 Supabase의 places 테이블을 구마다 실제로 변경해요. 목록에서 사라진 장소는
              비활성화됩니다.
            </p>
            <ul className="mt-3 space-y-1 text-xs text-gray-600 dark:text-gray-300">
              <li>· 대상: {reconciled.map((entry) => entryLabel(entry)).join(", ")}</li>
              <li>
                · 상세조회 최대 {totals.detail}회 (TourAPI detailIntro2, 남은 한도{" "}
                {budget ?? "확인 못 함"})
              </li>
              <li>· 무장애 상세 {totals.barrierFree}회 + 구별 목록 1회씩</li>
              <li>
                · 신규 {totals.added} / 수정 {totals.updated} / 삭제 {totals.removed}
              </li>
            </ul>
            <p className="mt-3 rounded bg-amber-50 p-2 text-xs text-amber-900 dark:bg-amber-950/30 dark:text-amber-100">
              구를 하나씩 순서대로 돌려요. 상세조회 상한은 걸지 않으므로 실행된 구는 비활성화
              판정까지 정상으로 끝나요. <strong>이 탭을 닫으면 순회가 거기서 멈춰요</strong> — 이미
              반영된 구는 그대로 남고, 돌고 있던 구의 job은 서버에서 끝까지 갑니다.
            </p>
            <label className="mt-4 block text-xs text-gray-600 dark:text-gray-300">
              확인을 위해 <span className="font-mono font-bold">{expectedConfirm}</span> 를
              입력하세요.
              <input
                aria-label="전 구 확인 문자열"
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
                disabled={confirm.trim() !== expectedConfirm}
                onClick={() => {
                  setShowDialog(false);
                  onApplyAll();
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
