question_type 판별:
- operating_hours: 운영시간/휴무일/현재 영업 여부 ("오늘 열어?", "몇 시까지?")
- fee: 입장료/이용료 ("입장료 얼마?", "무료야?")
- parking: 특정 장소 하나의 정적 주차 가능 여부/요금(그 장소 자체 안내문 그대로).
  "이 장소 주차 있어?"처럼 대상이 명확한 경우. "주변/근처 주차장"처럼 여러 곳을
  찾는 질문은 realtime_parking으로 본다.
- facility: 편의시설·접근성·동반자 적합성.
- event: 관광공사 기반 전시/행사/프로그램
- location_info: 위치/주소/찾아가는 법/도보 이동 시간
- general_info: 장소 개요/특징/일반 설명
- concentration: 특정 장소/지역의 방문객 혼잡도 예측
- realtime_commercial: 특정 업종의 지금 상권 활동 질문
- realtime_parking: 주변 주차장·주차 자리·잔여 여부를 묻는 질문. "지금/현재/실시간"이
  있으면 확실하지만, 없어도 "주변/근처"처럼 여러 주차장을 찾는 의도면 이 유형으로 본다.
- realtime_subway: "지금/현재"와 지하철 도착·몇 분 후를 함께 묻는 질문
- realtime_bus: "지금/현재"와 주변 버스정류장을 묻는 질문
- realtime_event: "지금/오늘"과 주변 행사·축제를 함께 묻는 질문
- realtime_traffic: 가는 길/도로 정체 여부를 묻는 질문

판별 우선순위:
- 주변 주차장·주차 자리/잔여를 찾는 질문(시제 키워드 없어도) → realtime_parking
- "지금/현재" + 지하철 도착/몇 분 후 → realtime_subway
- "지금/현재" + 버스정류장 → realtime_bus
- "지금/오늘" + 주변 행사 → realtime_event
- "가는길/도로" + "막혀/정체/소통" → realtime_traffic
- 업종/상권이 함께 있고 현재 활동을 묻는 경우 → realtime_commercial
- 동반자와 함께 갈 수 있는지를 묻는 질문은 facility로 본다.
- 혼잡·사람 수·붐빔을 직접 묻는 경우에만 concentration으로 보낸다.
- 위 어느 유형에도 맞지 않으면 general_info로 보낸다.
