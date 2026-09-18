# saved_place_lists 데이터 딕셔너리

## 개요

`public.saved_place_lists`는 세션 1개의 **장소 보관함**을 담는 테이블입니다(SCHEDULE-12, D-110, 마이그레이션 `202608310001_create_saved_place_lists.sql`). Package B 소유이며 `session_id`가 PK입니다. `agent_states`·`recommendation_histories`와 마찬가지로 애플리케이션이 통째로 읽고 통째로 다시 쓰는 방식으로 갱신됩니다.

**`recommendation_histories`에 얹지 않고 별도 엔티티로 둔 이유가 둘입니다.**

1. 이력은 append-only인데 보관함은 담기·빼기가 되는 **가변 상태**입니다.
2. `clear_recommended()`(계약 5.5절 history reset)가 `recommended`와 `closed_excluded`를 비우는데, 보관함이 거기 얹혀 있으면 **"다른 곳 보여줘" 한 번에 사용자가 담아둔 것이 함께 날아갑니다.**

`recommended`/`rejected`와 결정적으로 다른 점은 "누가 골랐는가"입니다 — `recommended`는 시스템이 보여준 것이고 `rejected`는 사용자가 물린 것이지만, 이것은 사용자가 **능동적으로 고른 것**입니다. 그래서 다음 SCHEDULE 턴의 후보 복귀(D-107)와 배치 보장에서 세 목록과 다르게 취급됩니다 — 직전 노출분은 새 SCHEDULE 턴에만 되살리지만 **보관함은 재조정 턴에도 되살립니다**(D-114).

| 필드 | 타입 | NULL 허용 | 정의 | 값 예시 | 활용 예시 |
| --- | --- | --- | --- | --- | --- |
| `session_id` | text | 아니오 | 세션 식별자. PK이며 `agent_states.session_id`와 같은 값 체계입니다. 빈 문자열 금지 제약이 걸려 있습니다. | `sess_1755840000000a1b2c3d4e5f6a` | `agent_states`와 조인해 세션의 조건과 보관함을 함께 읽습니다. |
| `user_id` | text | 예 | `agent_states.user_id`와 같은 규칙(비어 있을 때만 채우고 덮어쓰지 않음, FK 없음). **`user_preferences`·`saved_schedules`와 달리 uuid가 아니라 text이고 `auth.users` FK가 없습니다** — 이 테이블은 세션 단위라 만료 정리가 행을 걷어가므로 고아가 오래 남지 않습니다. | `3fa85f64-5717-4562-b3fc-2c963f66afa6` | 정식 인증 이후 보관함을 계정 단위로 옮길 때 이 필드가 이관 기준이 됩니다. |
| `items` | jsonb array | 아니오(기본값 `[]`) | 담은 장소 목록. 하위 필드는 "items 항목 필드" 표 참고. **순서는 담은 순서이며(오래된 것이 앞) 의미를 갖습니다.** 배열 타입 제약이 걸려 있습니다. | `[{"place_id":"2824887","name":"경복궁","saved_from_run_id":"run_...","saved_at":"2026-08-31T10:00:00+09:00"}]` | 일정 편성에서 "담은 장소를 반드시 넣기"의 입력으로 씁니다. |
| `updated_at` | timestamptz | 아니오(기본값 `now()`) | 보관함이 마지막으로 바뀐 시각. | `2026-08-31T10:05:00+09:00` | 최근에 담았는지 확인합니다. |

### items 항목 필드

| 필드 | 타입 | 정의 |
| --- | --- | --- |
| `place_id` | string | 장소 식별자(`places.content_id`). |
| `name` | string | 장소 이름. **"B는 place_id만 저장한다" 원칙의 예외입니다** — `RecommendedItem.name`을 SCHEDULE-09 2단계에서 예외로 넣은 것과 같은 이유입니다. "경복궁"류 지명 검색이 호출마다 조금씩 다른 좌표로 resolve돼 이번 턴 후보 목록이 매번 달라지는 사례가 실사용에서 확인됐고(2026-08-11), 그러면 담아둔 `place_id`를 이번 턴 후보에서 다시 못 찾아 이름을 못 채웁니다. 보관함은 담고 나서 여러 턴 뒤에 쓰이는 것이 정상이라 이 재검색 실패 확률이 `recommended`보다 오히려 높습니다. |
| `saved_from_run_id` | string | 어느 실행에서 노출된 것을 담았는지. `RecommendedItem.run_id`와 대조해 "그때 본 그 장소"를 되짚습니다. |
| `saved_at` | datetime | 담은 시각. |
| `latitude` | float \| null | 위도 스냅샷(D-114에서 채우기 시작). 후보 간 거리 계산이 이번 턴 C 응답에서만 좌표를 찾기 때문에, 검색 반경 밖의 보관함 장소는 이 값이 없으면 거리 근거를 잃습니다 — 강남 장소가 종로 일정 2번째에 꽂혀도 막을 수 없습니다. C 응답에 좌표가 있으면 그쪽을 우선합니다. |
| `longitude` | float \| null | 경도 스냅샷. 위와 같습니다. |

## 사용 시 유의사항

- **`items` 순서를 바꾸지 마세요.** 일정 편성에서 보관함 개수가 항목 수 상한을 넘을 때 무엇을 남길지 이 순서로 정합니다(SCHEDULE-12 설계안 4절) — 점수 순으로 자르면 왜 그 곳이 빠졌는지 사용자에게 설명할 수 없습니다.
- 담기·빼기는 **멱등**입니다(`changed=False`). 이력의 중복 허용 정책과 달리 같은 장소로 항목이 늘지 않습니다 — 보관함은 누적 기록이 아니라 현재 상태라 같은 장소가 두 줄이면 버그입니다.
- **`saved ∩ rejected = ∅`이 구조적으로 보장됩니다.** `record_rejected()`가 같은 `place_id`를 보관함에서 함께 빼며, 이 처리는 `service.py`가 아니라 `history.py`에 있습니다 — 호출부가 두 번 부르는 것을 잊으면 불변식이 조용히 깨지기 때문입니다(D-114).
- 담을 수 있는 것은 그 세션 노출 이력에 있는 `place_id`뿐이지만 **마지막 run으로 좁히지 않고 누적 전체를 봅니다**(`find_recommended_item()`). 화면에 이전 턴 카드가 남아 있어 스크롤을 올려 3턴 전 카드를 담는 것이 정상 동작이고, 좁히면 그 경로가 400으로 막힙니다.
- 담기·빼기는 **인텐트 분류를 거치지 않습니다.** 버튼 클릭은 해석할 여지가 없고 `/api/chat`을 통하면 오분류·LLM 지연이 붙습니다(`clarification_choice`와 같은 이유).
- `session_id` 삭제 시(세션 삭제, 만료 세션 정리 D-074) 이 테이블도 함께 삭제 대상입니다.
- 클라이언트 직접 접근은 막혀 있습니다(RLS 켬 + 정책 없음, `anon`/`authenticated` 권한 회수).
