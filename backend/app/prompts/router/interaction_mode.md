interaction_mode 판별 (Intent와 **별개로**, 항상 함께 채웁니다):

이 값은 Intent 중 하나가 아니라 나란히 놓인 다른 축입니다. Intent를 먼저 정하고,
그와 무관하게 아래 기준으로 interaction_mode를 정하세요.

- `situational` — 사용자가 **곤란함·불편·돌발상황을 표현**한 경우. 무엇을 해달라고
  콕 집어 말하지 않아도 됩니다. 오히려 요청 없이 상태만 말하는 쪽이 전형적입니다.
  - 몸 상태: "다리를 다쳤어", "발이 아파", "너무 지친다", "더워 죽겠어"
  - 날씨·환경: "아 비 오네", "바람 너무 세다", "추워"
  - 일정이 틀어짐: "아 오늘 휴관이래", "사람 너무 많다", "문 닫았대"
  - 동행의 어려움: "애가 힘들어해", "부모님이 지치셨어"
  - 막연한 답답함: "오늘 진짜 되는 일이 없네", "뭐 해야 할지 모르겠어"
- `direct_request` — 그 외 전부. 무엇을 해달라거나 무엇이 궁금한지 명확한 발화입니다.
  "종로 카페 추천해줘", "경복궁 오늘 열어?", "다른 곳 보여줘", "서울 여행 팁" 등.

두 축이 함께 붙는 경우가 정상입니다. 상황을 말하면서 요청도 같이 하면 Intent는
그 요청대로 정하고 interaction_mode만 `situational`로 둡니다.

- "비 오는데 실내 카페 추천해줘" → intent=RECOMMEND, interaction_mode=situational
- "지쳤는데 경복궁 지금 붐벼?" → intent=INFO, interaction_mode=situational
- "다리를 다쳤어" → intent=GENERAL, interaction_mode=situational
- "종로 카페 추천해줘" → intent=RECOMMEND, interaction_mode=direct_request

**상황 발화를 OUT_OF_SCOPE로 보내지 마세요.** 여행과 무관해 보여도, 여행 중에 겪는
불편은 우리가 도울 수 있는 범위입니다. 서비스 범위 밖(주식·코딩·해외여행·예약 대행)이나
유해 발언이 아닌 한, 곤란함을 말하는 발화는 OUT_OF_SCOPE가 아니라 GENERAL입니다.

**단, 상황이라고 해서 없는 요청을 지어내지 마세요.** "다리를 다쳤어"는 추천 요청이
아닙니다 — intent를 RECOMMEND로 놓고 조건 없는 검색을 시작하면 안 됩니다. 요청이
명확하지 않으면 intent는 GENERAL입니다.
