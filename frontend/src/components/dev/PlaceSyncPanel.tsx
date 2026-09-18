/*
 * 역할: 장소 DB 동기화를 구 선택 → 대조 → 반영 순서로 실행한다.
 * 입력: /api/dev/place-sync/{districts,reconcile,apply,jobs}.
 * 출력: 구 드롭다운, 대조 결과 표, 예상 호출수, 확인 다이얼로그, 진행바, 결과 카드.
 * 호출 시점: DeveloperOpsPage가 렌더링될 때.
 *
 * 대조는 목록 API 1회로 스냅샷을 남기고 비교만 한다(DB에 쓰지 않음). 반영은 그
 * 대조가 정한 대상에만 상세조회를 보낸다. 한 버튼으로 합치면 무엇이 바뀌는지
 * 모르는 채로 운영 DB에 쓰게 된다.
 *
 * dry-run 선택지는 두지 않는다. DB 쓰기만 막을 뿐 상세조회는 그대로 나가 한도를
 * 똑같이 쓰는데 결과는 남지 않는다. 같은 비용이면 상세조회 상한을 건 실제 실행이
 * 낫다 — 검증 효과는 같고 채운 값은 남으며, 상한이 걸린 실행은 비활성화도 건너뛴다.
 * (`scripts/sync_places.py --dry-run`은 그대로 있다.)
 *
 * 구를 드롭다운으로 고른다. 목록에 없는 구는 "구 추가"로 코드를 넣어 쓰되, 어디에도
 * 저장하지 않는다 — 한 번 대조하면 스냅샷 파일이, 반영하면 places 행이 생겨 자료
 * 자체가 다음부터의 목록이 된다. 따로 저장하면 자료 없이 이름만 남은 구가 쌓인다.
 */

import { useMemo, useState } from "react";
import type {
  DbStatus,
  KnownDistrict,
  ReconcileResult,
  ReconcileRow,
  SyncDistrict,
  SyncDistricts,
  SyncJob,
} from "../../api/dev";

const CHANGE_LABELS: Record<ReconcileRow["change_type"], string> = {
  added: "신규",
  removed: "삭제",
  updated: "수정",
};

const CHANGE_TONES: Record<ReconcileRow["change_type"], string> = {
  added: "bg-emerald-100 text-emerald-900 dark:bg-emerald-950/50 dark:text-emerald-100",
  removed: "bg-red-100 text-red-900 dark:bg-red-950/50 dark:text-red-100",
  updated: "bg-amber-100 text-amber-900 dark:bg-amber-950/50 dark:text-amber-100",
};

const PHASE_LABELS: Record<string, string> = {
  list: "목록 수집",
  upsert: "목록 반영",
  reparse: "운영시간 재파싱",
  details: "상세조회",
  barrier_free: "무장애 정보",
  deactivate: "비활성화",
  done: "완료",
};

function districtKey(district: { area_code: string; district_code: string }) {
  return `${district.area_code}-${district.district_code}`;
}

function districtLabel(district: SyncDistrict) {
  const name = district.district_name ?? `구 ${district.district_code}`;
  const state =
    district.place_count > 0
      ? `DB ${district.place_count}건`
      : district.latest_snapshot
        ? "DB 없음 · 스냅샷 있음"
        : "자료 없음";
  return `${name} ${districtKey(district)} · ${state}`;
}

