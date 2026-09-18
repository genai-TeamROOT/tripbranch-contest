import type { ScheduleItem, TravelMode } from "../types";

// 사람이 말하듯 적는다. 예전에는 "도보 이동 33분 · 추정"이었는데, "이동"(수단)과
// "추정"(그 숫자의 근거)이 층위가 달라서 점 하나로 나열하면 셋이 대등하게 읽힌다 —
// 실측에서 "뭘 말하는지 모르겠다"는 지적을 받았다. 추정은 **"약"**으로 담는다.
const MODE_LABEL: Record<TravelMode, string> = {
  walking: "걸어서",
  transit: "대중교통으로",
  driving: "차로",
};

/**
 * 구간 한 줄의 표기. (TP-216)
 *
 * **이동수단을 화면에서 정하지 않는다.** 예전에는 "도보 이동 약 N분"으로 고정
 * 표기했는데, 도보 예상시간이 임계값을 넘는 구간은 서버가 대중교통으로 전환한다
 * (tools/schedule_travel._select_mode) — 4.3km 61분 구간이 화면에는 도보로 떴다.
 * 서버가 내려준 travel_to_next_mode를 그대로 쓴다.
 *
 * **추천 카드와 규칙이 다르다.** 추천 카드는 실측이 없으면 시간을 아예 말하지
 * 않고 직선거리만 말한다(utils/travelDisplay.ts). 일정은 이동시간 없이 성립하지
 * 않으므로 값을 보여주고 추정임을 함께 밝힌다.
 *
 * 이동수단이 없는 구간은 서버가 좌표를 못 구해 시간표 폴백값(15분)을 쓴
 * 자리다. 근거가 없으므로 수단도 실측 여부도 말하지 않는다.
 */
export function scheduleTravelLabel(
  minutes: number,
  mode: TravelMode | null | undefined,
  measured: boolean | undefined,
): string {
  if (!mode) {
    return `이동 약 ${minutes}분`;
  }
  return `${MODE_LABEL[mode]} ${measured ? "" : "약 "}${minutes}분`;
}

/**
 * 추정 구간에 붙는 설명. 왜 실측이 아닌지(어느 API가 실패했는지)까지는 말하지 않는다.
 *
 * 한 문장으로 둔다 — 두 문장이던 것이 "…예상값이에요. …구간입니다."로 문체가 섞여
 * 있었다.
 */
export const SCHEDULE_TRAVEL_ESTIMATE_HINT =
  "실제 경로를 못 불러와서 직선거리로 어림한 시간이에요.";

/**
 * 두 정류장이 같은 묶음인가. (TP-243)
 *
 * **묶음은 자리가 아니라 구간의 성질이라 여기서 판정한다.** "가까이 붙어 있다"는
 * 두 곳 사이의 이야기이고, 화면에서도 카드가 아니라 카드 사이 이동 줄에 붙는다.
 * 채팅 타임라인과 일정 화면이 같은 규칙을 쓰게 하려고 한 곳에 둔다.
 *
 * 번호가 없는 옛 스냅샷은 항상 false다 — 묶음 표시가 없을 뿐 화면은 그대로다.
 */
export function isSameCluster(from: ScheduleItem, to: ScheduleItem): boolean {
  const id = from.cluster_id;
  return id !== null && id !== undefined && id === to.cluster_id;
}

// 묶음 기준 도보 시간(분). **백엔드 budget.SCHEDULE_CLUSTER_WALK_MINUTES와 같은
// 값이어야 한다** — 화면이 말하는 기준과 편성이 쓴 기준이 갈리면 사용자가 읽는
// 숫자가 거짓이 된다. 한쪽을 바꾸면 다른 쪽도 바꾼다.
export const SCHEDULE_CLUSTER_WALK_MIN = 5;

/**
 * 묶음 머리에 붙는 말. (TP-243)
 *
 * **묶음은 구간마다 반복하지 않고 묶음이 시작되는 자리에서 한 번만 말한다.**
 * 이동 줄에 덧붙였더니 "걸어서 약 1분 · 이어서 둘러보기"처럼 이동 이야기와
 * 묶음 이야기가 한 줄에서 부딪혔고, 세 곳이 묶이면 줄마다 반복됐다.
 *
 * **사실만 말한다.** "가볍게 둘러봐요" 같은 말은 넣지 않는다 — 묶였다고 모든
 * 분류의 체류가 줄지는 않아서(문화시설·식당은 그대로) 거짓이 되는 묶음이 있다.
 */
export function clusterBadgeLabel(count: number, isEn: boolean): string {
  return isEn
    ? `${count} stops within a ${SCHEDULE_CLUSTER_WALK_MIN}-min walk`
    : `걸어서 ${SCHEDULE_CLUSTER_WALK_MIN}분 안쪽인 ${count}곳`;
}

/**
 * `index` 자리에서 묶음이 새로 시작하면 그 묶음의 곳 수, 아니면 null. (TP-243)
 *
 * 배지를 어디에 한 번 그릴지 정하는 값이다. 한 곳짜리 묶음은 세지 않는다 —
 * 편성은 만들지 않지만 저장한 일정에서 항목을 지우면 번호가 혼자 남을 수 있고,
 * 그때 "걸어서 5분 안쪽인 1곳"은 말이 안 된다.
 */
export function clusterStartSize(items: ScheduleItem[], index: number): number | null {
  const id = items[index]?.cluster_id;
  if (id == null) return null;
  if (index > 0 && items[index - 1].cluster_id === id) return null;
  const size = items.filter((item) => item.cluster_id === id).length;
  return size < 2 ? null : size;
}
