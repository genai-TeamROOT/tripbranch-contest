/*
 * 역할: 전 구 갱신의 예산 계산과 건너뛰기 판정을 검증한다.
 * 입력: 구별 대조 결과와 오늘 사용량.
 * 출력: 예상 상세조회 수, 남은 한도, 구별 실행/건너뜀 판정에 대한 assertion.
 * 호출 시점: vitest 실행 시 호출된다.
 *
 * 판정을 화면에서 떼어 함수로 두는 이유가 여기 있다 — "이 구를 왜 건너뛰었나"는
 * 표 문구가 아니라 이 함수가 정하므로, 문구와 동작이 갈라지지 않는다.
 */

import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import type { DbStatus, ReconcileResult, SyncDistrict, SyncJob } from "../../api/dev";
import { AllDistrictSyncPanel } from "./AllDistrictSyncPanel";
import {
  EMPTY_ALL_SYNC_STATE,
  barrierFreeCell,
  barrierFreeTotals,
  barrierFreeUncheckedCount,
  buildMappingCommand,
  createEntry,
  jobHitQuota,
  planDistrict,
  plannedDetailCalls,
  remainingDetailBudget,
  reusedSnapshotDates,
  snapshotDate,
  unmappedDistricts,
  type AllSyncEntry,
} from "./allDistrictSync";

function _district(fields: Partial<SyncDistrict> = {}): SyncDistrict {
  return {
    area_code: "11",
    district_code: "110",
    district_name: "종로구",
    place_count: 841,
    active_count: 841,
    latest_snapshot: null,
    list_call_estimate: 1,
    ...fields,
  };
}

function _reconcile(fields: Partial<ReconcileResult> = {}): ReconcileResult {
  return {
    area_code: "11",
    district_code: "110",
    snapshot: "places_api_snapshot_11-110_20260829.csv",
    snapshot_count: 841,
    baseline: "places_api_snapshot_11-110_20260828.csv",
    baseline_source: "file",
    skipped_columns: [],
    counts: { added: 0, removed: 0, updated: 0 },
    detail_content_ids: [],
    detail_excluded_ids: [],
    detail_backfill_ids: [],
    detail_backfill_checked: true,
    barrier_free_detail_count: 0,
    barrier_free_checked: true,
    rows: [],
    source: "api",
    ...fields,
  };
}

function _entry(reconcile: ReconcileResult | null): AllSyncEntry {
  return { ...createEntry(_district()), reconcile };
}

function _detailCallsToday(
  fields: Partial<DbStatus["detail_calls_today"]> = {},
): DbStatus["detail_calls_today"] {
  return { count: 0, runs: 0, runs_without_count: 0, daily_limit: 1000, ...fields };
}

describe("plannedDetailCalls", () => {
  it("변경분과 지난 실행에서 못 채운 건을 함께 센다", () => {
    const reconcile = _reconcile({
      detail_content_ids: ["1", "2", "3"],
      detail_backfill_ids: ["4", "5"],
    });
    expect(plannedDetailCalls(reconcile)).toBe(5);
  });

  it("상세조회 제외분은 세지 않는다 — 구 단위 패널의 기본값과 같다", () => {
    const reconcile = _reconcile({
      detail_content_ids: ["1"],
      detail_excluded_ids: ["9", "8", "7"],
    });
    expect(plannedDetailCalls(reconcile)).toBe(1);
  });
});

describe("remainingDetailBudget", () => {
  it("한도에서 오늘 사용량을 뺀다", () => {
    expect(remainingDetailBudget(_detailCallsToday({ count: 996 }))).toBe(4);
  });

  it("사용량이 한도를 넘어도 음수가 되지 않는다", () => {
    expect(remainingDetailBudget(_detailCallsToday({ count: 1200 }))).toBe(0);
  });

  it("한도 설정을 못 읽으면 null이다 — 0회가 아니라 '모른다'", () => {
    expect(remainingDetailBudget(_detailCallsToday({ daily_limit: null }))).toBeNull();
    expect(remainingDetailBudget(null)).toBeNull();
  });
});

