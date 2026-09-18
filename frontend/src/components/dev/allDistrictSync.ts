/*
 * 역할: 전 구 갱신 순회가 쓰는 예산 계산과 구별 실행/건너뛰기 판정.
 * 입력: 구별 대조 결과, 오늘 detailIntro2 사용량, 지금까지 쓴 호출 수.
 * 출력: 예상 상세조회 수, 남은 한도, 이 구를 지금 돌릴 수 있는지와 못 돌릴 이유.
 * 호출 시점: DeveloperOpsPage의 순회 루프와 AllDistrictSyncPanel이 부른다.
 *
 * 화면에서 떼어 둔다. 여기가 "무엇을 건너뛰었나"를 정하는 유일한 자리라서,
 * 순회 로직과 표 문구가 같은 함수를 봐야 동작과 설명이 갈라지지 않는다.
 */

import type { DbStatus, ReconcileResult, SyncDistrict, SyncJob } from "../../api/dev";

/** 구 하나가 전 구 순회에서 어디까지 갔는지. */
export type AllSyncOutcome =
  "pending" | "reconciled" | "running" | "success" | "skipped" | "failed";

export type AllSyncEntry = {
  areaCode: string;
  districtCode: string;
  districtName: string | null;
  /** 1단계 결과. null이면 아직 대조하지 않았거나 대조가 실패했다. */
  reconcile: ReconcileResult | null;
  reconcileError: string | null;
  outcome: AllSyncOutcome;
  /** 건너뛴 이유. 한도 때문인지 대조 실패 때문인지 구분해야 다음 조치가 갈린다. */
  skipReason: string | null;
  job: SyncJob | null;
  applyError: string | null;
};

export type AllSyncPhase = "idle" | "reconciling" | "reviewing" | "applying" | "done";

export type AllSyncState = {
  phase: AllSyncPhase;
  entries: AllSyncEntry[];
  /** 지금 다루고 있는 entries 인덱스. 진행 표시에만 쓴다. */
  cursor: number;
  /** 이번 순회가 실제로 쓴 detailIntro2 횟수. 예상치가 아니라 job 결과의 실측이다. */
  spentDetailCalls: number;
  /** 한도 소진 응답을 실제로 받았는지. 받았으면 남은 구는 전부 건너뛴다. */
  quotaExhausted: boolean;
  error: string | null;
};

export const EMPTY_ALL_SYNC_STATE: AllSyncState = {
  phase: "idle",
  entries: [],
  cursor: 0,
  spentDetailCalls: 0,
  quotaExhausted: false,
  error: null,
};

/** 서버가 한도 소진으로 상세조회를 멈췄음을 알리는 코드. place_sync.py와 같은 문자열. */
export const QUOTA_EXCEEDED_CODE = "TOUR_DETAIL_QUOTA_EXCEEDED";

export function createEntry(district: SyncDistrict): AllSyncEntry {
  return {
    areaCode: district.area_code,
    districtCode: district.district_code,
    districtName: district.district_name,
    reconcile: null,
    reconcileError: null,
    outcome: "pending",
    skipReason: null,
    job: null,
    applyError: null,
  };
}

/** 이 구를 반영하면 나갈 detailIntro2 횟수.
 *
 * 변경분만이 아니라 지난 실행에서 못 채운 건(pending·failed)도 함께 나간다 —
 * 서버의 `_select_targets`가 그렇게 동작한다. 빼고 세면 화면이 "15회"라고
 * 해놓고 실제로는 157회를 쓴다.
 *
 * 상세조회 제외분(detail_excluded_ids)은 넣지 않는다. 구 단위 패널의 기본값과
 * 같다 — 수정시각이 그대로인 장소라 상세 내용은 안 바뀌었다고 본다. */
export function plannedDetailCalls(reconcile: ReconcileResult): number {
  return reconcile.detail_content_ids.length + reconcile.detail_backfill_ids.length;
}

