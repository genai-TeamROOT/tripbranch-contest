concentration_intent 판별:
- AVOID: 혼잡한 곳을 피하고 싶음 ("조용한 공원 추천해줘", "한적한 곳 가고싶어", "사람 없는 데")
- SEEK: 혼잡한(인기 있는) 곳을 원함 ("핫한 관광지 어디야", "인기 많은 곳 추천해줘", "북적이는 데")
- IGNORE: 혼잡도 관련 언급이 없거나, "사람 많아도 괜찮아"처럼 혼잡을 감수한다고 말하면
  concentration_intent=IGNORE. 이는 혼잡한 곳을 원하는 SEEK가 아니다.
- weather_intent와 달리 하드 필터(environment)에 관여하지 않는다. 판별이 애매해도
  needs_clarification을 유발하지 않는다 — null로 두면 IGNORE와 동일하게 처리된다
  (weather_intent 규칙을 여기 적용하지 말 것)
