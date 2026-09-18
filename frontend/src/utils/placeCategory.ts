/*
 * 역할: 장소의 분류를 화면에 쓸 한글 한 마디로 고른다.
 * 입력: 추천 항목의 category(대분류 코드)와 category_label(중분류 한글명).
 * 출력: 칩에 찍을 문자열. 둘 다 쓸 수 없으면 null이라 부르는 쪽이 칩을 접는다.
 * 호출 시점: 장소 분류를 보여주는 화면(상세 모달의 분류 칩 등).
 *
 * **두 값이 다른 것을 가리킨다.**
 * - `category`는 TourAPI contenttypeid를 옮긴 대분류 코드다(`restaurant`,
 *   `shopping`). 6종뿐이고 영어라 그대로 찍으면 화면에 `shopping`이 나온다.
 * - `category_label`은 소분류 코드를 풀어낸 중분류 한글명이다(`한식`, `면세점`).
 *   41종이라 훨씬 구체적이다. 추천 후보 7,448건이 모두 해석됐지만(2026-09-08
 *   실측, 실패 0건) 카드 조회가 실패하면 응답에 안 실린다.
 *
 * 그래서 중분류를 먼저 쓰고, 없으면 대분류를 한글로 옮긴다.
 */

/**
 * 대분류 코드의 한글 이름. **개발자 패널(DeveloperAuditPanel)의 place_types
 * 라벨과 같은 표여야 한다** — 같은 코드가 화면마다 다른 말로 불리면 안 된다.
 */
export const PLACE_CATEGORY_LABELS: Record<string, string> = {
  attraction: "관광지",
  cultural_facility: "문화시설",
  festival: "행사·축제",
  leisure: "레저",
  shopping: "쇼핑",
  restaurant: "음식점",
};

/*
 * 원본 데이터에 `카페/ 찻집`처럼 구분자 뒤에만 공백이 있는 값이 있다(2026-09-08
 * 실측). 칩은 짧은 한 마디라 그 틈이 눈에 띈다 — 구분자 주변 공백을 걷고 사이
 * 공백도 하나로 줄인다. 데이터를 고치지 않고 표시할 때만 다듬는다.
 */
function tidy(label: string): string {
  return label
    .replace(/\s*([/·])\s*/g, "$1")
    .replace(/\s+/g, " ")
    .trim();
}

export function placeCategoryLabel(place: {
  category?: string | null;
  category_label?: string | null;
}): string | null {
  const middle = place.category_label?.trim();
  if (middle) return tidy(middle);

  const large = place.category?.trim();
  if (!large) return null;
  /* 표에 없는 코드는 그대로 보여준다. 영어가 찍히긴 하지만, 분류를 아는데 감추는
     것보다는 낫고 새 코드가 들어온 것을 화면에서 바로 알아챌 수 있다. */
  return PLACE_CATEGORY_LABELS[large] ?? large;
}
