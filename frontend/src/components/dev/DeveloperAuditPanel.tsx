/*
 * 역할: /dev-chat 화면 오른쪽에서 발화별 Agent Runtime 실행 결과를 감사용으로 표시한다.
 * 입력: TripContext에 누적된 DeveloperAuditTurn 배열과 현재 선택된 turn id.
 * 출력: 발화 카드 목록, 선택 발화의 LLM/B/C/D/Raw JSON 상세.
 * 호출 시점: DeveloperChatPage가 개발자용 채팅 화면을 렌더링할 때 호출된다.
 */

import { useMemo, useState } from "react";
import { scheduleTravelLabel } from "../../utils/scheduleTravel";
import { PLACE_CATEGORY_LABELS } from "../../utils/placeCategory";
import type {
  CandidateConcentrationDebug,
  DeveloperAuditTurn,
  LLMExecutionMetadata,
  AgentStageTiming,
  RecommendationItem,
  ScheduleItem,
  ToolContextItemDebug,
  ToolExecutionDebug,
  ToolProviderDebug,
  UserConditions,
} from "../../types";

type AuditTab = "summary" | "timing" | "llm" | "state" | "tools" | "scoring" | "raw";

const TABS: { id: AuditTab; label: string }[] = [
  { id: "summary", label: "요약" },
  { id: "timing", label: "소요시간" },
  { id: "llm", label: "LLM 추출" },
  { id: "state", label: "B 상태" },
  { id: "tools", label: "C Tool" },
  { id: "scoring", label: "D Scoring" },
  { id: "raw", label: "Raw JSON" },
];

const CONDITION_LABELS: [keyof UserConditions, string][] = [
  ["current_location", "현재 위치"],
  ["search_center", "검색 중심"],
  ["place_types", "장소 종류"],
  ["place_tags", "장소 태그"],
  ["weather", "날씨"],
  ["weather_intent", "날씨 의도"],
  ["concentration_intent", "혼잡도 의도"],
  ["transport", "이동 수단"],
  ["max_travel_time", "최대 이동 시간"],
  ["travel_origin", "이동시간 출발점"],
  ["time_available", "가용 시간"],
  ["environment", "실내외"],
  ["companion", "동행"],
  ["budget", "예산"],
  ["exclude_tags", "제외 태그"],
  ["special_requirements", "특별 요구사항"],
  ["accessibility_needs", "무장애 요구"],
  ["taste_query", "취향 발화"],
];

function formatDuration(milliseconds: number | null | undefined) {
  if (typeof milliseconds !== "number" || !Number.isFinite(milliseconds)) return "-";
  return milliseconds >= 1000
    ? `${(milliseconds / 1000).toFixed(1)}초`
    : `${Math.round(milliseconds)}ms`;
}

const STAGE_PRESENTATION: Record<AgentStageTiming["stage"], { label: string; owner: string }> = {
  interpreting: { label: "LLM 의도·조건 추출", owner: "A → Gemini" },
  merging_conditions: { label: "세션 상태 병합", owner: "A → B" },
  fetching_context: { label: "장소·정보 조회", owner: "A → C" },
  scoring: { label: "추천 순위 계산", owner: "A → D" },
  scheduling: { label: "일정 편성", owner: "A → 일정 플래너·Gemini" },
  composing_message: { label: "답변 생성·정리", owner: "A → Gemini" },
};

