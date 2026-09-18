# Supabase 마이그레이션 관리

## 현재 적용 상태

- 대상 프로젝트: TripBranch (`llpcmlzumqkafpsvmyrb`)
- 최초 마이그레이션:
  `202607240001_create_place_tables.sql`
- 후속 마이그레이션:
  `202607240002_add_place_sync_locks.sql`
- 집중률 매핑 마이그레이션:
  `20260729104209_create_place_concentration_mappings.sql`
- 집중률 검색어 컬럼 마이그레이션:
  `202608040001_add_concentration_search_key.sql`
  (원격 이력에는 `20260804055402_add_concentration_search_key`로 기록됨 — MCP가
  적용 시각으로 버전을 붙여 파일명과 다르다)
- 주차·요금·이미지 컬럼 마이그레이션:
  `202608080001_add_place_parking_fee_image_columns.sql`
  (원격 이력에는 `20260808050321_add_place_parking_fee_image_columns`로 기록됨)
  (D-056. `places`에 `parking_info_raw`, `parking_fee_raw`, `use_fee_raw`,
  `discount_info_raw`, `first_image_url`, `thumbnail_url` 추가. 값 적재는
  provider·place_sync 배선 후)
- 집중률 검색어 목록 마이그레이션:
  `202608080002_add_concentration_search_keys.sql`
  (원격 이력에는 `20260808064030_add_concentration_search_keys`로 기록됨)
  (D-057. `concentration_search_keys text[]` 추가·backfill 후 단수 컬럼
  `concentration_search_key`를 삭제한다. 두 컬럼을 병행하면 진실의 원천이 둘이 되므로
  한 마이그레이션에서 끝낸다. 체크 제약에는 서브쿼리를 쓸 수 없어 공백 검사를
  `array_to_string(keys, ',') !~ '\s'`로 우회한다)
- 안내처 컬럼 마이그레이션:
  `202608100001_add_place_info_center_column.sql`
  (원격 이력에는 `20260810060044_add_place_info_center_column`으로 기록됨)
  (D-060. `places`에 `info_center_raw` 추가. 전화번호의 출처는 `detailCommon2`의
  `tel`이 아니라 `detailIntro2`의 안내처 계열이다 — `tel`은 축제(15)에만 채워진다)
- 편의시설 컬럼 마이그레이션:
  `202608100002_add_place_facility_columns.sql`
  (원격 이력에는 `20260810060605_add_place_facility_columns`로 기록됨)
  (D-060. `places`에 `baby_carriage_raw`, `pet_raw`, `credit_card_raw`,
  `restroom_raw` 추가. jsonb 하나로 합치지 않는다 — 소비 측이 키 이름을 알아야 하고
  "키가 없다"와 "정보가 없다"가 구분되지 않는다)
- 취향 근거 벡터 테이블 마이그레이션:
  `202608180001_create_place_embeddings.sql`
  (원격 이력에는 `20260818120611_create_place_embeddings`로 기록됨)
  (package_D §2.9·§7.12. `place_embeddings` 생성 —
  `vector(768)`, `unique(content_id, source_ref)`, `content_id` FK →
  `places`, HNSW + `content_id` 인덱스. §7.10에서 되돌린 이전 시도의 원격
  이력(`20260812080614` 등)이 남아 있으나 실제 객체는 그때 삭제됐고 이번이
  재생성이다)
- 근거 검색 RPC 마이그레이션:
  `202608180002_create_search_place_evidence.sql`
  (원격 이력에는 `20260818120625_create_search_place_evidence`로 기록됨)
  (package_D §2.10. RPC `search_place_evidence` 생성 — 후보 `content_id`로
  범위를 좁히고, 같은 글/리뷰는 1건만 남기고, 장소별 top-N 평균으로
  정렬한다. `min_similarity` 기본값 0.0은 적재 후 재실측 전까지의 임시값)
