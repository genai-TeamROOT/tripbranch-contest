# TourAPI 장소 개요 수집·전처리 가이드

## 1. 목적

Supabase `places`의 활성 장소를 기준으로 한국관광공사 TourAPI의 장소 개요
`overview`를 수집하고, 지역구별 RAG 입력 파일
`tour_overviews_preprocessed.csv`로 만드는 과정이다.

개요는 기존 `places` 테이블에 저장되어 있지 않으므로 TourAPI
`detailCommon2`를 장소별로 한 번씩 호출한다.

## 2. 사용하는 코드

| 단계 | 파일 | 역할 |
| --- | --- | --- |
| 수집 | `Review_RAG_Embedding/scripts/collect_tourapi_overviews_all_districts.py` | Supabase에서 수집 대상 장소 조회 후 TourAPI 호출 |
| 원문 정제 | `Review_RAG_Embedding/scripts/prepare_tourapi_overviews.py` | HTML·공백 등을 정리하고 `overview_clean` 생성 |
| 지역구 RAG 입력 생성 | `TripBranch_RAG_Distributed_Embedding/scripts/preprocess_tour_overviews.py` | `place_catalog.csv`와 매칭해 지역구 `input` 폴더에 저장 |

## 3. 필요한 환경변수

기본적으로 `tripbranch/backend/.env`에서 다음 값만 읽는다.

```dotenv
SUPABASE_URL=...
SUPABASE_SECRET_KEY=...
TOUR_API_SERVICE_KEY=...
```

- `SUPABASE_URL`, `SUPABASE_SECRET_KEY`: 활성 장소 목록 조회에 사용
- `TOUR_API_SERVICE_KEY`: TourAPI `detailCommon2` 호출에 사용
- 키 값은 로그나 Git에 기록하지 않는다.

## 4. 수집 대상 선정

수집 스크립트는 Supabase `public.places`에서 다음 조건을 만족하는 장소를 읽는다.

- `area_code = '11'`: 서울
- 지정한 `district_code`
- `is_active = true`
- 유효한 `content_id` 보유

다음 쇼핑 분류는 TourAPI를 호출하기 전에 제외한다.

```text
SH03, SH030100, SH04, SH040100, SH040200, SH040300
```

따라서 제외 대상 쇼핑 장소에는 API 호출 비용과 일일 할당량을 사용하지 않는다.

## 5. 단일 지역구 수집

예시로 서초구(`650`)만 수집한다.

```bash
cd /각자경로/Review_RAG_Embedding

python scripts/collect_tourapi_overviews_all_districts.py \
  --districts 650 \
  --raw data/raw/tourapi_overviews_seocho.jsonl \
  --out data/source/tourapi_overviews_seocho.csv
```

처음에는 5건만 호출해 연결과 키를 확인하는 것이 안전하다.

```bash
python scripts/collect_tourapi_overviews_all_districts.py \
  --districts 650 \
  --raw data/raw/tourapi_overviews_seocho.jsonl \
  --out data/source/tourapi_overviews_seocho.csv \
  --limit 5
```

정상이라면 `--limit`을 제거하고 같은 명령을 다시 실행한다. 앞서 완료한 장소는
자동으로 건너뛰므로 5건이 중복 호출되지 않는다.

## 6. 수집 결과

원본 JSONL은 장소별 최신 수집 이력을 누적해서 보존한다.

```text
data/raw/tourapi_overviews_<구>.jsonl
```

조회용 CSV에는 다음 주요 필드가 들어간다.

| 필드 | 설명 |
| --- | --- |
| `content_id` | TourAPI 및 DB 장소 식별자 |
| `title` | 장소명 |
| `district_code` | 지역구 코드 |
| `overview_raw` | TourAPI 개요 원문 |
| `homepage_raw` | TourAPI 홈페이지 원문 |
| `telephone_raw` | TourAPI 전화번호 원문 |
| `status` | `success`, `no_data`, `error`, `pending` |
| `error` | 실패 원인 |
| `fetched_at` | 수집 시각(UTC) |

상태 의미는 다음과 같다.

- `success`: `overview`가 정상 수집됨
- `no_data`: API 응답은 정상이지만 `overview`가 없음
- `error`: HTTP·네트워크·할당량 등의 오류
- `pending`: 아직 호출하지 않은 장소

## 7. 중단 후 이어서 수집

수집 스크립트는 기존 원본 JSONL을 먼저 읽는다.

- `success`, `no_data`: 재호출하지 않음
- `error`: 기본적으로 재호출하지 않음
- 수집되지 않은 장소: 이어서 호출

