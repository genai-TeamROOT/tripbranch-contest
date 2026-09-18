# saved_schedules 데이터 딕셔너리

## 개요

`public.saved_schedules`는 사용자가 **"이 일정을 쓰겠다"고 고른 일정**을 계정 단위로 보관하는 테이블입니다(SCHEDULE 카드 2, 마이그레이션 `202609030005_create_saved_schedules.sql`). Package B 소유이며 `id`(uuid, 서버 생성)가 PK이고 한 사용자가 여러 행을 갖습니다.

**화면 기록(`session_messages`)과 겸하지 않습니다.** 저쪽은 "그때 화면에 나갔던 것"이고 현재 상태로 다시 읽는 소비자를 두지 않는다는 전제 위에 있습니다. 이것은 사용자가 이름을 붙이고 나중에 열고 고칠 수 있어야 하는 것이라, 스냅샷을 편집 대상으로 겸하게 하면 그 전제가 깨집니다. 보관함(`saved_place_lists`)을 추천 이력과 분리한 것과 같은 판단입니다.

**`user_preferences`·`user_favorites`와 같은 계정 단위 엔티티입니다.** `user_id`가 필수이고 세션 TTL과 무관하게 남으며, 라우트도 `RequiredPrincipal`을 씁니다.

| 필드 | 타입 | NULL 허용 | 정의 | 값 예시 | 활용 예시 |
| --- | --- | --- | --- | --- | --- |
| `id` | uuid | 아니오(기본값 `gen_random_uuid()`) | PK. **서버(DB 기본값)가 만듭니다** — 저장 요청을 만들 때는 아직 없습니다(모델의 `id`가 `str \| None`인 이유). | `9b1f...` | 일정 하나를 열거나 고칠 때 씁니다. |
| `user_id` | uuid | 아니오 | 소유자. **`auth.users(id)`에 FK가 걸려 있고 `on delete cascade`입니다** — 계정이 지워지면 함께 사라집니다. | `3fa85f64-5717-4562-b3fc-2c963f66afa6` | 내 저장 일정 목록을 뽑습니다(`user_id, created_at desc` 인덱스). |
| `session_id` | text | 예 | 출처 표시. 이 일정이 나온 대화입니다. | `sess_1755840000000a1b2c3d4e5f6a` | 원본 대화로 이어가려는 화면에서 씁니다 — 아래 유의사항 참고. |
| `run_id` | text | 예 | 출처 표시. 이 일정을 만든 실행입니다. **`(user_id, run_id)`에 부분 유니크 인덱스가 걸려 있어**(run_id가 null이 아닐 때만) 같은 실행 결과를 두 번 저장하면 멱등하게 처리됩니다. | `run_1755840000000abcdef` | 같은 일정을 중복 저장하지 않게 막습니다. |
| `title` | text | 아니오 | 목록에 보여줄 이름. 빈 문자열 금지 제약이 걸려 있습니다. **`payload` 안의 `route_summary`는 LLM이 쓴 문장이고 이것은 사용자의 것입니다.** | `홍대 반나절 코스` | 저장 일정 목록에 표시합니다. |
| `payload` | jsonb | 아니오 | `ScheduleResult`를 직렬화한 그대로. **B는 열어보지 않습니다**(`session_messages.payload`와 같은 취급 — `app.schemas`에 의존하지 않습니다). 객체 타입 제약이 걸려 있습니다. | `{"items":[...],"total_duration_min":210,"route_summary":"..."}` | `/schedule` 화면이 일정을 다시 그리고 편집합니다. |
| `created_at` | timestamptz | 아니오(기본값 `now()`) | 저장한 시각. | `2026-09-03T14:30:00+09:00` | 목록 정렬 기준입니다(최신 순). |
| `updated_at` | timestamptz | 아니오(기본값 `now()`) | 마지막으로 고친 시각. | `2026-09-04T09:10:00+09:00` | 언제 수정했는지 표시합니다. |

## 사용 시 유의사항

- **`session_id`/`run_id`는 출처 표시일 뿐입니다.** 세션은 30일 뒤 정리되지만(D-074) 이 행은 남으므로, 이 값으로 원본 대화를 열려는 화면은 **없을 수 있다**를 전제로 다뤄야 합니다.
- **만료 세션 정리 대상이 아닙니다.** `cleanup_expired_sessions.py`의 `_delete_one()`이 지우는 여섯 테이블에 들어 있지 않습니다 — 사람에 딸린 값이라 대화를 지워도 남습니다. 정리는 계정 삭제 시 FK cascade로 일어납니다.
- **`ScheduleItem`을 그룹 구조로 바꾸지 않습니다.** 이미 저장된 스냅샷이 옛 모양이기 때문입니다 — TP-243의 근접 묶기는 `cluster_id` 번호만 얹어 이 결정을 지켰습니다.
- 계정 단위 라우트 테스트에는 `store.clear()` 픽스처를 두세요. `get_store()`가 프로세스 전역 InMemory를 돌려주므로 앞 테스트의 행을 봅니다 — 실제로 "저장한 일정이 그대로 돌아온다"가 앞 테스트의 멱등 경로에 걸려 `session_id`를 잃은 채 통과할 뻔했습니다.
- 클라이언트 직접 접근은 막혀 있습니다(RLS 켬 + 정책 없음, `anon`/`authenticated` 권한 회수).
- **이 마이그레이션은 Dashboard SQL Editor로 적용됐습니다.** 팀 관례(CLI `db push` 또는 MCP `apply_migration`)와 달라 원격 마이그레이션 이력에 남아 있지 않습니다 — `supabase/README.md`를 함께 보세요.