- 근거 검색 RPC 타임아웃 완화 마이그레이션:
  `202608180003_increase_search_place_evidence_timeout.sql`
  (원격 이력에는 `20260818123826_increase_search_place_evidence_timeout`로
  기록됨)
  (유사도 분포 실측 중 발견. 후보 844곳 전체를 넘기면 40,389행 코사인 거리
  계산에 7.5~9.2초가 걸리는데, PostgREST 연결 롤 `authenticator`의
  `statement_timeout=8s`에 걸려 500 에러가 났다. `search_place_evidence`
  함수에만 `set statement_timeout = '30s'`를 붙였다 — 이 함수는
  anon/authenticated 호출이 막혀 있어 전역 타임아웃을 안 건드려도 된다)
- 근거 검색 RPC 후보 상한 마이그레이션:
  `202608180004_add_search_place_evidence_candidate_limit.sql`
  (원격 이력에는 `20260818140058_add_search_place_evidence_candidate_limit`로
  기록됨)
  (외부 호출은 막혀 있지만(anon/authenticated 권한 없음), 내부 호출 코드가
  후보를 안 좁히면 40,389행 전체 스캔으로 커넥션을 오래 붙잡을 수 있다.
  실측(200~400건 100~300ms, 844건 6~9초)을 근거로 500건 초과 시 즉시
  에러를 내도록 가드를 추가했다. 절차형 분기가 필요해 `language sql`에서
  `plpgsql`로 바꿨다)
- 피드백 KST 조회 뷰 마이그레이션:
  `202608210002_add_response_feedback_kst_view.sql`
  (저장은 그대로 UTC(`timestamptz`) — Postgres/Supabase 테이블 에디터가 UTC로
  보여줘서 KST로 보기 불편한 문제만 조회용 뷰(`response_feedback_kst`)로
  해결한다. 아직 미적용 — Dashboard SQL Editor에서 실행 필요)
- 피드백 comment 컬럼 마이그레이션:
  `202608210003_add_comment_to_response_feedback.sql`
  (`response_feedback`에 `comment text null`(500자 제한) 추가 — "싫어요" 클릭 시
  선택적으로 남기는 짧은 사유. `response_feedback_kst` 뷰도 함께 갱신. 아직
  미적용 — Dashboard SQL Editor에서 실행 필요)
- 동기화 실행별 상세조회 수 마이그레이션:
  `202608210007_add_sync_run_detail_attempted_count.sql`
  (**Dashboard SQL Editor로 직접 적용해 원격 마이그레이션 이력
  `supabase_migrations.schema_migrations`에는 기록되지 않았다.** 스키마는
  적용됐다 — `place_sync_runs.detail_attempted_count`와 체크 제약을 조회로
  확인했다)
  (`place_sync_runs`에 `detail_attempted_count integer null` 추가. TourAPI
  일일 한도를 얼마나 썼는지 세는 근거다 — 호출량 집계는 프로세스 메모리라
  재시작하면 0이 되고 `backend/scripts` 실행분도 놓친다. `detailIntro2`를 부르는
  곳이 `PlaceSyncService` 하나뿐이고 그 경로가 실행마다 이 테이블에 행을 남기므로,
  호출마다 카운터를 올리지 않고 열 하나로 센다. nullable인 이유는 기존 행과 중간에
  죽은 실행이 "0회 불렀다"가 아니라 "재지 않았다"이기 때문이다. 재시도는 세지 않아
  이 값도 하한이다)
- 공식 출처 보강 컬럼 마이그레이션:
  `202608240001_add_official_facts_to_place_enrichments.sql`
  (원격 이력에는 `20260824234532_add_official_facts_to_place_enrichments`로 기록됨)
  (`place_enrichments`에 `official_facts jsonb` 추가. **현재 이 컬럼을 읽는 코드는
  없다** — 무장애 정보를 여기 담으려던 접근이 `place_barrier_free` 전용 테이블로
  나뉘면서(D-077) 보류됐다. 컬럼과 116행의 값은 전용 테이블이 담지 못하는 정보가
  있어 남겨둔다. **원격에는 적용돼 있었으나 저장소에 파일이 없어 2026-08-25에
  되살렸다** — 저장소만 보고 DB를 세우면 컬럼이 빠진다)
