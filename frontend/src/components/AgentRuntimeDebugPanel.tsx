/*
 * 역할: Agent Runtime(run_agent()) 전체 흐름 — Intent 분류, 장소 가중치 계산(Scoring),
 * 최종 챗봇 메시지 생성 — 을 실제 대화하듯 입력해보며 확인하는 개발용 테스트 패널.
 * IntentDebugPanel(/api/interpret 단독 호출)과 달리 이 패널은 /api/agent-debug를 호출해
 * B(세션 상태)·C(Tool)·D(Recommendation)까지 전부 통과한 실제 결과를 보여준다.
 * 입력: 자유 발화, 세션 유지 여부(MODIFY 연속 대화 테스트용), 기기 위치(위도,경도 —
 * "내 위치 사용" 버튼으로 navigator.geolocation을 호출해 실제 GPS 좌표를 채울 수 있음).
 * 출력: intent/status, 챗봇 메시지, 추천 카드(점수·feature_scores·weights_used 포함),
 * AgentResponse 원본 JSON.
 * 호출 시점: HomePage 하단, IntentDebugPanel 아래에서 개발자가 수동으로 실행할 때 호출된다.
 * TODO: 통합 Chat API가 확정되면 이 패널의 위치·형태를 다시 검토한다.
 */

import { useState } from "react";
import { ApiError } from "../api/client";
import { runAgentDebug } from "../api/trip";
import type { AgentResponse } from "../types";
import { getBrowserDeviceLocation } from "../utils/geolocation";

interface Preset {
  label: string;
  userInput: string;
}

const PRESETS: Preset[] = [
  { label: "RECOMMEND", userInput: "경복궁 근처 카페 추천해줘" },
  { label: "MODIFY · 다른 곳", userInput: "다른 곳 보여줘" },
  { label: "MODIFY · 조건 변경", userInput: "무료인 곳으로" },
  { label: "GENERAL", userInput: "경복궁은 언제 지어졌어?" },
  { label: "OUT_OF_SCOPE", userInput: "주식 추천해줘" },
];

const DEFAULT_DEVICE_LOCATION = "37.5788,126.9770";

