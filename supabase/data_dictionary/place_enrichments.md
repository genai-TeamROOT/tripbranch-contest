# place_enrichments 데이터 딕셔너리

## 개요

`public.place_enrichments`는 TourAPI가 제공하지 않는 TripBranch 자체 정보를 `places`와 분리해 담는 테이블입니다. `content_id`로 1:1 연결하며, 외부 데이터를 다시 동기화해도 팀이 채운 값이 덮이지 않게 하는 경계 역할을 합니다. 장소가 실제로 삭제되면 이 행도 함께 지워집니다(`on delete cascade`).

2026-08-25 기준 116행입니다. 현재 채워져 있는 것은 `place_type`·`source_type`과 `official_facts`이고, 태그 배열 세 개와 `estimated_visit_minutes`·`reservation_required`는 아직 값이 들어간 행이 없습니다.

| 필드 | 타입 | NULL 허용 | 정의 | 값 예시 | 활용 예시 |
| --- | --- | --- | --- | --- | --- |
| `content_id` | text | 아니오 | `places.content_id`와 같은 장소 식별자입니다. PK이자 FK입니다. | `2979653` | `places`와 조인해 장소 기본정보를 가져옵니다. |
| `place_type` | text | 아니오 | TripBranch가 정한 장소 유형입니다. `attraction`·`cultural_facility`·`festival`·`leisure`·`shopping`·`restaurant` 중 하나로 제약돼 있습니다. | `shopping` | TourAPI `contenttypeid`와 별개로 추천 로직이 쓰는 유형 구분이며, 인덱스가 걸려 있습니다. |
| `place_tags` | text[] | 아니오 | 장소의 세부 분류 태그 목록입니다. 기본값은 빈 배열이고 원소에 `NULL`이 들어갈 수 없습니다. GIN 인덱스가 걸려 있습니다. | `{}` | 배열 포함 검색으로 특정 성격의 장소를 골라냅니다. |
| `estimated_visit_minutes` | integer | 예 | 예상 체류 시간(분)입니다. 값이 있으면 0보다 커야 합니다. | `(현재 채워진 행 없음)` | 하루 일정에 몇 곳을 넣을지 계산하는 근거로 쓸 값입니다. |
| `recommendation_tags` | text[] | 아니오 | 추천 상황을 나타내는 태그 목록입니다. 기본값은 빈 배열이며 GIN 인덱스가 걸려 있습니다. | `{}` | "비 오는 날", "혼자" 같은 조건과 장소를 잇는 데 쓸 값입니다. |
| `weather_tags` | text[] | 아니오 | 날씨 조건 태그 목록입니다. 기본값은 빈 배열입니다. | `{}` | 실내·실외 여부에 따라 후보를 거르는 데 쓸 값입니다. |
| `reservation_required` | boolean | 예 | 예약이 필요한 장소인지 여부입니다. | `(현재 채워진 행 없음)` | 당일 방문 가능한 곳만 추천할 때 거르는 조건입니다. |
| `source_type` | text | 아니오 | 이 보강값의 출처 종류입니다. `manual_research`·`external_data`·`derived` 중 하나로 제약돼 있습니다. | `manual_research` | 사람이 조사한 값과 자동 유도한 값을 구분해 신뢰도를 다르게 씁니다. |
| `verified_at` | timestamptz | 예 | 값을 마지막으로 확인한 시각입니다. 116행 중 93행에 값이 있습니다. | `2026-08-24T23:59:12+09:00` | 오래된 조사값을 다시 확인할 대상으로 뽑습니다. |
| `created_at` | timestamptz | 아니오 | 이 행이 처음 저장된 시각입니다. | `2026-08-24T23:59:12+09:00` | 보강 작업 시점을 확인합니다. |
| `updated_at` | timestamptz | 아니오 | 마지막으로 수정된 시각입니다. `place_enrichments_set_updated_at` 트리거가 관리합니다. | `2026-08-24T23:59:12+09:00` | 최근에 손댄 행을 추립니다. |
| `official_facts` | jsonb object | 아니오 | TourAPI 누락을 공식 출처로 보강한 필드별 객체입니다. 기본값은 빈 객체이고 JSON 객체여야 한다는 제약(`place_enrichments_official_facts_is_object`)이 걸려 있습니다. 각 값은 `value`·`status`·`merge_policy`·`verified_at`·`sources`를 보존합니다. `status`는 `verified`·`needs_review`·`not_applicable`, `merge_policy`는 `fallback_if_places_missing`·`do_not_merge`가 관측됩니다. | `{"operating_hours_raw":{"value":"화~일 10:30~20:00","status":"verified","merge_policy":"fallback_if_places_missing","sources":[{"url":"https://napcheongyugi.com/26","publisher":"납청유기","checked_at":"2026-08-24T23:59:12+09:00"}]}}` | `places`의 해당 필드가 비어 있을 때 대신 쓰고, 출처 URL로 근거를 제시합니다. |

## 사용 시 유의사항

- `official_facts`의 최상위 키는 원칙적으로 `places`의 필드명과 같지만, 실제 데이터에는 `places`에 없는 이름도 섞여 있습니다. 2026-08-25 기준 관측된 14개 키 중 `visitor_access_raw`·`wheelchair_access_raw`는 어느 테이블에도 대응 컬럼이 없고, `accessible_parking_raw`·`accessible_restroom_raw`·`nursing_room_raw`·`wheelchair_rental_raw`는 `places`가 아니라 `place_barrier_free`의 컬럼명입니다. 키 이름만 보고 `places`에 그대로 병합하면 안 됩니다.
- `merge_policy`를 반드시 확인하고 병합합니다. `fallback_if_places_missing`은 `places` 쪽이 비어 있을 때만 쓰라는 뜻이고, `do_not_merge`는 참고용으로만 두라는 뜻입니다.
- 태그 배열 세 개는 스키마상 `not null`이지만 현재 전부 빈 배열입니다. "태그가 없다"와 "아직 채우지 않았다"가 지금은 구분되지 않으므로, 이 값으로 필터를 걸면 후보가 전부 걸러집니다.
- 이 테이블에 행이 있다는 것은 누군가 그 장소를 조사했다는 뜻입니다. 행이 없는 장소가 대부분이므로(전체 3,713곳 중 116곳), 조인은 반드시 외부 조인으로 합니다.
- `official_facts`를 읽는 코드는 현재 없습니다. 무장애 정보를 이 컬럼에 담으려던 접근이 전용 테이블 `place_barrier_free`로 나뉘면서(D-077) 보류됐고, 컬럼과 116행의 값만 남아 있습니다.
- 이름이 같은 `build_official_facts()`(`app/synthetic_reviews/review_generator.py`)는 이 컬럼과 무관합니다. 그 함수는 `places`의 원문 필드를 읽어 합성 리뷰 대조용 사실 묶음을 만듭니다. 이름만 보고 같은 데이터로 취급하면 안 됩니다.
- RLS가 켜져 있고 정책이 없으므로 서버 권한으로만 접근합니다.
