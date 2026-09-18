# 2026-08-04 집중률 검색어 컬럼 도입 전 백업

D-043 구현(마이그레이션 `20260804055402`, 매핑 101건 재적재, places 동기화) 직전 상태다.

| 파일 | 내용 |
| --- | --- |
| `places_api_snapshot_20260802.csv` | TourAPI 종로구 목록 844건. 이번 `sync_places --from-snapshot`의 입력이자, 매핑 생성의 장소 목록으로 썼다. |
| `concentration_place_mapping_20260729.csv` | 검색어 컬럼 도입 전 매핑. `concentration_search_key`가 없고 공백이 든 정식 명칭을 그대로 조회에 쓰던 시점의 것이다. |

## 이 시점에 무엇이 바뀌었나

- `place_concentration_mappings.concentration_search_key` 컬럼 추가
- 매핑 101건 재적재 — 검색어 24건, 별칭 2건(`청와대`, `창덕궁`)
- `places` 동기화 — 활성 844건으로 맞추고 숙박 3건(`서촌영락재`·`이호소락`·`여인숙 깔마`) 비활성화

## 되돌릴 때

매핑만 되돌리려면 이 폴더의 CSV를 적재한다. 다만 `concentration_search_key` 열이
없어 검색어가 비워지고, 공백이 든 정식 명칭(`서울 운현궁` 등 24건)은 다시 조회
0건이 된다.

```bash
python -m scripts.import_concentration_mappings \
  --csv supabase/data/backups/20260804_search_key_migration/concentration_place_mapping_20260729.csv --dry-run
```

## 재현하지 않는 것

집중률 장소명 목록(`concentration_place_names_*.csv`)은 남기지 않는다. 검색어 유일성은
목록 전체에 의존해서, 저장해둔 목록으로 다시 계산하면 그사이 추가된 장소 때문에
모호해진 검색어를 놓친다(D-043). 매핑을 만들 때마다 API로 다시 받아야 한다.
