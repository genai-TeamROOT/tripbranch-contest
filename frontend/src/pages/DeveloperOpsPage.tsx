/*
 * 역할: 개발자가 외부 API 호출량과 장소 DB 상태를 한 화면에서 확인하는 운영 패널.
 * 입력: /api/dev/api-usage, /api/dev/db-status.
 * 출력: 호출량 패널과 DB 상태 패널.
 * 호출 시점: /dev-ops 라우트가 활성화될 때 호출된다.
 *
 * /dev-chat의 Audit 패널은 발화 한 턴을 보지만 이 화면은 서버 전역·누적 상태를
 * 본다. 스코프가 달라 화면을 나눴다.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { ApiError } from "../api/client";
import {
  applyConcentrationMapping,
  applyPlaceSync,
  buildConcentrationMapping,
  fetchApiUsage,
  fetchConcentrationStatus,
  fetchDbStatus,
  fetchSnapshotRetention,
  fetchSyncDistricts,
  fetchSyncJob,
  pruneSnapshots,
  reconcilePlaces,
  resetApiUsage,
  type ApiUsageSnapshot,
  type ConcentrationApplyResult,
  type ConcentrationBuildResult,
  type ConcentrationDistrict,
  type ConcentrationStatus,
  type DbStatus,
  type ReconcileResult,
  type SnapshotPruneResult,
  type SnapshotRetention,
  type SyncDistrict,
  type SyncDistricts,
  type SyncJob,
} from "../api/dev";
import { fetchFeedbackStats } from "../api/feedback";
import { fetchTraceStats } from "../api/trace";
import type { FeedbackStatsResponse, TraceStatsResponse } from "../types";
import { ApiUsagePanel } from "../components/dev/ApiUsagePanel";
import { AllDistrictSyncPanel } from "../components/dev/AllDistrictSyncPanel";
import {
  EMPTY_ALL_SYNC_STATE,
  createEntry,
  jobHitQuota,
  planDistrict,
  plannedDetailCalls,
  remainingDetailBudget,
  type AllSyncEntry,
  type AllSyncState,
} from "../components/dev/allDistrictSync";
import { ConcentrationMappingPanel } from "../components/dev/ConcentrationMappingPanel";
import { CategoryCoveragePanel } from "../components/dev/CategoryCoveragePanel";
import { splitApproval } from "../components/dev/concentrationMapping";
import { DbStatusPanel } from "../components/dev/DbStatusPanel";
import { OpsNav, type OpsTab } from "../components/dev/OpsNav";
import { FeedbackStatsPanel } from "../components/dev/FeedbackStatsPanel";
import { PlaceSyncPanel } from "../components/dev/PlaceSyncPanel";
import { SnapshotRetentionPanel } from "../components/dev/SnapshotRetentionPanel";
import { DEFAULT_KEEP, PRUNE_CONFIRM } from "../components/dev/snapshotRetention";
import { TracePanel } from "../components/dev/TracePanel";

const AUTO_REFRESH_INTERVAL_MS = 3000;
const JOB_POLL_INTERVAL_MS = 1000;

/** job이 끝날 때까지 기다린다. 전 구 순회가 구를 하나씩 돌리려면 필요하다.
 *
 * 중단(`shouldStop`)은 순회를 멈출 뿐 서버 job을 취소하지 않는다 — 돌고 있는
 * 동기화는 끝까지 가고, 그 결과는 DB와 place_sync_runs에 남는다. 여기서 멈추면
 * 그 구의 실제 상세조회 수를 못 세므로, 호출한 쪽이 예상치로 대신 센다. */
async function waitForJob(jobId: string, shouldStop: () => boolean): Promise<SyncJob> {
  for (;;) {
    await new Promise((resolve) => window.setTimeout(resolve, JOB_POLL_INTERVAL_MS));
    const next = await fetchSyncJob(jobId);
    if (next.status !== "running" || shouldStop()) return next;
  }
}

