직전 턴에 질문 종류를 이미 파악했는데 장소를 몰라 되물은 상태입니다.
- 이전 질문 종류(question_type): {{pending_question_type}}
- 이전 원문 질문: {{pending_specific_question}}
- 이전 visit_time: {{pending_visit_time}}

이번 발화가 그 질문에 대한 장소 답변으로 보이면(지명만 던지는 짧은 응답 등)
question_type과 specific_question, visit_time은 위 값을 유지하고 place_name만 이번
발화에서 채우세요. 이번 발화가 완전히 다른 새 질문이면 이 정보는 무시하고 새로
판단하세요.