/** 오늘 남은 detailIntro2 한도. 한도 설정이 없으면 null(제한 없음으로 다룬다).
 *
 * 이 값은 **어림이고 낙관적이다.** 빼는 쪽 사용량(`detail_calls_today.count`)이
 * 하한이기 때문이다 — 재시도는 세지 않고, 완료 처리를 못 한 실행은 사용량이
 * 비어 있다. 그래서 예산을 다 쓰지 않았는데도 서버가 한도 소진을 돌려줄 수 있고,
 * 그때는 `quotaExhausted`로 갈아탄다. */
export function remainingDetailBudget(
  detailCallsToday: DbStatus["detail_calls_today"] | null,
): number | null {
  if (!detailCallsToday || detailCallsToday.daily_limit === null) return null;
  return Math.max(0, detailCallsToday.daily_limit - detailCallsToday.count);
}

/** 이 구를 지금 반영할 수 있는지. 못 하면 건너뛸 이유를 함께 준다.
 *
 * 판정을 순회 로직에서 떼어 둔다 — 여기가 "무엇을 건너뛰었나"를 정하는 유일한
 * 자리라서, 화면 문구와 실제 동작이 갈라지지 않으려면 한 곳에 있어야 한다. */
export function planDistrict(input: {
  entry: AllSyncEntry;
  spent: number;
  budget: number | null;
  quotaExhausted: boolean;
}): { run: true } | { run: false; reason: string } {
  const { entry, spent, budget, quotaExhausted } = input;
  if (entry.reconcile === null) {
    return { run: false, reason: "대조에 실패해 반영할 스냅샷이 없어요." };
  }
  if (quotaExhausted) {
    return {
      run: false,
      reason: "앞 구에서 오늘 상세조회 한도가 소진됐어요. 내일 다시 실행하세요.",
    };
  }
  const planned = plannedDetailCalls(entry.reconcile);
  if (budget !== null && spent + planned > budget) {
    return {
      run: false,
      reason: `상세조회 ${planned}회가 필요한데 남은 한도가 ${Math.max(0, budget - spent)}회예요.`,
    };
  }
  return { run: true };
}

/** job 결과가 "오늘 한도를 다 썼다"를 담고 있는지. */
export function jobHitQuota(job: SyncJob | null): boolean {
  return Boolean(job?.result?.error_summary?.[QUOTA_EXCEEDED_CODE]);
}

/** 집중률 API가 쓰는 시군구 코드. `places`의 코드와 자릿수가 다르다.
 *
 * 집중률 API는 법정동 시군구 5자리(시도 2 + 시군구 3)를 쓰고 `places`는 뒤 3자리만
 * 담는다(`scripts/build_concentration_mappings.py`의 `places_district_code`가 그
 * 변환을 반대 방향으로 한다). 화면에 보이는 `11-110`을 그대로 스크립트에 넣으면
 * 먹지 않으므로, 안내 문구에는 붙인 형태로 내보낸다. */
export function concentrationDistrictCode(areaCode: string, districtCode: string): string {
  return `${areaCode}${districtCode}`;
}

export type UnmappedDistrict = {
  label: string;
  areaCode: string;
  /** 집중률 API·스크립트가 받는 5자리 코드. */
  concentrationCode: string;
  count: number;
};

/** 집중률 매핑이 없는 신규 장소가 생긴 구. 건수가 많은 순으로.
 *
 * 매핑이 없으면 그 장소는 혼잡도 조회를 통째로 건너뛴다(enrichment_service).
 * 오류가 나지 않고 그 장소만 조용히 판정에서 빠지므로, 알리지 않으면 아무도
 * 모른다. 동기화는 매핑 테이블을 건드리지 않는다 — 적재는 별도 스크립트 소관이다. */
export function unmappedDistricts(entries: AllSyncEntry[]): UnmappedDistrict[] {
  const rows: UnmappedDistrict[] = [];
  for (const entry of entries) {
    const count = entry.job?.unmapped_new_place_ids.length ?? 0;
    if (count === 0) continue;
    rows.push({
      label: `${entry.districtName ?? `구 ${entry.districtCode}`} ${entry.areaCode}-${entry.districtCode}`,
      areaCode: entry.areaCode,
      concentrationCode: concentrationDistrictCode(entry.areaCode, entry.districtCode),
      count,
    });
  }
  return rows.sort((left, right) => right.count - left.count);
}