function ChangeRows({ rows }: { rows: ReconcileRow[] }) {
  if (rows.length === 0) {
    return <p className="text-xs text-gray-500">변경된 장소가 없습니다.</p>;
  }
  return (
    <div className="max-h-64 overflow-y-auto rounded-md border border-gray-200 dark:border-gray-800">
      <table className="w-full text-left text-xs">
        <tbody>
          {rows.map((row) => (
            <tr
              key={`${row.change_type}-${row.content_id}`}
              className="border-b border-gray-100 last:border-0 dark:border-gray-800/60"
            >
              <td className="w-14 py-1.5 pl-2">
                <span
                  className={`rounded px-1.5 py-0.5 font-semibold ${CHANGE_TONES[row.change_type]}`}
                >
                  {CHANGE_LABELS[row.change_type]}
                </span>
              </td>
              <td className="py-1.5 pr-2">{row.title}</td>
              <td className="py-1.5 pr-2 font-mono text-[11px] text-gray-400">
                {row.content_id}
              </td>
              <td className="py-1.5 pr-2 font-mono text-[11px] text-gray-500">
                {row.changed_columns.join(", ")}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** 목록에 없는 구를 코드로 넣는다. 사전에 없는 코드는 받지 않는다 —
 *  그런 코드로 동기화를 걸면 TourAPI가 빈 목록을 주고, 그 결과는
 *  "장소가 0건인 구"와 구분되지 않는다. */
function DistrictPicker({
  options,
  known,
  selected,
  disabled,
  onSelect,
  onAdd,
}: {
  options: SyncDistrict[];
  known: KnownDistrict[];
  selected: SyncDistrict | null;
  disabled: boolean;
  onSelect: (district: SyncDistrict) => void;
  onAdd: (district: SyncDistrict) => void;
}) {
  const [adding, setAdding] = useState(false);
  const [code, setCode] = useState("");
  const [addError, setAddError] = useState<string | null>(null);

  function submitCode() {
    const trimmed = code.trim();
    const match = known.find((district) => district.district_code === trimmed);
    if (!match) {
      setAddError(`시군구 사전에 없는 코드예요: ${trimmed || "(비어 있음)"}`);
      return;
    }
    const existing = options.find(
      (option) => districtKey(option) === districtKey(match),
    );
    if (existing) {
      onSelect(existing);
    } else {
      onAdd({
        ...match,
        place_count: 0,
        active_count: 0,
        latest_snapshot: null,
        // 자료가 없어 쪽수를 어림할 근거가 없다. 최소 1회는 확실하다.
        list_call_estimate: 1,
      });
    }
    setAdding(false);
    setCode("");
    setAddError(null);
  }

  return (
    <div className="flex flex-wrap items-center gap-2">
      <label className="flex items-center gap-1.5 text-xs">
        <span className="text-gray-500 dark:text-gray-400">대상 구</span>
        <select
          aria-label="대상 구"
          value={selected ? districtKey(selected) : ""}
          disabled={disabled || options.length === 0}
          onChange={(event) => {
            const next = options.find(
              (option) => districtKey(option) === event.target.value,
            );
            if (next) onSelect(next);
          }}
          className="rounded-md border border-gray-300 px-2 py-1 text-xs disabled:opacity-50 dark:border-gray-700 dark:bg-gray-950"
        >
          {options.length === 0 && <option value="">불러오는 중…</option>}
          {options.map((option) => (
            <option key={districtKey(option)} value={districtKey(option)}>
              {districtLabel(option)}
            </option>
          ))}
        </select>
      </label>

      {adding ? (
        <span className="flex flex-wrap items-center gap-1.5">
          <input
            aria-label="추가할 구 코드"
            value={code}
            onChange={(event) => setCode(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") submitCode();
            }}
            placeholder="200"
            className="w-24 rounded-md border border-gray-300 px-2 py-1 text-xs dark:border-gray-700 dark:bg-gray-950"
          />
          <button
            type="button"
            onClick={submitCode}
            className="rounded-md border border-gray-300 px-2 py-1 text-xs dark:border-gray-700"
          >
            추가 완료
          </button>
          <button
            type="button"
            onClick={() => {
              setAdding(false);
              setCode("");
              setAddError(null);
            }}
            className="rounded-md px-1.5 py-1 text-xs text-gray-500"
          >
            취소
          </button>
        </span>
      ) : (
        <button
          type="button"
          onClick={() => setAdding(true)}
          disabled={disabled}
          className="rounded-md border border-gray-300 px-2 py-1 text-xs disabled:opacity-50 dark:border-gray-700"
        >
          구 추가
        </button>
      )}

      {addError && (
        <span className="text-xs text-red-600 dark:text-red-400">{addError}</span>
      )}
    </div>
  );
}

function JobProgress({ job }: { job: SyncJob }) {
  const ratio = job.total > 0 ? Math.min(1, job.processed / job.total) : 0;
  const running = job.status === "running";
  return (
    <section className="mt-3 rounded-md border border-gray-200 p-3 dark:border-gray-800">
      <div className="flex items-center justify-between text-xs">
        <span className="font-semibold">
          {running ? "실행 중" : `종료: ${job.status}`} ·{" "}
          {PHASE_LABELS[job.phase] ?? job.phase}
          {job.params.dry_run ? " (dry-run)" : ""} · {job.params.area_code}-
          {job.params.district_code}
        </span>
        <span className="tabular-nums text-gray-500">
          {job.processed} / {job.total}
        </span>
      </div>
      <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-gray-200 dark:bg-gray-700">
        <div
          className={`h-full ${running ? "bg-blue-500" : "bg-emerald-500"}`}
          style={{ width: `${ratio * 100}%` }}
        />
      </div>
      {job.params.details_limit !== null && (
        <p className="mt-2 rounded bg-amber-50 p-2 text-xs text-amber-900 dark:bg-amber-950/30 dark:text-amber-100">
          상세조회를 {job.params.details_limit}건으로 제한한 실행이라{" "}
          <strong>비활성화를 건너뜁니다</strong>. 목록을 다 처리하지 못했으니 "사라진
          장소"를 판정할 수 없어요.
        </p>
      )}
      {job.error && (
        <p className="mt-2 rounded bg-red-50 p-2 text-xs text-red-900 dark:bg-red-950/40 dark:text-red-100">
          {job.error}
        </p>
      )}
      {job.unmapped_new_place_ids.length > 0 && (
        <p className="mt-2 rounded bg-amber-50 p-2 text-xs text-amber-900 dark:bg-amber-950/30 dark:text-amber-100">
          집중률 매핑이 없는 신규 장소 {job.unmapped_new_place_ids.length}건 (
          {job.unmapped_new_place_ids.slice(0, 5).join(", ")}
          {job.unmapped_new_place_ids.length > 5 ? " …" : ""}). 매핑이 없는 장소는 혼잡도
          조회를 <strong>건너뜁니다</strong> — 오류 없이 그 장소만 판정에서 빠져요.
          <code className="ml-1">python -m scripts.build_concentration_mappings</code> 를
          실행해 매핑을 갱신하세요. 이 동기화는 매핑 테이블을 건드리지 않아요.
        </p>
      )}
      {job.result && job.params.dry_run && (
        <p className="mt-2 rounded bg-amber-50 p-2 text-xs text-amber-900 dark:bg-amber-950/30 dark:text-amber-100">
          <strong>dry-run이라 DB에는 아무것도 쓰지 않았어요.</strong> 아래 신규는
          "반영했다면 새로 들어갔을 장소"이고, 장소 DB 상태 패널도 바뀌지 않아요.
          비활성은 아예 판정하지 않았습니다. 다만{" "}
          <strong>
            상세조회 {job.result.detail_attempted_count}회는 실제로 나가 오늘 한도를
            그만큼 썼어요
          </strong>{" "}
          — 그 결과는 어디에도 저장되지 않았습니다.
        </p>
      )}
      {job.result && (
        <dl className="mt-2 grid grid-cols-3 gap-2 text-xs sm:grid-cols-7">
          {[
            ["처리", job.result.processed_count],
            ["신규", job.result.new_count],
            // "값이 바뀐 수"가 아니라 "목록에 있던 장소 중 DB에 이미 있던 수"다.
            ["기존", job.result.updated_count],
            // dry-run은 비활성화 판정 자체를 건너뛴다. 0으로 보이면 "사라진 장소가
            // 없다"로 읽히지만 실제로는 보지도 않았다.
            ["비활성", job.params.dry_run ? "미판정" : job.result.deactivated_count],
            ["상세조회", job.result.detail_attempted_count],
            // 부른 수가 아니라 값이 있어 저장된 수다. 무장애 목록에 있어도 15개
            // 필드가 전부 빈 장소가 있어 둘이 다르다.
            [
              "무장애",
              `${job.result.barrier_free_stored_count}/${job.result.barrier_free_attempted_count}`,
            ],
            ["실패", job.result.failed_count],
          ].map(([label, value]) => (
            <div key={String(label)} className="rounded bg-gray-100 p-1.5 dark:bg-gray-800">
              <dt className="text-[11px] text-gray-500">{label}</dt>
              <dd className="font-semibold tabular-nums">{value}</dd>
            </div>
          ))}
        </dl>
      )}
    </section>
  );
}

export function PlaceSyncPanel({
  districts,
  selected,
  reconcile,
  job,
  error,
  reconciling,
  applying,
  detailCallsToday,
  busy,
  onSelectDistrict,
  onReconcile,
  onApply,
}: {
  districts: SyncDistricts | null;
  selected: SyncDistrict | null;
  reconcile: ReconcileResult | null;
  job: SyncJob | null;
  error: string | null;
  reconciling: boolean;
  applying: boolean;
  /** 오늘 detailIntro2 사용량. place_sync_runs에서 센 값이라 서버 재시작과
   *  scripts 실행을 견디지만, 여전히 하한이다. 집계가 없으면 null. */
  detailCallsToday: DbStatus["detail_calls_today"] | null;
  /** 전 구 순회처럼 이 패널 밖의 실행이 돌고 있는지. 동기화 job은 서버에서 한
   *  번에 하나만 돌아서, 여기서 막지 않으면 409로 거부되는 버튼을 누르게 된다. */
  busy: boolean;
  onSelectDistrict: (district: SyncDistrict) => void;
  onReconcile: () => void;
  onApply: (input: {
    confirm: string;
    includeExcluded: boolean;
    detailsLimit: number | null;
  }) => void;
}) {
  const [includeExcluded, setIncludeExcluded] = useState(false);
  const [limitInput, setLimitInput] = useState("");
  const [confirm, setConfirm] = useState("");
  const [showDialog, setShowDialog] = useState(false);
  const [added, setAdded] = useState<SyncDistrict[]>([]);

  // 서버가 준 목록에 이번 세션에서 추가한 구를 얹는다. 이미 자료가 생긴 구는
  // 서버 쪽 항목이 남아야 건수가 보인다.
  const options = useMemo(() => {
    const merged = [...(districts?.loaded ?? [])];
    for (const extra of added) {
      if (!merged.some((option) => districtKey(option) === districtKey(extra))) {
        merged.push(extra);
      }
    }
    return merged;
  }, [districts, added]);

  const expectedConfirm = reconcile
    ? `${reconcile.area_code}-${reconcile.district_code}`
    : "";
  const changedCount =
    (reconcile?.detail_content_ids.length ?? 0) +
    (includeExcluded ? (reconcile?.detail_excluded_ids.length ?? 0) : 0);
  // 반영은 변경분과 함께 지난 실행에서 못 채운 건도 부른다. 빼고 세면 화면이
  // 실제보다 훨씬 적은 수를 보여준다.
  const backfillCount = reconcile?.detail_backfill_ids.length ?? 0;
  const detailCount = changedCount + backfillCount;
  const parsedLimit = limitInput.trim() === "" ? null : Number(limitInput.trim());
  const detailsLimit =
    parsedLimit !== null && Number.isFinite(parsedLimit) && parsedLimit >= 1
      ? Math.floor(parsedLimit)
      : null;
  const plannedCalls = detailsLimit === null ? detailCount : Math.min(detailCount, detailsLimit);
  // 대조가 무장애 목록을 1회 불러 낸 실제 대상 수다. 상한을 걸어 실행하면
  // 상세조회와 같은 상한이 무장애 호출에도 함께 걸린다.
  const barrierFreeTargets = reconcile?.barrier_free_detail_count ?? 0;
  const barrierFreeCalls =
    detailsLimit === null ? barrierFreeTargets : Math.min(barrierFreeTargets, detailsLimit);
  // 이 패널의 job이든 전 구 순회든, 무언가 돌고 있으면 조작을 막는다.
  const locked = job?.status === "running" || busy;
  const isNewDistrict =
    selected !== null && selected.place_count === 0 && selected.latest_snapshot === null;

  return (
    <section className="rounded-lg border border-gray-200 bg-white p-4 dark:border-gray-800 dark:bg-gray-900">
      <header className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="text-base font-bold text-gray-950 dark:text-gray-50">DB 갱신</h2>
          <p className="mt-0.5 text-xs text-gray-500 dark:text-gray-400">
            1단계 대조는 목록 API 1회로 스냅샷만 남겨요 (DB 변경 없음). 2단계 반영은
            변경된 장소에만 상세조회를 보내요.
          </p>
        </div>
        <button
          type="button"
          onClick={onReconcile}
          disabled={reconciling || locked || selected === null}
          className="rounded-md border border-gray-300 px-3 py-1.5 text-sm disabled:opacity-50 dark:border-gray-700"
        >
          {reconciling ? "대조 중…" : "1. 스냅샷 대조"}
        </button>
      </header>

      <div className="mt-3 border-t border-gray-200 pt-3 dark:border-gray-800">
        <DistrictPicker
          options={options}
          known={districts?.known ?? []}
          selected={selected}
          disabled={reconciling || locked}
          onSelect={onSelectDistrict}
          onAdd={(district) => {
            setAdded((current) => [...current, district]);
            onSelectDistrict(district);
          }}
        />
        {selected && (
          /* areaBasedList2도 오퍼레이션 단위로 일일 한도가 걸려 있다(2026-08-07
           * 소진). 한 번에 1회라 작아 보이지만 구를 바꿔가며 누르면 그만큼 쌓인다. */
          <p className="mt-1.5 text-xs text-gray-500 dark:text-gray-400">
            대조는 목록 API를 {selected.list_call_estimate}회 써요
            {isNewDistrict && " (자료가 없는 구라 어림값이에요)"}. 반영은 이 스냅샷을
            다시 쓰므로 목록을 부르지 않아요.
          </p>
        )}
      </div>

      {isNewDistrict && (
        <p className="mt-3 rounded-md border border-amber-300 bg-amber-50 p-3 text-xs text-amber-900 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-100">
          <strong>자료가 없는 구예요.</strong> 기준으로 삼을 스냅샷도 DB 행도 없어서
          목록 전량이 신규로 잡혀요. 그 구의 장소 수만큼 <code>detailIntro2</code>를
          쓰게 되니(중구는 892건, 용산구는 486건이었어요) 아래 상세조회 상한을 함께
          쓰는 걸 권해요.
        </p>
      )}

      <p className="mt-3 rounded-md border border-amber-300 bg-amber-50 p-3 text-xs text-amber-900 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-100">
        <strong>일일 호출 한도를 크게 소모할 수 있어요.</strong> 상세조회는 장소 1건당
        TourAPI <code>detailIntro2</code> 1회예요. 변경분이 많으면 한 번의 반영으로
        오늘 한도(1,000회)가 소진돼 그 뒤로는 추천·상세조회가 전부 실패합니다. 다른
        테스트와 시연을 모두 끝낸 뒤 마지막에 실행하는 걸 권해요.
      </p>

      {error && (
        <p className="mt-3 rounded-md bg-red-50 p-3 text-xs text-red-900 dark:bg-red-950/40 dark:text-red-100">
          {error}
        </p>
      )}

      {reconcile && (
        <>
          <p className="mt-3 text-xs text-gray-500 dark:text-gray-400">
            스냅샷 <span className="font-mono">{reconcile.snapshot}</span> (
            {reconcile.snapshot_count}건) · 기준{" "}
            <span className="font-mono">{reconcile.baseline ?? "없음"}</span>
            {reconcile.baseline_count !== undefined && ` (${reconcile.baseline_count}건)`}
          </p>

          {reconcile.baseline_source === "database" && (
            <p className="mt-2 rounded-md bg-blue-50 p-3 text-xs text-blue-900 dark:bg-blue-950/30 dark:text-blue-100">
              스냅샷 파일이 없어 <strong>places 테이블로 기준을 만들었어요.</strong> 이
              기준은 파일로 남지 않아요 — 날짜가 오늘과 겹치면 이번 대조가 쓰는 파일에
              덮어써지거든요. 오늘 저장된 스냅샷이 다음 대조의 기준이 됩니다.
            </p>
          )}

          {reconcile.message && (
            <p className="mt-2 rounded-md bg-amber-50 p-3 text-xs text-amber-900 dark:bg-amber-950/30 dark:text-amber-100">
              {reconcile.message}
            </p>
          )}

          {!reconcile.detail_backfill_checked && (
            <p className="mt-2 rounded-md bg-amber-50 p-3 text-xs text-amber-900 dark:bg-amber-950/30 dark:text-amber-100">
              상세를 못 채운 장소가 DB에 얼마나 있는지 확인하지 못했어요. 반영은 그
              장소들도 함께 부르므로, 아래 예상 호출수보다 실제가 많을 수 있어요.
            </p>
          )}

          {!reconcile.barrier_free_checked && (
            <p className="mt-2 rounded-md bg-amber-50 p-3 text-xs text-amber-900 dark:bg-amber-950/30 dark:text-amber-100">
              무장애 목록을 확인하지 못했어요(자격증명이 없거나 조회에 실패했어요).
              아래 무장애 예상 호출수는 0으로 나오지만 실제로는 그만큼 나갈 수 있어요.
            </p>
          )}

          {reconcile.skipped_columns.length > 0 && (
            <p className="mt-2 rounded-md bg-amber-50 p-3 text-xs text-amber-900 dark:bg-amber-950/30 dark:text-amber-100">
              기준 스냅샷에 없는 열은 비교하지 않았어요:{" "}
              {reconcile.skipped_columns.join(", ")}. 이 열들은 "안 바뀐 것"이 아니라
              "안 본 것"이에요.
            </p>
          )}

          <dl className="mt-3 grid grid-cols-4 gap-2">
            {[
              ["신규", reconcile.counts.added],
              ["삭제", reconcile.counts.removed],
              ["수정", reconcile.counts.updated],
              ["상세조회 대상", reconcile.detail_content_ids.length],
            ].map(([label, value]) => (
              <div key={String(label)} className="rounded-md bg-gray-100 p-2.5 dark:bg-gray-800">
                <dt className="text-[11px] text-gray-500 dark:text-gray-400">{label}</dt>
                <dd className="text-lg font-bold tabular-nums">{value}</dd>
              </div>
            ))}
          </dl>

          {reconcile.detail_excluded_ids.length > 0 && (
            <label className="mt-2 flex items-start gap-2 rounded-md bg-amber-50 p-3 text-xs text-amber-900 dark:bg-amber-950/30 dark:text-amber-100">
              <input
                type="checkbox"
                checked={includeExcluded}
                onChange={(event) => setIncludeExcluded(event.target.checked)}
                className="mt-0.5"
              />
              <span>
                상세조회 제외 {reconcile.detail_excluded_ids.length}건 — 수정시각
                (source_modified_at)은 그대로인데 다른 열만 바뀐 장소예요. 상세 내용은
                안 바뀌었다고 보고 detailIntro2를 아껴요. 체크하면 이 건들도 상세조회에
                포함합니다.
              </span>
            </label>
          )}

          <div className="mt-3">
            <ChangeRows rows={reconcile.rows} />
          </div>

          <div className="mt-4 flex flex-wrap items-center gap-3 border-t border-gray-200 pt-3 dark:border-gray-800">
            <label className="flex items-center gap-1.5 text-xs">
              <span className="text-gray-500 dark:text-gray-400">상세조회 상한</span>
              <input
                aria-label="상세조회 상한"
                value={limitInput}
                inputMode="numeric"
                onChange={(event) => setLimitInput(event.target.value)}
                placeholder="제한 없음"
                className="w-24 rounded-md border border-gray-300 px-2 py-1 text-xs dark:border-gray-700 dark:bg-gray-950"
              />
            </label>
            <span className="text-xs text-gray-500">
              예상 외부 호출: 목록 0회 + 상세조회 {plannedCalls}회
              {backfillCount > 0 &&
                ` (이번 변경분 ${changedCount} + 지난 실행에서 못 채운 ${backfillCount})`}
              {" + 무장애 목록 1회 + 무장애 상세 "}
              {barrierFreeCalls}회{" · DB 쓰기 있음"}
            </span>
            <button
              type="button"
              onClick={() => {
                setConfirm("");
                setShowDialog(true);
              }}
              disabled={applying || locked}
              className="ml-auto rounded-md bg-gray-900 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50 dark:bg-gray-100 dark:text-gray-900"
            >
              2. 반영 실행
            </button>
          </div>
        </>
      )}

      {job && <JobProgress job={job} />}

      {showDialog && reconcile && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
          <div className="w-full max-w-md rounded-lg bg-white p-5 dark:bg-gray-900">
            <h3 className="text-base font-bold">DB에 반영합니다</h3>
            <p className="mt-2 text-sm text-gray-600 dark:text-gray-300">
              운영 Supabase의 places 테이블을 실제로 변경해요. 삭제된 장소는
              비활성화됩니다.
            </p>
            <ul className="mt-3 space-y-1 text-xs text-gray-600 dark:text-gray-300">
              <li>· 대상: {expectedConfirm}</li>
              <li>· 스냅샷: {reconcile.snapshot}</li>
              <li>
                · 상세조회 {plannedCalls}회 (TourAPI detailIntro2)
                {backfillCount > 0 &&
                  ` — 이번 변경분 ${changedCount} + 지난 실행에서 상세를 못 채운 ${backfillCount}`}
              </li>
              <li>
                · 무장애 정보 {barrierFreeCalls + 1}회 (TourAPI KorWithService2,
                목록 1회 포함) — 아직 확인하지 않은 장소 중 무장애 목록에 등록된
                곳만 부릅니다
              </li>
              <li>
                · 신규 {reconcile.counts.added} / 수정 {reconcile.counts.updated} / 삭제{" "}
                {reconcile.counts.removed}
              </li>
            </ul>
            {detailsLimit !== null && (
              <p className="mt-3 rounded bg-amber-50 p-2 text-xs text-amber-900 dark:bg-amber-950/30 dark:text-amber-100">
                상한 {detailsLimit}건이 걸린 실행이라 <strong>비활성화를 건너뜁니다</strong>.
                목록을 다 처리하지 못했으니 사라진 장소를 판정할 수 없어요.
              </p>
            )}
            <p className="mt-3 rounded bg-amber-50 p-2 text-xs text-amber-900 dark:bg-amber-950/30 dark:text-amber-100">
              이 실행으로 <code>detailIntro2</code> {plannedCalls}회를 씁니다 (한도{" "}
              {detailCallsToday?.daily_limit ?? 1000}회).
              {detailCallsToday && (
                <>
                  {" "}
                  오늘 기록된 사용량은 {detailCallsToday.count}회예요
                  {detailCallsToday.runs_without_count > 0 &&
                    ` (사용량을 못 남긴 실행 ${detailCallsToday.runs_without_count}건 제외)`}
                  . 재시도는 세지 않아 실제는 이보다 많을 수 있어요.
                </>
              )}
            </p>
            <label className="mt-4 block text-xs text-gray-600 dark:text-gray-300">
              확인을 위해 <span className="font-mono font-bold">{expectedConfirm}</span>{" "}
              를 입력하세요.
              <input
                aria-label="확인 문자열"
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
                  onApply({ confirm: confirm.trim(), includeExcluded, detailsLimit });
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
