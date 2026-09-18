# SCHEDULE Prompt History

## 현재 활성 슬롯

| 슬롯 | 관리 버전 | 템플릿 |
| --- | --- | --- |
| schedule.plan | v2.0 | plan.md |
| schedule.plan_context | v1.2 | plan_context.md |
| schedule.fill | v2.0 | fill.md |
| schedule.fill_context | v1.1 | fill_context.md |

## 승인 이력

| 기준선 | 날짜 | 커밋 | 슬롯 | 변경 내용 | 변경 이유 | 상태 |
| --- | --- | --- | --- | --- | --- | --- |
| legacy-git-f640ec1 | 2026-08-07 | `f640ec1` | `schedule.plan`, `schedule.plan_context` | 일정 편성 프롬프트와 후보·거리 컨텍스트를 실제 Runtime에 연결 | INT-07 SCHEDULE 최초 실동작 | 승인됨 |
| legacy-1.0.7 | 2026-08-08 | `c30bb68` | Router 의존성 | SCHEDULE 되묻기 진행 상태를 Intent 분류에 반영 | 일정 답변이 MODIFY로 오분류되는 문제 방지 | 승인됨 |
| legacy-1.0.11 | 2026-08-12 | `16e3a9d` | `schedule.fill`, `schedule.fill_context` | 특정 순번·이름만 바꾸는 부분 재편성 슬롯 신설 | 전체 일정을 다시 만드는 제한 해소 | 승인됨 |
| legacy-git-f4d0526 | 2026-08-12 | `f4d0526` | `schedule.plan` | 짧은 활동 시간에 3~5개 장소를 강제하지 않도록 보정 | 비현실적 일정 방지 | 승인됨 |
| legacy-git-d2e516d | 2026-08-18 | `d2e516d` | `schedule.plan` | 운영시간·폐점 스탑 경고와 긴 일정 과소 채움 규칙 보정 | 닫힌 장소·짧은 일정 문제 방지 | 승인됨 |
| 2026-08-26-co-visited | 2026-08-26 | (커밋 예정) | `schedule.plan`, `schedule.plan_context`, `schedule.fill`, `schedule.fill_context` | place_associations(D-088) 기반 "함께 방문된 이력" 섹션·활용 규칙 추가(opt-in, co_visited_fetcher 미주입 시 항상 "(없음)"). 부분 재편성(fill) 경로는 pinned_items+candidates 전체 place_id로 조회 | SCHEDULE에 실제 co-visit 데이터를 참고 신호로 반영(전체 편성·부분 재편성 둘 다) | 검토 중 |
| 2026-08-31-must-include | 2026-08-31 | (커밋 예정) | `schedule.plan`, `schedule.plan_context` | 보관함에 담은 장소를 반드시 포함하라는 규칙과 `[반드시 포함]` 섹션 추가(비어 있으면 "(없음)"으로 항상 같은 구조로 렌더링). 개수 상한 안에서 이 장소를 먼저 배치하고 남는 자리를 다른 후보로 채우도록 지시 | SCHEDULE-12 — 후보 풀에 넣는 것만으로는 채점 순위에서 밀려 빠진다. 프롬프트 지시는 부탁이고 `planner.plan_schedule()`의 하드 검증이 계약이다(SCHEDULE-07과 같은 철학) | 검토 중 |
| 2026-09-02-timeline-engine | 2026-09-02 | (커밋 예정) | `schedule.plan`, `schedule.fill` | 도착시각·이동시간·총 소요시간을 만들라는 지시를 전부 삭제하고, 후보에 없는 place_id를 만들지 말라는 규칙을 추가. estimated_duration_min은 시스템이 카테고리 정책으로 클램프하는 제안값임을 명시 | TP-215 — 시각을 LLM이 각각 따로 만들어 서로 맞는지 확인하는 곳이 없었다. 순서와 체류시간이 정해지면 나머지 시각은 `app.schedule.timeline`이 결정적으로 계산한다. 메이저 인상인 이유는 응답 스키마(`ScheduleLLMPlan`)에서 필드가 사라져 이전 버전과 호환되지 않기 때문이다 | 검토 중 |
| 2026-09-04-budget-derived-count | 2026-09-04 | (커밋 예정) | `schedule.plan` | 목표 개수를 예산 산수로 유도한 값으로 받아 쓰고, `duration_rule`에서 "상한 개수에 가깝게 채우고 체류시간도 넉넉히 잡아 시간을 다 쓰라"는 지시를 삭제. 대신 개수가 이미 예산에서 계산된 값이라는 근거와 "개수·체류시간을 늘려 잡지 말라"는 지시로 교체 | TP-239 — 그 지시는 2026-08-18(`d2e516d`)에 과소-채움을 막으려고 넣은 것인데, 그때는 개수 상한이 버킷 상수라 예산과 무관했다. 지금은 상한이 예산에서 나오고 총 소요시간은 `budget.fit_durations_to_budget()`이 체류시간을 조절해 맞추므로, 넉넉히 잡으라는 지시가 그 조절과 정면으로 싸운다 — LLM이 길게 잡고 엔진이 줄이면 남는 것은 장소별 상대 판단이 뭉개지는 것뿐이다. **`plan.md` 템플릿 파일은 한 글자도 바뀌지 않았다**(`{{duration_rule}}` 자리에 주입되는 문자열만 바뀜) — 그래서 `meta.yaml`의 `schedule.plan` 버전은 2.0.0 그대로 두었고 Langfuse 대조에도 안 걸린다 | 검토 중 |
