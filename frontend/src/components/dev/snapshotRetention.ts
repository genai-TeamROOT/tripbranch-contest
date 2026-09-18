/*
 * 역할: 스냅샷 보관 화면이 쓰는 상수와 합계 계산.
 * 입력: 서버가 준 구별 보관 상태.
 * 출력: 기본 유지 개수, 확인 문자열, 지울 파일 합계.
 * 호출 시점: SnapshotRetentionPanel과 DeveloperOpsPage가 부른다.
 *
 * "무엇이 지울 후보인가"는 여기서 정하지 않는다 — 서버의 `select_prunable` 하나가
 * 정하고 화면은 그 목록을 세기만 한다. 화면이 따로 판정하면 미리보기와 실제
 * 정리가 갈라져, 보여준 것과 다른 파일이 지워진다.
 */

import type { SnapshotRetention } from "../../api/dev";

/** 기본 유지 개수. 서버의 DEFAULT_SNAPSHOT_KEEP과 같은 값이다.
 *
 *  1개만 남기면 같은 날 두 번째 대조가 기준을 잃는다 — 파일명이 날짜라 첫 대조가
 *  만든 파일을 덮어쓰고, 남은 것이 그것뿐이면 기준 탐색이 빈손으로 돌아와 places
 *  재구성 기준으로 떨어진다. */
export const DEFAULT_KEEP = 2;

export const PRUNE_CONFIRM = "PRUNE";

/** 지금 유지 개수로 지워질 파일 수. 스냅샷과 대조 CSV를 갈라 센다 —
 *  둘은 성격이 달라서(하나는 다음 대조의 기준, 하나는 사람이 읽는 기록)
 *  합쳐 보여주면 무엇을 잃는지 판단할 수 없다. */
export function pruneTotals(
  retention: SnapshotRetention | null,
  includeReconciliations: boolean,
): { snapshots: number; reconciliations: number; districts: number } {
  if (retention === null) return { snapshots: 0, reconciliations: 0, districts: 0 };
  let snapshots = 0;
  let reconciliations = 0;
  let districts = 0;
  for (const district of retention.districts) {
    const snapshotCount = district.prunable_snapshots.length;
    const reconciliationCount = includeReconciliations
      ? district.prunable_reconciliations.length
      : 0;
    snapshots += snapshotCount;
    reconciliations += reconciliationCount;
    if (snapshotCount + reconciliationCount > 0) districts += 1;
  }
  return { snapshots, reconciliations, districts };
}
