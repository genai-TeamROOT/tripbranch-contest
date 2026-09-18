/*
 * 역할: 지난 대화를 펼칠 때, 그때 나갔던 장소 묶음을 어느 말풍선 뒤에 놓을지 정한다.
 * 입력: 저장된 대화 턴 목록과 추천 목록(둘 다 시간순).
 * 출력: 턴별로 붙일 묶음의 목록.
 * 호출 시점: TripContext의 RESTORE_SESSION.
 *
 * 별도 모듈인 이유는 이 규칙이 시각 비교 하나로 조용히 틀릴 수 있어서다 —
 * 리듀서 안에 두면 앱을 통째로 띄우지 않고는 경계값을 확인할 수 없다.
 */

import type { PastRecommendation, StoredConversationTurn } from "../types";

/*
 * 추천은 그 턴이 기록되기 **전에** 남는다(실측 102쌍 중 97쌍이 0~120초 먼저,
 * 평균 97초). 그래서 각 묶음을 "그 묶음보다 늦게 기록된 첫 턴"에 붙인다.
 *
 * 창을 두는 이유는 recent_turns가 5개만 남기 때문이다. 화면에 없는 옛 턴의
 * 추천도 규칙상 남아 있는 첫 턴에 걸리는데, 그대로 붙이면 하지도 않은 질문의
 * 답으로 보인다. 10분은 실측 간격에 넉넉히 여유를 둔 값이고, 창을 벗어난 묶음은
 * 어디에도 붙이지 않고 버린다 — 말풍선 없이 카드만 뜬 블록보다 낫다.
 */
const RECOMMENDATION_ATTACH_WINDOW_MS = 10 * 60 * 1000;

export function attachRecommendationsToTurns(
  turns: StoredConversationTurn[],
  places: PastRecommendation[],
): PastRecommendation[][][] {
  /* 턴 하나에 묶음이 여러 개 붙을 수 있다 — 같은 턴에서 "다른 곳 보여줘"로
     추천이 두 번 나가면 run_id가 둘이다. 그래서 턴별 "묶음의 목록"이다. */
  const groupsPerTurn: PastRecommendation[][][] = turns.map(() => []);
  if (places.length === 0) return groupsPerTurn;

  /* 같은 턴에 함께 나간 장소는 한 묶음이다. 서버가 시간순으로 주므로 순서는
     그대로 따르고, 묶음의 시각은 그중 가장 이른 것으로 본다. */
  const groups = new Map<string, PastRecommendation[]>();
  for (const place of places) {
    const group = groups.get(place.run_id);
    if (group) group.push(place);
    else groups.set(place.run_id, [place]);
  }

  const turnTimes = turns.map((turn) => Date.parse(turn.at));
  for (const group of groups.values()) {
    const shownAt = Math.min(...group.map((place) => Date.parse(place.shown_at)));
    if (Number.isNaN(shownAt)) continue;
    const index = turnTimes.findIndex(
      (at) => !Number.isNaN(at) && at >= shownAt && at - shownAt <= RECOMMENDATION_ATTACH_WINDOW_MS,
    );
    if (index >= 0) groupsPerTurn[index].push(group);
  }
  return groupsPerTurn;
}