/** 끝난 job을 표에 어떻게 적을지. 상태가 세 가지라 실패만 갈라낸다.
 *
 * `partial_failure`는 실패로 치지 않는다 — 한도 소진이나 일부 장소의 상세조회
 * 실패라서, 목록 반영과 비활성화는 정상으로 끝난 상태다. 대신 어떤 오류였는지를
 * 표에 남겨 다음 실행에서 무엇이 남았는지 읽을 수 있게 한다. */
function jobOutcome(job: SyncJob): {
  outcome: AllSyncEntry["outcome"];
  note: string | null;
} {
  if (job.status === "failed") {
    return { outcome: "failed", note: job.error ?? "동기화가 실패했어요." };
  }
  const codes = Object.keys(job.result?.error_summary ?? {});
  return {
    outcome: "success",
    note: codes.length > 0 ? `${job.status}: ${codes.join(", ")}` : null,
  };
}

function toMessage(error: unknown, fallback: string) {
  if (error instanceof ApiError) {
    // 라우터는 APP_ENV=local일 때만 등록된다 — 404는 "서버가 로컬 모드가 아님"이다.
    if (error.code === "invalid_request") {
      return "개발자 API를 찾을 수 없어요. 백엔드가 APP_ENV=local로 떠 있는지 확인하세요.";
    }
    return error.message;
  }
  return fallback;
}

