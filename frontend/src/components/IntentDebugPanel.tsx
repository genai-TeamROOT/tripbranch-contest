/*
 * 역할: /api/interpret의 Intent 분류·조건 추출(LLMOutput) 결과를 원본 그대로 확인하는
 * 개발용 테스트 패널. 기존 "추천 시작하기" 흐름과는 완전히 별개로 동작한다.
 * 입력: 자유 발화, MODIFY/COMPARE 판별에 쓰이는 이전 추천 이력 컨텍스트(체크박스·숫자),
 * MODIFY 조건 병합 기준이 되는 현재 조건(JSON 텍스트).
 * 출력: intent/status/clarification/modify 요약과 LLMOutput 원본 JSON.
 * 호출 시점: HomePage 하단에서 개발자가 수동으로 실행할 때 호출된다.
 * TODO: current_conditions를 JSON 텍스트 대신 필드별 입력으로 바꾸면 오탈자를 줄일 수 있다.
 */

import { useState } from "react";
import { ApiError } from "../api/client";
import { interpretDebug } from "../api/trip";
import type { LLMOutput } from "../types";

interface Preset {
  label: string;
  userInput: string;
  hasPreviousRecommendation: boolean;
  shownPlaceCount: number;
  currentConditionsText: string;
}

const PRESETS: Preset[] = [
  {
    label: "RECOMMEND",
    userInput: "경복궁 근처 카페 추천해줘",
    hasPreviousRecommendation: false,
    shownPlaceCount: 0,
    currentConditionsText: "",
  },
  {
    label: "RECOMMEND · 날씨 모호(되묻기)",
    userInput: "눈 오는데 카페 추천해줘",
    hasPreviousRecommendation: false,
    shownPlaceCount: 0,
    currentConditionsText: "",
  },
  {
    label: "MODIFY · 전체 거절",
    userInput: "다른 곳 보여줘",
    hasPreviousRecommendation: true,
    shownPlaceCount: 3,
    currentConditionsText: JSON.stringify(
      { search_center: "경복궁", place_types: ["restaurant"], place_tags: ["카페"] },
      null,
      2,
    ),
  },
  {
    label: "MODIFY · 조건 변경",
    userInput: "무료인 곳으로",
    hasPreviousRecommendation: true,
    shownPlaceCount: 2,
    currentConditionsText: JSON.stringify(
      { search_center: "경복궁", place_types: ["restaurant"] },
      null,
      2,
    ),
  },
  {
    label: "GENERAL",
    userInput: "경복궁은 언제 지어졌어?",
    hasPreviousRecommendation: false,
    shownPlaceCount: 0,
    currentConditionsText: "",
  },
  {
    label: "OUT_OF_SCOPE",
    userInput: "주식 추천해줘",
    hasPreviousRecommendation: false,
    shownPlaceCount: 0,
    currentConditionsText: "",
  },
];

