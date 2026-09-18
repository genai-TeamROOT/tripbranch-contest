/*
 * 역할: 브라우저 위치를 다시 확인할 시점을 한 곳에서 판정한다.
 * 입력: 현재 위치값, 마지막 브라우저 위치 확인 시각(ms), "이 시각까지는 다시 묻지
 *       않기로" 미룬 시각(ms), 기준 시각(ms).
 * 출력: 위치 재확인 질문 필요 여부와 화면 표시용 경과 분.
 *
 * capturedAt(실제 GPS를 받은 시각)과 snoozedUntil(사용자가 "이전 위치로 계속"을
 * 눌러 재확인을 미룬 마감 시각)은 서로 다른 사실이라 필드를 분리했다. capturedAt만
 * 있으면 "이전 위치로 계속"을 눌렀을 때 이 값을 갱신해야 재질문이 멈추는데, 그러면
 * 실제로는 GPS를 다시 받지 않았는데도 나이 표시가 0분으로 리셋된다. snoozedUntil을
 * 따로 둬서 재질문 억제와 나이 표시를 독립적으로 정확하게 유지한다.
 */

/** 사용자가 새 위치를 선택한 뒤 다시 확인을 묻기까지의 시간. */
export const LOCATION_RECONFIRM_AFTER_MS = 30 * 60 * 1000;

export function isLocationRefreshDue(
  deviceLocation: string | null,
  capturedAt: number | null,
  snoozedUntil: number | null,
  now: number = Date.now(),
): boolean {
  if (deviceLocation === null) return false;
  if (snoozedUntil !== null && now < snoozedUntil) return false;
  return capturedAt === null || now - capturedAt >= LOCATION_RECONFIRM_AFTER_MS;
}

export function getLocationAgeMinutes(
  capturedAt: number | null,
  now: number = Date.now(),
): number | null {
  if (capturedAt === null) return null;
  return Math.max(1, Math.floor((now - capturedAt) / 60_000));
}