describe("planDistrict", () => {
  it("남은 한도 안에 들면 실행한다", () => {
    const entry = _entry(_reconcile({ detail_content_ids: ["1", "2"] }));
    expect(planDistrict({ entry, spent: 0, budget: 10, quotaExhausted: false })).toEqual({
      run: true,
    });
  });

  it("남은 한도를 넘기면 건너뛰고 필요한 수와 남은 수를 알린다", () => {
    const entry = _entry(
      _reconcile({ detail_backfill_ids: Array.from({ length: 814 }, (_, i) => `${i}`) }),
    );
    const plan = planDistrict({ entry, spent: 200, budget: 1000, quotaExhausted: false });
    expect(plan.run).toBe(false);
    if (plan.run) return;
    expect(plan.reason).toContain("814회");
    expect(plan.reason).toContain("800회");
  });

  it("한도가 없으면 건수와 무관하게 실행한다", () => {
    const entry = _entry(
      _reconcile({ detail_backfill_ids: Array.from({ length: 9999 }, (_, i) => `${i}`) }),
    );
    expect(planDistrict({ entry, spent: 0, budget: null, quotaExhausted: false }).run).toBe(true);
  });

  it("앞 구에서 한도가 실제로 소진됐으면 남은 구는 전부 건너뛴다", () => {
    // 예산은 남아 있다 — 오늘 사용량 집계가 하한이라 실제 잔여가 더 적었던 경우다.
    const entry = _entry(_reconcile({ detail_content_ids: ["1"] }));
    const plan = planDistrict({ entry, spent: 0, budget: 1000, quotaExhausted: true });
    expect(plan.run).toBe(false);
    if (plan.run) return;
    expect(plan.reason).toContain("한도가 소진");
  });

  it("대조에 실패한 구는 반영하지 않는다", () => {
    const plan = planDistrict({
      entry: _entry(null),
      spent: 0,
      budget: 1000,
      quotaExhausted: false,
    });
    expect(plan.run).toBe(false);
    if (plan.run) return;
    expect(plan.reason).toContain("스냅샷이 없어요");
  });
});

function _job(errorSummary: Record<string, number>): SyncJob {
  return {
    job_id: "job-1",
    params: {
      area_code: "11",
      district_code: "110",
      snapshot: "s.csv",
      dry_run: false,
      detail_target_count: 3,
      added_count: 0,
      details_limit: null,
    },
    status: "partial_failure",
    started_at: "2026-08-29T01:00:00+09:00",
    finished_at: "2026-08-29T01:01:00+09:00",
    phase: "done",
    processed: 3,
    total: 3,
    result: {
      status: "partial_failure",
      dry_run: false,
      sync_run_id: "run-1",
      processed_count: 3,
      success_count: 3,
      failed_count: 1,
      new_count: 0,
      updated_count: 0,
      deactivated_count: 0,
      detail_target_count: 3,
      detail_attempted_count: 1,
      reparse_count: 0,
      barrier_free_target_count: 0,
      barrier_free_attempted_count: 0,
      barrier_free_stored_count: 0,
      error_summary: errorSummary,
    },
    error: null,
    unmapped_new_place_ids: [],
  };
}

describe("jobHitQuota", () => {
  it("한도 소진 코드가 있으면 참이다", () => {
    expect(jobHitQuota(_job({ TOUR_DETAIL_QUOTA_EXCEEDED: 1 }))).toBe(true);
  });

  it("다른 실패는 한도 소진이 아니다 — 무장애 한도는 오퍼레이션이 다르다", () => {
    expect(jobHitQuota(_job({ BARRIER_FREE_QUOTA_EXCEEDED: 1 }))).toBe(false);
    expect(jobHitQuota(_job({ TOUR_DETAIL_TIMEOUT: 2 }))).toBe(false);
    expect(jobHitQuota(null)).toBe(false);
  });
});

describe("unmappedDistricts", () => {
  function _entryWithUnmapped(
    districtCode: string,
    districtName: string,
    ids: string[],
  ): AllSyncEntry {
    return {
      ...createEntry(_district({ district_code: districtCode, district_name: districtName })),
      outcome: "success",
      job: {
        ..._job({}),
        unmapped_new_place_ids: ids,
      },
    };
  }

  it("건수가 많은 구부터 돌려준다", () => {
    const rows = unmappedDistricts([
      _entryWithUnmapped("110", "종로구", ["1", "2"]),
      _entryWithUnmapped("680", "강남구", ["3", "4", "5"]),
      _entryWithUnmapped("140", "중구", []),
    ]);
    expect(rows.map((row) => row.label)).toEqual(["강남구 11-680", "종로구 11-110"]);
    expect(rows.map((row) => row.count)).toEqual([3, 2]);
  });

  it("아직 반영하지 않은 구는 세지 않는다", () => {
    const entry = createEntry(_district());
    expect(unmappedDistricts([entry])).toEqual([]);
  });

  it("집중률 코드는 5자리로 붙인다 — places는 뒤 3자리만 담는다", () => {
    const [row] = unmappedDistricts([_entryWithUnmapped("110", "종로구", ["1"])]);
    expect(row.concentrationCode).toBe("11110");
    expect(buildMappingCommand(row)).toBe(
      "python -m scripts.build_concentration_mappings --area-code 11 --district-code 11110",
    );
  });
});