export function AgentRuntimeDebugPanel() {
  const [userInput, setUserInput] = useState("");
  const [deviceLocation, setDeviceLocation] = useState(DEFAULT_DEVICE_LOCATION);
  const [keepSession, setKeepSession] = useState(true);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<AgentResponse | null>(null);
  const [isLocating, setIsLocating] = useState(false);
  const [locationError, setLocationError] = useState<string | null>(null);

  function applyPreset(preset: Preset) {
    setUserInput(preset.userInput);
    setError(null);
  }

  function resetSession() {
    setSessionId(null);
    setResult(null);
    setError(null);
  }

  async function useBrowserLocation() {
    setIsLocating(true);
    setLocationError(null);
    try {
      setDeviceLocation(await getBrowserDeviceLocation());
    } catch (error) {
      setLocationError(error instanceof Error ? error.message : "위치를 가져오지 못했어요.");
    } finally {
      setIsLocating(false);
    }
  }

  async function handleSend() {
    const trimmed = userInput.trim();
    if (!trimmed || isLoading) return;

    setIsLoading(true);
    setError(null);
    try {
      const response = await runAgentDebug({
        user_input: trimmed,
        session_id: keepSession ? sessionId : null,
        device_location: deviceLocation.trim() || null,
      });
      setResult(response);
      setSessionId(response.state.session_id);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "요청 처리 중 오류가 발생했어요.");
    } finally {
      setIsLoading(false);
    }
  }

  const allItems = result?.recommendations
    ? [
        ...result.recommendations.recommendations.map((item) => ({ item, verified: true })),
        ...result.recommendations.unverified_recommendations.map((item) => ({
          item,
          verified: false,
        })),
      ]
    : [];

  return (
    <section className="mx-auto flex w-full max-w-xl flex-col gap-3 rounded-md border border-dashed border-emerald-300 bg-emerald-50 p-4 text-sm text-emerald-950 dark:border-emerald-700 dark:bg-emerald-950/30 dark:text-emerald-100">
      <div>
        <h2 className="font-semibold">개발용 Agent Runtime 채팅 테스트</h2>
        <p className="mt-1 text-xs text-emerald-900/80 dark:text-emerald-200/80">
          /api/agent-debug를 호출해 Intent 분류부터 장소 가중치 계산, 최종 챗봇 메시지 생성까지
          전체 흐름을 그대로 확인합니다. "세션 유지"를 켜두면 이전 대화 맥락으로 MODIFY 같은
          연속 대화도 테스트할 수 있습니다.
        </p>
      </div>

      <div className="flex flex-wrap gap-2">
        {PRESETS.map((preset) => (
          <button
            key={preset.label}
            type="button"
            onClick={() => applyPreset(preset)}
            className="rounded-full border border-emerald-300 px-3 py-1 text-xs dark:border-emerald-700"
          >
            {preset.label}
          </button>
        ))}
      </div>

      <textarea
        value={userInput}
        onChange={(event) => setUserInput(event.target.value)}
        rows={2}
        placeholder="예: 경복궁 근처 카페 추천해줘"
        className="w-full resize-none rounded-md border border-emerald-300 p-2 text-sm dark:border-emerald-700 dark:bg-emerald-950/50"
      />

      <label className="flex flex-col gap-1 text-xs">
        기기 위치 (위도,경도)
        <div className="flex gap-2">
          <input
            type="text"
            value={deviceLocation}
            onChange={(event) => setDeviceLocation(event.target.value)}
            className="w-full rounded-md border border-emerald-300 px-2 py-1 font-mono dark:border-emerald-700 dark:bg-emerald-950/50"
          />
          <button
            type="button"
            onClick={useBrowserLocation}
            disabled={isLocating}
            className="shrink-0 rounded-md border border-emerald-300 px-2 py-1 disabled:opacity-50 dark:border-emerald-700"
          >
            {isLocating ? "조회 중..." : "내 위치 사용"}
          </button>
        </div>
      </label>
      {locationError && <p className="text-xs text-red-700 dark:text-red-300">{locationError}</p>}

      <div className="flex flex-wrap items-center gap-3 text-xs">
        <label className="flex items-center gap-2">
          <input
            type="checkbox"
            checked={keepSession}
            onChange={(event) => setKeepSession(event.target.checked)}
          />
          세션 유지 (MODIFY 등 연속 대화 테스트)
        </label>
        <span className="text-emerald-900/70 dark:text-emerald-200/70">
          session_id: {sessionId ?? "없음"}
        </span>
        <button
          type="button"
          onClick={resetSession}
          className="rounded-full border border-emerald-300 px-2 py-0.5 dark:border-emerald-700"
        >
          세션 초기화
        </button>
      </div>

      {error && <p className="text-xs text-red-700 dark:text-red-300">{error}</p>}

      <button
        type="button"
        disabled={isLoading || !userInput.trim()}
        onClick={handleSend}
        className="w-fit rounded-md bg-emerald-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-50 dark:bg-emerald-200 dark:text-emerald-950"
      >
        {isLoading ? "실행 중..." : "Agent Runtime 실행"}
      </button>

      {result && (
        <div className="flex flex-col gap-3 rounded-md border border-emerald-200 bg-white p-3 dark:border-emerald-800 dark:bg-emerald-950/40">
          <div>
            <span className="font-medium">intent:</span> {result.llm_output.intent}
            {"   "}
            <span className="font-medium">status:</span> {result.llm_output.status}
          </div>

          <div className="rounded-md bg-emerald-100 p-3 text-emerald-950 dark:bg-emerald-900/50 dark:text-emerald-100">
            <span className="font-medium">챗봇 메시지:</span> {result.message}
          </div>

          {allItems.length > 0 && (
            <div className="flex flex-col gap-2">
              <h3 className="font-medium">추천 카드 ({allItems.length}건)</h3>
              {allItems.map(({ item, verified }) => (
                <div
                  key={item.place_id}
                  className="rounded-md border border-emerald-200 p-2 text-xs dark:border-emerald-800"
                >
                  <div className="font-medium">
                    {item.name}{" "}
                    <span className="font-normal text-emerald-900/70 dark:text-emerald-200/70">
                      ({item.category}, {verified ? "운영시간 확인됨" : "운영시간 미확인"})
                    </span>
                  </div>
                  <div>
                    거리 {item.distance_km}km · score {item.score.toFixed(3)}
                  </div>
                  <div>{item.recommendation_reason}</div>
                  {item.explanations.length > 0 && (
                    <div>explanations: {item.explanations.join(" / ")}</div>
                  )}
                  {item.warnings.length > 0 && (
                    <div className="text-amber-700 dark:text-amber-300">
                      warnings: {item.warnings.join(" / ")}
                    </div>
                  )}
                  <div className="mt-1 font-mono">
                    feature_scores: {JSON.stringify(item.feature_scores)}
                  </div>
                  <div className="font-mono">
                    weights_used: {JSON.stringify(item.weights_used)}
                  </div>
                  {item.taste_evidence.length > 0 && (
                    <div className="font-mono">
                      taste_evidence: {JSON.stringify(item.taste_evidence)}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}

          <details>
            <summary className="cursor-pointer text-xs font-medium">
              AgentResponse 원본 JSON
            </summary>
            <pre className="mt-2 max-h-80 overflow-auto rounded bg-gray-900 p-2 text-xs text-gray-100">
              {JSON.stringify(result, null, 2)}
            </pre>
          </details>
        </div>
      )}
    </section>
  );
}