export function IntentDebugPanel() {
  const [userInput, setUserInput] = useState("");
  const [hasPreviousRecommendation, setHasPreviousRecommendation] = useState(false);
  const [shownPlaceCount, setShownPlaceCount] = useState(0);
  const [currentConditionsText, setCurrentConditionsText] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<LLMOutput | null>(null);

  function applyPreset(preset: Preset) {
    setUserInput(preset.userInput);
    setHasPreviousRecommendation(preset.hasPreviousRecommendation);
    setShownPlaceCount(preset.shownPlaceCount);
    setCurrentConditionsText(preset.currentConditionsText);
    setError(null);
    setResult(null);
  }

  async function handleTest() {
    const trimmed = userInput.trim();
    if (!trimmed || isLoading) return;

    let currentConditions: Record<string, unknown> | null = null;
    if (currentConditionsText.trim()) {
      try {
        currentConditions = JSON.parse(currentConditionsText);
      } catch {
        setError("현재 조건 JSON 형식이 올바르지 않아요.");
        return;
      }
    }

    setIsLoading(true);
    setError(null);
    setResult(null);
    try {
      const output = await interpretDebug({
        user_input: trimmed,
        has_previous_recommendation: hasPreviousRecommendation,
        shown_place_count: shownPlaceCount,
        current_conditions: currentConditions,
      });
      setResult(output);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "요청 처리 중 오류가 발생했어요.");
    } finally {
      setIsLoading(false);
    }
  }

  return (
    <section className="mx-auto flex w-full max-w-xl flex-col gap-3 rounded-md border border-dashed border-blue-300 bg-blue-50 p-4 text-sm text-blue-950 dark:border-blue-700 dark:bg-blue-950/30 dark:text-blue-100">
      <div>
        <h2 className="font-semibold">개발용 Intent · 조건 추출 테스트</h2>
        <p className="mt-1 text-xs text-blue-900/80 dark:text-blue-200/80">
          "추천 시작하기"와 별개로, /api/interpret이 반환하는 Intent 분류와 조건 추출 결과를
          원본 그대로 확인합니다.
        </p>
      </div>

      <div className="flex flex-wrap gap-2">
        {PRESETS.map((preset) => (
          <button
            key={preset.label}
            type="button"
            onClick={() => applyPreset(preset)}
            className="rounded-full border border-blue-300 px-3 py-1 text-xs dark:border-blue-700"
          >
            {preset.label}
          </button>
        ))}
      </div>

      <textarea
        value={userInput}
        onChange={(event) => setUserInput(event.target.value)}
        rows={2}
        placeholder="테스트할 발화를 입력하세요"
        className="w-full resize-none rounded-md border border-blue-300 p-2 text-sm dark:border-blue-700 dark:bg-blue-950/50"
      />

      <label className="flex items-center gap-2 text-xs">
        <input
          type="checkbox"
          checked={hasPreviousRecommendation}
          onChange={(event) => setHasPreviousRecommendation(event.target.checked)}
        />
        이전 추천 이력 있음 (MODIFY/COMPARE 판별에 사용)
      </label>

      <label className="flex items-center gap-2 text-xs">
        노출된 장소 수
        <input
          type="number"
          min={0}
          value={shownPlaceCount}
          onChange={(event) => setShownPlaceCount(Number(event.target.value) || 0)}
          className="w-16 rounded border border-blue-300 px-1 dark:border-blue-700 dark:bg-blue-950/50"
        />
      </label>

      <label className="flex flex-col gap-1 text-xs">
        현재 조건 (JSON, MODIFY 테스트용 — 비워두면 없음으로 전송)
        <textarea
          value={currentConditionsText}
          onChange={(event) => setCurrentConditionsText(event.target.value)}
          rows={4}
          placeholder='{"search_center": "경복궁", "place_types": ["restaurant"]}'
          className="w-full resize-none rounded-md border border-blue-300 p-2 font-mono text-xs dark:border-blue-700 dark:bg-blue-950/50"
        />
      </label>

      {error && <p className="text-xs text-red-700 dark:text-red-300">{error}</p>}

      <button
        type="button"
        disabled={isLoading || !userInput.trim()}
        onClick={handleTest}
        className="w-fit rounded-md bg-blue-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-50 dark:bg-blue-200 dark:text-blue-950"
      >
        {isLoading ? "확인 중..." : "인텐트/조건 추출 테스트"}
      </button>

      {result && (
        <div className="flex flex-col gap-2 rounded-md border border-blue-200 bg-white p-3 dark:border-blue-800 dark:bg-blue-950/40">
          <div>
            <span className="font-medium">intent:</span> {result.intent}
            {"   "}
            <span className="font-medium">status:</span> {result.status}
          </div>
          {result.clarification && (
            <div>
              <span className="font-medium">되묻기 메시지:</span> {result.clarification.message}
            </div>
          )}
          {result.modify && (
            <div>
              <span className="font-medium">modify_type:</span> {result.modify.modify_type}
              {"   "}
              <span className="font-medium">changed_fields:</span>{" "}
              {result.modify.changed_fields.join(", ") || "없음"}
            </div>
          )}
          {result.out_of_scope && (
            <div>
              <span className="font-medium">category:</span> {result.out_of_scope.category}
              {"   "}
              <span className="font-medium">severity:</span> {result.out_of_scope.severity}
            </div>
          )}
          <pre className="max-h-80 overflow-auto rounded bg-gray-900 p-2 text-xs text-gray-100">
            {JSON.stringify(result, null, 2)}
          </pre>
        </div>
      )}
    </section>
  );
}
