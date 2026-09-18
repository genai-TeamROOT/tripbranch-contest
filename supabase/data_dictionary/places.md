# places 데이터 딕셔너리

## 개요

`public.places`는 TourAPI 지역기반 목록(`areaBasedList2`)의 장소 기본정보와 상세 운영정보(`detailIntro2`)를 함께 캐시하는 테이블입니다. 추천 경로는 TourAPI를 직접 부르지 않고 이 테이블을 읽습니다. 원문(`*_raw`)과 정규화 결과(`operating_schedule`)를 모두 보존해, 파서를 고쳤을 때 API를 다시 부르지 않고 재처리할 수 있게 했습니다.

2026-08-25 기준 3,713행이며, 서울(`area_code = 11`) 12개 구의 장소가 들어 있습니다.

| 필드 | 타입 | NULL 허용 | 정의 | 값 예시 | 활용 예시 |
| --- | --- | --- | --- | --- | --- |
| `content_id` | text | 아니오 | TourAPI `contentid`. 이 저장소 전체에서 장소를 가리키는 기준 식별자이며 PK입니다. | `1013079` | `place_barrier_free`·`place_enrichments`를 조인하는 키로 씁니다. |
| `content_type_id` | text | 아니오 | TourAPI `contenttypeid`. 12 관광지, 14 문화시설, 15 축제공연행사, 25 여행코스, 28 레포츠, 32 숙박, 38 쇼핑, 39 음식점입니다. | `38` | 음식점만 골라 식사 후보를 만드는 등 1차 유형 필터에 씁니다. |
| `title` | text | 아니오 | 장소명입니다. 공백만 있는 값은 제약으로 막습니다. | `숭례문(남대문) 수입상가` | 사용자 화면 표기와 외부 API 검색어의 원본으로 씁니다. |
| `address` | text | 예 | TourAPI `addr1` 도로명 주소입니다. | `서울특별시 중구 남대문시장4길 21 (남창동)` | 길찾기 출발·도착지 문자열과 지역 확인에 씁니다. |
| `latitude` | double precision | 예 | 위도입니다. TourAPI 응답의 `mapy`에서 옵니다. −90~90 범위를 제약으로 강제합니다. | `37.5592411902` | 두 장소 사이 거리 계산과 동선 묶기에 씁니다. |
| `longitude` | double precision | 예 | 경도입니다. TourAPI 응답의 `mapx`에서 옵니다. −180~180 범위를 제약으로 강제합니다. | `126.9776685255` | 위도와 함께 좌표 기반 후보 필터링에 씁니다. |
| `area_code` | text | 아니오 | TourAPI 지역코드입니다. 서울이 `11`입니다. | `11` | 동기화 단위와 조회 범위를 나누는 기준입니다. |
| `district_code` | text | 아니오 | 시·군·구 코드입니다. 응답의 `lDongSignguCd`를 그대로 씁니다. 종로구 `110`, 중구 `140`, 용산구 `170`입니다. | `140` | 구 단위 동기화와 "종로구에서 갈 만한 곳" 같은 조회의 필터입니다. |
| `lcls_systm1` | text | 예 | TourAPI 분류체계 대분류입니다. 2026-08-25 기준 관측된 값은 `SH` 쇼핑(1,693건), `FD` 음식(805), `VE` 관람시설(519), `AC` 숙박(249), `HS` 역사(161), `EV` 행사(113), `EX` 체험(60), `NA` 자연(48), `LS` 레포츠(44), `C01` 여행코스(21)입니다. | `SH` | 유형별 후보 비율을 맞추거나 특정 대분류를 제외할 때 씁니다. |
| `lcls_systm2` | text | 예 | 분류체계 중분류입니다. 값이 있으면 `lcls_systm1`도 반드시 있어야 합니다. | `SH05` | 대분류보다 좁은 범위로 후보를 거를 때 씁니다. |
| `lcls_systm3` | text | 예 | 분류체계 소분류입니다. 값이 있으면 대·중분류가 모두 있어야 합니다. | `SH050200` | 활성 장소에 인덱스가 걸려 있어 세부 분류 조회의 주 조건으로 씁니다. |
| `operating_hours_raw` | text | 예 | `detailIntro2` 운영시간 원문입니다. 유형별 응답 키(`usetime`, `opentimefood` 등)를 모읍니다. | `06:30~18:30` | 파서가 읽는 입력이며, 파싱이 실패한 장소를 사람이 직접 확인할 때도 봅니다. |
| `rest_date_raw` | text | 예 | `detailIntro2` 휴무일 원문입니다. | `매주 일요일` | 휴무 규칙 파싱과 방문 가능 여부 판정에 씁니다. |
| `operating_schedule` | jsonb object | 예 | 운영시간·휴무일 원문을 파싱한 결과입니다. `rules`(요일·기간별 시간대), `closure_rules`(휴무 규칙), `availability`(`scheduled`·`all_day`·`unknown`), `warnings`, `assumption_reason`을 담습니다. | `{"availability":"scheduled","rules":[{"time_ranges":[{"start":"06:30","end":"18:30","crosses_midnight":false}]}],"closure_rules":[{"weekdays":[6],"source_text":"매주 일요일"}]}` | 특정 시각에 문을 여는지 판정하고, 일정표의 방문 시간대를 정하는 데 씁니다. |
| `operating_parse_status` | text | 아니오 | 파싱 결과의 신뢰도입니다. `parsed`·`partial`·`unknown`·`assumed` 중 하나이며 기본값은 `unknown`입니다. | `parsed` | `unknown`인 장소는 영업 여부를 단정하지 않고 안내 문구를 다르게 냅니다. |
| `operating_parser_version` | text | 아니오 | 이 행을 파싱한 파서 버전입니다. 컬럼 기본값은 `operating-hours-1.0.0`이지만 현재 대부분 `operating-hours-1.2.0`입니다. | `operating-hours-1.2.0` | 파서를 고친 뒤 재파싱이 필요한 행을 골라낼 때 씁니다. |
| `source_modified_at` | timestamptz | 예 | TourAPI가 알려준 원본 수정 시각(`modifiedtime`)입니다. | `2025-05-29T10:48:34+09:00` | 원본이 바뀐 장소만 상세조회를 다시 하도록 갱신 대상을 좁힙니다. |
| `list_fetched_at` | timestamptz | 아니오 | 이 장소를 목록 조회로 마지막에 받아온 시각입니다. | `2026-08-22T00:34:56+09:00` | 이미지·좌표 등 목록 응답에서 오는 값의 최신성 기준입니다. |
| `detail_fetched_at` | timestamptz | 예 | `detailIntro2` 상세조회를 마지막으로 성공한 시각입니다. | `2026-08-20T16:21:43+09:00` | 상세조회 TTL을 넘긴 장소를 재조회 대상으로 뽑습니다. |
| `last_seen_at` | timestamptz | 아니오 | 목록 응답에서 이 장소를 마지막으로 본 시각입니다. `list_fetched_at`과 같은 값이 들어갑니다. | `2026-08-22T00:34:56+09:00` | 이번 실행에서 보이지 않은 장소를 비활성으로 돌리는 판정 기준입니다. |
| `detail_fetch_status` | text | 아니오 | 상세조회 상태입니다. `pending`·`success`·`empty`·`failed` 중 하나이며 기본값은 `pending`입니다. `empty`는 조회는 됐지만 운영시간·휴무일이 둘 다 없었다는 뜻입니다. | `success` | 아직 상세를 못 받은 장소를 다음 실행의 조회 대상으로 뽑습니다. |
| `detail_error_code` | text | 예 | 상세조회 실패 원인 코드입니다. `failed`일 때만 값이 있고 그 외 상태에서는 반드시 `NULL`이어야 한다는 제약이 걸려 있습니다. | `TOUR_DETAIL_QUOTA_EXCEEDED` | 일일 한도 초과와 응답 오류를 구분해 재시도 여부를 정합니다. |
| `is_active` | boolean | 아니오 | 추천 후보로 쓸 수 있는 장소인지 여부입니다. 기본값은 `true`입니다. | `true` | 조회 인덱스가 모두 `where is_active` 부분 인덱스라, 후보 조회는 항상 이 조건을 답니다. |
| `inactive_reason` | text | 예 | 비활성 사유입니다. `missing_from_source`·`closed`·`moved_outside_area`·`invalid_data`·`manual_exclusion` 중 하나입니다. | `missing_from_source` | 원본에서 사라진 것과 폐업 확인을 구분해 되살릴지 판단합니다. |
| `inactive_at` | timestamptz | 예 | 비활성으로 바꾼 시각입니다. | `2026-07-29T19:28:28+09:00` | 언제부터 후보에서 빠졌는지 추적합니다. |
| `last_sync_run_id` | uuid | 예 | 이 행을 마지막으로 건드린 동기화 실행의 ID입니다. `place_sync_runs.id`를 참조하며, 실행 이력을 지워도 장소는 남도록 `on delete set null`입니다. | `01ccdf04-2675-4b27-9c37-14b7206314f4` | 특정 실행이 무엇을 바꿨는지 되짚을 때 씁니다. |
| `created_at` | timestamptz | 아니오 | 이 장소 행이 처음 저장된 시각입니다. | `2026-08-20T16:21:46+09:00` | 신규 유입 시점을 확인합니다. |
| `updated_at` | timestamptz | 아니오 | 마지막으로 수정된 시각입니다. `places_set_updated_at` 트리거가 관리합니다. | `2026-08-22T00:34:57+09:00` | 최근에 값이 바뀐 장소를 추립니다. |
| `parking_info_raw` | text | 예 | `detailIntro2` 주차 안내 원문입니다. 유형별 필드(`parking`, `parkingculture` 등)를 모읍니다. 축제(15)는 해당 필드가 없어 항상 `NULL`입니다. | `가능` | 차량 방문 가능 여부 안내에 씁니다. |
| `parking_fee_raw` | text | 예 | `detailIntro2` 주차 요금 원문(`parkingfee`·`parkingfeeleports`)입니다. 이용요금과 구분합니다. | `- 30분 900원<br>\n- 60분 1,800원` | 주차 비용을 안내하고 유·무료를 구분합니다. |
| `use_fee_raw` | text | 예 | `detailIntro2` 이용요금 원문(`usefee`·`usefeeleports`·`usetimefestival`)입니다. 축제는 필드명이 `usetimefestival`이지만 내용은 요금입니다. | `※ 전시마다 상이하므로 전화문의 요망` | 예산 조건에 맞는 장소를 고르고 입장료를 안내합니다. |
| `discount_info_raw` | text | 예 | `detailIntro2` 할인정보 원문(`discountinfo`·`discountinfofestival`·`discountinfofood`)입니다. | `50% 감면 : 종로구에 주소를 둔 관람객 / 한복착용자` | 할인 대상 안내에 씁니다. |
| `first_image_url` | text | 예 | 목록 응답 `firstimage`의 대표 이미지 URL입니다. `list_fetched_at` 주기로 갱신됩니다. | `http://tong.visitkorea.or.kr/cms/resource/29/3083029_image2_1.JPG` | 추천 카드의 대표 이미지로 씁니다. |
| `thumbnail_url` | text | 예 | 목록 응답 `firstimage2`의 썸네일 URL입니다. `list_fetched_at` 주기로 갱신됩니다. | `http://tong.visitkorea.or.kr/cms/resource/29/3083029_image3_1.JPG` | 목록 화면의 작은 이미지로 씁니다. |
| `info_center_raw` | text | 예 | `detailIntro2` 안내처 원문입니다. 유형별 필드(`infocenter`, `infocenterculture` 등)를 모읍니다. 전화번호 외에 기관명이 섞일 수 있고, 축제(15)는 `sponsor1tel`을 쓰므로 항상 `NULL`입니다. | `02-753-2805` | 전화 문의처 안내에 씁니다. |
| `baby_carriage_raw` | text | 예 | `detailIntro2` 유모차 대여 원문(`chkbabycarriage` 계열)입니다. 숙박·축제는 항상 `NULL`입니다. | `없음` | 아이 동반 방문 조건 안내에 씁니다. |
| `pet_raw` | text | 예 | `detailIntro2` 반려동물 동반 원문(`chkpet` 계열)입니다. 3,713행 중 9건에만 값이 있습니다. | `불가` | 반려동물 동반 가능 장소를 고를 때 씁니다. |
| `credit_card_raw` | text | 예 | `detailIntro2` 신용카드 가능 원문(`chkcreditcard` 계열)입니다. 쇼핑(38)에 값이 몰려 있습니다. | `없음` | 결제 수단 안내에 씁니다. |
| `restroom_raw` | text | 예 | `detailIntro2` 화장실 설명 원문(`restroom`)입니다. 유형 구분 없이 한 키이며 쇼핑(38)에 값이 몰려 있습니다. | `있음` | 편의시설 안내에 씁니다. 장애인 화장실은 `place_barrier_free.accessible_restroom_raw`가 따로 담습니다. |

