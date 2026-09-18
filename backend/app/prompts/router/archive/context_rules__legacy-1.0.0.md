맥락 의존 판별 (이전 추천 이력에 따라 같은 문장도 다르게 판정):
- 이전 추천 있음 + "다른 곳" → MODIFY
- 이전 추천 없음 + "다른 곳" → RECOMMEND로 처리 (전제조건 미충족이므로 MODIFY로 판정하지 않음)
- 이전 추천 2개 이상 + "어디가 좋아?" → COMPARE
- 이전 추천 1개 이하 + "어디가 좋아?" → RECOMMEND 또는 GENERAL로 처리 (COMPARE 전제조건 미충족)
- 이전 추천 있음 + "카페 말고 맛집" → MODIFY (조건 변경)
- 이전 추천 없음 + "카페 말고 맛집" → RECOMMEND (place_types=["restaurant"])
- 이전 추천 있음 + "더 가까운 곳" → MODIFY
- 이전 추천 없음 + "더 가까운 곳" → RECOMMEND
