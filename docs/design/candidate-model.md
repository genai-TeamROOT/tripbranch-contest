
# 후보 정규화 모델 v0.1

## 문서 정보

| 항목 | 값 |
|------|-----|
| 버전 | v0.1 |
| 상태 | 초안 (Draft) |
| 최종 수정 | 2026-07-22 |
| 경로 | `docs/design/candidate-model.md` |

---

## 1. 개요

외부 장소 API(TourAPI)의 원본 응답을 추천 엔진이 직접 사용하지 않는다. Provider가 외부 응답을 내부 정규화 모델로 변환한 뒤 추천 엔진에 전달한다.

```
TourAPI 원본 응답 → Provider 변환 → PlaceCandidate (내부 모델) → 추천 엔진
```

---

## 2. PlaceCandidate 모델

```typescript
interface PlaceCandidate {
  // 식별
  content_id: string;
  content_type_id: string;
  name: string;

  // 위치
  latitude: number;
  longitude: number;
  address: string;
  distance_km: number | null;  // search_center로부터 직선거리 (조회 시 계산)

  // 분류
  place_type: PlaceType;
  place_tag: PlaceTag | null;
  cat1: string | null;
  cat2: string | null;
  cat3: string | null;

  // 운영시간
  operating_status: OperatingStatus;
  operating_hours_raw: string | null;
  remaining_minutes: number | null;

  // 환경
  environment_type: EnvironmentType;

  // 부가 정보
  image_url: string | null;
  tel: string | null;
  overview: string | null;
}
```

---

## 3. 필드 상세

### 필수 필드

| 필드 | 설명 | 소스 |
|------|------|------|
| `content_id` | TourAPI 고유 ID | locationBasedList2 → contentid |
| `content_type_id` | 유형 코드 | locationBasedList2 → contenttypeid |
| `name` | 장소명 | locationBasedList2 → title |
| `latitude` | 위도 | locationBasedList2 → mapy |
| `longitude` | 경도 | locationBasedList2 → mapx |
| `address` | 주소 | locationBasedList2 → addr1 |
| `place_type` | 내부 유형 분류 | content_type_id로 매핑 |
| `operating_status` | 운영 상태 | detailIntro2에서 판정 |
| `environment_type` | 실내/야외 | 카테고리 기반 매핑 |

### 선택 필드

| 필드 | 설명 | 없을 때 |
|------|------|---------|
| `distance_km` | 직선거리 | 위치기반 조회가 아니면 null → 별도 계산 |
| `place_tag` | 세부 분류 | 매핑 불가 시 null |
| `remaining_minutes` | 남은 운영시간 | 판정 불가 시 null |
| `operating_hours_raw` | 운영시간 원본 텍스트 | 조회 실패 시 null |
| `image_url` | 대표 이미지 | 없으면 null |
| `tel` | 연락처 | 없으면 null |
| `overview` | 장소 개요 | 없으면 null |

---

## 4. OperatingStatus 정의

```typescript
type OperatingStatus =
  | "open"      // 현재 영업 중 (남은 시간 계산 가능)
  | "closed"    // 현재 영업 종료 또는 정기 휴무 (= 운영종료)
  | "unknown";  // 운영시간 확인 불가
```

| 상태 | 추천 처리 |
|------|-----------|
| `open` | 정상 추천 후보 |
| `closed` | Hard Filter에서 제외 |
| `unknown` | 별도 그룹으로 분리하여 안내 |

