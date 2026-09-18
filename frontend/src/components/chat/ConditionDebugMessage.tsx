/*
 * 역할: 개발 모드에서 사용자 입력 해석 결과를 채팅 메시지로 표시한다.
 * 입력: 원문 입력, 구조화된 조건, 적용 상태.
 * 출력: LLM이 추출한 조건 전체와 추천 요청에 실제로 전달된 값.
 *
 * Agent(/api/chat)가 해석과 추천을 한 번에 끝내므로 중간에 사용자가 진행을
 * 승인하는 단계가 없다 — 카드는 "무엇으로 추천됐는지" 확인용으로만 남는다.
 * 호출 시점: ChatPage가 condition_debug 메시지를 렌더링할 때 호출된다.
 * TODO: 조건 직접 수정이 필요해지면 이 컴포넌트 안에 편집 form을 추가한다.
 */

import type { Intent, InterpretedConditions, UserConditions } from "../../types";

/*
 * LLM이 추출하는 조건 전체를 표시 순서대로 나열한다. 구형 4필드(location_query 등)만
 * 보여주면 weather_intent/environment처럼 발화의 핵심이 화면에 드러나지 않는다.
 */
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
  if (value === null || value === undefined || value === "") return "없음";
  return String(value);
}

interface ConditionDebugMessageProps {
  userInput: string;
  conditions: InterpretedConditions;
  mergedConditions: UserConditions | null;
  deviceLocation: string | null;
  intent: Intent | null;
  status: "pending" | "confirmed";
}

/* 이번 턴에 LLM이 실제로 값을 뽑은 필드만 "필드=값" 형태로 나열한다. */
function extractedSummary(conditions: UserConditions | null | undefined) {
  if (!conditions) return "없음";
  const filled = CONDITION_LABELS.filter(([key]) => {
    const value = conditions[key];
    return Array.isArray(value) ? value.length > 0 : value !== null && value !== undefined;
  }).map(([key, label]) => `${label}=${formatValue(conditions[key])}`);
  return filled.length ? filled.join(" / ") : "없음";
}

export function ConditionDebugMessage({
  userInput,
  conditions,
  mergedConditions,
  deviceLocation,
  intent,
  status,
}: ConditionDebugMessageProps) {
  // 실제 추천에 쓰이는 값은 B가 병합한 누적 조건이다. 되묻기 턴에서는 이번 턴
  // 추출분에 앞 턴 조건이 남아 있지 않으므로 둘을 구분해서 보여준다.
  const effective = mergedConditions ?? conditions.raw_conditions ?? null;
  return (
    <article className="mr-auto flex w-full max-w-xl flex-col gap-3 rounded-md border border-dashed border-amber-300 bg-amber-50 p-4 text-sm text-amber-950 dark:border-amber-700 dark:bg-amber-950/30 dark:text-amber-100">
      <div className="flex items-center justify-between gap-2">
        <h2 className="font-semibold">개발용 입력 해석 결과 (누적 조건)</h2>
        <div className="flex items-center gap-2">
          <span className="rounded bg-amber-200 px-2 py-0.5 text-xs font-semibold text-amber-900 dark:bg-amber-800 dark:text-amber-100">
            Intent: {intent ?? "미확인"}
          </span>
          <span className="rounded bg-amber-200 px-2 py-0.5 text-xs text-amber-900 dark:bg-amber-800 dark:text-amber-100">
            {status === "confirmed" ? "적용됨" : "확인 대기"}
          </span>
        </div>
      </div>

      <dl className="grid gap-2">
        <div>
          <dt className="font-medium">사용자 원문</dt>
          <dd>{userInput}</dd>
        </div>
        <div>
          <dt className="font-medium">기기 GPS (위도,경도)</dt>
          <dd>
            {deviceLocation ? `${deviceLocation} · 장소 검색 기준으로 사용` : "미사용"}
          </dd>
        </div>
        {effective ? (
          CONDITION_LABELS.map(([key, label]) => (
            <div key={key}>
              <dt className="font-medium">{label}</dt>
              <dd>{formatValue(effective[key])}</dd>
            </div>
          ))
        ) : (
          <div>
            <dt className="font-medium">누적 조건</dt>
            <dd>없음 (RECOMMEND 조건이 비어 있음)</dd>
          </div>
        )}
      </dl>

      <dl className="grid gap-2 border-t border-amber-300 pt-3 dark:border-amber-700">
        <div>
          <dt className="font-medium">이번 턴에 추출된 조건</dt>
          <dd className="text-xs">{extractedSummary(conditions.raw_conditions)}</dd>
        </div>
      </dl>

    </article>
  );
}
