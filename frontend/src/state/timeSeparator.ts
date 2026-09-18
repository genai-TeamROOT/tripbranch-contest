/*
 * 역할: 새 발화 위에 시각 구분선을 넣을지 정한다.
 * 입력: 앞 턴이 오간 시각과 지금.
 * 출력: 넣을지 여부.
 * 호출 시점: TripContext의 START_CHAT_TURN.
 *
 * 별도 모듈인 이유는 이 판정이 화면으로 재현하기 번거로운 자리에 있어서다 —
 * "30분 뒤에 이어 물었다"를 UI로 만들려면 시계를 조작해야 한다.
 */

/*
 * 30분은 세션 TTL과 같은 값이다(backend state/session.py의 SESSION_TTL).
 * 그보다 오래 자리를 비웠으면 서버는 이미 낡은 조건을 버린 뒤라, 사용자 쪽에서도
 * "이어서 하는 말"이 아니라 "다시 시작하는 말"에 가깝다.
 */
export const TIME_SEPARATOR_GAP_MS = 30 * 60 * 1000;

/**
 * 앞 턴과 이번 발화 사이에 시각 구분선을 넣을 만한 틈이 있는지.
 *
 * 앞 턴이 없으면(대화의 첫 발화) 넣지 않는다 — 위에 갈라 보일 것이 없다.
 */
export function hasTimeGap(previous: string | null, now: string): boolean {
  if (!previous) return false;
  const before = Date.parse(previous);
  if (Number.isNaN(before)) return false;
  return Date.parse(now) - before > TIME_SEPARATOR_GAP_MS;
}
