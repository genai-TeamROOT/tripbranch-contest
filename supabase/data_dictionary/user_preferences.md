# user_preferences 데이터 딕셔너리

## 개요

`public.user_preferences`는 **계정 단위 취향 설정**을 담는 테이블입니다(D-062 Phase 5 후속, TP-222, 마이그레이션 `202609030001_create_user_preferences.sql`). Package B 소유이며 `user_id`가 PK입니다 — 한 사용자에 한 행입니다.

취향 설정 화면에서 고른 값은 그동안 `localStorage`에만 있어 기기를 벗어나지 못했습니다. 이메일 회원가입이 들어오면서 계정에 붙일 수 있게 됐습니다.

**이 저장소가 처음으로 `session_id`가 아니라 `user_id`로 키를 잡았습니다.** `agent_states` · `recommendation_histories` · `saved_place_lists`는 전부 세션 단위이고 세션 TTL과 함께 소멸하지만, 취향은 세션을 넘어 사람에게 붙는 값입니다 — 세션에 얹으면 대화를 새로 시작할 때마다 취향을 다시 골라야 합니다. 그래서 모델의 `user_id`도 `str | None`이 아니라 필수이고, 라우트는 이 프로젝트에서 **`RequiredPrincipal`을 쓰는 첫 라우트**입니다(신원이 곧 저장 키라 토큰이 없으면 어디에 저장할지가 정해지지 않습니다).

| 필드 | 타입 | NULL 허용 | 정의 | 값 예시 | 활용 예시 |
| --- | --- | --- | --- | --- | --- |
| `user_id` | uuid | 아니오 | 소유자. PK. **`auth.users(id)`에 FK가 걸려 있고 `on delete cascade`입니다** — 아래 "FK를 여기서만 건 이유" 참고. | `3fa85f64-5717-4562-b3fc-2c963f66afa6` | 홈 화면이 저장해 둔 취향을 다시 보여줍니다. |
| `items` | jsonb array | 아니오(기본값 `[]`) | `UserPreference` 배열. 하위 필드는 "items 항목 필드" 표 참고. **순서는 사용자가 고른 순서이고 화면이 그대로 보여줍니다.** 배열 타입 제약이 걸려 있습니다. | `[{"label":"조용한 곳","source":"preference","codes":["quiet"]}]` | 추천 요청에 실을 조건으로 씁니다. |
| `updated_at` | timestamptz | 아니오(기본값 `now()`) | 취향이 마지막으로 바뀐 시각. | `2026-09-03T11:00:00+09:00` | 언제 고쳤는지 표시합니다. |

### items 항목 필드

프론트 `state/preferenceStorage.ts`의 `SavedPreference`와 같은 모양입니다. **백엔드는 이 값을 해석하지 않고 그대로 보관합니다** — 칩과 DB 코드의 대응은 화면이 갖고 있고(`pages/preferenceOptions.ts`), 여기서 다시 검증하면 칩 목록을 고칠 때마다 두 곳이 갈립니다.

| 필드 | 타입 | 정의 |
| --- | --- | --- |
| `label` | string | 화면에 보이는 이름. |
| `source` | string | `preference` \| `place_tag` \| `custom` 셋 중 하나. B는 검증하지 않습니다. |
| `codes` | list[string] | 대응하는 DB 코드. **`custom`은 사용자가 직접 넣은 키워드라 대응 코드가 없어 빈 배열입니다.** |

## FK를 여기서만 건 이유

`saved_place_lists.user_id`는 text이고 FK가 없습니다. 여기서는 다르게 갑니다.

세션 단위 테이블은 만료 세션 정리(`cleanup_expired_sessions.py`)가 행을 걷어가므로 고아가 오래 남지 않습니다. 취향은 세션 수명에 묶이지 않아 **아무것도 이 행을 치우지 않습니다** — 계정이 지워져도 남습니다. 그래서 `auth.users`에 직접 걸어 계정과 함께 사라지게 했습니다. 익명 계정 정리(`cleanup_anonymous_users.py`)도 별도 수정 없이 함께 정리됩니다.

이것은 `agent_states.user_id`에 FK를 걸지 않은 D-063 결정 4와 모순이 아닙니다 — 그쪽은 익명 사용자 정리와 세션 정리가 서로를 막지 않게 하려는 것이고, 이쪽은 치워주는 주체가 없어서 계정에 매다는 것입니다.

## 사용 시 유의사항

- **만료 세션 정리 대상이 아닙니다.** 대화를 지워도 남습니다.
- 클라이언트 직접 접근은 막혀 있습니다(RLS 켬 + 정책 없음, `anon`/`authenticated` 권한 회수). **프론트가 Supabase에 직접 붙는 방식도 검토했으나 택하지 않았습니다** — 이 프로젝트 최초의 RLS 정책이 생기고 데이터 경로가 둘로 갈립니다. 취향은 결국 추천 요청에 실을 값이라 백엔드가 읽어야 하는데, 그때 백엔드가 또 다른 길로 같은 값을 읽게 됩니다.
- **빈 목록도 정상적인 저장입니다**(전부 해제한 경우). 값이 없다고 미저장으로 취급하지 마세요.
- 프론트 전역 모듈 캐시(`preferenceSync`)가 테스트 사이로 샙니다 — 새 전역 캐시를 만들면 `resetXxxCache()`도 같이 만들고, 그 캐시를 쓰는 테스트 파일의 `beforeEach`에 넣으세요. 단 전역 `beforeEach`에 넣으면 자기 `fetch`를 세우는 파일에서 동기화가 실제로 돌면서 `/preferences`를 때려 다른 테스트가 터집니다.