> **[2026-08-13 Superseded]** Scoring 계층(`ScoringCandidate.operating_hours:
> OperatingHours`)에서는 "현재 영업 종료"와 "정기 휴무"를 이 표처럼 `closed`
> 하나로 뭉개지 않는다. `OperatingHours.is_regular_closure: bool`로 정기
> 휴무 여부를 별도로 표시하고, 실제 개장~마감 시각은 그대로 보존한다 —
> 예전에는 운영종료를 알리려고 시각 자체를 `00:00~00:00`으로 지웠으나, 그
> 표식이 표시할 시간까지 함께 지워버려 추천 카드에 "00:00~00:00"이 그대로
> 노출되는 문제가 있었다. 또한 `closed`는 더 이상 무조건 Hard Filter 제외가
> 아니다 — 검색 결과가 운영종료 후보뿐이라 0건으로 응답한 턴에서 사용자가
> "운영 중이 아닌 곳도 볼게요" 되묻기를 선택하면(`no_data_closed`,
> [clarification-options.md](./clarification-options.md)) `ignore_operating_hours=True`로
> 운영종료 후보도 채점에 포함한다. 최신 판정 규칙은
> [recommendation-scoring.md §3](./recommendation-scoring.md#3-제외-규칙-하드-필터)이
> 소유한다.

---

## 5. EnvironmentType 정의

```typescript
type EnvironmentType =
  | "indoor"   // 실내
  | "outdoor"  // 야외
  | "mixed"    // 실내+야외 혼합
  | "unknown"; // 판단 불가
```

### 카테고리 기반 기본 매핑

| place_tag / place_type | environment_type |
|------------------------|-----------------|
| 박물관, 미술관, 도서관, 과학관, 전시관 | indoor |
| 카페, 음식점 (restaurant 전체) | indoor |
| 쇼핑몰, 백화점, 면세점 | indoor |
| 공원, 산, 해변, 호수, 계곡, 둘레길 | outdoor |
| 궁궐, 사찰, 성곽 | mixed |
| 테마파크, 동물원 | mixed |
| 시장 | mixed |
| 기타 / 판단 불가 | unknown |

---

## 6. 운영시간 Unknown 처리 정책

### 발생 원인

- detailIntro2에서 운영시간 필드가 빈 값
- detailIntro2 API 호출 실패
- 비정형 텍스트 파싱 불가

### 처리 규칙

```
1. operating_status = "unknown"으로 설정
2. remaining_minutes = null
3. 추천 시 정상 점수 목록에 섞지 않음
4. 별도 영역("운영시간 확인 필요")에 표시
5. "방문 전에 운영 여부를 확인해주세요" 경고 표시

표시 조건:
  - 정상 후보(open)가 3개 미만일 때만 unknown 후보를 추가 표시
  - 정상 후보가 충분하면 unknown 후보는 숨김
```

### unknown 후보 정렬 기준

```
카테고리 매칭 → 거리 (운영시간 점수 제외)
```

---

## 7. 날씨 데이터 없음 처리 정책

### 발생 원인

- 사용자가 날씨를 언급하지 않음
- 날씨 API 호출 실패
- 사용자에게 날씨 입력을 요청했으나 미제공

### 처리 규칙

```
1. weather = null, weather_intent = "IGNORE"로 설정
2. 추천 점수 계산에서 날씨 가중치를 제외
3. 나머지 가중치를 100%로 재정규화
4. 사용자에게 안내:
   "현재 날씨를 확인하지 못해 날씨 조건을 제외하고 추천했어요."
```

### 재정규화 예시

> **[2026-07-23 Superseded]** 아래 예시의 `category` 가중치는 Scoring v1 결정
> (D-008, [`recommendation-scoring.md`](./recommendation-scoring.md))에 따라
> 폐기되었습니다. 카테고리는 하드 필터로만 처리합니다. `remaining_time`은
> 이름이 `remaining_operating_time`으로 바뀌었고, 분 단위 값 자체는 유지하되
> 후보의 원문 운영시간을 `open_time`/`close_time`(`OperatingHours`)으로
> 정규화한 뒤 기준 시각(`now`)과 비교해 계산합니다. 현재 기본 가중치는 날씨
> 0.40 / 남은 운영시간 0.40 / 거리 0.20이며, 날씨·남은 운영시간은 후보마다
> 독립적으로 결측될 수 있어 결측된 Feature(들)만 나머지에 비례 재분배합니다.
> 운영종료 여부(운영 유무) 자체는 가중치가 아니라 `now`/`OperatingHours` 비교로
> 판정하는 최종 하드 필터입니다(`ignore_operating_hours=True`면 예외 — §4 참고).

```
정상 (날씨 있음):
  category: 0.40
  remaining_time: 0.30
  weather: 0.20
  distance: 0.10

날씨 없음:
  category: 0.50  (0.40 / 0.80)
  remaining_time: 0.375  (0.30 / 0.80)
  distance: 0.125  (0.10 / 0.80)
```


