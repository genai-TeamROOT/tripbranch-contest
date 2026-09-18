weather_intent 판별:
- AVOID: 날씨를 피하고 싶음 ("비 오는데 갈 곳", "더운데 시원한 곳") → environment도 indoor로.
  특히 비/눈/더위/추위와 함께 "실내", "피해", "피할", "시원한 곳", "따뜻한 곳"을
  말하면 날씨 회피가 명확하므로 반드시 AVOID다
  (예: "비와서 실내로 바꿔줘" → weather=rain, weather_intent=AVOID,
  environment=indoor). 이 경우 ENJOY로 분류하지 않는다.
- ENJOY: 날씨를 즐기고 싶음 ("눈 오는 거리 걷고 싶어", "단풍 보러") → environment도 outdoor로
  — "걷고 싶어", "보고 싶어", "즐기고 싶어"처럼 날씨 자체를 활동 목적으로 명시한 경우만 쓴다.
- NO_MENTION: 날씨 언급이 없음 ("경복궁 근처 카페 추천해줘")
- IGNORE: "날씨 상관없어", "비 와도 괜찮아"처럼 날씨를 감수하거나 무관함을 명시하면
  weather_intent=IGNORE. 날씨를 허용한 것이 그 날씨를 즐기고 싶다는 뜻은 아니므로 ENJOY로
  분류하지 않는다.
- 판별이 애매하면(예: "눈 오는데 추천" — 피하고 싶은지 즐기고 싶은지 불명확) weather_intent를
  null로 두고 status를 needs_clarification으로, clarification.ambiguous_fields에
  weather_intent 항목을 채운다
