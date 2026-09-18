# place_sync_runs 데이터 딕셔너리

## 개요

`public.place_sync_runs`는 한 시·군·구의 장소 동기화(목록 조회 + 상세조회) 한 번을 한 행으로 기록하는 실행 이력 테이블입니다. 장소별 최신 상태는 `places`가 갖고 있으므로, 이 테이블은 "이번 실행이 어디까지 처리했고 무엇이 실패했는가"를 판단하는 데 씁니다. 배치가 중간에 끊겨 일부만 반영된 상황을 장소 데이터만 보고는 알 수 없기 때문입니다.

2026-08-25 기준 38행이며 `success` 17건, `partial_failure` 19건, `failed` 2건입니다.

| 필드 | 타입 | NULL 허용 | 정의 | 값 예시 | 활용 예시 |
| --- | --- | --- | --- | --- | --- |
| `id` | uuid | 아니오 | 실행 식별자입니다. PK이며 `gen_random_uuid()`로 생성합니다. | `2152d7f6-5bde-4051-a44f-ddee9161a2bd` | `places.last_sync_run_id`·`place_sync_locks.sync_run_id`가 이 값을 참조합니다. |
| `area_code` | text | 아니오 | 동기화한 지역코드입니다. 서울이 `11`입니다. | `11` | 지역별 실행 이력을 나눠 봅니다. |
| `district_code` | text | 아니오 | 동기화한 시·군·구 코드입니다. | `380` | 구 단위로 마지막 동기화가 언제였는지 확인합니다. |
| `started_at` | timestamptz | 아니오 | 실행을 시작한 시각입니다. 기본값은 `now()`이며 내림차순 인덱스가 걸려 있습니다. | `2026-08-25T16:44:54+09:00` | 최근 실행부터 조회하는 운영 화면의 정렬 기준입니다. |
| `completed_at` | timestamptz | 예 | 실행이 끝난 시각입니다. `running` 상태에서는 반드시 비어 있고, 종료 상태에서는 반드시 값이 있으며 `started_at` 이후여야 합니다. | `2026-08-25T16:44:55+09:00` | 실행에 걸린 시간을 재고, 끝나지 않은 실행을 찾아냅니다. |
| `status` | text | 아니오 | 실행 결과입니다. `running`·`success`·`partial_failure`·`failed` 중 하나이며 기본값은 `running`입니다. `success`인 행은 `failed_count`가 0이어야 한다는 제약이 함께 걸려 있습니다. | `partial_failure` | 부분 실패를 성공과 구분해 재실행 대상을 정합니다. |
| `api_total_count` | integer | 예 | TourAPI 목록 응답이 알려준 전체 건수입니다. | `146` | 응답이 말한 전체 수와 실제 처리 수를 비교해 누락을 찾습니다. |
| `processed_count` | integer | 아니오 | 이번 실행이 실제로 처리한 장소 수입니다. 기본값 0입니다. | `146` | `api_total_count`와 비교해 중간에 끊겼는지 봅니다. |
| `success_count` | integer | 아니오 | 처리에 성공한 장소 수입니다. 기본값 0입니다. | `145` | 성공률을 계산합니다. |
| `failed_count` | integer | 아니오 | 처리에 실패한 장소 수입니다. 기본값 0입니다. | `1` | 실패가 있으면 `status`가 `success`가 될 수 없습니다. |
| `new_count` | integer | 아니오 | 이번 실행에서 새로 추가된 장소 수입니다. 기본값 0입니다. | `146` | 구를 처음 동기화했는지, 증분인지 구분합니다. |
| `updated_count` | integer | 아니오 | 기존 장소 중 값이 갱신된 수입니다. 기본값 0입니다. | `0` | 원본 변경이 얼마나 반영됐는지 확인합니다. |
| `deactivated_count` | integer | 아니오 | 이번 목록에서 보이지 않아 비활성으로 바뀐 장소 수입니다. 기본값 0입니다. | `0` | 원본에서 사라진 장소가 한 번에 많이 생기면 원본 이상을 의심합니다. |
| `error_summary` | jsonb object | 예 | 오류 코드별 발생 횟수입니다. JSON 객체여야 한다는 제약이 걸려 있습니다. | `{"TOUR_DETAIL_QUOTA_EXCEEDED":1,"BARRIER_FREE_QUOTA_EXCEEDED":1}` | 어떤 오류로 부분 실패했는지 코드 단위로 확인합니다. |
| `created_at` | timestamptz | 아니오 | 이 행이 만들어진 시각입니다. 기본값 `now()`이며 실무적으로 `started_at`과 같습니다. | `2026-08-25T16:44:54+09:00` | 이력 보존 기간을 정할 때 씁니다. |
| `detail_attempted_count` | integer | 예 | 이 실행이 `detailIntro2`를 부른 장소 수입니다. `NULL`이면 "0회 불렀다"가 아니라 "재지 않은 실행"(열 추가 이전이거나 중단된 실행)입니다. | `172` | TourAPI 오퍼레이션별 일일 한도(1,000회) 대비 오늘 사용량을 집계합니다. |

## 사용 시 유의사항

- `detail_attempted_count`의 `NULL`과 0은 뜻이 다릅니다. 2026-08-25 기준 38행 중 19행이 `NULL`이며, 이 값들을 0으로 취급해 합계를 내면 실제보다 정확한 수치로 오해하게 됩니다. 화면은 값이 비어 있는 실행 수를 함께 보여줍니다.
- `detail_attempted_count`는 하한입니다. 재시도(`external_api_retry_count`)는 한 장소를 여러 번 부를 수 있는데, 여기서 세는 것은 호출 횟수가 아니라 장소 수입니다.
- 호출량을 이 테이블에 남기는 이유는 프로세스 메모리 집계(`app/observability/api_usage.py`)가 서버를 재시작하면 0이 되고, `backend/scripts`로 돌린 실행분은 다른 프로세스라 아예 잡히지 않기 때문입니다. 실행당 행이 하나씩 이미 남으므로 호출마다 카운터를 올리는 것보다 DB 쓰기가 훨씬 적습니다.
- `status = 'running'`인데 `started_at`이 한참 지난 행은 중단된 실행입니다. 같은 구의 잠금이 만료돼 새 실행에 넘어가면, 잠금 획득 함수가 이전 실행을 `failed`로 바꾸고 `error_summary`에 `STALE_SYNC_LOCK_REPLACED`를 남깁니다.
- `error_summary`는 오류 코드별 횟수만 담습니다. 실패한 개별 장소는 `places.detail_error_code`에서 확인합니다.
- RLS가 켜져 있고 정책이 없으므로 서버 권한으로만 접근합니다.
