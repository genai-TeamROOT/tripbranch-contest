/*
 * 역할: 취향 설정 화면(/preferences)의 칩 목록과, 각 칩이 DB의 무엇에
 *   대응하는지를 정의한다.
 * 호출 시점: PreferencesPage가 렌더링할 때. 값은 정적이다.
 *
 * 칩 문구는 예시가 아니라 **DB에 실제로 대응이 있는 것만 남긴 목록이다**
 * (2026-09-02 실측). 이전 목록은 Figma 시안의 예시 문구를 그대로 쓰고 있어서,
 * 저장 경로가 생겨도 조회되지 않을 문구가 섞여 있었다 — 반려동물 동반·감성
 * 인테리어·브런치·디저트 맛집·루프탑 다섯은 대응 데이터가 0건이었다. 특히
 * 반려동물은 `places.pet_raw`에 값이 있는 곳이 활성 8,007곳 중 17곳뿐이고
 * 그 17곳이 전부 "불가"/"없음"이라, 누르면 결과가 반드시 비는 칩이었다.
 *
 * 화면 밖으로 내보내는 경로는 아직 없다(사용자 단위 취향을 받아 줄 자리가
 * 백엔드에 없다). 그래도 코드를 적어 두는 이유는 **근거가 있는 문구와 없는
 * 문구를 갈라 두기 위해서다.**
 */

/**
 * 칩 하나가 DB의 무엇에 대응하는지.
 *
 * - `preference`: `place_preference_tags.preference_code`. 리뷰·블로그에서 뽑은
 *   장소별 취향 태그다. **적재된 구가 용산·성동 둘뿐이라**(654곳, 활성 장소의
 *   8.2%) 다른 구에서는 아직 붙는 장소가 없다. 나중에 실제로 쓸 때는 후보를
 *   걸러내는 필터가 아니라 순위 가중치로 쓰는 편이 안전하다 — 필터로 쓰면
 *   다른 구에서 결과가 통째로 사라진다.
 * - `place_tag`: TourAPI 소분류에 매핑된 `PlaceTag` 값. 전 지역에 있다. 한 칩이
 *   여러 태그를 뜻할 수 있어 배열이다.
 */
export interface PreferenceOption {
  label: string;
  /** 영어 UI에서 보여줄 표시용 문구. `label`은 선택 상태·저장값의 식별자라 언어가
   *  바뀌어도 그대로 두고, 화면에 그릴 때만 이 값으로 바꿔 쓴다. */
  labelEn: string;
  source: "preference" | "place_tag";
  codes: readonly string[];
}

/*
 * 아래 괄호 안 숫자는 그 코드가 붙어 있는 장소 수다(2026-09-02 실측,
 * place_preference_tags 2,611행 / 654곳 · 활성 장소 8,007곳 기준).
 * place_tag 쪽은 places.lcls_systm3로 센 활성 장소 수다.
 */
export const MOOD_OPTIONS: readonly PreferenceOption[] = [
  { label: "사진 명소", labelEn: "Photo spots", source: "preference", codes: ["photo_spot"] }, // 363
  { label: "힙한 분위기", labelEn: "Trendy vibe", source: "preference", codes: ["trendy_hotspot"] }, // 130
  { label: "힐링하기 좋은", labelEn: "Good for healing", source: "preference", codes: ["healing"] }, // 119
  { label: "색다른 경험", labelEn: "Unique experience", source: "preference", codes: ["unique"] }, // 106
  { label: "아늑한 공간", labelEn: "Cozy space", source: "preference", codes: ["cozy"] }, // 79
  { label: "전망 좋은", labelEn: "Great view", source: "preference", codes: ["good_view"] }, // 75
  { label: "조용한 곳", labelEn: "Quiet place", source: "preference", codes: ["quiet"] }, // 65
  { label: "야경 명소", labelEn: "Night view spots", source: "preference", codes: ["night_visit"] }, // 60
  { label: "넓고 쾌적한", labelEn: "Spacious & comfortable", source: "preference", codes: ["spacious"] }, // 50
];

/*
 * 테마는 둘을 섞는다. **분류로 답할 수 있는 것은 place_tag를 먼저 쓴다** —
 * 취향 태그는 두 개 구에만 있지만 분류는 전 지역에 있어서, 같은 칩이라면
 * 분류 쪽이 훨씬 넓게 걸린다(예: 전시·문화는 취향 태그 155곳 vs 분류 305곳).
 */
export const THEME_OPTIONS: readonly PreferenceOption[] = [
  { label: "전시·문화", labelEn: "Exhibits & culture", source: "place_tag", codes: ["박물관", "미술관", "전시관", "전시회"] }, // 305
  { label: "카페", labelEn: "Cafes", source: "place_tag", codes: ["카페", "찻집"] }, // 247
  { label: "시장·쇼핑", labelEn: "Markets & shopping", source: "place_tag", codes: ["시장", "쇼핑몰", "백화점"] }, // 127
  { label: "자연·공원", labelEn: "Nature & parks", source: "place_tag", codes: ["공원", "산", "호수", "계곡", "수목원"] }, // 101
  { label: "전통·역사", labelEn: "Tradition & history", source: "place_tag", codes: ["궁궐", "사찰", "성곽", "전통체험", "마을"] }, // 89
  { label: "체험·액티비티", labelEn: "Experiences & activities", source: "preference", codes: ["experience"] }, // 195
  { label: "산책하기 좋은", labelEn: "Good for walking", source: "preference", codes: ["walk"] }, // 121
  { label: "로컬 맛집", labelEn: "Local food spots", source: "preference", codes: ["food_exploration"] }, // 107
  { label: "날씨 상관없는 곳", labelEn: "Weather-proof spots", source: "preference", codes: ["indoor"] }, // 98
  { label: "책 읽기 좋은", labelEn: "Good for reading", source: "preference", codes: ["reading"] }, // 10
];

export const COMPANION_OPTIONS: readonly PreferenceOption[] = [
  { label: "데이트 코스", labelEn: "Date course", source: "preference", codes: ["date"] }, // 207
  { label: "친구와 함께", labelEn: "With friends", source: "preference", codes: ["with_friends"] }, // 206
  { label: "아이와 함께", labelEn: "With kids", source: "preference", codes: ["with_kids"] }, // 67
  { label: "단체 모임", labelEn: "Group gathering", source: "preference", codes: ["group_gathering"] }, // 56
  { label: "부모님과 함께", labelEn: "With parents", source: "preference", codes: ["with_parents"] }, // 48
  { label: "혼자 가기 좋은", labelEn: "Good for solo trips", source: "preference", codes: ["alone"] }, // 37
];

/** 세 축 전체. 선택 상태를 label로 들고 있어서, 테스트가 라벨 중복까지 막는다. */
export const PREFERENCE_GROUPS: readonly (readonly PreferenceOption[])[] = [
  MOOD_OPTIONS,
  THEME_OPTIONS,
  COMPANION_OPTIONS,
];
