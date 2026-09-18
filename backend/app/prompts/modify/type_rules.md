modify_type 판별:
- REJECT_ALL: 이전 추천 전체를 거부하고 다른 결과를 원함. 특정 순번을 예외로
  남기겠다는 언급이 전혀 없는 경우만 해당한다
  ("다른 곳 보여줘", "전부 별로야", "다른 거 없어?", "다 마음에 안 들어")
  → condition_changes는 null, changed_fields는 빈 배열, target_indices는 빈 배열
- REJECT_SPECIFIC: 이전 추천 중 일부만 거부하고 그 자리만 다른 곳으로 바꾸고
  싶음. 순번("두 번째")이 아니라 "아래 노출된 항목 목록"에 있는 장소 이름을
  직접 언급해도 동일하게 REJECT_SPECIFIC이다(예: "두가헌 레스토랑은 빼줘").
  아래 두 방향 모두 REJECT_SPECIFIC이며 target_indices의 의미가 정반대이므로
  혼동하지 않는다(자세한 계산은 target_indices 판별 규칙 참고):
  1) 바꿀 자리를 직접 지목 ("두 번째는 별로야", "세 번째만 다른 데로", "첫 번째 빼줘",
     "두가헌 레스토랑은 빼줘")
  2) 남길 자리를 지목하고 그 외 전부를 거부 ("두 번째 말고는 다 마음에 안 들어",
     "세 번째만 남기고 나머지는 바꿔줘", "두가헌 레스토랑만 빼고 다 별로야") —
     표면상 "다 마음에 안 들어"처럼 REJECT_ALL 예문과 겹쳐 보여도, "N번째/이름
     말고는·빼고는"으로 특정 항목을 예외 처리했다면 REJECT_ALL이 아니라
     REJECT_SPECIFIC이다
  → 두 경우 모두 condition_changes는 null, changed_fields는 빈 배열
- CHANGE_CONDITION: 추천 조건 자체를 바꾸고 싶음
  ("더 가까운 곳", "무료인 곳으로", "실내로 바꿔줘", "카페 말고 맛집")
  → condition_changes에 병합 후 최종 값을 채우고, changed_fields에 실제로 바뀐 필드명을 나열,
    target_indices는 빈 배열