export function DeveloperOpsPage() {
  const navigate = useNavigate();
  /* 탭을 URL에 둔다. 새로고침해도 자리를 지키고 링크로 공유된다 — 전 구 순회는
   * 오래 걸려서 도중에 새로고침할 일이 생긴다. */
  const [searchParams, setSearchParams] = useSearchParams();
  const tabParam = searchParams.get("tab");
  const tab: OpsTab = tabParam === "sync" || tabParam === "categories" ? tabParam : "observe";
  const selectTab = useCallback(
    (next: OpsTab) => {
      // replace로 바꾼다. 탭 전환을 뒤로가기 이력에 쌓으면 채팅 화면으로 돌아가는
      // 데 뒤로가기를 여러 번 눌러야 한다.
      setSearchParams(next === "observe" ? {} : { tab: next }, { replace: true });
    },
    [setSearchParams],
  );
  const [usage, setUsage] = useState<ApiUsageSnapshot | null>(null);
  const [usageError, setUsageError] = useState<string | null>(null);
  const [autoRefresh, setAutoRefresh] = useState(false);

  const [dbStatus, setDbStatus] = useState<DbStatus | null>(null);
  const [dbError, setDbError] = useState<string | null>(null);
  const [dbLoading, setDbLoading] = useState(false);

  const [feedbackStats, setFeedbackStats] = useState<FeedbackStatsResponse | null>(null);
  const [feedbackStatsError, setFeedbackStatsError] = useState<string | null>(null);
  const [feedbackStatsLoading, setFeedbackStatsLoading] = useState(false);

  const loadFeedbackStats = useCallback(async () => {
    setFeedbackStatsLoading(true);
    try {
      setFeedbackStats(await fetchFeedbackStats());
      setFeedbackStatsError(null);
    } catch (error) {
      setFeedbackStatsError(toMessage(error, "피드백 통계를 불러오지 못했어요."));
    } finally {
      setFeedbackStatsLoading(false);
    }
  }, []);

  const [traceStats, setTraceStats] = useState<TraceStatsResponse | null>(null);
  const [traceStatsError, setTraceStatsError] = useState<string | null>(null);
  const [traceStatsLoading, setTraceStatsLoading] = useState(false);

  const loadTraceStats = useCallback(async () => {
    setTraceStatsLoading(true);
    try {
      setTraceStats(await fetchTraceStats());
      setTraceStatsError(null);
    } catch (error) {
      setTraceStatsError(toMessage(error, "Trace 통계를 불러오지 못했어요."));
    } finally {
      setTraceStatsLoading(false);
    }
  }, []);

  const loadUsage = useCallback(async () => {
    try {
      setUsage(await fetchApiUsage());
      setUsageError(null);
    } catch (error) {
      setUsageError(toMessage(error, "호출량을 불러오지 못했어요."));
    }
  }, []);

  const loadDbStatus = useCallback(async () => {
    setDbLoading(true);
    try {
      setDbStatus(await fetchDbStatus());
      setDbError(null);
    } catch (error) {
      setDbError(toMessage(error, "DB 상태를 불러오지 못했어요."));
    } finally {
      setDbLoading(false);
    }
  }, []);

  const [reconcile, setReconcile] = useState<ReconcileResult | null>(null);
  const [job, setJob] = useState<SyncJob | null>(null);
  const [syncError, setSyncError] = useState<string | null>(null);
  const [reconciling, setReconciling] = useState(false);
  const [applying, setApplying] = useState(false);
  const [districts, setDistricts] = useState<SyncDistricts | null>(null);
  const [selectedDistrict, setSelectedDistrict] = useState<SyncDistrict | null>(null);

  const loadDistricts = useCallback(async () => {
    try {
      const next = await fetchSyncDistricts();
      setDistricts(next);
      // 처음 한 번만 고른다. 이후 새로고침이 사용자의 선택을 되돌리면 안 된다.
      setSelectedDistrict((current) => current ?? next.loaded[0] ?? null);
      setSyncError(null);
    } catch (error) {
      setSyncError(toMessage(error, "구 목록을 불러오지 못했어요."));
    }
  }, []);

  /* 구를 바꾸면 앞 구의 대조 결과와 job을 비운다. 남겨두면 중구를 대조한 화면에서
   * 용산구를 반영하는 조작이 가능해 보인다 — 서버가 거부하긴 하지만, 화면이 그런
   * 조작을 제안하는 것 자체가 잘못이다. */
  const handleSelectDistrict = useCallback((district: SyncDistrict) => {
    setSelectedDistrict(district);
    setReconcile(null);
    setJob(null);
    setSyncError(null);
  }, []);

  const handleReconcile = useCallback(async () => {
    if (!selectedDistrict) return;
    setReconciling(true);
    try {
      setReconcile(
        await reconcilePlaces({
          areaCode: selectedDistrict.area_code,
          districtCode: selectedDistrict.district_code,
        }),
      );
      setSyncError(null);
    } catch (error) {
      setSyncError(toMessage(error, "대조에 실패했어요."));
    } finally {
      setReconciling(false);
    }
  }, [selectedDistrict]);

  const handleApply = useCallback(
    async (input: { confirm: string; includeExcluded: boolean; detailsLimit: number | null }) => {
      if (!reconcile) return;
      setApplying(true);
      try {
        const started = await applyPlaceSync({
          // 구는 대조 결과에서 가져온다 — 드롭다운은 그 사이 바뀔 수 있고,
          // 반영은 어디까지나 이 스냅샷이 담고 있는 구에 대한 것이다.
          areaCode: reconcile.area_code,
          districtCode: reconcile.district_code,
          snapshot: reconcile.snapshot,
          /* 못 채운 건을 함께 보낸다. 서버가 알아서 더하기도 하지만, 보내지
           * 않으면 job 파라미터의 대상 수가 화면이 예고한 수와 어긋난다. */
          detailContentIds: [
            ...reconcile.detail_content_ids,
            ...(input.includeExcluded ? reconcile.detail_excluded_ids : []),
            ...reconcile.detail_backfill_ids,
          ],
          addedContentIds: reconcile.rows
            .filter((row) => row.change_type === "added")
            .map((row) => row.content_id),
          // 패널은 항상 실제 반영이다. dry-run은 한도를 똑같이 쓰면서 결과를
          // 남기지 않아, 모르고 켜두면 "돌렸는데 아무것도 안 바뀜"이 된다.
          dryRun: false,
          detailsLimit: input.detailsLimit,
          confirm: input.confirm,
        });
        setJob(started);
        setSyncError(null);
      } catch (error) {
        setSyncError(toMessage(error, "반영을 시작하지 못했어요."));
      } finally {
        setApplying(false);
      }
    },
    [reconcile],
  );

  /* 전 구 순회. 구 단위 대조·반영을 코드 순으로 하나씩 부르는 것이라 서버에는
   * 새 엔드포인트가 없다. 동기화 job은 서버에서 한 번에 하나만 돌기 때문에
   * 순차 실행이 선택이 아니라 전제다. */
  const [allSync, setAllSync] = useState<AllSyncState>(EMPTY_ALL_SYNC_STATE);
  // 중단 신호. 상태로 두면 루프가 잡아둔 값이 낡아 중단이 먹지 않는다.
  const stopAllRef = useRef(false);

  const handleCancelAll = useCallback(() => {
    stopAllRef.current = true;
  }, []);

  /* source가 "saved"면 저장된 스냅샷을 그대로 읽어 대조만 다시 계산한다 —
   * 외부 호출이 0회다. 오늘 상세조회 한도가 없어 반영을 못 하고 다음 날 이어서
   * 할 때 쓴다. 스냅샷이 없거나 기준을 못 세우는 구는 서버가 거부하므로, 그 구만
   * 실패로 남기고 순회는 계속한다. */
  const handleReconcileAll = useCallback(
    async (source: "api" | "saved") => {
      const loaded = districts?.loaded ?? [];
      if (loaded.length === 0) return;
      stopAllRef.current = false;
      const entries = loaded.map(createEntry);
      setAllSync({ ...EMPTY_ALL_SYNC_STATE, phase: "reconciling", entries });

      for (let index = 0; index < entries.length; index += 1) {
        if (stopAllRef.current) break;
        setAllSync((current) => ({ ...current, cursor: index }));
        const entry = entries[index];
        try {
          entries[index] = {
            ...entry,
            reconcile: await reconcilePlaces({
              areaCode: entry.areaCode,
              districtCode: entry.districtCode,
              source,
            }),
            outcome: "reconciled",
          };
        } catch (error) {
          // 한 구가 실패해도 멈추지 않는다. 나머지 구의 변경분은 그대로 볼 수 있고,
          // 실패한 구는 표에 남아 반영 단계에서 건너뛴다.
          entries[index] = {
            ...entry,
            outcome: "failed",
            reconcileError: toMessage(
              error,
              source === "saved" ? "저장된 스냅샷으로 대조하지 못했어요." : "대조에 실패했어요.",
            ),
          };
        }
        setAllSync((current) => ({
          ...current,
          entries: [...entries],
          cursor: index + 1,
        }));
      }
      setAllSync((current) => ({
        ...current,
        phase: "reviewing",
        entries: [...entries],
      }));
    },
    [districts],
  );

  const handleApplyAll = useCallback(async () => {
    stopAllRef.current = false;
    // 앞선 반영 결과를 비우고 다시 시작한다. 대조 결과는 그대로 쓴다.
    const entries = allSync.entries.map((entry) =>
      entry.reconcile === null
        ? entry
        : {
            ...entry,
            outcome: "reconciled" as const,
            skipReason: null,
            job: null,
            applyError: null,
          },
    );
    const budget = remainingDetailBudget(dbStatus?.detail_calls_today ?? null);
    let spent = 0;
    let quotaExhausted = false;

    setAllSync((current) => ({
      ...current,
      phase: "applying",
      entries: [...entries],
      cursor: 0,
      spentDetailCalls: 0,
      quotaExhausted: false,
      error: null,
    }));

    const flush = (index: number) =>
      setAllSync((current) => ({
        ...current,
        entries: [...entries],
        cursor: index,
        spentDetailCalls: spent,
        quotaExhausted,
      }));

    for (let index = 0; index < entries.length; index += 1) {
      if (stopAllRef.current) break;
      const entry = entries[index];
      const plan = planDistrict({ entry, spent, budget, quotaExhausted });
      if (!plan.run) {
        /* 한도를 넘길 구는 건너뛰고 다음 구로 간다. 거기서 멈추면 뒤쪽 구가
         * 영영 돌지 못한다 — 상세 미완이 많은 구 하나가 하루 한도를 다 쓴다.
         *
         * 대조가 실패한 구는 이유를 덮어쓰지 않는다. 표에 남은 대조 오류가
         * "왜 못 했는가"를 말하는데, 여기서 "스냅샷이 없다"로 바꾸면 원인이
         * 결과로 대체된다. */
        entries[index] =
          entry.reconcile === null
            ? { ...entry, outcome: "failed" }
            : { ...entry, outcome: "skipped", skipReason: plan.reason };
        flush(index + 1);
        continue;
      }

      const reconcile = entry.reconcile;
      if (reconcile === null) continue;
      entries[index] = { ...entry, outcome: "running" };
      flush(index);
      try {
        const started = await applyPlaceSync({
          areaCode: reconcile.area_code,
          districtCode: reconcile.district_code,
          snapshot: reconcile.snapshot,
          detailContentIds: [...reconcile.detail_content_ids, ...reconcile.detail_backfill_ids],
          addedContentIds: reconcile.rows
            .filter((row) => row.change_type === "added")
            .map((row) => row.content_id),
          dryRun: false,
          /* 상한을 걸지 않는다. 상한이 걸린 실행은 비활성화를 건너뛰어 사라진
           * 장소가 활성인 채로 남는다. 한도는 구 단위로 건너뛰어 지킨다. */
          detailsLimit: null,
          confirm: `${reconcile.area_code}-${reconcile.district_code}`,
        });
        const finished = await waitForJob(started.job_id, () => stopAllRef.current);
        /* 실제 호출 수로 센다. 중단으로 아직 running인 job은 그 값이 없으므로
         * 예상치로 대신 센다 — 적게 세면 남은 구에서 한도를 넘긴다. */
        spent += finished.result?.detail_attempted_count ?? plannedDetailCalls(reconcile);
        if (jobHitQuota(finished)) quotaExhausted = true;
        const { outcome, note } = jobOutcome(finished);
        entries[index] = { ...entry, outcome, job: finished, applyError: note };
      } catch (error) {
        entries[index] = {
          ...entry,
          outcome: "failed",
          applyError: toMessage(error, "반영을 시작하지 못했어요."),
        };
      }
      flush(index + 1);
    }

    setAllSync((current) => ({
      ...current,
      phase: "done",
      entries: [...entries],
      cursor: entries.length,
      spentDetailCalls: spent,
      quotaExhausted,
    }));
    // 구 목록도 함께 읽는다 — 건수와 오늘 사용량이 이번 순회로 바뀌었다.
    void loadDbStatus();
    void loadDistricts();
  }, [allSync.entries, dbStatus, loadDbStatus, loadDistricts]);

  /* 스냅샷 보관. 지울 후보 판정은 서버에만 둔다 — 화면이 따로 세면 미리보기와
   * 실제 정리가 갈라져, 보여준 것과 다른 파일이 지워진다. */
  const [retention, setRetention] = useState<SnapshotRetention | null>(null);
  const [retentionError, setRetentionError] = useState<string | null>(null);
  const [retentionLoading, setRetentionLoading] = useState(false);
  const [pruneResult, setPruneResult] = useState<SnapshotPruneResult | null>(null);
  const [pruning, setPruning] = useState(false);
  const [keep, setKeep] = useState(DEFAULT_KEEP);

  const loadRetention = useCallback(async (nextKeep: number) => {
    setRetentionLoading(true);
    try {
      setRetention(await fetchSnapshotRetention(nextKeep));
      setRetentionError(null);
    } catch (error) {
      setRetentionError(toMessage(error, "스냅샷 목록을 불러오지 못했어요."));
    } finally {
      setRetentionLoading(false);
    }
  }, []);

  const handleChangeKeep = useCallback(
    (nextKeep: number) => {
      setKeep(nextKeep);
      // 후보는 유지 개수에 따라 달라진다. 서버에 다시 물어야 표와 실제가 맞는다.
      void loadRetention(nextKeep);
    },
    [loadRetention],
  );

  const handlePrune = useCallback(
    async (input: { includeReconciliations: boolean }) => {
      setPruning(true);
      try {
        setPruneResult(
          await pruneSnapshots({
            keep,
            includeReconciliations: input.includeReconciliations,
            confirm: PRUNE_CONFIRM,
          }),
        );
        setRetentionError(null);
        // 지운 뒤 개수가 바뀌었다. 다시 읽지 않으면 이미 없는 파일을 계속 보여준다.
        await loadRetention(keep);
      } catch (error) {
        setRetentionError(toMessage(error, "정리하지 못했어요."));
      } finally {
        setPruning(false);
      }
    },
    [keep, loadRetention],
  );

  /* 집중률 매핑. 매핑이 없는 장소는 혼잡도 조회를 통째로 건너뛰므로(enrichment_service)
   * 장소 동기화 뒤에는 새로 만들어야 한다. 구 하나가 몇 초라 job을 두지 않고 화면이
   * 구를 골라 부른다. */
  const [concentration, setConcentration] = useState<ConcentrationStatus | null>(null);
  const [concentrationDistrict, setConcentrationDistrict] = useState<ConcentrationDistrict | null>(
    null,
  );
  const [concentrationResult, setConcentrationResult] = useState<ConcentrationBuildResult | null>(
    null,
  );
  const [concentrationApplied, setConcentrationApplied] = useState<ConcentrationApplyResult | null>(
    null,
  );
  const [concentrationError, setConcentrationError] = useState<string | null>(null);
  const [concentrationLoading, setConcentrationLoading] = useState(false);
  const [building, setBuilding] = useState(false);
  const [applyingMapping, setApplyingMapping] = useState(false);
  /* 승인한 애매한 후보. 기본은 전부 켠다 — 지금도 이 매칭들이 자동으로 CSV에
   * 들어가고 있어서, 기본을 꺼두면 있던 매핑이 조용히 사라진다. */
  const [approved, setApproved] = useState<Set<string>>(new Set());

  const loadConcentration = useCallback(async () => {
    setConcentrationLoading(true);
    try {
      const next = await fetchConcentrationStatus();
      setConcentration(next);
      setConcentrationDistrict((current) => current ?? next.districts[0] ?? null);
      setConcentrationError(null);
    } catch (error) {
      setConcentrationError(toMessage(error, "집중률 매핑 현황을 불러오지 못했어요."));
    } finally {
      setConcentrationLoading(false);
    }
  }, []);

  /* 구를 바꾸면 앞 구의 결과를 비운다. 남겨두면 종로구를 생성한 화면에서 중구를
   * 적재하는 조작이 가능해 보인다 — 서버가 확인 문자열로 막지만, 화면이 그런
   * 조작을 제안하는 것 자체가 잘못이다. */
  const handleSelectConcentrationDistrict = useCallback((district: ConcentrationDistrict) => {
    setConcentrationDistrict(district);
    setConcentrationResult(null);
    setConcentrationApplied(null);
    setApproved(new Set());
    setConcentrationError(null);
  }, []);

  const handleBuildConcentration = useCallback(async () => {
    if (!concentrationDistrict) return;
    setBuilding(true);
    setConcentrationApplied(null);
    try {
      const next = await buildConcentrationMapping({
        areaCode: concentrationDistrict.area_code,
        districtCode: concentrationDistrict.district_code,
      });
      setConcentrationResult(next);
      setApproved(new Set(next.ambiguous.map((row) => row.content_id)));
      setConcentrationError(null);
    } catch (error) {
      setConcentrationError(toMessage(error, "매핑을 만들지 못했어요."));
    } finally {
      setBuilding(false);
    }
  }, [concentrationDistrict]);

  const handleToggleApproved = useCallback((contentId: string) => {
    setApproved((current) => {
      const next = new Set(current);
      if (next.has(contentId)) next.delete(contentId);
      else next.add(contentId);
      return next;
    });
  }, []);

  const handleApplyConcentration = useCallback(
    async (input: { confirm: string }) => {
      if (!concentrationResult) return;
      setApplyingMapping(true);
      try {
        const { rows, rejections } = splitApproval(concentrationResult, approved);
        setConcentrationApplied(
          await applyConcentrationMapping({
            areaCode: concentrationResult.area_code,
            districtCode: concentrationResult.district_code,
            rows,
            rejections,
            confirm: input.confirm,
          }),
        );
        setConcentrationError(null);
        // 매핑 수가 바뀌었다. 다시 읽지 않으면 표가 적재 전 수를 계속 보여준다.
        await loadConcentration();
      } catch (error) {
        setConcentrationError(toMessage(error, "매핑을 적재하지 못했어요."));
      } finally {
        setApplyingMapping(false);
      }
    },
    [approved, concentrationResult, loadConcentration],
  );

  const allBusy = allSync.phase === "reconciling" || allSync.phase === "applying";

  const handleReset = useCallback(async () => {
    try {
      setUsage(await resetApiUsage());
      setUsageError(null);
    } catch (error) {
      setUsageError(toMessage(error, "카운터를 초기화하지 못했어요."));
    }
  }, []);

  useEffect(() => {
    void loadUsage();
    void loadDbStatus();
    void loadDistricts();
    void loadFeedbackStats();
    void loadTraceStats();
    void loadRetention(DEFAULT_KEEP);
    void loadConcentration();
  }, [
    loadConcentration,
    loadDbStatus,
    loadDistricts,
    loadFeedbackStats,
    loadRetention,
    loadTraceStats,
    loadUsage,
  ]);

  // 폴링은 호출량에만 건다. DB 상태는 844행을 훑어 Supabase 호출이 따라붙으므로
  // 3초마다 부르면 패널 자체가 트래픽을 만든다.
  const loadUsageRef = useRef(loadUsage);
  loadUsageRef.current = loadUsage;
  useEffect(() => {
    if (!autoRefresh) return;
    const timer = window.setInterval(() => {
      void loadUsageRef.current();
    }, AUTO_REFRESH_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [autoRefresh]);

  // 실행 중인 job은 1초마다 확인한다. 진행률은 서버 메모리에만 있어(place_sync_runs
  // 행은 시작·종료만 남는다) 폴링 말고는 알 방법이 없다.
  const jobId = job?.status === "running" ? job.job_id : null;
  useEffect(() => {
    if (!jobId) return;
    const timer = window.setInterval(async () => {
      try {
        const next = await fetchSyncJob(jobId);
        setJob(next);
        if (next.status !== "running") {
          // 끝났으면 DB 상태를 다시 읽어 반영 결과를 화면에 맞춘다. 구 목록도
          // 함께 읽는다 — 이번에 처음 적재한 구는 여기서부터 건수가 생긴다.
          void loadDbStatus();
          void loadDistricts();
        }
      } catch (error) {
        setSyncError(toMessage(error, "job 상태를 불러오지 못했어요."));
      }
    }, JOB_POLL_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [jobId, loadDbStatus, loadDistricts]);

  return (
    <main className="min-h-screen bg-gray-50 text-gray-950 dark:bg-gray-950 dark:text-gray-50">
      <header className="flex items-center justify-between gap-3 border-b border-gray-200 bg-white px-5 py-4 dark:border-gray-800 dark:bg-gray-900">
        <div>
          <h1 className="text-xl font-bold">TripBranch Ops</h1>
          <p className="text-sm text-gray-500 dark:text-gray-400">
            외부 API 호출량 · 장소 DB 상태 · 동기화 (로컬 전용)
          </p>
        </div>
        <div className="flex gap-2">
          {/* /dev-chat 라우트 자체가 배포 빌드엔 없다(App.tsx) — 이 페이지 상단이
              이미 "(로컬 전용)"이라 밝히고 있는 것과 같은 이유로, 없는 라우트로
              가는 깨진 링크를 만들지 않는다. */}
          {import.meta.env.DEV && (
            <button
              type="button"
              onClick={() => navigate("/dev-chat")}
              className="rounded-md border border-gray-300 px-3 py-1.5 text-sm dark:border-gray-700"
            >
              개발자 채팅
            </button>
          )}
          <button
            type="button"
            onClick={() => navigate("/chat")}
            className="rounded-md border border-gray-300 px-3 py-1.5 text-sm dark:border-gray-700"
          >
            사용자 화면
          </button>
        </div>
      </header>

      {/* 패널을 탭으로 가르되 상태와 순회 루프는 이 페이지가 그대로 들고 있다.
       * 상태까지 자식으로 내리면 순회 도중에 탭을 옮기는 순간 컴포넌트가
       * 언마운트되면서 25개 구를 돌던 루프가 끊긴다. */}
      <div className="mx-auto flex max-w-6xl flex-col gap-4 px-5 py-5 sm:flex-row">
        <OpsNav tab={tab} syncRunning={allBusy} onSelect={selectTab} />

        <div className="flex min-w-0 flex-1 flex-col gap-4">
          {tab === "observe" ? (
            <>
              <ApiUsagePanel
                snapshot={usage}
                error={usageError}
                autoRefresh={autoRefresh}
                onToggleAutoRefresh={setAutoRefresh}
                onRefresh={() => void loadUsage()}
                onReset={() => void handleReset()}
              />
              <DbStatusPanel
                status={dbStatus}
                error={dbError}
                loading={dbLoading}
                onRefresh={() => void loadDbStatus()}
              />
              <FeedbackStatsPanel
                stats={feedbackStats}
                error={feedbackStatsError}
                loading={feedbackStatsLoading}
                onRefresh={() => void loadFeedbackStats()}
              />
              <TracePanel
                stats={traceStats}
                error={traceStatsError}
                loading={traceStatsLoading}
                onRefresh={() => void loadTraceStats()}
              />
            </>
          ) : tab === "categories" ? (
            <CategoryCoveragePanel
              status={dbStatus}
              error={dbError}
              loading={dbLoading}
              onRefresh={() => void loadDbStatus()}
            />
          ) : (
            <>
              <PlaceSyncPanel
                districts={districts}
                selected={selectedDistrict}
                reconcile={reconcile}
                job={job}
                error={syncError}
                reconciling={reconciling}
                applying={applying}
                /* 잔여를 계산하지 않는 이유: 이 값도 하한이다. 재시도가 안 세지고,
                 * 완료 처리를 못 한 실행은 사용량이 비어 있다. 한도에서 빼면 실제보다
                 * 여유가 있는 것처럼 보인다. */
                detailCallsToday={dbStatus?.detail_calls_today ?? null}
                /* 전 구 순회 중에는 구 단위 조작을 막는다. 서버가 job 하나만 허용해
                 * 409로 거부되긴 하지만, 화면이 그런 조작을 제안하는 것 자체가 잘못이다. */
                busy={allBusy}
                onSelectDistrict={handleSelectDistrict}
                onReconcile={() => void handleReconcile()}
                onApply={(input) => void handleApply(input)}
              />
              <AllDistrictSyncPanel
                districts={districts?.loaded ?? []}
                state={allSync}
                detailCallsToday={dbStatus?.detail_calls_today ?? null}
                busy={reconciling || applying || job?.status === "running"}
                onReconcileAll={() => void handleReconcileAll("api")}
                onReuseSnapshots={() => void handleReconcileAll("saved")}
                onApplyAll={() => void handleApplyAll()}
                onCancel={handleCancelAll}
              />
              <SnapshotRetentionPanel
                retention={retention}
                result={pruneResult}
                error={retentionError}
                loading={retentionLoading}
                pruning={pruning}
                keep={keep}
                /* 반영은 대조가 남긴 스냅샷 파일을 읽는다. 그 사이 지우면 돌고 있는
                 * 동기화가 읽을 파일이 사라진다. */
                busy={allBusy || reconciling || applying || job?.status === "running"}
                onChangeKeep={handleChangeKeep}
                onRefresh={() => void loadRetention(keep)}
                onPrune={(input) => void handlePrune(input)}
              />
              <ConcentrationMappingPanel
                status={concentration}
                selected={concentrationDistrict}
                result={concentrationResult}
                applyResult={concentrationApplied}
                error={concentrationError}
                loading={concentrationLoading}
                building={building}
                applying={applyingMapping}
                /* 같은 구의 장소를 읽는 중에 매핑을 만들면 방금 들어온 장소가
                 * 빠진 채로 붙는다. */
                busy={allBusy || reconciling || applying || job?.status === "running"}
                approved={approved}
                onSelectDistrict={handleSelectConcentrationDistrict}
                onToggleApproved={handleToggleApproved}
                onRefresh={() => void loadConcentration()}
                onBuild={() => void handleBuildConcentration()}
                onApply={(input) => void handleApplyConcentration(input)}
              />
            </>
          )}
        </div>
      </div>
    </main>
  );
}