## 사용 시 유의사항

- 좌표의 출처가 헷갈리기 쉽습니다. TourAPI는 `mapx`가 경도, `mapy`가 위도입니다. `latitude`에 `mapx`를 넣는 실수가 실제로 있었습니다.
- `district_code`는 좌표가 아니라 응답의 `lDongSignguCd`를 그대로 믿습니다. 둘이 어긋나는 장소가 실재하기 때문입니다 — 서울역 부속 시설 72건은 용산구로 등록돼 있지만 좌표는 중구 안에 있습니다(2026-08-24 실측).
- `detail_fetch_status`는 운영정보(운영시간·휴무일) 확보 여부만 뜻합니다(D-056). 주차·요금·안내처가 채워져도 `empty`가 `success`로 바뀌지 않습니다. 이 판정을 바꾸면 재조회 주기가 함께 달라집니다.
- `first_image_url`·`thumbnail_url`은 목록 응답에서 오므로 상세조회가 실패해도 갱신됩니다. 이미지 최신성의 기준은 `detail_fetched_at`이 아니라 `list_fetched_at`입니다.
- 테이블에 있는 장소가 모두 추천 후보가 되는 것은 아닙니다. 여행코스(25)와 숙박(32)은 후보 매핑 단계(`app/providers/mappers.py`의 `_UNSUPPORTED_CONTENT_TYPE_IDS`)에서 제외됩니다. 2026-08-25 기준 숙박 245건, 여행코스 21건이 저장돼 있지만 추천에는 쓰이지 않습니다.
- `is_active = false`는 대부분 "이번 목록 응답에 없었다"(`missing_from_source`)는 뜻이지 폐업 확정이 아닙니다. 2026-08-25 기준 비활성 42건이 전부 이 사유이며, 다음 실행에서 다시 보이면 자동으로 되살아납니다.
- `operating_parser_version`이 한 종류가 아닙니다. 2026-08-25 기준 3,532행이 `operating-hours-1.2.0`, 181행이 `operating-hours-1.0.0`입니다. 파싱 결과를 비교할 때는 버전을 함께 봐야 합니다.
- `detailIntro2`에서 오는 원문 컬럼은 유형에 따라 응답 키 자체가 없습니다. `NULL`은 "그렇지 않다"가 아니라 "그 유형에는 해당 필드가 없거나 값이 비어 있다"는 뜻입니다.
- RLS가 켜져 있고 정책을 만들지 않았으므로 `anon`·`authenticated` 키로는 한 행도 보이지 않습니다. 서버(FastAPI)의 `service_role` 권한으로만 읽고 씁니다.