describe("reusedSnapshotDates", () => {
  function _saved(fileName: string): AllSyncEntry {
    return {
      ...createEntry(_district()),
      reconcile: _reconcile({ snapshot: fileName, source: "saved" }),
      outcome: "reconciled",
    };
  }

  it("파일명에서 날짜를 읽는다", () => {
    expect(snapshotDate("places_api_snapshot_11-110_20260829.csv")).toBe("2026-08-29");
    expect(snapshotDate("seongdong_places.csv")).toBeNull();
  });

  it("구마다 날짜가 다르면 다 보여준다", () => {
    // 하나로 뭉치면 8/25에 멈춰 있던 구까지 어제 것으로 읽힌다.
    const dates = reusedSnapshotDates([
      _saved("places_api_snapshot_11-110_20260829.csv"),
      _saved("places_api_snapshot_11-140_20260825.csv"),
      _saved("places_api_snapshot_11-170_20260829.csv"),
    ]);
    expect(dates).toEqual(["2026-08-25", "2026-08-29"]);
  });

  it("목록을 새로 받은 구는 재사용이 아니다", () => {
    const entry: AllSyncEntry = {
      ...createEntry(_district()),
      reconcile: _reconcile({ source: "api" }),
      outcome: "reconciled",
    };
    expect(reusedSnapshotDates([entry])).toEqual([]);
  });
});

describe("무장애 표시", () => {
  function _entry(fields: Partial<AllSyncEntry> = {}): AllSyncEntry {
    return { ...createEntry(_district()), ...fields };
  }

  it("확인하지 못한 구는 0이 아니라 미확인이다", () => {
    // 0으로 적으면 "부를 게 없다"로 읽힌다. 실제로는 안 본 것이다.
    const entry = _entry({
      reconcile: _reconcile({ barrier_free_checked: false, barrier_free_detail_count: 0 }),
    });
    expect(barrierFreeCell(entry)).toBe("미확인");
    expect(barrierFreeUncheckedCount([entry])).toBe(1);
  });

  it("확인한 구는 예상치를 보여준다", () => {
    const entry = _entry({
      reconcile: _reconcile({ barrier_free_checked: true, barrier_free_detail_count: 36 }),
    });
    expect(barrierFreeCell(entry)).toBe("36 (예상)");
    expect(barrierFreeUncheckedCount([entry])).toBe(0);
  });

  it("반영이 끝나면 실제로 저장한 수를 보여준다", () => {
    // 목록에 있어도 항목이 전부 빈 장소가 있어 저장 수와 호출 수가 다르다.
    const job = _job({});
    const entry = _entry({
      reconcile: _reconcile({ barrier_free_checked: false }),
      job: {
        ...job,
        result: {
          ...job.result!,
          barrier_free_attempted_count: 40,
          barrier_free_stored_count: 33,
        },
      },
    });
    expect(barrierFreeCell(entry)).toBe("33/40");
    expect(barrierFreeTotals([entry])).toEqual({ stored: 33, attempted: 40 });
  });
});

