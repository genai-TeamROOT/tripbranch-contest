# user_favorites 데이터 딕셔너리

## 개요

`public.user_favorites`는 **계정 단위 즐겨찾기 장소**를 담는 테이블입니다(위치 설정 화면, 마이그레이션 `202609030006_create_user_favorites.sql`). Package B 소유이며 `user_id`가 PK입니다 — 한 사용자에 한 행입니다.

**`user_preferences`와 같은 모양입니다** — 세션이 아니라 사람에게 붙는 값이라 키가 `user_id`이고, `auth.users(id)`에 FK가 `on delete cascade`로 걸려 있으며, `items`의 순서는 담은 순서이고 화면이 그대로 보여줍니다. FK를 건 이유도 같습니다(`user_preferences.md`의 "FK를 여기서만 건 이유" 참고 — 세션 정리가 이 행을 치워주지 않으므로 계정에 매답니다). 라우트는 `RequiredPrincipal`을 씁니다.

| 필드 | 타입 | NULL 허용 | 정의 | 값 예시 | 활용 예시 |
| --- | --- | --- | --- | --- | --- |
| `user_id` | uuid | 아니오 | 소유자. PK. `auth.users(id)` FK, `on delete cascade`. | `3fa85f64-5717-4562-b3fc-2c963f66afa6` | 기기를 넘어 즐겨찾기를 유지합니다(`7e3dc63`). |
| `items` | jsonb array | 아니오(기본값 `[]`) | `UserFavorite` 배열. 하위 필드는 "items 항목 필드" 표 참고. 순서는 담은 순서입니다. 배열 타입 제약이 걸려 있습니다. | `[{"id":"fav_1","label":"회사","search_center_name":"역삼역","address":"서울 강남구 ..."}]` | 위치 설정 화면에서 출발지를 고르게 합니다. |
| `updated_at` | timestamptz | 아니오(기본값 `now()`) | 즐겨찾기가 마지막으로 바뀐 시각. | `2026-09-03T12:00:00+09:00` | 언제 고쳤는지 표시합니다. |

### items 항목 필드

프론트 `state/sidebarStorage.ts`의 `FavoritePlace`와 같은 모양입니다. **백엔드는 이 값을 해석하지 않고 그대로 보관합니다**(`UserPreference`와 같은 판단).

| 필드 | 타입 | 정의 |
| --- | --- | --- |
| `id` | string | 항목 식별자. 프론트가 만듭니다. |
| `label` | string | 사용자가 붙인 이름. 바꿀 수 있습니다. |
| `search_center_name` | string \| null | 담을 때의 실제 장소 이름. **`label`과 나눠 둔 이유는 사용자가 이름을 바꾸기 때문입니다** — "역삼역"을 담아 "회사"로 고쳐도 검색에는 담을 때의 장소 이름이 나가야 합니다. 사이드바에서 자유 입력으로 만든 옛 항목에는 이 값이 없어 `label`로 떨어집니다. |
| `address` | string \| null | 주소. |

## 사용 시 유의사항

- **만료 세션 정리 대상이 아닙니다.** 대화를 지워도 남습니다. 정리는 계정 삭제 시 FK cascade로 일어납니다.
- **빈 목록도 정상적인 저장입니다**(전부 지운 경우).
- 즐겨찾기로 출발지를 정해 두면 **홈에서 채팅을 시작할 때 GPS를 아예 부르지 않습니다**(`4d4e85b`). 위치 관련 재현 절차를 쓸 때 이 분기를 먼저 확인하세요.
- 클라이언트 직접 접근은 막혀 있습니다(RLS 켬 + 정책 없음, `anon`/`authenticated` 권한 회수).
