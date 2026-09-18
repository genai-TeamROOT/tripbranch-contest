/*
 * 역할: 응답 피드백(좋아요/싫어요) API 함수.
 * 입력: session_id, run_id, rating / 통계 조회 시 since·until·top_intents.
 * 출력: 백엔드가 반환한 기록 시각 / 집계 결과.
 * 호출 시점: RecommendationResultMessage/ScheduleResultMessage의 피드백 버튼 클릭 시.
 *      통계는 dev-ops 패널(FeedbackStatsPanel, TP-146)이 호출한다 — 이 엔드포인트는
 *      dev.ts의 다른 함수들과 달리 APP_ENV=local 여부와 무관하게 항상 등록돼 있다
 *      (backend/app/main.py, feedback_router는 무조건 include).
 * 참고: backend/app/routes/feedback.py POST /api/feedback, GET /api/feedback/stats.
 */

import { apiClient } from "./client";
import type {
  FeedbackStatsResponse,
  RecordFeedbackRequest,
  RecordFeedbackResponse,
} from "../types";

export function sendFeedback(request: RecordFeedbackRequest) {
  return apiClient.post<RecordFeedbackResponse>("/feedback", request);
}

export function fetchFeedbackStats(params?: {
  since?: string;
  until?: string;
  topIntents?: number;
}) {
  const query = new URLSearchParams();
  if (params?.since) query.set("since", params.since);
  if (params?.until) query.set("until", params.until);
  if (params?.topIntents !== undefined) {
    query.set("top_intents", String(params.topIntents));
  }
  const qs = query.toString();
  return apiClient.get<FeedbackStatsResponse>(`/feedback/stats${qs ? `?${qs}` : ""}`);
}