- 무장애 정보 테이블 마이그레이션:
  `202608250002_create_place_barrier_free.sql`
  (원격 이력에는 `20260825042434_create_place_barrier_free`로 기록됨. 이후
  `20260825050116_drop_place_barrier_free_listed_flag`로 `listed_in_barrier_free`
  칸을 지웠는데, 저장소 파일은 그 결과까지 반영한 최종 형태라 별도 파일이 없다)
  (D-077. 같은 날 `202608250001`을 `create_place_google_profiles`가 먼저 써서
  번호가 겹쳤고, 2026-08-25에 이 파일을 `0002`로 옮겼다)
- 실행 Trace 지표 컬럼 마이그레이션:
  `202609040001_add_metrics_to_trace_records.sql`
  (`trace_records`에 `metrics jsonb null` 추가 — 일정 편성 품질 지표(TP-242)를 담는
  자리다. 컬럼을 하나씩 늘리지 않고 jsonb 하나로 둔 이유는 Trace 계약이 "호출자가
  해석한 값을 그대로 저장한다"를 원칙으로 세워 뒀기 때문이다. 기존 행과 지표를
  싣지 않는 단계는 null로 남는다. **적용 확인됨(2026-09-07) — 적용 경로는 미확인이다.**
  원격에 컬럼이 존재하고 `metrics`가 실린 행도 쌓이고 있다. 아래 한 줄로 확인한다.
  `select count(*) from information_schema.columns where table_schema='public' and
  table_name='trace_records' and column_name='metrics';` -> 1.
  이력에 남지 않았다면 Dashboard SQL Editor로 적용된 것이다(팀 관례는 CLI `db push`
  또는 MCP `apply_migration`이고 그쪽만 원격 이력에 기록된다). `add column if not
  exists`라 재실행은 안전하지만, **미적용으로 보고 다시 돌릴 필요는 없다**)

- 장소 보관함 테이블 마이그레이션:
  `202608310001_create_saved_place_lists.sql`
  (아직 미적용 — Dashboard SQL Editor에서 실행 필요)
  (D-110 / SCHEDULE-12. 사용자가 추천 카드에서 명시적으로 담은 장소를 세션 단위로
  보관한다. `recommendation_histories`에 컬럼을 더하지 않고 별도 테이블로 둔 이유가
  이 결정의 핵심이다 — history reset(`clear_recommended()`)이 `recommended`와
  `closed_excluded`를 비우므로, 보관함이 그 테이블에 얹혀 있으면 "다른 곳 보여줘"
  한 번에 사용자가 담아둔 장소가 함께 날아간다. `STATE_STORE_BACKEND=supabase`
  환경에서는 이 테이블이 없으면 담기 요청이 502로 실패한다 — `save_saved_places()`가
  `SavedPlaceList` 전체를 통째로 upsert하기 때문이다(202608130001·202608200001과
  같은 이유). `memory` 백엔드는 영향이 없다)
- 실제 DB 적용일: 2026-07-24, 2026-07-29, 2026-08-04, 2026-08-08, 2026-08-10,
  2026-08-18, 2026-08-21
- 적용 방법: Supabase Dashboard SQL Editor 및 Supabase MCP `apply_migration`
- 적용 결과: `places`, `place_enrichments`, `place_sync_runs`,
  `place_sync_locks`, `place_concentration_mappings`, `place_embeddings` 및
  잠금·근거 검색 RPC 생성 완료

## 값 적재가 끝나지 않은 컬럼 (2026-08-11 기준)

`202608100001`·`202608100002`로 추가한 컬럼은 **스키마만 있고 값이 일부 비어 있다.**
컬럼이 비어 있다고 해서 그 장소에 정보가 없는 것이 아니다.

| 컬럼 | 채움 | 비고 |
| --- | --- | --- |
| `info_center_raw` | 632 / 844 | 전화번호 |
| `credit_card_raw` | 181 / 844 | 쇼핑(38)에 집중 |
| `restroom_raw` | 112 / 844 | 쇼핑(38)에 집중 |
| `baby_carriage_raw` | 97 / 844 | |
| `pet_raw` | 2 / 844 | 원본에도 거의 없다 |