기존 오류 건도 다시 호출하려면 `--retry-errors`를 추가한다.

```bash
python scripts/collect_tourapi_overviews_all_districts.py \
  --districts 650 \
  --raw data/raw/tourapi_overviews_seocho.jsonl \
  --out data/source/tourapi_overviews_seocho.csv \
  --retry-errors
```

일일 한도가 소진되면 실행을 중단하고, 한도가 회복된 다음 같은 `--raw` 경로로
재실행한다. 원본 파일을 바꾸면 완료 이력을 찾지 못해 같은 장소를 다시 호출할 수
있으므로 이어받기 작업에서는 경로를 유지한다.

## 8. 원문 전처리

수집된 JSONL을 정제 CSV로 변환한다.

```bash
python scripts/prepare_tourapi_overviews.py \
  --raw data/raw/tourapi_overviews_seocho.jsonl \
  --source-out data/source/tourapi_overviews_seocho_collected.csv \
  --clean-out data/processed/tourapi_overviews_seocho_cleaned.csv \
  --report-out reports/tourapi_overviews_seocho_report.json
```

이 단계에서는 다음을 수행한다.

- `status=success`의 최신 행 선택
- `overview_raw`의 HTML 및 불필요한 공백 정리
- `overview_clean` 생성
- `source_type=tour_overview` 지정
- `source_ref=tourapi-overview:<content_id>` 생성
- 정제 본문 `content_hash` 생성
- 전처리 버전 기록
- 빈 본문과 중복 본문 통계 보고서 생성

원문을 요약하거나 문장을 새로 작성하지 않고 TourAPI 문장을 정제해서 사용한다.

## 9. 지역구별 RAG 입력 파일 생성

정제 파일을 분산 임베딩 패키지의 해당 지역구 `input` 폴더에 넣는다.

```bash
cd /각자경로/TripBranch_RAG_Distributed_Embedding

python scripts/preprocess_tour_overviews.py \
  --input ../Review_RAG_Embedding/data/processed/tourapi_overviews_seocho_cleaned.csv \
  --district 650_seocho
```

필요하면 카탈로그를 직접 지정할 수 있다.

```bash
python scripts/preprocess_tour_overviews.py \
  --input /경로/tourapi_overviews_seocho_cleaned.csv \
  --district 650_seocho \
  --catalog districts/650_seocho/input/place_catalog.csv
```

생성 파일:

```text
districts/650_seocho/input/tour_overviews_preprocessed.csv
```

이 단계에서는 해당 지역구의 `place_catalog.csv`에 있는 `content_id`만 남긴다.
카탈로그는 이미 비활성 장소와 제외 쇼핑 카테고리를 반영한 기준 목록이다.

## 10. Colab 임베딩에서의 처리

`tour_overviews_preprocessed.csv`는 선택 입력이다.

- 파일이 있으면 `tour_overview` 출처로 함께 임베딩
- 파일이 없으면 네이버 블로그와 Google 리뷰만 임베딩
- 과거 manifest 체크섬과 비교하지 않음
- 실행 시 현재 파일의 존재 여부와 문서 수를 출력

현재 v8 기준 TourAPI 개요는 120토큰 이하이면 한 청크로 유지하고, 초과하면
문단·문장 구조를 지키며 최대 120토큰으로 분할한다.

## 11. 호출량과 주의사항

- 장소 한 곳당 `detailCommon2` 한 번을 호출한다.
- 수집 대상이 500곳이면 최대 약 500회가 필요하다.
- 현재 확인된 일일 호출 제한을 넘지 않도록 지역구별 대상 건수를 먼저 확인한다.
- `--limit 5`로 연결 테스트 후 전체 수집을 권장한다.
- 오류 재시도는 기본 2회이고, 재시도 사이에는 짧은 대기 시간을 둔다.
- `success`와 `no_data`는 재실행 시 자동으로 건너뛴다.
- 개요가 없는 지역구도 임베딩 작업 자체는 진행할 수 있다.
- 수집 중 생성되는 원본 JSONL을 삭제하면 이어받기와 오류 분석이 어려워진다.

## 12. 팀 전달 시 필요한 파일

수집 담당자가 전달할 최소 파일은 다음과 같다.

```text
collect_tourapi_overviews_all_districts.py
prepare_tourapi_overviews.py
preprocess_tour_overviews.py
해당 구 place_catalog.csv
```

임베딩 담당자에게는 최종 산출물인 다음 파일만 전달해도 된다.

```text
districts/<지역구>/input/tour_overviews_preprocessed.csv
```
