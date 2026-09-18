/*
 * 역할: 스냅샷 보관 패널이 지울 파일을 실제와 같게 보여주는지 검증한다.
 * 입력: 서버가 준 구별 보관 상태.
 * 출력: 합계 계산과 미리보기·버튼 상태에 대한 assertion.
 * 호출 시점: vitest 실행 시 호출된다.
 *
 * 후보 판정은 서버(`select_prunable`)가 한다. 화면이 검증할 것은 "서버가 준
 * 후보를 그대로 보여주는가"이지 "무엇이 후보인가"가 아니다 — 화면이 따로 세면
 * 미리보기와 실제 정리가 갈라진다.
 */

import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import type { SnapshotRetention, SnapshotRetentionDistrict } from "../../api/dev";
import { SnapshotRetentionPanel } from "./SnapshotRetentionPanel";
import { pruneTotals } from "./snapshotRetention";

function _district(fields: Partial<SnapshotRetentionDistrict> = {}): SnapshotRetentionDistrict {
  return {
    area_code: "11",
    district_code: "110",
    district_name: "종로구",
    snapshot_count: 3,
    reconciliation_count: 3,
    latest_snapshot: "places_api_snapshot_11-110_20260829.csv",
    prunable_snapshots: ["places_api_snapshot_11-110_20260820.csv"],
    prunable_reconciliations: ["places_reconciliation_11-110_20260820.csv"],
    ...fields,
  };
}

function _retention(districts: SnapshotRetentionDistrict[]): SnapshotRetention {
  return {
    snapshots: [],
    data_dir: "/repo/supabase/data",
    keep: 2,
    districts,
  };
}

function _panel(retention: SnapshotRetention | null, busy = false) {
  return (
    <SnapshotRetentionPanel
      retention={retention}
      result={null}
      error={null}
      loading={false}
      pruning={false}
      keep={2}
      busy={busy}
      onChangeKeep={() => {}}
      onRefresh={() => {}}
      onPrune={() => {}}
    />
  );
}

describe("pruneTotals", () => {
  it("스냅샷과 대조 CSV를 갈라 센다", () => {
    const totals = pruneTotals(
      _retention([
        _district(),
        _district({
          district_code: "140",
          district_name: "중구",
          prunable_snapshots: ["a.csv", "b.csv"],
          prunable_reconciliations: [],
        }),
      ]),
      true,
    );
    expect(totals).toEqual({ snapshots: 3, reconciliations: 1, districts: 2 });
  });

  it("대조 CSV를 빼면 그만큼만 줄고 구 수도 다시 센다", () => {
    const totals = pruneTotals(
      _retention([
        _district(),
        // 지울 스냅샷이 없고 대조 CSV만 있는 구. 체크를 끄면 대상 구에서 빠진다.
        _district({
          district_code: "140",
          district_name: "중구",
          prunable_snapshots: [],
          prunable_reconciliations: ["c.csv"],
        }),
      ]),
      false,
    );
    expect(totals).toEqual({ snapshots: 1, reconciliations: 0, districts: 1 });
  });

  it("자료를 못 읽었으면 0이다", () => {
    expect(pruneTotals(null, true)).toEqual({
      snapshots: 0,
      reconciliations: 0,
      districts: 0,
    });
  });
});

describe("SnapshotRetentionPanel", () => {
  it("지울 파일을 이름 그대로 보여준다", () => {
    render(_panel(_retention([_district()])));
    expect(screen.getByText(/places_api_snapshot_11-110_20260820\.csv/)).toBeInTheDocument();
    expect(screen.getByText(/places_reconciliation_11-110_20260820\.csv/)).toBeInTheDocument();
  });

  it("지울 것이 없으면 정리를 실행할 수 없다", () => {
    render(
      _panel(_retention([_district({ prunable_snapshots: [], prunable_reconciliations: [] })])),
    );
    expect(screen.getByRole("button", { name: "정리 실행" })).toBeDisabled();
  });

  it("대조·반영이 도는 중에는 정리를 막고 이유를 알린다", () => {
    render(_panel(_retention([_district()]), true));
    expect(screen.getByRole("button", { name: "정리 실행" })).toBeDisabled();
    expect(screen.getByText(/돌고 있는 동기화가 읽을 파일이 사라져요/)).toBeInTheDocument();
  });
});
