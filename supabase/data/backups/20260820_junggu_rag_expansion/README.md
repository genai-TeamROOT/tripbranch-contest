# 2026-08-20 중구 RAG 확장 실험용 sync

팀 회의에서 나온 "종로구 외 다른 구도 RAG를 나눠서 해보자" 논의에 따라, Package B
담당자가 개인적으로 중구(사이군구코드 140)를 맡아 취향 근거 임베딩 파이프라인을
재현해본 작업의 일부다. **아직 D-025(`docs/decision-log.md`, "MVP는 서울특별시
종로구로 한정") 정책이 개정되지 않았으므로, 중구는 정식 서비스 지역이 아니다.**
이 작업은 `place_embeddings.jsonl` 생성·적재까지만 다루고, 실제 추천 파이프라인에
중구를 노출하는 배선은 하지 않았다.

| 파일 | 내용 |
| --- | --- |
| `places_api_snapshot_20260820.csv` | TourAPI `areaBasedList2` 기준 중구 스냅샷, 892건. `lDongRegnCd=11`, `lDongSignguCd=140`. |
| `places_reconciliation_20260820.csv` | `snapshot_places.py`가 자동으로 이전 스냅샷과 대조한 결과. **주의**: 비교 대상이 종로구 스냅샷(`places_api_snapshot_20260810.csv`, 844건)이었다 — 스크립트가 구 단위로 baseline을 구분하지 않고 "가장 최근 스냅샷 파일"만 본다. "삭제 844건"으로 나온 항목은 실제 폐업이 아니라 전부 종로구 장소이며, 이번 대조 결과 자체는 의미 있는 정보가 아니다.

## 이 시점에 무엇이 바뀌었나

`sync_places.py`로 중구 892곳을 `places` 테이블에 upsert했다(신규 892 / 상세조회
대상 669 / 실패 0). 종로구 데이터는 건드리지 않았다 — 같은 테이블에 `district_code`로
구분되어 나란히 들어간다.

## 겪은 것 — 다음에 같은 작업을 할 때

- `areaBasedList2`/`detailIntro2` 파라미터가 `areaCode`/`sigunguCode`/`arrangeType`이
  아니라 `lDongRegnCd`/`lDongSignguCd`/`arrange`다(KorService2 전환, PR #192). 옛
  이름으로 부르면 `INVALID_REQUEST_PARAMETER_ERROR`가 난다.
- 기본 `PLACE_SYNC_DETAIL_CONCURRENCY=5`, `PLACE_SYNC_DETAIL_MIN_INTERVAL_SECONDS=0`
  조합은 이 서비스키 기준 초당 한도(`LIMITED_NUMBER_OF_SERVICE_REQUESTS_PER_SECOND_EXCEEDS_ERROR`,
  reasonCode 23)에 바로 걸린다. `PLACE_SYNC_DETAIL_CONCURRENCY=1`,
  `PLACE_SYNC_DETAIL_MIN_INTERVAL_SECONDS=0.5`로 낮추니 669건 전부 성공했다.
- `places` 테이블에 종로구 외 구 데이터가 섞이면 `resolve_location`의 이름 기반
  위치 검색(`find_active_places_by_name`)이 지역 구분 없이 동작해 다른 구 장소를
  "지원 지역"으로 잘못 취급할 수 있었다(D-044 전제 붕괴). 이 sync를 실행하기 전에
  `app/repositories/supabase_places.py`에 `area_code`/`district_code` 필터를 먼저
  추가해서 막아뒀다.

## 관련 작업

- `junggu_rag/place_embeddings.jsonl` (커밋 대상 아님, `.gitignore` 처리) — 이 892곳의
  Google 리뷰를 문장 단위로 청킹·임베딩한 결과. `scripts/import_place_embeddings.py`로
  `place_embeddings` 테이블에 적재 중.
- `docs/decision-log.md` D-025, D-044