/** 그 구의 매핑을 다시 만드는 명령. `--district-code`는 required라 구를 반드시 준다. */
export function buildMappingCommand(district: UnmappedDistrict): string {
  return (
    "python -m scripts.build_concentration_mappings " +
    `--area-code ${district.areaCode} --district-code ${district.concentrationCode}`
  );
}

/** 스냅샷 파일명에서 날짜를 읽는다. `..._11-110_20260829.csv` → `2026-08-29`. */
export function snapshotDate(fileName: string): string | null {
  const matched = /_(\d{4})(\d{2})(\d{2})\.csv$/.exec(fileName);
  return matched ? `${matched[1]}-${matched[2]}-${matched[3]}` : null;
}

/** 재사용한 스냅샷의 날짜들. 며칠 전 자료로 반영하는지가 화면에 보여야 한다.
 *
 * 구마다 마지막 대조 날짜가 다를 수 있어 하나로 뭉치지 않는다 — "8/29"라고만
 * 적으면 8/25에 멈춰 있던 구까지 어제 것으로 읽힌다. */
export function reusedSnapshotDates(entries: AllSyncEntry[]): string[] {
  const dates = new Set<string>();
  for (const entry of entries) {
    if (entry.reconcile?.source !== "saved") continue;
    const date = snapshotDate(entry.reconcile.snapshot);
    if (date) dates.add(date);
  }
  return [...dates].sort();
}

/** 표에 적을 무장애 값.
 *
 * 반영이 끝난 구는 실제로 저장한 수를 보여준다. 아직이면 대조가 센 예상치인데,
 * 저장된 스냅샷을 재사용한 대조는 무장애 목록을 부르지 않아 그 수를 모른다 —
 * 0으로 적으면 "부를 게 없다"로 읽히므로 "미확인"이라고 적는다.
 *
 * 값이 있어 저장된 수와 실제 부른 수를 함께 보여준다. 무장애 목록에 있어도 15개
 * 필드가 전부 빈 장소가 있어 둘이 다르다. */
export function barrierFreeCell(entry: AllSyncEntry): string {
  const result = entry.job?.result;
  if (result) {
    return `${result.barrier_free_stored_count}/${result.barrier_free_attempted_count}`;
  }
  if (entry.reconcile === null) return "—";
  if (!entry.reconcile.barrier_free_checked) return "미확인";
  return `${entry.reconcile.barrier_free_detail_count} (예상)`;
}

/** 대조가 무장애 목록을 확인하지 못한 구 수. 합계가 확정이 아님을 알리는 데 쓴다. */
export function barrierFreeUncheckedCount(entries: AllSyncEntry[]): number {
  return entries.filter(
    (entry) => entry.reconcile !== null && !entry.reconcile.barrier_free_checked,
  ).length;
}

/** 이번 순회가 실제로 저장한 무장애 정보와 부른 횟수. */
export function barrierFreeTotals(entries: AllSyncEntry[]): {
  stored: number;
  attempted: number;
} {
  let stored = 0;
  let attempted = 0;
  for (const entry of entries) {
    const result = entry.job?.result;
    if (!result) continue;
    stored += result.barrier_free_stored_count;
    attempted += result.barrier_free_attempted_count;
  }
  return { stored, attempted };
}

/** 못 채운 건이 DB에 얼마나 있는지 확인하지 못한 구 수.
 *
 * 자격증명이 없어 못 본 것과 "보충할 게 없다"를 같은 0으로 뭉개면, 화면이 예상
 * 호출수를 확정된 값처럼 보여준다. 실제로는 그보다 많이 나갈 수 있다. */
export function backfillUncheckedCount(entries: AllSyncEntry[]): number {
  return entries.filter(
    (entry) => entry.reconcile !== null && !entry.reconcile.detail_backfill_checked,
  ).length;
}