describe("AllDistrictSyncPanel", () => {
  it("합계가 남은 한도를 넘으면 건너뛴다는 사실을 알린다", () => {
    const entry: AllSyncEntry = {
      ...createEntry(_district()),
      reconcile: _reconcile({
        detail_backfill_ids: Array.from({ length: 814 }, (_, i) => `${i}`),
      }),
      outcome: "reconciled",
    };
    render(
      <AllDistrictSyncPanel
        districts={[_district()]}
        state={{ ...EMPTY_ALL_SYNC_STATE, phase: "reviewing", entries: [entry] }}
        detailCallsToday={_detailCallsToday({ count: 400 })}
        busy={false}
        onReconcileAll={() => {}}
        onReuseSnapshots={() => {}}
        onApplyAll={() => {}}
        onCancel={() => {}}
      />,
    );
    expect(screen.getByText(/합계가 남은 한도를 넘어요/)).toBeInTheDocument();
    expect(screen.getByText(/건너뛰고 다음 구로 가며/)).toBeInTheDocument();
  });

  it("순회가 끝나면 미매핑이 생긴 구와 실행할 명령을 알린다", () => {
    const entry: AllSyncEntry = {
      ...createEntry(_district({ district_code: "680", district_name: "강남구" })),
      reconcile: _reconcile(),
      outcome: "success",
      job: { ..._job({}), unmapped_new_place_ids: ["1", "2"] },
    };
    render(
      <AllDistrictSyncPanel
        districts={[_district()]}
        state={{ ...EMPTY_ALL_SYNC_STATE, phase: "done", entries: [entry] }}
        detailCallsToday={_detailCallsToday()}
        busy={false}
        onReconcileAll={() => {}}
        onReuseSnapshots={() => {}}
        onApplyAll={() => {}}
        onCancel={() => {}}
      />,
    );
    expect(screen.getByText(/1개 구에 2건/)).toBeInTheDocument();
    // 표에 보이는 11-680이 아니라 스크립트가 받는 11680이어야 한다.
    expect(screen.getByText(/--district-code 11680/)).toBeInTheDocument();
  });

  it("저장된 스냅샷을 쓰면 목록 날짜와 무장애 미확인을 알린다", () => {
    const entry: AllSyncEntry = {
      ...createEntry(_district()),
      reconcile: _reconcile({
        snapshot: "places_api_snapshot_11-110_20260829.csv",
        source: "saved",
      }),
      outcome: "reconciled",
    };
    render(
      <AllDistrictSyncPanel
        districts={[_district()]}
        state={{ ...EMPTY_ALL_SYNC_STATE, phase: "reviewing", entries: [entry] }}
        detailCallsToday={_detailCallsToday()}
        busy={false}
        onReconcileAll={() => {}}
        onReuseSnapshots={() => {}}
        onApplyAll={() => {}}
        onCancel={() => {}}
      />,
    );
    expect(screen.getByText(/2026-08-29/)).toBeInTheDocument();
    expect(screen.getByText(/다음 대조로 넘어가요/)).toBeInTheDocument();
  });

  it("저장된 스냅샷 쓰기 버튼을 따로 낸다", () => {
    render(
      <AllDistrictSyncPanel
        districts={[_district()]}
        state={EMPTY_ALL_SYNC_STATE}
        detailCallsToday={_detailCallsToday()}
        busy={false}
        onReconcileAll={() => {}}
        onReuseSnapshots={() => {}}
        onApplyAll={() => {}}
        onCancel={() => {}}
      />,
    );
    expect(screen.getByRole("button", { name: "저장된 스냅샷 쓰기" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /전 구 대조/ })).toBeInTheDocument();
  });

  it("무장애를 확인하지 못한 구가 있으면 합계가 확정이 아님을 알린다", () => {
    const entry: AllSyncEntry = {
      ...createEntry(_district()),
      reconcile: _reconcile({ source: "saved", barrier_free_checked: false }),
      outcome: "reconciled",
    };
    render(
      <AllDistrictSyncPanel
        districts={[_district()]}
        state={{ ...EMPTY_ALL_SYNC_STATE, phase: "reviewing", entries: [entry] }}
        detailCallsToday={_detailCallsToday()}
        busy={false}
        onReconcileAll={() => {}}
        onReuseSnapshots={() => {}}
        onApplyAll={() => {}}
        onCancel={() => {}}
      />,
    );
    expect(screen.getByText(/이 합계에 안/)).toBeInTheDocument();
    expect(screen.getByText(/반영할 때는 정상적으로 조회하고 채웁니다/)).toBeInTheDocument();
  });

  it("대조 전에는 반영 버튼을 내주지 않는다", () => {
    render(
      <AllDistrictSyncPanel
        districts={[_district()]}
        state={EMPTY_ALL_SYNC_STATE}
        detailCallsToday={_detailCallsToday()}
        busy={false}
        onReconcileAll={() => {}}
        onReuseSnapshots={() => {}}
        onApplyAll={() => {}}
        onCancel={() => {}}
      />,
    );
    expect(screen.getByRole("button", { name: /전 구 대조/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /전 구 반영/ })).toBeNull();
  });
});
