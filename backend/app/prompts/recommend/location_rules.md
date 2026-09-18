위치 필드 규칙:
- current_location: 사용자가 자기가 어디서 움직이는지를 직접 밝힌 경우만 채움. 그 외엔 null
  (GPS로 보충되는 값이므로 언급 없으면 비워둔다)
  - "나 지금 ~~야", "~~에 있는데"처럼 **지금 있는 곳**을 말한 경우
  - "~~에서 출발할건데", "~~에서 출발해서", "~~에서 갈 건데"처럼 **출발점**을 말한 경우도
    똑같이 채운다. 이동시간(max_travel_time)을 함께 말했는지는 상관없다.
  - 한 발화에 출발점과 목적지가 같이 나오면 출발점이 current_location, 목적지가
    search_center다. 발화에 취향·동행·장소 유형이 길게 붙어 있어도 출발점을 버리지 않는다.
    - 예) "효창공원역앞에서 출발할건데 용문동에 아이랑 체험하기 좋은 곳 추천해줘"
      → current_location="효창공원역앞", search_center="용문동"
- search_center: 사용자가 "~~ 근처", "~~ 주변", "~~ 가려는데" 또는 지명만 단독으로
  목적지를 밝히면 그 장소. 목적지 언급이 없으면 null. 단순 지명은 해당 장소의 정보 질문이
  아니라 그 장소 근처 추천 요청으로 취급한다 — 정보성 질문은 Intent 분류 단계에서만 INFO다.
- travel_origin: max_travel_time(이동시간)을 말한 요청에서, 그 시간을 "어디서부터"
  잰 것인지 조사가 분명히 밝히면 "search_center". 장소 뒤에 "에서"/"까지"가 붙어 그
  장소가 출발점임을 확정하는 경우만 채운다 — 그 외(근처/주변, 지명 단독, 조사 없는
  "~~ 10분 거리")는 출발점이 불분명하므로 null로 둔다. "user_location"은 이 단계에서
  쓰지 않는다.
  - 예) "안국역에서 10분 안에 갈 수 있는 카페" → search_center="안국역", travel_origin="search_center"
  - 예) "안국역까지 도보 10분 거리인 곳" → search_center="안국역", travel_origin="search_center"
  - 예) "안국역 근처에 10분 안에 갈 수 있는 카페" → search_center="안국역", travel_origin=null
  - 예) "안국역 10분 거리에 있는 카페" → search_center="안국역", travel_origin=null (조사 없음)
  - max_travel_time을 언급하지 않은 요청에는 이 필드를 채우지 않는다.
- 값이 빈 문자열이거나 공백만 있으면 null로 반환하세요("" 또는 "   " 금지, 값이 없으면 null)
