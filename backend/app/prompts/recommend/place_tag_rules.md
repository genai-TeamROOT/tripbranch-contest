place_types / place_tags 규칙:
- place_tags가 있으면 소속 place_types를 자동으로 함께 채운다 (예: "카페" → restaurant)
- place_types만 있고 place_tags가 없으면 해당 유형 전체를 의미 (place_tags: [])
- 음식점 대분류(`restaurant`)는 카페·찻집·주점까지 포함하므로, 아래처럼 **명시된
  음식 업종은 반드시 place_tags에도 넣어** 소분류로 좁힌다.
  - "식당", "음식점", "맛집", "밥집", "혼밥" → place_types: [restaurant], place_tags: [식당]
  - "카페", "찻집" → place_types: [restaurant], place_tags: [카페]
  - "술집", "주점", "펍" → place_types: [restaurant], place_tags: [주점]
  - "한식", "일식", "중식", "양식", "분식"처럼 더 구체적인 업종은 해당 태그를 넣는다.
  - "카페 말고 식당"처럼 제외를 말하면 제외 대상 태그를 넣지 말고, 남은 요청 업종만 넣는다.
- 아무 유형도 언급하지 않았으면 둘 다 빈 배열 (전체 검색)
- 복수 유형이 언급되면 언급 순서대로 모두 담는다 (예: "박물관이나 카페" →
  place_types: [cultural_facility, restaurant], place_tags: [박물관, 카페])
- **행사를 가리키는 말은 담지 않는다.** 축제·행사·전시회·공연·콘서트는 장소가 아니라
  기간이 있는 행사라 장소 추천이 다루지 않는다 — festival 유형과 축제·전시회·공연·
  콘서트 태그를 쓰지 말고, 그 말만 있으면 place_types·place_tags를 모두 빈 배열로 둔다.
  건물을 가리키는 말은 그대로 담는다 (미술관 → cultural_facility/미술관, 공연장 →
  cultural_facility/공연장)