활성 844건 중 **142건이 `detail_fetch_status='failed'`** 상태다. 2026-08-10 적재 중
TourAPI `detailIntro2`의 일일 요청 한도를 소진해 끝내지 못했다. 한도 리셋 후 아래를
실행하면 그 142건만 대상이 된다.

```bash
cd backend
PLACE_SYNC_DETAIL_CONCURRENCY=1 PLACE_SYNC_DETAIL_MIN_INTERVAL_SECONDS=0.15 \
  python -m scripts.sync_places --from-snapshot ../supabase/data/places_api_snapshot_11-110_20260810.csv
```

`--force-details`는 쓰지 않는다 — 이미 채운 702건까지 다시 부른다. 겪은 한도 문제와
실행 이력은 `supabase/data/backups/20260810_before_detail_refill/README.md`에 있다.

## Supabase CLI 이력 관련 주의

SQL Editor로 실행했기 때문에 실제 스키마는 생성됐지만 Supabase CLI의 원격
마이그레이션 이력에는 `202607240001`, `202607240002`가 아직 기록되지 않았다.
`20260729104209`와 `20260804055402`는 Supabase MCP로 적용해 원격 마이그레이션 이력에
기록됐다.

2026-08-21 기준으로 이 폴더의 파일 24개 중 이력 표
(`supabase_migrations.schema_migrations`)에 이름이 있는 것은 10개뿐이다. SQL
Editor로 적용하면 표에 줄이 생기지 않기 때문이다. **지금 진실의 기준은 이 폴더이지
그 표가 아니다.**

빠진 14개는 초기 4개(`202607240001`, `202607240002`, `202607280001`,
`202608030001`)와 `202608130001`, 그리고 08-20 이후 추가한 9개
(`202608200001`~`202608210007`) 전부다. 반대로 표에는 있는데 파일이 없는 이름도
하나 있다(`secure_place_embeddings` — 되돌린 임베딩 시도의 잔재다).

한 건만 손으로 채워 넣지 않았다. 표에는 도구가 붙인 14자리 시각
(`20260818140058`)이 들어 있는데 파일명은 12자리 일련번호라 형식이 섞이고,
`statements` 열이 빈 줄이 생겨 "적용됐다는 기록은 있는데 무엇을 실행했는지는 모르는"
상태가 된다. 표를 다시 기준으로 삼으려면 빠진 14건 전부와 번호 형식을 함께 정리해야 한다.

## Supabase CLI 최초 도입 시 필수 작업

프로젝트를 CLI에 연결한 뒤, 최초 마이그레이션을 다시 실행하지 말고 이미 적용된
상태로 기록한다.

```bash
supabase migration repair 202607240001 --status applied
supabase migration repair 202607240002 --status applied
```

그다음 원격과 로컬 마이그레이션 이력이 일치하는지 확인한다.

```bash
supabase migration list
```

두 버전이 로컬과 원격에서 모두 적용된 상태로 표시되고,
`20260729104209`도 양쪽에 존재하는지 확인한 뒤에만 `supabase db push`를 사용한다.

## 주의사항

- 대화 제목 마이그레이션:
  `202609030002_add_agent_states_title.sql`
  (TP-222 후속. 사이드바 채팅 히스토리가 목록에 쓸 제목이다. `recent_turns`에서
  파생하지 않는 이유는 그 배열이 MAX_RECENT_TURNS(=5)개만 남아, 대화를 이어갈수록
  첫 질문이 밀려나 제목이 저절로 바뀌기 때문이다 — 신원이 붙은 대화 105개 중
  22개(21%)가 이미 그 상태였다. 채우는 규칙은 `user_id`와 같다: 비어 있으면
  채우고 값이 있으면 덮어쓰지 않는다)

