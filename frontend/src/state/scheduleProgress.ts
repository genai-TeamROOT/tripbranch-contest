/*
 * 역할: 일정 상세에서 "다녀왔어요"로 체크한 정류장들을 저장한다.
 * 입력: 일정을 구분하는 키(= 저장한 일정의 id). "지금 일정" 줄을 없앤
 *   2026-09-16부터 저장하지 않은 일정은 상세로 열리지 않아, 메시지 id를
 *   키로 쓰는 경로는 남아 있지 않다.
 * 출력: 체크한 정류장들의 인덱스 목록.
 * 호출 시점: ScheduleRoute가 마운트되거나 체크를 켜고 끌 때.
 *
 * localStorage에 둔다 — 화면을 나갔다 들어와도(세션이 끊겨도) 어디까지 왔는지는
 * 남아 있는 게 자연스럽다. 계정에 묶을 정도로 중요한 값은 아니라서 서버에는
 * 안 보낸다.
 *
 * **정류장마다 독립적으로 켜고 끈다**(2026-09-07). 처음엔 "지금 있는 곳"
 * 하나만 가리키는 인덱스 하나였는데, 그걸 크게 보여주는 화면 자체를 접으면서
 * (ScheduleRoute 상단 docstring) 굳이 순서를 강제할 이유도 사라졌다 — 아무
 * 카드나 체크하고 아무 때나 되돌릴 수 있다.
 */

const PREFIX = "tripbranch_schedule_progress:";

export function loadVisitedIndices(scheduleKey: string): number[] {
  try {
    const raw = localStorage.getItem(PREFIX + scheduleKey);
    if (raw === null) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter((value): value is number => typeof value === "number");
  } catch {
    return [];
  }
}

export function saveVisitedIndices(scheduleKey: string, indices: number[]): void {
  try {
    localStorage.setItem(PREFIX + scheduleKey, JSON.stringify(indices));
  } catch {
    /* 프라이빗 모드 등으로 저장이 막혀도 체크 자체는 계속 동작해야 한다. */
  }
}

/*
 * 일정마다 키가 하나씩 생기므로(PREFIX + id) 지울 때는 접두어로 훑는다.
 * `clearLocalUserData()`가 로그아웃 때 부른다 — 다음 사람 화면에 앞사람이
 * 체크한 것이 남아 있으면 안 되고, 안 지우면 본 일정마다 키가 영구히 쌓인다.
 */
export function clearScheduleProgress(): void {
  try {
    const keys = Object.keys(localStorage).filter((key) => key.startsWith(PREFIX));
    keys.forEach((key) => localStorage.removeItem(key));
  } catch {
    /* 저장소를 못 읽는 환경이면 지울 것도 없다. */
  }
}
