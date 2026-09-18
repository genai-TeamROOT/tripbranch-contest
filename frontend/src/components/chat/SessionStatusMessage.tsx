/*
 * 역할: 로컬 테스트용 "/status" 명령의 결과(현재 세션의 누적 UserConditions)를 표시한다.
 * 입력: GET /api/state/{session_id} 응답 또는 조회 실패 사유.
 * 출력: 누적 조건 전체와 세션 메타(조건 버전, 노출/제외 place 수, api_context).
 * 호출 시점: ChatMessageList가 session_status 메시지를 렌더링할 때 호출된다.
 *
 * 이 화면은 커밋하지 않는 로컬 확인용이다 — 조건 카드(ConditionDebugMessage)와 달리
 * 대화 턴과 무관하게 "지금 서버가 들고 있는 값"만 보여준다.
 */

import type { SessionContextResponse, UserConditions } from "../../types";

const CONDITION_LABELS: [keyof UserConditions, string][] = [
  ["current_location", "현재 위치"],
  ["search_center", "검색 중심"],
  ["place_types", "장소 종류"],
  ["place_tags", "장소 태그"],
  ["weather", "날씨"],
  ["weather_intent", "날씨 의도"],
  ["concentration_intent", "혼잡도 의도"],
  ["transport", "이동 수단"],
  ["max_travel_time", "최대 이동 시간(분)"],
  ["travel_origin", "이동시간 출발점"],
  ["time_available", "가용 시간(분)"],
  ["environment", "실내외"],
  ["companion", "동행"],
  ["budget", "예산"],
  ["exclude_tags", "제외 태그"],
  ["special_requirements", "특별 요구사항"],
  ["accessibility_needs", "무장애 요구"],
  ["taste_query", "취향 발화"],
];

function formatValue(value: unknown): string {
  if (Array.isArray(value)) return value.length ? value.join(", ") : "없음";
  if (typeof value === "boolean") return value ? "예" : "아니오";
  if (value === null || value === undefined || value === "") return "없음";
  return String(value);
}

interface SessionStatusMessageProps {
  status: SessionContextResponse | null;
  error: string | null;
}

export function SessionStatusMessage({ status, error }: SessionStatusMessageProps) {
  return (
    <article className="mr-auto flex w-full max-w-xl flex-col gap-3 rounded-md border border-dashed border-sky-300 bg-sky-50 p-4 text-sm text-sky-950 dark:border-sky-700 dark:bg-sky-950/30 dark:text-sky-100">
      <h2 className="font-semibold">/status — 현재 세션 상태 (로컬 확인용)</h2>

      {error || !status ? (
        <p>{error ?? "세션 상태를 불러오지 못했어요."}</p>
      ) : (
        <>
          <dl className="grid gap-2">
            <div>
              <dt className="font-medium">세션</dt>
              <dd className="break-all text-xs">
                {status.session_id ?? "없음"} / 존재={formatValue(status.session_exists)} / 조건
                버전={status.condition_version}
              </dd>
            </div>
            <div>
              <dt className="font-medium">추천 이력</dt>
              <dd className="text-xs">
                추천됨={formatValue(status.has_recommendation)} / 추천 수=
                {status.recommended_count} / 노출={status.shown_place_ids.length} / 제외=
                {status.excluded_place_ids.length}
              </dd>
            </div>
            <div>
              <dt className="font-medium">직전 턴</dt>
              <dd className="text-xs">
                intent={formatValue(status.last_intent)} / 되묻기=
                {formatValue(status.pending_clarification)}
              </dd>
            </div>
            <div>
              <dt className="font-medium">api_context</dt>
              <dd className="text-xs">
                gps={formatValue(status.api_context.gps_location)} (만료=
                {formatValue(status.api_context.gps_expired)}) / weather=
                {formatValue(status.api_context.api_weather)} (만료=
                {formatValue(status.api_context.weather_expired)})
              </dd>
            </div>
          </dl>

          <dl className="grid gap-2 border-t border-sky-300 pt-3 dark:border-sky-700">
            {CONDITION_LABELS.map(([key, label]) => (
              <div key={key}>
                <dt className="font-medium">{label}</dt>
                <dd>{formatValue(status.user_conditions[key])}</dd>
              </div>
            ))}
          </dl>
        </>
      )}
    </article>
  );
}
