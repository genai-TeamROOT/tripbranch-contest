/*
 * 역할: LLMOps 실행 Trace 통계 API 함수.
 * 입력: 통계 조회 시 since·until·recentErrorsLimit.
 * 출력: 백엔드가 반환한 step별 집계 + 최근 에러 목록.
 * 호출 시점: dev-ops 패널(TracePanel, TP-157)이 호출한다 — 이 엔드포인트는
 *      dev.ts의 다른 함수들과 달리 APP_ENV=local 여부와 무관하게 항상 등록돼 있다
 *      (backend/app/main.py, trace_router는 무조건 include).
 * 참고: backend/app/routes/trace.py GET /api/trace/stats.
 */

import { apiClient } from "./client";
import type { TraceStatsResponse } from "../types";

export function fetchTraceStats(params?: {
  since?: string;
  until?: string;
  recentErrorsLimit?: number;
}) {
  const query = new URLSearchParams();
  if (params?.since) query.set("since", params.since);
  if (params?.until) query.set("until", params.until);
  if (params?.recentErrorsLimit !== undefined) {
    query.set("recent_errors_limit", String(params.recentErrorsLimit));
  }
  const qs = query.toString();
  return apiClient.get<TraceStatsResponse>(`/trace/stats${qs ? `?${qs}` : ""}`);
}
