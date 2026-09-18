/*
 * 역할: 서버가 준 운영시간 표시값이 "상시 개방"을 뜻하는지 판정한다.
 * 입력: RecommendationItem.operating_hours_display("09:00~18:00"·"24시간" 등).
 * 출력: 상시 개방 여부.
 * 호출 시점: 추천 목록 카드와 상세 모달이 운영 상태를 그릴 때.
 */

/**
 * 그 장소가 시간을 안 보고 가도 되는 곳인지.
 *
 * **목록과 상세가 같은 규칙을 써야 해서 여기로 뺐다.** 각자 판단하면 목록에서는
 * 상시라고 해 놓고 상세에서는 "영업 중"이라고 말하는 어긋남이 생긴다.
 *
 * 서버는 원문이 "상시 개방"이든 "24시간"이든 같은 00:00~24:00 구간으로 바꿔
 * 보내므로(`domain/operating_hours.py`의 `_ALWAYS_OPEN_PATTERN`), 실제로 오는
 * 값은 대부분 `"24시간"` 하나다. 나머지 표기는 이전 응답이나 원문이 그대로 실려
 * 오는 경우를 위한 것이다.
 */
export function isAlwaysOpen(operatingHours: string): boolean {
  const normalized = operatingHours.replaceAll(/\s/g, "").toLowerCase();
  return (
    normalized.includes("24시간") ||
    normalized.includes("상시개방") ||
    normalized.includes("연중무휴") ||
    normalized === "00:00~24:00" ||
    normalized === "00:00-24:00"
  );
}
