# place_associations 데이터 딕셔너리

## 개요

`public.place_associations`는 한국관광공사 TourAPI `TarRlteTarService1`(관광지별 연관 관광지 정보)에서 수집한 실제 동선(Tmap 내비게이션 co-visitation) 기반 연관 관광지 엣지를 저장합니다. 원본 응답은 `tAtsCd`/`rlteTatsCd`(32자리 해시코드)로 장소를 가리켜 TourAPI 표준 `content_id`와 다르므로, `build_place_association_mappings.py`로 이름+구 매칭한 결과(`place_concentration_mappings`와 같은 문제를 같은 방식으로 푼 전례, D-043/D-057)만 골라 `import_place_associations.py`가 이 테이블에 적재합니다.

수집 범위는 서비스 지원 12개 구(D-083, 종로구·중구·용산구·성동구·광진구·동대문구·중랑구·성북구·강북구·도봉구·노원구·은평구)입니다. `areaBasedList1`을 이 12개 구로 호출했기 때문에 `from_content_id`(기준 관광지)는 항상 이 열두 구에 속하고, `to_content_id`(연관 관광지)는 그 구들의 관광지가 실제로 함께 방문된 다른 구(지원 지역 밖 포함)의 장소도 섞여 있는 단방향 데이터입니다. 서울 전역 25개 구 확장과 SCHEDULE/RECOMMEND 파이프라인 연동은 별도 작업입니다.

2026-08-26 기준 1,612행이며(원본 5,511건 중 양쪽 다 `content_id`로 매칭된 것만 적재, 자기참조 13건·중복 83건 제외), `category`는 `관광지` 1,013건·`음식` 438건·`숙박` 161건입니다. `from_content_id`는 168개 장소(12개 구 기준 관광지 중 자신도 매칭에 성공한 장소만)입니다.

| 필드 | 타입 | NULL 허용 | 정의 | 값 예시 | 활용 예시 |
| --- | --- | --- | --- | --- | --- |
| `from_content_id` | text | 아니오 | 기준 관광지의 `places.content_id`입니다. FK이며 복합 PK의 일부입니다. | `945824` | "이 장소와 함께 다니면 좋은 곳"을 조회할 때 기준값으로 씁니다. |
| `to_content_id` | text | 아니오 | 연관 관광지의 `places.content_id`입니다. FK이며 복합 PK의 일부입니다. `from_content_id`와 같을 수 없습니다(자기참조 제약). | `130473` | 조회 결과로 사용자에게 보여줄 장소입니다. |
| `category` | text | 아니오 | 연관 관광지의 대분류입니다. `전체`·`관광지`·`음식`·`숙박` 중 하나로 제약돼 있습니다(현재 적재된 값은 `관광지`·`음식`·`숙박`뿐입니다). | `관광지` | RECOMMEND에서 식당/숙소만 따로 걸러 보여줄 때 씁니다. |
| `rank` | smallint | 아니오 | 기준 관광지 기준 연관도 순위(1~50)입니다. 값이 작을수록 연관도가 높습니다. | `2` | 연관 장소를 순위순으로 정렬해 상위 몇 개만 보여줍니다. |
| `base_ym` | text | 아니오 | 원본 데이터의 기준 연월(`YYYYMM`)입니다. 복합 PK의 일부라 같은 두 장소라도 월이 다르면 별도 행으로 이력이 쌓입니다. | `202607` | 데이터가 최신 몇 월 기준인지 확인합니다. |
| `created_at` | timestamptz | 아니오 | 이 행이 처음 저장된 시각입니다. 같은 `base_ym`을 재수집해 upsert해도 이 값은 바뀌지 않습니다(적재 스크립트가 upsert 페이로드에 `created_at`을 아예 안 보냅니다). | `2026-08-26T21:10:00+09:00` | 최초 수집 시점을 추적합니다. |

## 사용 시 유의사항

- 데이터가 단방향입니다. `from_content_id → to_content_id`만 있고 역방향(예: 백범김구기념관 → 경교장)은 원본에 없는 한 자동으로 생기지 않습니다. 양방향 조회가 필요하면 호출부에서 `to_content_id`로도 조회해야 합니다(`to_content_id`에도 인덱스가 있습니다).
- `from_content_id`는 항상 지원 12개 구(D-083)입니다. 그 밖의 구 장소를 기준으로 조회하면 결과가 없는 게 정상이며, 매칭 실패나 버그가 아닙니다(서울 25개 구 전역 확장 전까지).
- 재수집 정책은 "같은 `base_ym`은 덮어쓰기, 다른 `base_ym`은 이력 보존"입니다. `import_place_associations.py`가 `on_conflict=from_content_id,to_content_id,base_ym` + `resolution=merge-duplicates`로 upsert하므로, 매달 재수집해도 과거 `base_ym` 행은 남습니다. 12개 구 확장(2026-08-26)도 같은 `base_ym`(202607)으로 재수집·재적재해 종로구·중구 기존 행을 upsert로 덮어쓰고 나머지 10개 구를 새로 추가했습니다.
- 원본 JSONL의 엣지 중 상당수(5,511건 중 3,803건)가 매핑 실패로 제외됐습니다. 대부분 요식업·숙박업 프랜차이즈처럼 `places`에 아직 없는 카테고리라서 그렇습니다(`build_place_association_mappings.py`의 unmatched/out_of_coverage 목록 참고). 이 테이블에 없다고 해서 실제로 연관이 없는 건 아니라, 매칭 커버리지의 한계입니다.
- RLS가 켜져 있고 `service_role`에만 권한을 줬으므로 서버 권한으로만 접근합니다. 조회 헬퍼는 `backend/scripts/query_place_associations.py`에 있습니다.
