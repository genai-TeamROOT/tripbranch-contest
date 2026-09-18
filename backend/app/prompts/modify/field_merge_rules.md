필드 병합 규칙 — condition_changes에는 이번 발화로 실제로 바뀌는 필드만 값을 채우고,
그 외 모든 필드는 null(목록형 필드는 빈 배열)로 두세요. 현재 조건 값(current_json)을
그대로 복사해서 채우지 마세요 — 바뀌지 않은 필드는 서버가 별도로 유지하므로, 여기서
다시 채울 필요가 없습니다. 단, 대화 이력 규칙에 따라 직전(또는 전전) 턴의 대화 흐름상
실제로 이어지는 조건이 있다면(예: 직전 INFO 턴에서 물은 장소 유형), 그건 current_json을
복사하는 것이 아니라 이번 발화가 실질적으로 요구하는 값이므로 채우고 changed_fields에도
넣으세요.

- current_location/search_center/weather/weather_intent/concentration_intent/transport/
  max_travel_time/time_available/environment/companion: 언급된 필드만 새 값으로 채운다
- max_travel_time/time_available: 시간 제한이 없다고 말하거나(해제) 언급이 없으면
  null로 채우세요. 0을 반환하지 마세요.
- max_travel_time/time_available은 **분(minute) 단위 정수**입니다. "시간(hour)"으로
  말했으면 60을 곱해 분으로 환산하세요 — 숫자만 그대로 옮기지 마세요
  (예: "5시간" → 300, "2시간 30분" → 150, "30분" → 30(환산 불필요)).
- budget: "무료만" 같은 교체는 새 값으로("free" 리터럴 사용, 아래 budget 규칙 참고),
  "가격 상관없어" 같은 해제는 null로
- 장소 유형·태그를 새로 요청하면 place_types와 place_tags를 반드시 함께 채운다.
  두 필드는 각각 최종 목록으로 전체 교체되며, changed_fields에도 둘 다 넣는다.
- 단순한 새 유형 요청은 기존 유형을 대체한다. 조사 "도"만으로는 추가가 아니다.
  예: 현재 place_types=["restaurant"], place_tags=["카페"]에서 "공원도 추천해줘" →
  place_types=["attraction"], place_tags=["공원"],
  changed_fields=["place_types", "place_tags"].
- 복수 유형을 함께 원한다는 표현("카페와 공원 같이 추천해줘", "카페나 공원",
  "카페와 공원 모두")은 발화에서 언급한 유형을 모두 최종 목록에 넣는다. 예:
  place_types=["restaurant", "attraction"], place_tags=["카페", "공원"].
- 기존 조건에 새 유형을 명시적으로 더하는 표현("공원도 포함해줘", "공원도 함께 넣어줘")은
  현재 목록과 새 유형을 합친 최종 목록을 채운다. 예: 현재 ["카페"]에서 "공원도 포함" →
  place_types=["restaurant", "attraction"], place_tags=["카페", "공원"].
- "카페 말고 공원"처럼 제외·대체를 명시한 경우에도 새 유형만 최종 목록에 넣는다.
- exclude_tags/special_requirements: 추가/제거를 반영한 최종 목록
- accessibility_needs: place_types와 같은 방식으로 병합한다. 단순 교체 표현("휠체어 말고
  유모차로", "유모차 갈 수 있는 곳으로 바꿔줘")은 새로 언급된 값만 최종 목록에 넣고
  기존 값은 대체한다 — 조건은 AND로 좁혀지므로, 교체 의도인데 기존 값을 남기면 두 조건을
  모두 만족하는 곳으로 후보가 잘못 좁아진다. "~도"처럼 명시적으로 더하는 표현("장애인
  화장실도 있는 곳으로", "유모차도 되게 해줘")만 기존 목록에 합친다. 값 이름은 반드시
  위 accessibility_needs 규칙에 있는 9개 중에서만 고른다 — 없는 값을 만들어내지 않는다.
- "더 가까운 곳"처럼 상대적 표현은 "현재 조건"의 값을 참고해서 계산한 새 값을 채운다
  (예: 현재 max_travel_time=30 → 15). 이 경우도 값이 실제로 바뀌는 것이므로 채운다

changed_fields에는 이번 발화로 값을 채운 UserConditions 필드명만 정확히 나열하세요.
condition_changes에서 값을 채운 필드와 changed_fields의 목록은 항상 일치해야 합니다.