function formatValue(value: unknown): string {
  if (Array.isArray(value)) return value.length ? value.join(", ") : "없음";
  if (value === null || value === undefined || value === "") return "없음";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function compactJson(value: unknown) {
  return JSON.stringify(value, null, 2);
}

function getRecommendationItems(turn: DeveloperAuditTurn): RecommendationItem[] {
  if (!turn.recommendations) return [];
  return [
    ...turn.recommendations.recommendations,
    ...turn.recommendations.unverified_recommendations,
  ];
}

/** SCHEDULE 응답은 recommendations가 아니라 response.schedule로 온다
 * (agent_runtime.py가 SCHEDULE일 때 recommendations를 null로 보낸다) —
 * getRecommendationItems()만 쓰면 SCHEDULE 턴은 항상 0건으로 보인다. */
function getScheduleItems(turn: DeveloperAuditTurn): ScheduleItem[] {
  return turn.response?.schedule?.items ?? [];
}

function isScheduleTurn(turn: DeveloperAuditTurn): boolean {
  return turn.intent === "SCHEDULE";
}

function getResultCountLabel(turn: DeveloperAuditTurn): string {
  return isScheduleTurn(turn)
    ? `일정 ${getScheduleItems(turn).length}곳`
    : `추천 ${getRecommendationItems(turn).length}건`;
}

function getConditionChanges(before: UserConditions | null, after: UserConditions | null) {
  if (!after) return [];
  return CONDITION_LABELS.filter(([key]) => {
    const beforeValue = before?.[key] ?? null;
    const afterValue = after[key] ?? null;
    return JSON.stringify(beforeValue) !== JSON.stringify(afterValue);
  }).map(([key, label]) => ({
    key,
    label,
    before: before?.[key] ?? null,
    after: after[key] ?? null,
  }));
}

type ConditionSummaryEntry = {
  key: keyof UserConditions;
  label: string;
  value: unknown;
};

const CONDITION_LABEL_BY_KEY = new Map(CONDITION_LABELS);

const CONDITION_VALUE_LABELS: Partial<Record<keyof UserConditions, Record<string, string>>> = {
  // 분류 칩(상세 모달)과 같은 표를 쓴다 — 같은 코드가 화면마다 다른 말로 불리면 안 된다.
  place_types: PLACE_CATEGORY_LABELS,
  place_tags: {
    cafe: "카페",
    museum: "박물관",
    park: "공원",
  },
  weather: {
    rain: "비",
    snow: "눈",
    hot: "더위",
    cold: "추위",
  },
  weather_intent: {
    AVOID: "날씨 피하기",
    ENJOY: "날씨 즐기기",
    IGNORE: "상관없음",
    NO_MENTION: "언급 없음",
  },
  concentration_intent: {
    AVOID: "조용한 곳 선호",
    SEEK: "핫플·활기찬 곳 선호",
    IGNORE: "상관없음",
  },
  transport: {
    WALK: "도보",
    TRANSIT: "대중교통",
    CAR: "자동차",
  },
  environment: {
    indoor: "실내",
    outdoor: "실외",
    any: "실내외 상관없음",
  },
  travel_origin: {
    user_location: "사용자 위치 기준",
    search_center: "검색 중심점 기준",
  },
  accessibility_needs: {
    wheelchair_access: "휠체어 접근",
    stroller_access: "유모차 접근",
    accessible_restroom: "장애인 화장실",
    accessible_parking: "장애인 주차",
    visual_guide: "시각 안내",
    infant_facilities: "유아 시설",
    wheelchair_rental: "휠체어 대여",
    seating_available: "입식 좌석",
    low_floor_transit: "저상 교통수단",
  },
};

function isDefaultIntentValue(key: keyof UserConditions, value: unknown) {
  return key === "weather_intent" && (value === "NO_MENTION" || value === "IGNORE");
}

function hasConditionValue(value: unknown) {
  return !(
    value === null ||
    value === undefined ||
    value === "" ||
    (Array.isArray(value) && value.length === 0)
  );
}

function formatConditionValue(key: keyof UserConditions, value: unknown): string {
  if (Array.isArray(value)) {
    return value.map((item) => formatConditionValue(key, item)).join(", ");
  }
  if (typeof value !== "string") return formatValue(value);

  const koreanLabel = CONDITION_VALUE_LABELS[key]?.[value];
  return koreanLabel ? `${value} (${koreanLabel})` : value;
}

function getConditionEntries(
  conditions: UserConditions,
  keys: readonly (keyof UserConditions)[] = CONDITION_LABELS.map(([key]) => key),
): ConditionSummaryEntry[] {
  return keys.flatMap((key) => {
    const value = conditions[key];
    const label = CONDITION_LABEL_BY_KEY.get(key) ?? key;
    if (!hasConditionValue(value) || isDefaultIntentValue(key, value)) return [];
    return [{ key, label, value }];
  });
}

function getLlmExtractedConditionEntries(turn: DeveloperAuditTurn): ConditionSummaryEntry[] {
  const output = turn.response?.llm_output;
  if (!output) return [];

  if (output.recommend) {
    return getConditionEntries(output.recommend.conditions);
  }

  if (output.modify?.condition_changes) {
    const changes = output.modify.condition_changes;
    return output.modify.changed_fields.map((key) => ({
      key: key as keyof UserConditions,
      label: CONDITION_LABEL_BY_KEY.get(key as keyof UserConditions) ?? key,
      value: changes[key as keyof UserConditions],
    }));
  }

  return [];
}

function ConditionSummaryCard({
  title,
  description,
  entries,
  emptyMessage,
}: {
  title: string;
  description: string;
  entries: ConditionSummaryEntry[];
  emptyMessage: string;
}) {
  return (
    <section className="rounded-lg border border-gray-200 bg-white p-3 dark:border-gray-800 dark:bg-gray-900">
      <h3 className="text-sm font-semibold text-gray-900 dark:text-gray-100">{title}</h3>
      <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">{description}</p>
      {entries.length === 0 ? (
        <p className="mt-3 rounded bg-gray-100 px-2.5 py-2 text-xs text-gray-600 dark:bg-gray-800 dark:text-gray-300">
          {emptyMessage}
        </p>
      ) : (
        <dl className="mt-3 flex flex-wrap gap-2">
          {entries.map((entry) => (
            <div
              key={entry.key}
              className="rounded-md border border-emerald-200 bg-emerald-50 px-2.5 py-2 dark:border-emerald-900 dark:bg-emerald-950/40"
            >
              <dt className="text-[11px] font-medium text-emerald-800 dark:text-emerald-200">
                {entry.label}
              </dt>
              <dd className="mt-0.5 text-xs font-semibold text-emerald-950 dark:text-emerald-50">
                {hasConditionValue(entry.value)
                  ? formatConditionValue(entry.key, entry.value)
                  : "해제"}
              </dd>
            </div>
          ))}
        </dl>
      )}
    </section>
  );
}

function toLlmExecutionMetadata(value: unknown): LLMExecutionMetadata | null {
  if (!value || typeof value !== "object") return null;
  const calls = (value as { calls?: unknown }).calls;
  if (!Array.isArray(calls)) return null;
  return {
    calls: calls.flatMap((call) => {
      if (!call || typeof call !== "object") return [];
      const entry = call as Record<string, unknown>;
      if (typeof entry.operation !== "string" || !Array.isArray(entry.attempted_models)) return [];
      return [
        {
          operation: entry.operation,
          attempted_models: entry.attempted_models.filter(
            (model): model is string => typeof model === "string",
          ),
          served_model: typeof entry.served_model === "string" ? entry.served_model : null,
          latency_ms: typeof entry.latency_ms === "number" ? entry.latency_ms : null,
          retry_count: typeof entry.retry_count === "number" ? entry.retry_count : null,
        },
      ];
    }),
  };
}

function getLlmExecution(turn: DeveloperAuditTurn): LLMExecutionMetadata | null {
  if (turn.response?.llm_execution) return turn.response.llm_execution;
  const details = turn.failure?.details;
  if (!details || typeof details !== "object") return null;
  return toLlmExecutionMetadata((details as { llm_execution?: unknown }).llm_execution);
}

function LlmExecutionCards({ execution }: { execution: LLMExecutionMetadata | null }) {
  if (!execution?.calls.length) {
    return (
      <p className="rounded-md bg-amber-50 p-3 text-xs text-amber-900 dark:bg-amber-950/30 dark:text-amber-100">
        LLM 실행 모델 정보가 없습니다. Fake LLM 사용, LLM 호출 전 실패, 또는 이전 응답일 수
        있습니다.
      </p>
    );
  }
  return (
    <div className="flex flex-col gap-2">
      {execution.calls.map((call, index) => {
        const usedFallback = call.attempted_models.length > 1;
        return (
          <section
            key={`${call.operation}-${index}`}
            className="rounded-md border border-gray-200 bg-white p-3 dark:border-gray-800 dark:bg-gray-900"
          >
            <p className="text-xs font-semibold text-gray-900 dark:text-gray-100">
              {index + 1}. {call.operation}
            </p>
            <p className="mt-2 text-xs text-gray-500">시도: {call.attempted_models.join(" → ")}</p>
            <p className="mt-1 text-xs text-gray-700 dark:text-gray-300">
              응답 모델: {call.served_model ?? "응답 없음(실패)"}
              {usedFallback ? " · 폴백 시도" : ""}
            </p>
            {call.retry_count != null ? (
              <p
                className={
                  call.retry_count > 0
                    ? "mt-1 text-xs text-amber-700 dark:text-amber-300"
                    : "mt-1 text-xs text-gray-500 dark:text-gray-400"
                }
              >
                {call.retry_count > 0
                  ? `시도 ${call.retry_count + 1}회 끝에 ${
                      call.served_model ? "성공" : "실패"
                    } — 소요 시간에 재시도 대기가 포함돼 있어요.`
                  : "시도 1회로 끝났어요 — 재시도 없음."}
              </p>
            ) : null}
          </section>
        );
      })}
    </div>
  );
}

const CONTEXT_ITEM_LABELS: Record<string, string> = {
  location: "위치 해석",
  weather: "날씨",
  places: "장소 후보",
  holidays: "공휴일",
  concentration: "혼잡도 조회",
  concentration_candidates: "후보 혼잡도 보강",
  comparison_candidates: "비교 후보",
};

const TOOL_OPERATION_LABELS: Record<NonNullable<ToolExecutionDebug["operation"]>, string> = {
  context_fetch: "기본 Context 조회",
  info_concentration: "INFO 혼잡도 조회",
  info_realtime_commercial: "INFO 실시간 카페 상권 조회",
  info_realtime_population: "INFO 실시간 인구 혼잡도 조회",
  info_realtime_citydata: "INFO 실시간 도시데이터 조회",
  candidate_enrichment: "후보 혼잡도 보강",
  compare_fetch: "COMPARE 후보 조회",
};

/** Provider 이름에 stub/fake가 들어가면 실제 외부 API가 아니라는 뜻이다.
 * D-042(Real 실패 시 Fake로 자동 전환하지 않는다)를 화면에서 바로 확인하기 위해
 * 다른 색으로 구분한다 — "테스트 카페"가 조용히 추천되는 상황을 눈으로 잡는다. */
function isFakeSource(source: string) {
  const normalized = source.toLowerCase();
  return normalized.includes("stub") || normalized.includes("fake");
}

function ToolProviderCards({ providers }: { providers: ToolProviderDebug[] }) {
  if (!providers.length) {
    return (
      <p className="rounded-md bg-amber-50 p-3 text-xs text-amber-900 dark:bg-amber-950/30 dark:text-amber-100">
        Provider 호출 기록이 없습니다. C가 모든 항목을 캐시로 처리했거나 조회 전에 종료된 요청일 수
        있습니다.
      </p>
    );
  }
  return (
    <div className="flex flex-col gap-2">
      {providers.map((provider, index) => (
        <section
          key={`${provider.source}-${provider.status}-${index}`}
          className={
            isFakeSource(provider.source)
              ? "rounded-md border border-amber-300 bg-amber-50 p-3 dark:border-amber-700 dark:bg-amber-950/30"
              : "rounded-md border border-gray-200 bg-white p-3 dark:border-gray-800 dark:bg-gray-900"
          }
        >
          <p className="text-xs font-semibold text-gray-900 dark:text-gray-100">
            {provider.source}
            {isFakeSource(provider.source) ? " · 실제 API 아님" : ""}
          </p>
          <p className="mt-1 text-xs text-gray-700 dark:text-gray-300">
            상태: {provider.status} · 조회 시각: {provider.retrieved_at ?? "없음"}
          </p>
        </section>
      ))}
    </div>
  );
}

function CandidateConcentrationRows({ rows }: { rows: CandidateConcentrationDebug[] }) {
  const proxyCount = rows.filter((row) => row.is_proxy).length;
  return (
    <>
      <h4 className="text-xs font-semibold text-gray-500 dark:text-gray-400">후보별 혼잡도 출처</h4>
      <p className="text-xs text-gray-500 dark:text-gray-400">
        {proxyCount > 0
          ? `근사치 ${proxyCount}건 — 집중률 매핑이 없어 인근 매핑 장소의 값을 빌렸어요. 후보 본인의 혼잡도가 아니에요.`
          : "값이 있는 후보는 모두 자기 매핑으로 직접 조회했어요."}
      </p>
      <div className="flex flex-col gap-1.5">
        {rows.map((row) => (
          <div
            key={row.place_id}
            className={`rounded-md border p-2 text-xs ${
              row.is_proxy
                ? "border-amber-200 bg-amber-50 dark:border-amber-900 dark:bg-amber-950/30"
                : "border-gray-200 bg-white dark:border-gray-800 dark:bg-gray-900"
            }`}
          >
            <div className="flex items-center justify-between gap-2">
              <span className="font-medium text-gray-900 dark:text-gray-100">{row.name}</span>
              <span className="text-[11px] text-gray-500">{row.status}</span>
            </div>
            <p className="mt-0.5 text-[11px] text-gray-600 dark:text-gray-300">
              {row.is_proxy
                ? `근사치 ← ${row.proxy_place_name ?? "알 수 없음"}${
                    row.proxy_distance_km !== null ? ` (${row.proxy_distance_km.toFixed(2)}km)` : ""
                  }`
                : row.status === "success"
                  ? "직접 조회"
                  : "값 없음"}
            </p>
          </div>
        ))}
      </div>
    </>
  );
}

function ToolContextItemRows({ items }: { items: ToolContextItemDebug[] }) {
  return (
    <div className="flex flex-col gap-2">
      {items.map((item) => (
        <section
          key={item.key}
          className="rounded-md border border-gray-200 p-3 dark:border-gray-800"
        >
          <p className="text-xs font-semibold text-gray-900 dark:text-gray-100">
            {CONTEXT_ITEM_LABELS[item.key] ?? item.key}
          </p>
          <p className="mt-1 text-xs text-gray-700 dark:text-gray-300">
            {item.fetched
              ? `상태: ${item.status ?? "-"}${
                  item.item_count !== null ? ` · ${item.item_count}건` : ""
                }`
              : "조회하지 않음"}
          </p>
          {item.error_code && (
            <p className="mt-1 text-xs text-red-600 dark:text-red-400">오류: {item.error_code}</p>
          )}
          {item.warning_codes.length > 0 && (
            <p className="mt-1 text-xs text-amber-700 dark:text-amber-300">
              경고: {item.warning_codes.join(", ")}
            </p>
          )}
        </section>
      ))}
    </div>
  );
}

function ToolExecutionDetails({ execution }: { execution: ToolExecutionDebug }) {
  return (
    <div className="flex flex-col gap-3">
      <dl className="grid grid-cols-2 gap-2">
        <DetailRow label="C 응답 상태" value={execution.status} />
        <DetailRow label="C 소요 시간" value={formatDuration(execution.latency_ms)} />
        {/* 후보 보강은 후보가 여럿이라 "해석된 위치" 한 칸으로 표현되지 않는다.
            빈 칸으로 두면 채워져야 하는데 빠진 것처럼 보인다. */}
        {execution.operation !== "candidate_enrichment" && (
          <>
            <DetailRow label="해석된 위치" value={execution.resolved_location_name} />
            <DetailRow label="해석된 주소" value={execution.resolved_location_address} />
          </>
        )}
        {execution.is_proxy !== null && (
          <DetailRow label="근접 관광지 대체" value={execution.is_proxy ? "사용" : "미사용"} />
        )}
        {execution.error_code && <DetailRow label="오류 코드" value={execution.error_code} />}
        {execution.clarification_code && (
          <DetailRow label="되묻기 코드" value={execution.clarification_code} />
        )}
      </dl>

      {Object.keys(execution.candidate_status_counts).length > 0 && (
        <>
          <h4 className="text-xs font-semibold text-gray-500 dark:text-gray-400">후보별 결과</h4>
          <dl className="grid grid-cols-3 gap-2">
            {Object.entries(execution.candidate_status_counts).map(([status, count]) => (
              <DetailRow key={status} label={status} value={`${count}건`} />
            ))}
          </dl>
        </>
      )}

      {execution.candidate_concentration && execution.candidate_concentration.length > 0 && (
        <CandidateConcentrationRows rows={execution.candidate_concentration} />
      )}

      <h4 className="text-xs font-semibold text-gray-500 dark:text-gray-400">호출한 Provider</h4>
      <ToolProviderCards providers={execution.providers} />

      <h4 className="text-xs font-semibold text-gray-500 dark:text-gray-400">항목별 상태</h4>
      <ToolContextItemRows items={execution.context_items} />

      {Object.keys(execution.rule_versions).length > 0 && (
        <>
          <h4 className="text-xs font-semibold text-gray-500 dark:text-gray-400">규칙 버전</h4>
          <dl className="grid grid-cols-2 gap-2">
            {Object.entries(execution.rule_versions).map(([name, version]) => (
              <DetailRow key={name} label={name} value={version} />
            ))}
          </dl>
        </>
      )}
    </div>
  );
}

function ToolExecutionSection({ executions }: { executions: ToolExecutionDebug[] }) {
  if (!executions.length) {
    return (
      <p className="rounded-md bg-amber-50 p-3 text-xs text-amber-900 dark:bg-amber-950/30 dark:text-amber-100">
        C 실행 정보가 없습니다. C 호출 전에 끝난 요청이거나, 실행 정보 필드가 추가되기 전의 이전
        응답일 수 있습니다.
      </p>
    );
  }
  return (
    <div className="flex flex-col gap-3">
      {executions.map((execution) => (
        <section
          key={`${execution.operation ?? "context_fetch"}-${execution.request_id}`}
          className="flex flex-col gap-3 rounded-md border border-gray-200 p-3 dark:border-gray-800"
        >
          <h3 className="text-sm font-semibold text-gray-900 dark:text-gray-100">
            {TOOL_OPERATION_LABELS[execution.operation ?? "context_fetch"]}
          </h3>
          <ToolExecutionDetails execution={execution} />
        </section>
      ))}
    </div>
  );
}

function TimingCard({
  timing,
  turn,
  llmExecution,
}: {
  timing: AgentStageTiming;
  turn: DeveloperAuditTurn;
  llmExecution: LLMExecutionMetadata | null;
}) {
  const presentation = STAGE_PRESENTATION[timing.stage];
  const executions = turn.response?.tool_executions?.length
    ? turn.response.tool_executions
    : turn.response?.tool_execution
      ? [turn.response.tool_execution]
      : [];
  const llmCalls = llmExecution?.calls ?? [];
  const relevantLlmCalls =
    timing.stage === "interpreting"
      ? llmCalls.filter(
          (call) => call.operation === "classify_intent" || call.operation.startsWith("extract_"),
        )
      : timing.stage === "composing_message"
        ? llmCalls.filter(
            (call) =>
              call.operation.startsWith("generate_") || call.operation.startsWith("stream_"),
          )
        : timing.stage === "scheduling"
          ? llmCalls.filter(
              (call) =>
                call.operation === "generate_schedule_plan" ||
                call.operation === "generate_schedule_fill",
            )
          : [];

  return (
    <section className="rounded-lg border border-gray-200 bg-white p-3 dark:border-gray-800 dark:bg-gray-900">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold text-gray-950 dark:text-gray-50">
            {presentation.label}
          </h3>
          <p className="mt-0.5 text-xs text-gray-500 dark:text-gray-400">{presentation.owner}</p>
        </div>
        <span className="rounded-md bg-indigo-50 px-2 py-1 text-sm font-bold text-indigo-800 dark:bg-indigo-950/50 dark:text-indigo-200">
          {formatDuration(timing.duration_ms)}
        </span>
      </div>
      <p className="mt-2 text-xs text-gray-600 dark:text-gray-300">{timing.message}</p>

      {timing.stage === "composing_message" && timing.time_to_first_token_ms != null && (
        <p className="mt-3 border-t border-gray-100 pt-2 text-xs text-gray-700 dark:border-gray-800 dark:text-gray-200">
          첫 글자 도착(TTFT) · {formatDuration(timing.time_to_first_token_ms)}
        </p>
      )}

      {relevantLlmCalls.length > 0 && (
        <div className="mt-3 border-t border-gray-100 pt-2 text-xs dark:border-gray-800">
          <p className="font-medium text-gray-500 dark:text-gray-400">세부 LLM 호출</p>
          {relevantLlmCalls.map((call) => (
            <p key={call.operation} className="mt-1 text-gray-700 dark:text-gray-200">
              {call.operation} · {call.served_model ?? "응답 실패"}
              {call.latency_ms != null ? ` · ${formatDuration(call.latency_ms)}` : ""}
              {call.retry_count != null ? (
                <span
                  className={
                    call.retry_count > 0
                      ? "ml-1 rounded bg-amber-100 px-1 py-0.5 text-[11px] font-medium text-amber-800 dark:bg-amber-900/40 dark:text-amber-200"
                      : "ml-1 rounded bg-gray-100 px-1 py-0.5 text-[11px] font-medium text-gray-600 dark:bg-gray-800 dark:text-gray-300"
                  }
                >
                  시도 {call.retry_count + 1}회
                </span>
              ) : null}
            </p>
          ))}
        </div>
      )}

      {timing.stage === "merging_conditions" && turn.response && (
        <p className="mt-3 border-t border-gray-100 pt-2 text-xs text-gray-700 dark:border-gray-800 dark:text-gray-200">
          적용 연산 {turn.response.state.applied_operations?.length ?? 0}건 · 무시 연산{" "}
          {turn.response.state.ignored_operations?.length ?? 0}건
        </p>
      )}

      {timing.stage === "fetching_context" && (
        <div className="mt-3 border-t border-gray-100 pt-2 text-xs dark:border-gray-800">
          {executions.length ? (
            executions.map((execution) => (
              <p key={execution.request_id} className="mt-1 text-gray-700 dark:text-gray-200">
                {TOOL_OPERATION_LABELS[execution.operation ?? "context_fetch"]} ·{" "}
                {formatDuration(execution.latency_ms)} · {execution.status}
              </p>
            ))
          ) : (
            <p className="text-gray-500 dark:text-gray-400">C 호출 없음</p>
          )}
        </div>
      )}

      {timing.stage === "scoring" && (
        <p className="mt-3 border-t border-gray-100 pt-2 text-xs text-gray-700 dark:border-gray-800 dark:text-gray-200">
          D 결과 {getRecommendationItems(turn).length}건
        </p>
      )}
    </section>
  );
}

function TimingSection({
  turn,
  llmExecution,
}: {
  turn: DeveloperAuditTurn;
  llmExecution: LLMExecutionMetadata | null;
}) {
  const total = turn.serverElapsedMs ?? turn.elapsedMsClient;
  if (!turn.stageTimings.length) {
    return (
      <p className="rounded-md border border-dashed border-gray-300 p-4 text-sm text-gray-500 dark:border-gray-700">
        단계별 시간은 SSE 실행 응답부터 기록됩니다. 이 이전 턴은 총 클라이언트 시간만 확인할 수
        있습니다: {formatDuration(total)}
      </p>
    );
  }
  const measured = turn.stageTimings.reduce((sum, timing) => sum + timing.duration_ms, 0);
  return (
    <div className="flex flex-col gap-3">
      <section className="rounded-lg border border-indigo-200 bg-indigo-50 p-3 dark:border-indigo-900 dark:bg-indigo-950/30">
        <div className="flex items-center justify-between gap-3">
          <div>
            <h3 className="text-sm font-semibold text-indigo-950 dark:text-indigo-50">
              이번 요청 총 소요
            </h3>
            <p className="mt-0.5 text-xs text-indigo-700 dark:text-indigo-300">
              서버 기준 {formatDuration(total)} · 단계 합계 {formatDuration(measured)}
            </p>
          </div>
          <span className="text-lg font-bold text-indigo-900 dark:text-indigo-100">
            {formatDuration(total)}
          </span>
        </div>
      </section>
      {turn.stageTimings.map((timing) => (
        <TimingCard
          key={`${timing.stage}-${timing.started_at_ms}`}
          timing={timing}
          turn={turn}
          llmExecution={llmExecution}
        />
      ))}
    </div>
  );
}

function DetailRow({ label, value }: { label: string; value: unknown }) {
  return (
    <div className="rounded-md border border-gray-200 p-3 dark:border-gray-800">
      <dt className="text-xs font-medium text-gray-500 dark:text-gray-400">{label}</dt>
      <dd className="mt-1 break-words text-sm text-gray-900 dark:text-gray-100">
        {formatValue(value)}
      </dd>
    </div>
  );
}

function JsonBlock({ value }: { value: unknown }) {
  return (
    <pre className="max-h-96 overflow-auto rounded-md bg-gray-950 p-3 text-xs text-gray-100">
      {compactJson(value)}
    </pre>
  );
}

/** D가 실제로 쓰는 축 이름을 사람이 읽는 이름과 설명으로 바꾼다.
 * 서버가 새 축을 추가해도 아래에 없다는 이유로 점수 행이 사라지지 않도록
 * label은 항상 feature 원문으로 폴백한다. */
const SCORING_FEATURES: Record<string, { label: string; description: string; tone: string }> = {
  weather: {
    label: "날씨 적합도",
    description: "현재 날씨와 실내외 조건의 적합도",
    tone: "bg-sky-50 text-sky-800 dark:bg-sky-950/40 dark:text-sky-200",
  },
  environment: {
    label: "실내외 적합도",
    description: "요청한 실내·실외 환경과의 적합도",
    tone: "bg-teal-50 text-teal-800 dark:bg-teal-950/40 dark:text-teal-200",
  },
  remaining_operating_time: {
    label: "운영시간",
    description: "지금부터 남은 운영시간의 적합도",
    tone: "bg-violet-50 text-violet-800 dark:bg-violet-950/40 dark:text-violet-200",
  },
  distance: {
    label: "거리·이동시간",
    description: "검색 반경 또는 이동시간 예산과의 적합도",
    tone: "bg-blue-50 text-blue-800 dark:bg-blue-950/40 dark:text-blue-200",
  },
  taste: {
    label: "취향",
    description: "임베딩 또는 취향 태그의 일치도",
    tone: "bg-rose-50 text-rose-800 dark:bg-rose-950/40 dark:text-rose-200",
  },
  concentration: {
    label: "혼잡도",
    description: "원하는 혼잡 수준과의 적합도",
    tone: "bg-amber-50 text-amber-800 dark:bg-amber-950/40 dark:text-amber-200",
  },
  co_visited: {
    label: "함께 방문한 장소",
    description: "대화에서 언급한 장소와의 연계도",
    tone: "bg-emerald-50 text-emerald-800 dark:bg-emerald-950/40 dark:text-emerald-200",
  },
};

function formatScore(value: number): string {
  return value.toFixed(2);
}

function formatWeight(weight: number): string {
  return `${(weight * 100).toFixed(1)}%`;
}

const TASTE_EMBEDDING_CUT = 0.43;
const TASTE_EMBEDDING_FULL_SCORE = 0.65;
const TASTE_TAG_BONUS_FACTOR = 0.35;

function getScoringFeatureKeys(item: RecommendationItem): string[] {
  const weighted = Object.keys(item.weights_used);
  const unweighted = Object.keys(item.feature_scores).filter((feature) => !weighted.includes(feature));
  return [...weighted, ...unweighted];
}

function TasteEvidencePanel({ item }: { item: RecommendationItem }) {
  const hasEvidence = item.taste_evidence.length > 0;
  const tasteScore = item.feature_scores.taste;
  const tagScore = item.taste_tag_score;
  const hasTagScore = typeof tagScore === "number";
  const embeddingSimilarity = item.taste_embedding_similarity;
  const embeddingScore = item.taste_embedding_score;
  const combinedScore = item.taste_combined_score ?? tasteScore;

  if (tasteScore === undefined) return null;

  return (
    <section className="mt-3 space-y-3">
      <div className="rounded-lg border border-emerald-100 bg-emerald-50/60 p-3 dark:border-emerald-950 dark:bg-emerald-950/20">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <div>
            <h5 className="text-xs font-semibold text-emerald-950 dark:text-emerald-50">
              최종 취향 결합점수
            </h5>
            <p className="mt-0.5 text-[11px] text-emerald-700 dark:text-emerald-300">
              임베딩을 기본으로 유지하고 태그가 남은 점수 여백의 35%까지만 가산돼요.
            </p>
          </div>
          <span className="rounded-full bg-white px-2 py-0.5 text-xs font-bold text-emerald-800 shadow-sm dark:bg-gray-900 dark:text-emerald-200">
            {typeof combinedScore === "number"
              ? `최종 ${formatScore(combinedScore)}`
              : "취향 점수 없음"}
          </span>
        </div>
        {typeof combinedScore === "number" && (
          <p className="mt-2 rounded-md bg-white px-2.5 py-2 font-mono text-[11px] text-emerald-800 dark:bg-gray-900 dark:text-emerald-200">
            {formatScore(embeddingScore ?? 0)} + {TASTE_TAG_BONUS_FACTOR} × {formatScore(tagScore ?? 0)} ×
            (1 - {formatScore(embeddingScore ?? 0)}) = {formatScore(combinedScore)}
          </p>
        )}
      </div>

      <div className="rounded-lg border border-violet-100 bg-violet-50/60 p-3 dark:border-violet-950 dark:bg-violet-950/20">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <div>
            <h5 className="text-xs font-semibold text-violet-950 dark:text-violet-50">취향 태그 일치도</h5>
            <p className="mt-0.5 text-[11px] text-violet-700 dark:text-violet-300">
              같은 후보군에서 해당 취향의 긍정 언급 문서가 가장 많은 장소를 1.00으로 둔 상대 점수예요.
            </p>
          </div>
          <span className="rounded-full bg-white px-2 py-0.5 text-xs font-bold text-violet-800 shadow-sm dark:bg-gray-900 dark:text-violet-200">
            {hasTagScore ? `태그 점수 ${formatScore(tagScore)}` : "태그 점수 미반영"}
          </span>
        </div>

        {hasTagScore && item.taste_tag_details && item.taste_tag_details.length > 0 ? (
          <details className="mt-2 rounded-md border border-violet-100 bg-white dark:border-violet-900 dark:bg-gray-900">
            <summary className="cursor-pointer px-2.5 py-2 text-xs font-medium text-violet-800 marker:text-violet-400 dark:text-violet-200">
              태그 계산 자세히 보기
            </summary>
            <div className="divide-y divide-violet-100 border-t border-violet-100 dark:divide-violet-900 dark:border-violet-900">
              {item.taste_tag_details.map((detail) => (
                <div key={detail.code} className="px-2.5 py-2 text-xs text-gray-700 dark:text-gray-200">
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-semibold">{detail.label ?? detail.code}</span>
                    <span className="font-mono font-bold text-violet-800 dark:text-violet-200">
                      {formatScore(detail.relative_score)}
                    </span>
                  </div>
                  <p className="mt-1 text-[11px] text-gray-500 dark:text-gray-400">
                    긍정 문서 {detail.positive_document_count}건 / 후보군 최다 {detail.candidate_max_positive_document_count}건
                    {detail.negative_document_count > 0 ? ` · 부정 문서 ${detail.negative_document_count}건` : ""}
                  </p>
                  <p className="mt-0.5 font-mono text-[11px] text-violet-700 dark:text-violet-300">
                    {detail.positive_document_count} / {detail.candidate_max_positive_document_count} = {formatScore(detail.relative_score)}
                  </p>
                </div>
              ))}
            </div>
          </details>
        ) : (
          <p className="mt-2 text-xs text-violet-700 dark:text-violet-300">
            이번 발화에서 사전 취향 태그를 인식하지 못해 태그 점수는 순위에 반영되지 않았습니다.
          </p>
        )}
      </div>

      <div className="rounded-lg border border-rose-100 bg-rose-50/60 p-3 dark:border-rose-950 dark:bg-rose-950/20">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <div>
            <h5 className="text-xs font-semibold text-rose-950 dark:text-rose-50">
              텍스트 임베딩 유사도
            </h5>
            <p className="mt-0.5 text-[11px] text-rose-700 dark:text-rose-300">
              질의와 근거 문장의 평균 유사도를 {TASTE_EMBEDDING_CUT}~
              {TASTE_EMBEDDING_FULL_SCORE} 구간에서 0~1 점수로 환산해요.
            </p>
          </div>
          {typeof embeddingScore === "number" && (
            <span className="rounded-full bg-white px-2 py-0.5 text-xs font-bold text-rose-800 shadow-sm dark:bg-gray-900 dark:text-rose-200">
              임베딩 환산점수 {formatScore(embeddingScore)}
            </span>
          )}
        </div>

        {typeof embeddingSimilarity === "number" && typeof embeddingScore === "number" && (
          <p className="mt-2 rounded-md bg-white px-2.5 py-2 font-mono text-[11px] text-rose-800 dark:bg-gray-900 dark:text-rose-200">
            ({formatScore(embeddingSimilarity)} - {TASTE_EMBEDDING_CUT}) / (
            {TASTE_EMBEDDING_FULL_SCORE} - {TASTE_EMBEDDING_CUT}) = {formatScore(embeddingScore)}
          </p>
        )}

        {hasEvidence ? (
          <ul className="mt-2 space-y-2">
            {item.taste_evidence.map((evidence, index) => (
              <li
                key={`${evidence.similarity}-${evidence.text.slice(0, 24)}`}
                className="rounded-md border border-rose-100 bg-white p-2 dark:border-rose-900 dark:bg-gray-900"
              >
                <div className="flex items-center justify-between gap-2 text-[11px]">
                  <span className="font-semibold text-rose-800 dark:text-rose-200">
                    근거 {index + 1} · 유사도 원점수
                  </span>
                  <span className="font-mono font-bold text-rose-950 dark:text-rose-50">
                    {formatScore(evidence.similarity)}
                  </span>
                </div>
                <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-rose-100 dark:bg-rose-950">
                  <div
                    className="h-full rounded-full bg-rose-400 dark:bg-rose-500"
                    style={{ width: `${Math.max(0, Math.min(100, evidence.similarity * 100))}%` }}
                  />
                </div>
                <p className="mt-1.5 line-clamp-3 text-xs leading-5 text-gray-700 dark:text-gray-300">
                  “{evidence.text}”
                </p>
              </li>
            ))}
          </ul>
        ) : (
          <p className="mt-2 text-xs text-rose-700 dark:text-rose-300">
            임베딩 검색은 실행됐지만 표시 기준을 넘는 근거 문장을 찾지 못했습니다.
          </p>
        )}
      </div>
    </section>
  );
}

function ScoringBreakdownCard({
  item,
  index,
  selected = false,
}: {
  item: RecommendationItem;
  index: number;
  selected?: boolean;
}) {
  const featureKeys = getScoringFeatureKeys(item);
  const totalContribution = featureKeys.reduce((sum, feature) => {
    const score = item.feature_scores[feature];
    const weight = item.weights_used[feature];
    return typeof score === "number" && typeof weight === "number" ? sum + score * weight : sum;
  }, 0);

  return (
    <section
      data-testid={`scoring-breakdown-${item.place_id}`}
      className="rounded-xl border border-gray-200 bg-white p-3 shadow-sm dark:border-gray-800 dark:bg-gray-900"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h4 className="truncate text-sm font-semibold text-gray-950 dark:text-gray-50">
            {item.scoring_rank ?? index + 1}. {item.name}
          </h4>
          <p className="mt-0.5 text-xs text-gray-500">
            {item.category_label ?? PLACE_CATEGORY_LABELS[item.category] ?? item.category} · {item.distance_km}km
          </p>
          <span
            className={`mt-1 inline-flex rounded-full px-1.5 py-0.5 text-[10px] font-semibold ${
              selected
                ? "bg-emerald-50 text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300"
                : "bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-300"
            }`}
          >
            {selected ? "사용자에게 추천" : "채점 후 미노출"}
          </span>
        </div>
        <div className="shrink-0 rounded-lg bg-gray-950 px-2.5 py-1.5 text-right text-white dark:bg-gray-100 dark:text-gray-950">
          <span className="block text-[10px] font-medium opacity-70">총점</span>
          <span className="block text-base font-bold leading-5">{formatScore(item.score)}</span>
        </div>
      </div>

      <div className="mt-3 overflow-hidden rounded-lg border border-gray-100 dark:border-gray-800">
        <div className="grid grid-cols-[minmax(0,1fr)_48px_44px_54px] gap-1 border-b border-gray-100 bg-gray-50 px-2 py-1.5 text-[10px] font-semibold text-gray-500 dark:border-gray-800 dark:bg-gray-950 dark:text-gray-400">
          <span>평가 항목</span>
          <span className="text-right">원점수</span>
          <span className="text-right">가중치</span>
          <span className="text-right">기여점</span>
        </div>
        <div className="divide-y divide-gray-100 dark:divide-gray-800">
          {featureKeys.map((feature) => {
            const score = item.feature_scores[feature];
            const weight = item.weights_used[feature];
            const contribution =
              typeof score === "number" && typeof weight === "number" ? score * weight : null;
            const presentation = SCORING_FEATURES[feature];
            return (
              <div
                key={feature}
                className="grid grid-cols-[minmax(0,1fr)_48px_44px_54px] items-center gap-1 px-2 py-2 text-xs"
              >
                <div className="min-w-0">
                  <span
                    className={`inline-flex rounded-full px-1.5 py-0.5 text-[10px] font-semibold ${
                      presentation?.tone ?? "bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-200"
                    }`}
                  >
                    {presentation?.label ?? feature}
                  </span>
                  <p className="mt-0.5 truncate text-[10px] text-gray-500" title={presentation?.description}>
                    {presentation?.description ?? "서버가 추가한 평가 항목"}
                  </p>
                </div>
                <span className="text-right font-mono text-gray-700 dark:text-gray-200">
                  {typeof score === "number" ? formatScore(score) : "-"}
                </span>
                <span className="text-right font-mono text-gray-600 dark:text-gray-300">
                  {typeof weight === "number" ? formatWeight(weight) : "미반영"}
                </span>
                <span className="text-right font-mono font-semibold text-gray-950 dark:text-gray-50">
                  {contribution === null ? "-" : formatScore(contribution)}
                </span>
              </div>
            );
          })}
        </div>
        <div className="flex items-center justify-between bg-gray-50 px-2 py-2 text-xs dark:bg-gray-950">
          <span className="font-medium text-gray-600 dark:text-gray-300">가중치 기여점 합계</span>
          <span className="font-mono font-bold text-gray-950 dark:text-gray-50">
            {formatScore(totalContribution)}
          </span>
        </div>
      </div>

      <TasteEvidencePanel item={item} />

      <details className="mt-3 rounded-lg border border-gray-200 bg-gray-50 dark:border-gray-800 dark:bg-gray-950">
        <summary className="cursor-pointer px-3 py-2 text-xs font-medium text-gray-600 marker:text-gray-400 dark:text-gray-300">
          원본 점수 JSON 보기
        </summary>
        <div className="border-t border-gray-200 p-2 dark:border-gray-800">
          <JsonBlock
            value={{
              feature_scores: item.feature_scores,
              weights_used: item.weights_used,
              score: item.score,
              explanations: item.explanations,
              warnings: item.warnings,
              taste_evidence: item.taste_evidence,
              taste_tag_score: item.taste_tag_score,
              taste_tag_label: item.taste_tag_label,
              taste_tag_documents: item.taste_tag_documents,
              taste_tag_details: item.taste_tag_details,
              taste_embedding_similarity: item.taste_embedding_similarity,
              taste_embedding_score: item.taste_embedding_score,
              taste_combined_score: item.taste_combined_score,
              scoring_rank: item.scoring_rank,
              preference_tags: item.preference_tags,
            }}
          />
        </div>
      </details>
    </section>
  );
}

const EXCLUSION_REASON_LABELS: Record<string, string> = {
  closed: "현재 운영시간 아님",
  already_shown: "이 대화에서 이미 추천함",
  rejected: "사용자가 이전에 제외함",
};

function RecommendationScoringPanel({ turn }: { turn: DeveloperAuditTurn }) {
  const shownItems = getRecommendationItems(turn);
  const shownIds = new Set(shownItems.map((item) => item.place_id));
  const scoredItems = turn.recommendations?.scoring_candidates?.length
    ? [...turn.recommendations.scoring_candidates].sort(
        (left, right) => (left.scoring_rank ?? 10_000) - (right.scoring_rank ?? 10_000),
      )
    : shownItems;
  const hiddenItems = scoredItems.filter((item) => !shownIds.has(item.place_id));
  const excludedItems = turn.recommendations?.scoring_excluded_candidates ?? [];

  if (shownItems.length === 0 && scoredItems.length === 0 && excludedItems.length === 0) {
    return (
      <p className="rounded-md border border-dashed border-gray-300 p-4 text-sm text-gray-500 dark:border-gray-700">
        D Scoring 결과가 없습니다. INFO/GENERAL이거나 C 단계에서 후보가 없을 수 있습니다.
      </p>
    );
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="grid grid-cols-3 gap-2 rounded-lg border border-gray-200 bg-white p-3 text-center dark:border-gray-800 dark:bg-gray-900">
        <div>
          <p className="text-[10px] text-gray-500">사용자 추천</p>
          <p className="text-sm font-bold text-emerald-700 dark:text-emerald-300">
            {shownItems.length}곳
          </p>
        </div>
        <div>
          <p className="text-[10px] text-gray-500">채점 후 미노출</p>
          <p className="text-sm font-bold text-gray-800 dark:text-gray-200">
            {hiddenItems.length}곳
          </p>
        </div>
        <div>
          <p className="text-[10px] text-gray-500">하드 필터 탈락</p>
          <p className="text-sm font-bold text-amber-700 dark:text-amber-300">
            {excludedItems.length}곳
          </p>
        </div>
      </div>

      {shownItems.map((item, index) => (
        <ScoringBreakdownCard key={item.place_id} item={item} index={index} selected />
      ))}

      {hiddenItems.length > 0 && (
        <details className="rounded-lg border border-gray-200 bg-gray-50 dark:border-gray-800 dark:bg-gray-950">
          <summary className="cursor-pointer px-3 py-2.5 text-sm font-semibold text-gray-700 marker:text-gray-400 dark:text-gray-200">
            추천되지 않은 채점 후보 {hiddenItems.length}곳 점수 보기
          </summary>
          <div className="flex flex-col gap-3 border-t border-gray-200 p-3 dark:border-gray-800">
            {hiddenItems.map((item, index) => (
              <ScoringBreakdownCard key={item.place_id} item={item} index={index} />
            ))}
          </div>
        </details>
      )}

      {excludedItems.length > 0 && (
        <details className="rounded-lg border border-amber-200 bg-amber-50/50 dark:border-amber-900 dark:bg-amber-950/20">
          <summary className="cursor-pointer px-3 py-2.5 text-sm font-semibold text-amber-800 marker:text-amber-500 dark:text-amber-200">
            점수 계산 전 탈락 후보 {excludedItems.length}곳 보기
          </summary>
          <div className="divide-y divide-amber-100 border-t border-amber-200 dark:divide-amber-900 dark:border-amber-900">
            {excludedItems.map((item) => (
              <div key={item.place_id} className="px-3 py-2.5 text-xs">
                <div className="flex items-center justify-between gap-3">
                  <span className="font-semibold text-gray-900 dark:text-gray-100">{item.name}</span>
                  <span className="shrink-0 rounded-full bg-white px-2 py-0.5 text-[10px] font-medium text-amber-800 dark:bg-gray-900 dark:text-amber-200">
                    {EXCLUSION_REASON_LABELS[item.reason] ?? item.reason}
                  </span>
                </div>
                <p className="mt-1 text-gray-500">
                  {PLACE_CATEGORY_LABELS[item.category] ?? item.category} · {item.distance_km}km · 취향·총점 미계산
                </p>
              </div>
            ))}
          </div>
        </details>
      )}
    </div>
  );
}

interface DeveloperAuditPanelProps {
  turns: DeveloperAuditTurn[];
  selectedTurnId: string | null;
  onSelectTurn: (turnId: string) => void;
  debugIgnoreOperatingHours: boolean;
  onToggleDebugIgnoreOperatingHours: (enabled: boolean) => void;
}

export function DeveloperAuditPanel({
  turns,
  selectedTurnId,
  onSelectTurn,
  debugIgnoreOperatingHours,
  onToggleDebugIgnoreOperatingHours,
}: DeveloperAuditPanelProps) {
  const [activeTab, setActiveTab] = useState<AuditTab>("summary");
  const selectedTurn = turns.find((turn) => turn.id === selectedTurnId) ?? turns.at(-1) ?? null;
  const conditionChanges = useMemo(
    () =>
      selectedTurn
        ? getConditionChanges(selectedTurn.beforeConditions, selectedTurn.afterConditions)
        : [],
    [selectedTurn],
  );
  const llmExecution = selectedTurn ? getLlmExecution(selectedTurn) : null;
  const llmExtractedConditions = selectedTurn ? getLlmExtractedConditionEntries(selectedTurn) : [];
  const mergedConditions = selectedTurn?.afterConditions
    ? getConditionEntries(selectedTurn.afterConditions)
    : [];

  return (
    <aside className="flex h-full min-h-0 min-w-0 flex-col overflow-hidden border-l border-gray-200 bg-gray-50 dark:border-gray-800 dark:bg-gray-950">
      <header className="border-b border-gray-200 px-5 py-4 dark:border-gray-800">
        <div className="flex items-start justify-between gap-3">
          <div>
            <p className="text-xs font-semibold uppercase tracking-widest text-emerald-700 dark:text-emerald-400">
              TripBranch Developer Console
            </p>
            <h2 className="mt-1 text-lg font-bold text-gray-950 dark:text-gray-50">
              Agent Runtime Audit
            </h2>
          </div>
          <label
            className={`flex shrink-0 items-center gap-1.5 rounded-md border px-2 py-1 text-[11px] ${
              debugIgnoreOperatingHours
                ? "border-amber-400 bg-amber-50 text-amber-900 dark:border-amber-600 dark:bg-amber-950/40 dark:text-amber-100"
                : "border-gray-300 dark:border-gray-700"
            }`}
            title="켜두면 이후 발화가 폐점 후보도 항상 채점에 포함해요 — no_data_closed 되묻기를 매번 누르지 않아도 재현/우회할 수 있어요."
          >
            <input
              type="checkbox"
              checked={debugIgnoreOperatingHours}
              onChange={(event) => onToggleDebugIgnoreOperatingHours(event.target.checked)}
            />
            운영시간 무시
          </label>
        </div>
      </header>

      <div className="min-h-0 flex-1 overflow-auto px-5 py-4">
        <section className="flex flex-col gap-3">
          <div className="flex items-center justify-between gap-3">
            <h3 className="text-sm font-semibold text-gray-900 dark:text-gray-100">
              발화별 감사 기록
            </h3>
            <span className="text-xs text-gray-500">{turns.length}건</span>
          </div>

          {turns.length === 0 ? (
            <p className="rounded-md border border-dashed border-gray-300 p-4 text-sm text-gray-500 dark:border-gray-700">
              아직 실행된 발화가 없습니다. 가운데 채팅에서 질문을 보내면 이곳에 기록됩니다.
            </p>
          ) : (
            <div className="flex flex-col gap-2">
              {turns.map((turn, index) => {
                const isSelected = selectedTurn?.id === turn.id;
                return (
                  <button
                    key={turn.id}
                    type="button"
                    onClick={() => onSelectTurn(turn.id)}
                    className={`rounded-md border p-3 text-left transition ${
                      isSelected
                        ? "border-emerald-500 bg-white shadow-sm dark:bg-gray-900"
                        : "border-gray-200 bg-white hover:border-gray-300 dark:border-gray-800 dark:bg-gray-900"
                    }`}
                  >
                    <div className="flex items-start justify-between gap-3">
                      <p className="line-clamp-2 text-sm font-medium text-gray-950 dark:text-gray-50">
                        {index + 1}. {turn.userInput}
                      </p>
                      <span
                        className={`shrink-0 rounded px-2 py-0.5 text-xs font-semibold ${
                          turn.intent === "ERROR"
                            ? "bg-red-100 text-red-800 dark:bg-red-900/50 dark:text-red-100"
                            : "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/50 dark:text-emerald-100"
                        }`}
                      >
                        {turn.intent}
                      </span>
                    </div>
                    <p className="mt-2 text-xs text-gray-500">
                      {turn.status} · {formatDuration(turn.elapsedMsClient)} ·{" "}
                      {getResultCountLabel(turn)}
                    </p>
                  </button>
                );
              })}
            </div>
          )}
        </section>

        {selectedTurn && (
          <section className="mt-5 flex flex-col gap-4">
            {selectedTurn.failure && (
              <div className="rounded-md border border-red-300 bg-red-50 p-3 dark:border-red-800 dark:bg-red-950/40">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="text-xs font-semibold uppercase tracking-wide text-red-700 dark:text-red-300">
                      오류 발생 · {selectedTurn.failure.code}
                    </p>
                    <p className="mt-1 break-words text-sm text-red-900 dark:text-red-100">
                      {selectedTurn.failure.message}
                    </p>
                  </div>
                  <button
                    type="button"
                    onClick={() => setActiveTab("raw")}
                    className="shrink-0 rounded-md border border-red-400 bg-white px-2.5 py-1.5 text-xs font-medium text-red-800 hover:bg-red-100 dark:border-red-700 dark:bg-red-950 dark:text-red-100 dark:hover:bg-red-900"
                  >
                    오류 상세 확인
                  </button>
                </div>
              </div>
            )}

            <div className="flex flex-wrap gap-2">
              {TABS.map((tab) => (
                <button
                  key={tab.id}
                  type="button"
                  onClick={() => setActiveTab(tab.id)}
                  className={`rounded-md px-3 py-1.5 text-xs font-medium ${
                    activeTab === tab.id
                      ? "bg-gray-900 text-white dark:bg-gray-100 dark:text-gray-900"
                      : "border border-gray-200 text-gray-600 dark:border-gray-800 dark:text-gray-300"
                  }`}
                >
                  {tab.label}
                </button>
              ))}
            </div>

            {activeTab === "summary" && (
              <div className="flex flex-col gap-3">
                <ConditionSummaryCard
                  title="LLM 이번 턴 추출"
                  description="이번 발화에서 새로 설정·변경하겠다고 판단한 조건입니다."
                  entries={llmExtractedConditions}
                  emptyMessage="이 Intent에서는 UserConditions를 새로 추출하지 않았습니다."
                />
                <ConditionSummaryCard
                  title="B 병합 후 최종 조건"
                  description="이전 턴에서 유지된 값까지 포함한, 이번 응답 생성에 사용된 누적 조건입니다."
                  entries={mergedConditions}
                  emptyMessage="저장된 사용자 조건이 없습니다."
                />
                {selectedTurn.response?.schedule && (
                  <section className="rounded-lg border border-gray-200 bg-white p-3 dark:border-gray-800 dark:bg-gray-900">
                    <h3 className="text-sm font-semibold text-gray-900 dark:text-gray-100">
                      SCHEDULE 결과
                    </h3>
                    <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
                      {selectedTurn.response.schedule.route_summary}
                    </p>
                    <dl className="mt-3 flex flex-wrap gap-2">
                      <div className="rounded-md border border-emerald-200 bg-emerald-50 px-2.5 py-2 dark:border-emerald-900 dark:bg-emerald-950/40">
                        <dt className="text-[11px] font-medium text-emerald-800 dark:text-emerald-200">
                          선택된 장소
                        </dt>
                        <dd className="mt-0.5 text-xs font-semibold text-emerald-950 dark:text-emerald-50">
                          {selectedTurn.response.schedule.items.length}곳
                        </dd>
                      </div>
                      <div className="rounded-md border border-emerald-200 bg-emerald-50 px-2.5 py-2 dark:border-emerald-900 dark:bg-emerald-950/40">
                        <dt className="text-[11px] font-medium text-emerald-800 dark:text-emerald-200">
                          총 소요 시간
                        </dt>
                        <dd className="mt-0.5 text-xs font-semibold text-emerald-950 dark:text-emerald-50">
                          {selectedTurn.response.schedule.total_duration_min}분
                        </dd>
                      </div>
                    </dl>
                  </section>
                )}
                <dl className="grid grid-cols-2 gap-2">
                  <DetailRow label="Intent" value={selectedTurn.intent} />
                  <DetailRow
                    label="question_type"
                    value={
                      selectedTurn.response?.info_place_card?.question_type ??
                      selectedTurn.response?.secondary_info_place_card?.question_type ??
                      null
                    }
                  />
                  <DetailRow label="Session ID" value={selectedTurn.sessionId} />
                  <DetailRow label="Run ID" value={selectedTurn.runId} />
                  <DetailRow label="기기 GPS" value={selectedTurn.deviceLocation} />
                  <DetailRow
                    label="클라이언트 소요"
                    value={formatDuration(selectedTurn.elapsedMsClient)}
                  />
                  <DetailRow
                    label="서버 소요"
                    value={formatDuration(selectedTurn.serverElapsedMs)}
                  />
                  <DetailRow
                    label={isScheduleTurn(selectedTurn) ? "일정 결과" : "추천 결과"}
                    value={
                      isScheduleTurn(selectedTurn)
                        ? `${getScheduleItems(selectedTurn).length}곳`
                        : `${getRecommendationItems(selectedTurn).length}건`
                    }
                  />
                  <DetailRow
                    label="LLM 응답 모델"
                    value={llmExecution?.calls
                      .map((call) => call.served_model ?? "실패")
                      .join(", ")}
                  />
                  <DetailRow
                    label="LLM 폴백"
                    value={
                      llmExecution?.calls.some((call) => call.attempted_models.length > 1)
                        ? "시도됨"
                        : "없음"
                    }
                  />
                  {selectedTurn.failure && (
                    <>
                      <DetailRow label="오류 코드" value={selectedTurn.failure.code} />
                      <DetailRow label="재시도 가능" value={selectedTurn.failure.retryable} />
                    </>
                  )}
                </dl>
              </div>
            )}

            {activeTab === "timing" && (
              <TimingSection turn={selectedTurn} llmExecution={llmExecution} />
            )}

            {activeTab === "llm" && (
              <div className="flex flex-col gap-3">
                <DetailRow label="사용자 발화" value={selectedTurn.userInput} />
                <DetailRow
                  label={selectedTurn.failure ? "오류 메시지" : "챗봇 메시지"}
                  value={selectedTurn.message}
                />
                {selectedTurn.failure ? (
                  <>
                    <JsonBlock value={selectedTurn.failure} />
                    <LlmExecutionCards execution={llmExecution} />
                  </>
                ) : (
                  <>
                    <LlmExecutionCards execution={llmExecution} />
                    <JsonBlock value={selectedTurn.response?.llm_output} />
                  </>
                )}
              </div>
            )}

            {activeTab === "state" &&
              (selectedTurn.response ? (
                <div className="flex flex-col gap-3">
                  <section className="rounded-md border border-gray-200 p-3 dark:border-gray-800">
                    <h4 className="text-sm font-semibold text-gray-900 dark:text-gray-100">
                      이번 턴 조건 변경
                    </h4>
                    {conditionChanges.length === 0 ? (
                      <p className="mt-2 text-sm text-gray-500">변경된 누적 조건이 없습니다.</p>
                    ) : (
                      <dl className="mt-3 grid gap-2">
                        {conditionChanges.map((change) => (
                          <div
                            key={change.key}
                            className="rounded bg-gray-100 p-2 text-xs dark:bg-gray-900"
                          >
                            <dt className="font-semibold text-gray-700 dark:text-gray-200">
                              {change.label}
                            </dt>
                            <dd className="mt-1 text-gray-600 dark:text-gray-300">
                              {formatValue(change.before)} → {formatValue(change.after)}
                            </dd>
                          </div>
                        ))}
                      </dl>
                    )}
                  </section>
                  <JsonBlock
                    value={{
                      user_conditions: selectedTurn.response.state.user_conditions,
                      applied_operations: selectedTurn.response.state.applied_operations ?? [],
                      ignored_operations: selectedTurn.response.state.ignored_operations ?? [],
                      reset_applied: selectedTurn.response.state.reset_applied,
                      condition_changed: selectedTurn.response.state.condition_changed,
                    }}
                  />
                </div>
              ) : (
                <p className="rounded-md border border-dashed border-gray-300 p-4 text-sm text-gray-500 dark:border-gray-700">
                  LLM 또는 HTTP 오류로 B 상태 병합 전 요청이 중단됐습니다.
                </p>
              ))}

            {activeTab === "tools" &&
              (selectedTurn.response ? (
                <div className="flex flex-col gap-3">
                  <dl className="grid grid-cols-2 gap-2">
                    <DetailRow
                      label="검색 중심"
                      value={selectedTurn.afterConditions?.search_center}
                    />
                    <DetailRow label="기기 GPS" value={selectedTurn.deviceLocation} />
                    <DetailRow
                      label="API 날씨 캐시"
                      value={selectedTurn.response.state.api_context?.api_weather}
                    />
                    <DetailRow
                      label="GPS 만료"
                      value={selectedTurn.response.state.api_context?.gps_expired}
                    />
                    <DetailRow
                      label="날씨 만료"
                      value={selectedTurn.response.state.api_context?.weather_expired}
                    />
                    <DetailRow
                      label="혼잡도 보강 대상"
                      value={
                        selectedTurn.intent === "RECOMMEND" &&
                        (selectedTurn.afterConditions?.concentration_intent === "SEEK" ||
                          selectedTurn.afterConditions?.concentration_intent === "AVOID")
                          ? "대상 (실행 결과는 현재 미표시)"
                          : "미대상"
                      }
                    />
                  </dl>
                  <ToolExecutionSection
                    executions={
                      selectedTurn.response.tool_executions?.length
                        ? selectedTurn.response.tool_executions
                        : selectedTurn.response.tool_execution
                          ? [selectedTurn.response.tool_execution]
                          : []
                    }
                  />
                </div>
              ) : (
                <p className="rounded-md border border-dashed border-gray-300 p-4 text-sm text-gray-500 dark:border-gray-700">
                  LLM 단계에서 실패해 C Tool은 호출되지 않았습니다.
                </p>
              ))}

            {activeTab === "scoring" && isScheduleTurn(selectedTurn) && (
              <div className="flex flex-col gap-3">
                {getScheduleItems(selectedTurn).length === 0 ? (
                  <p className="rounded-md border border-dashed border-gray-300 p-4 text-sm text-gray-500 dark:border-gray-700">
                    선택된 일정 항목이 없습니다. 후보가 부족했거나 활동 가능 시간이 너무 짧아
                    plan_schedule()이 LLM을 아예 부르지 않았을 수 있습니다.
                  </p>
                ) : (
                  <>
                    <p className="rounded-md bg-amber-50 p-3 text-xs text-amber-900 dark:bg-amber-950/30 dark:text-amber-100">
                      SCHEDULE 응답은 recommendations가 아니라 schedule 필드로 옵니다. D가 채점한
                      feature_scores·weights_used 같은 세부 내역은 이 응답에 포함되지 않고, LLM이
                      최종 선택한 장소와 배치 이유만 아래에 표시됩니다.
                    </p>
                    {getScheduleItems(selectedTurn).map((item) => (
                      <section
                        key={item.place_id}
                        className="rounded-md border border-gray-200 bg-white p-3 dark:border-gray-800 dark:bg-gray-900"
                      >
                        <div className="flex items-start justify-between gap-3">
                          <h4 className="text-sm font-semibold text-gray-950 dark:text-gray-50">
                            {item.order}. {item.place_name}
                          </h4>
                          <span className="shrink-0 rounded bg-gray-900 px-2 py-0.5 text-xs font-semibold text-white dark:bg-gray-100 dark:text-gray-900">
                            {item.estimated_arrival} 도착
                          </span>
                        </div>
                        <p className="mt-1 text-xs text-gray-500">
                          머무는 시간 {item.estimated_duration_min}분
                          {item.travel_to_next_min !== null &&
                            ` · 다음 장소까지 ${scheduleTravelLabel(
                              item.travel_to_next_min,
                              item.travel_to_next_mode,
                              item.travel_to_next_measured,
                            )}`}
                        </p>
                        <p className="mt-1 text-xs text-gray-700 dark:text-gray-300">
                          {item.reason}
                        </p>
                        {item.warnings != null && item.warnings.length > 0 && (
                          <p className="mt-1 rounded bg-amber-50 px-2 py-1 text-xs text-amber-800 dark:bg-amber-950/30 dark:text-amber-200">
                            warnings: {item.warnings.join(" / ")}
                          </p>
                        )}
                      </section>
                    ))}
                  </>
                )}
              </div>
            )}

            {activeTab === "scoring" && !isScheduleTurn(selectedTurn) && (
              <RecommendationScoringPanel turn={selectedTurn} />
            )}

            {activeTab === "raw" && (
              <JsonBlock value={selectedTurn.response ?? selectedTurn.failure} />
            )}
          </section>
        )}
      </div>
    </aside>
  );
}
