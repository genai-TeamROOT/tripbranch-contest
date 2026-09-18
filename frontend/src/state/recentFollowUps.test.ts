/*
 * 이미 보여준 후속 질문 목록을 쌓는 규칙을 못박는다.
 *
 * 이 목록은 다음 발화에 함께 나가 서버가 같은 문구를 다시 권하지 않는 근거가 된다.
 * 서버가 스스로 만들 수 없는 값이라(보여주기만 하고 누르지 않은 문구는 화면 말고
 * 아는 곳이 없다) 여기서 잘못 쌓이면 서버 쪽에는 고칠 방법이 없다.
 */

import { MAX_RECENT_FOLLOW_UPS, mergeRecentFollowUps } from "./TripContext";

it("보여준 문구를 뒤에 이어 붙인다", () => {
  const merged = mergeRecentFollowUps(["다른 곳도 보여줘"], ["이 장소들로 일정 짜줘"]);

  expect(merged).toEqual(["다른 곳도 보여줘", "이 장소들로 일정 짜줘"]);
});

it("상한 안에서는 이전 턴 문구도 함께 남는다", () => {
  /* 직전 턴이 두 개만 냈으면 그 앞 턴의 문구 하나가 자리에 남는다 — 매번 최신 한
     벌로 갈아치우지 않는다. */
  let merged = mergeRecentFollowUps([], ["A", "B", "C"]);
  merged = mergeRecentFollowUps(merged, ["D", "E"]);

  expect(merged).toEqual(["C", "D", "E"]);
});

it("같은 문구가 또 나오면 한 벌만 남긴다", () => {
  const merged = mergeRecentFollowUps(["A", "B"], ["B", "C"]);

  expect(merged).toEqual(["A", "B", "C"]);
});

it("상한이 세 개다 - 직전 턴에 보여준 버튼 전부에 해당한다", () => {
  /* 이 값이 커지면 같은 장소로 이어가는 대화에서 권할 것이 남지 않아 버튼이 사라진다.
     backend/app/schemas.py의 MAX_RECENT_FOLLOW_UPS 주석에 측정값이 있다. */
  expect(MAX_RECENT_FOLLOW_UPS).toBe(3);
});

it("띄어쓰기와 물음표만 다른 문구도 같은 것으로 본다", () => {
  /* 표기 차이로 두 벌이 쌓이면 열 자리가 같은 말로 차서 실제로 막는 범위가 줄어든다. */
  const merged = mergeRecentFollowUps(["여기 주차되나요?"], ["여기 주차 되나요"]);

  expect(merged).toEqual(["여기 주차 되나요"]);
});

it("상한을 넘으면 오래된 것부터 버린다", () => {
  const 기존 = Array.from({ length: MAX_RECENT_FOLLOW_UPS }, (_, i) => `문구 ${i}`);

  const merged = mergeRecentFollowUps(기존, ["새 문구"]);

  expect(merged).toHaveLength(MAX_RECENT_FOLLOW_UPS);
  expect(merged.at(0)).toBe("문구 1");
  expect(merged.at(-1)).toBe("새 문구");
});
