/*
 * 역할: 취향 설정 화면에서 고른 취향을 localStorage에 저장·복원한다.
 * 입력: 화면에서 만든 SavedPreference 배열.
 * 출력: 저장된 값 또는 없으면 빈 배열.
 * 호출 시점: PreferencesPage가 저장·초기화할 때, HomePage가 마운트될 때.
 * TODO: 사용자 단위 취향을 받는 백엔드 엔드포인트가 생기면 이 모듈을 API 호출로
 *   교체한다 — 지금은 이 기기에만 남는다(sidebarStorage와 같은 처지다).
 *
 * **저장한다고 추천이 달라지지는 않는다.** 고른 값을 추천 요청에 실어 보내는
 * 경로는 아직 없다. 순위가 바뀌는 변경이라 실측하고 넣기로 했다 — 취향 질의는
 * 어미 하나로 통과 수가 크게 흔들리고(백엔드 real_recommendation_provider.py의
 * 실측 주석), 3~5개를 한꺼번에 붙였을 때가 어떤지는 아직 재본 적이 없다.
 */

const PREFERENCES_KEY = "tb_preferences";

export interface SavedPreference {
  label: string;
  /**
   * 이 취향이 DB의 무엇에 대응하는지. `custom`은 사용자가 직접 입력한 키워드라
   * 대응하는 코드가 없다(codes가 빈 배열이다).
   */
  source: "preference" | "place_tag" | "custom";
  codes: readonly string[];
}

function isSavedPreference(value: unknown): value is SavedPreference {
  if (!value || typeof value !== "object") return false;
  const entry = value as Record<string, unknown>;
  return (
    typeof entry.label === "string" &&
    entry.label.trim() !== "" &&
    (entry.source === "preference" || entry.source === "place_tag" || entry.source === "custom") &&
    Array.isArray(entry.codes) &&
    entry.codes.every((code) => typeof code === "string")
  );
}

export function loadPreferences(): SavedPreference[] {
  try {
    const raw = localStorage.getItem(PREFERENCES_KEY);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    /* 형태가 어긋난 항목만 버린다 — 하나가 깨졌다고 나머지까지 잃을 이유는 없다. */
    return parsed.filter(isSavedPreference);
  } catch {
    return [];
  }
}

export function savePreferences(preferences: readonly SavedPreference[]): void {
  try {
    localStorage.setItem(PREFERENCES_KEY, JSON.stringify(preferences));
  } catch {
    /* localStorage가 막혀 있어도(시크릿 모드 등) 화면 동작 자체는 계속돼야 한다. */
  }
}

export function clearPreferences(): void {
  try {
    localStorage.removeItem(PREFERENCES_KEY);
  } catch {
    /* 위와 같다. */
  }
}