- 계정 단위 취향 마이그레이션:
  `202609030001_create_user_preferences.sql`
  (TP-222 후속. `user_preferences` 신설 — **이 프로젝트에서 세션이 아니라 계정을
  키로 잡는 첫 테이블이다.** 취향은 세션 TTL과 함께 사라지면 안 되는 값이다.
  `saved_place_lists`와 달리 `user_id`에 `auth.users(id)` FK를 걸고
  `on delete cascade`를 준다 — 세션 단위 테이블은 만료 세션 정리가 행을
  걷어가지만 이 테이블은 아무것도 치우지 않아 계정이 지워져도 남기 때문이다.
  RLS는 켜고 정책은 만들지 않는다: 프론트가 직접 붙지 않고 FastAPI만 접근한다)

- 계정 단위 즐겨찾기 마이그레이션:
  `202609030006_create_user_favorites.sql`
  (위치 설정 화면, PR #361 후속. `user_favorites` 신설 — `user_preferences`와 같은
  모양이다. 세션이 아니라 사람에게 붙는 값이라 `user_id`를 PK로 `auth.users(id)`에
  `on delete cascade`로 건다. 항목마다 행을 두지 않고 `items jsonb` 배열 하나로
  두는 이유도 같다 — 화면이 순서·이름 변경을 목록 단위로 다룬다. RLS는 켜고
  정책은 만들지 않는다: 프론트가 직접 붙지 않고 FastAPI만 접근한다)

- `202607240001_create_place_tables.sql`을 현재 프로젝트에 다시 실행하지 않는다.
  테이블과 관련 객체가 이미 존재하므로 중복 생성 오류가 발생한다.
- `202607240002_add_place_sync_locks.sql`도 현재 프로젝트에 다시 실행하지 않는다.
- `20260729104209_create_place_concentration_mappings.sql`도 적용 완료 상태이므로
  다시 실행하지 않는다.
- `202608080001_add_place_parking_fee_image_columns.sql`도 적용 완료 상태다.
  `add column if not exists`라 재실행해도 오류는 나지 않지만 다시 실행하지 않는다.
- `202608100001`, `202608100002`도 적용 완료 상태다. 위와 같은 이유로 재실행하지
  않는다.
- `202608180001`, `202608180002`, `202608180003`, `202608180004`도 적용 완료
  상태다. 위와 같은 이유로 재실행하지 않는다.
- `202609030001_create_user_preferences.sql`도 적용 완료 상태다(2026-09-03, MCP
  `apply_migration`, 원격 이력에는 `create_user_preferences`로 기록).
- `202609030006_create_user_favorites.sql`도 적용 완료 상태다(2026-09-03, MCP
  `apply_migration`, 원격 이력에는 `create_user_favorites`로 기록).
- `202609030002_add_agent_states_title.sql`도 적용 완료 상태다(2026-09-03).
  `agent_states.title` 추가 + 기존 283건 backfill + `(user_id, last_active_at desc)`
  부분 인덱스. **backfill 값은 근사치다** — 남아 있는 가장 오래된 턴이라
  MAX_RECENT_TURNS(=5)를 채운 대화에서는 실제 첫 질문이 아니다.
- `202609030005_create_saved_schedules.sql`도 적용 완료 상태다(2026-09-03,
  **Supabase Dashboard SQL Editor**). 저장한 일정 테이블 + `(user_id, created_at desc)`
  인덱스 + `(user_id, run_id) where run_id is not null` 부분 유니크 인덱스.
  **CLI/MCP가 아니라 SQL Editor로 적용해 원격 마이그레이션 이력에는 남지 않는다** —
  다음 사람이 `db push`를 돌리면 미적용으로 보고 다시 실행하려 할 수 있는데,
  `create table`이라 재실행하면 오류가 난다. 그때는 실행하지 말고 이 줄을 근거로
  건너뛴다.
- 기존 마이그레이션 파일은 적용 후 수정하지 않는다.
- 이후 스키마 변경은 새 타임스탬프를 가진 마이그레이션 파일로 추가한다.
- 새 마이그레이션은 가능한 한 Supabase CLI `db push` 또는 MCP
  `apply_migration`으로 적용해 원격 이력과 함께 관리한다.
