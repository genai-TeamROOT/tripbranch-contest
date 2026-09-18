# 장소 보관함 — 사용자가 고른 장소로 일정을 짠다

## 문서 정보

| 항목 | 값 |
|------|-----|
| 버전 | v1 (Notion "SCHEDULE-12 설계안"을 저장소로 옮기고 구현 결과까지 반영) |
| 상태 | 카드 4장 중 3장 완료 · 구 간 이동(4번)만 남음 |
| 최종 수정 | 2026-09-01 |
| 관련 결정 | D-107, D-110, D-114, D-116 |
| 관련 카드 | TP-180(SCHEDULE-11), SCHEDULE-12·13·14 |
| 관련 코드 | `backend/app/state/saved_places.py`, `backend/app/routes/state.py`, `backend/app/services/recommendation_pipeline.py`, `backend/app/schedule/planner.py` |
| 선행 문서 | [`int-07-schedule.md`](int-07-schedule.md) (SCHEDULE 기능 전체 설계) |

이 문서는 **사용자가 추천 카드에서 고른 장소**를 상태로 들고 있다가 일정 편성에
반드시 반영하는 경로를 다룬다. 일정을 어떤 순서로 몇 시에 배치할지는
[`schedule-engine.md`](schedule-engine.md)가 다룬다.

---

## 1. 무엇을 만드나

사용자가 추천 카드에서 **장바구니에 담듯 장소를 저장**하고, 담긴 장소들로 일정을
짜는 기능. 핵심은 두 가지다.

1. State에 **"사용자가 고른 장소"** 개념을 만든다 — 그전에는 "보여준 장소"와
   "거절한 장소"밖에 없었다.
2. 담긴 장소가 일정에 **반드시** 들어가도록 구조적으로 보장한다 — 후보 풀에
   넣는 것만으로는 부족하다.

1번은 D-110으로, 2번은 D-114로 끝났다. 다만 2번의 "구조적 보장"은 담아둔 장소가
그 턴 후보에 들어오지 못하면 무력해진다는 것이 검증 중에 드러나 두 번 더 고쳤다
(SCHEDULE-14, D-116). 4절에 그 경위가 있다.

### 왜 필요했나

`int-07-schedule.md` 9절의 미결 항목 "대화 중 고른 장소로 일정 구성"이 이
설계의 출발점이다. SCHEDULE-11(TP-180, D-107)이 그 갭을 **부분만** 해소했다.

| 항목 | 상태 |
|------|------|
| 직전 턴에 본 장소가 후보에 남는가 | 된다 — `_effective_excluded_place_ids()`가 되살린다 |
| 3턴 전에 본 장소는 | 안 된다 — `shown_place_ids`는 마지막 run만 (`history.py:192`) |
| "고른 것"과 "보여준 것"을 구분하는가 | 해소됨(D-110) — `saved_places` 신설 |
| 고른 장소가 일정에 반드시 들어가는가 | 해소됨(D-114 + 후보 주입 + 재부착) — 4절 |

---

## 2. 보관함이 사는 곳 — 서버 세션 상태

프론트 로컬 상태로 두면 안 된다. 일정 편성 판단이 서버(`_run_schedule_branch`)에서
일어나므로 그 시점에 서버가 읽을 수 있어야 한다.

`RecommendationHistory`에 얹지 않고 **별도 엔티티**로 둔다.

- `RecommendationHistory`는 append-only 이력이고, 보관함은 담기/빼기가 되는
  가변 상태다
- `clear_recommended()`(history reset)가 `recommended`·`closed_excluded`를
  비우므로, 여기 얹으면 **"다른 곳 보여줘" 한 번에 담아둔 장소가 함께 날아간다**

```python
class SavedPlaceItem(BaseModel):
    place_id: str
    name: str
    saved_from_run_id: str
    saved_at: datetime
    latitude: float | None = None
    longitude: float | None = None

class SavedPlaceList(BaseModel):
    session_id: str
    user_id: str | None = None   # AgentState와 동일 규칙(D-063 결정 3)
    items: list[SavedPlaceItem]
    updated_at: datetime
```

`SessionContextResponse.saved_places`로 노출해 `agent_runtime`이 추가 조회 없이
받는다. Supabase는 별도 테이블 `saved_place_lists`(마이그레이션 `202608310001`).

계정 단위 보존은 정식 인증(D-062 Phase 5)에 막혀 있으므로 V1은 **세션 TTL 30분과
함께 소멸**한다. `user_id` 필드만 미리 심어뒀다.

---

## 3. 담기·빼기 — LLM을 태우지 않는다

전용 REST로 처리한다. 인텐트 분류를 거치지 않으니 지연이 0이고 오분류가 없다.

```
POST   /api/state/{session_id}/saved-places      body: {place_id}
DELETE /api/state/{session_id}/saved-places/{place_id}
GET    /api/state/{session_id}/saved-places
```

- 셋 다 갱신된 전체 목록 + `changed`를 반환한다(프론트 낙관적 갱신 후 서버 값으로
  확정)
- 세션 소유권 검증은 기존 `/api/state/{session_id}` 경로를 그대로 재사용한다
- `place_id`는 **그 세션의 노출 이력에 있는 것만** 허용한다 — 임의 id 주입을 막고,
  `name`을 그 스냅샷에서 그대로 가져온다
- 담기/빼기는 **멱등**이다(`changed=False`). 이력의 중복 허용 정책과 달리 항목을
  늘리지는 않는다 — 보관함은 누적 기록이 아니라 현재 상태다

**설계 변경(구현 중 확정)**: 초안은 `place_id` 검증을 `shown_recommendations`
(마지막 run) 기준으로 잡았으나 **누적 이력 전체**로 바꿨다
(`find_recommended_item()` 신설). 화면에는 이전 턴의 추천 카드도 그대로 남아 있어
스크롤을 올려 3턴 전 카드를 담는 것이 정상 동작이고, 마지막 run으로 좁히면 그
경로가 400으로 막힌다. 주입 차단 효과는 동일하다.

---

## 4. 일정에 들어가는 방식 — 관문이 네 개다

담은 장소가 일정에 실리려면 서로 다른 네 지점을 모두 통과해야 한다. 각각 따로
막혀 있었고, 각각 따로 뚫어야 했다.

| # | 관문 | 막히면 생기는 일 | 방어 |
|---|------|------------------|------|
| 1 | 제외 목록 | 이미 보여준 장소라 후보 조회에서 제외됨 | D-107 + D-110 (`_revivable_place_ids()`) |
| 2 | 후보 수집 | 이번 턴 검색 반경 밖이라 C 응답에 없음 | 편성 직전 상세 재조회 후 후보 컨텍스트 주입 (SCHEDULE-14) |
| 3 | 점수순 자르기 | 거리 점수가 0이라 `ranked[:limit]`에서 잘림 | 재점수 후 목록 끝에 재부착 (D-116 정정) |
| 4 | LLM 선택 | 프롬프트에 있어도 LLM이 안 고름 | `must_include_place_ids` 하드 검증 + 1회 재시도 (D-114) |

### 4.1 제외 목록 복귀 (D-107 확장)

```python
def _revivable_place_ids(llm_output, session_context) -> Sequence[str]:
    saved = [i.place_id for i in session_context.saved_places]
    if llm_output.modify is not None:
        return saved          # 재조정 턴이어도 담아둔 건 살린다
    return [*session_context.shown_place_ids, *saved]
```

거절과의 충돌은 **담기 시점 규칙으로 원천 제거**한다. `record_rejected()`가 같은
place_id를 보관함에서 자동 제거하면 `saved ∩ rejected = ∅`이 구조적으로 보장되고,
D-107이 막았던 "거절 이력 무력화"가 재발하지 않는다.

`get_exclusion_place_ids()`의 계약은 건드리지 않는다 — "이번 턴에 무엇을 넘길지"만
조정한다. 적용 지점은 `_fetch_tool_context()`와 `_score_recommendations()` **두 곳
모두**다. 조회 단계에서 걸러지면 채점 단계에는 후보 자체가 없다.

### 4.2 후보 주입 (SCHEDULE-14)

담아둔 장소는 편성 직전에 Supabase `places`에서 상세를 다시 조회해 후보
컨텍스트(`tool_context.places`)에 주입한다. 그래서 **이번 턴 검색 반경과 무관**하다.
종로에서 2곳 담고 → 강남 검색해서 2곳 담고 → "일정 짜줘" 하면 4곳 전부
`must_include`로 들어간다.

**우회에는 전제가 있다.** 주입은 Supabase `places`에 상세 행이 있는 장소만 된다.
없으면 후보가 되지 못하고 `ScheduleResult.absent_saved_place_names`로 안내된다.

담아둔 장소가 후보에 못 들어오는 사유는 둘이고 **필드를 갈라 둔다**(TP-236).
방문 시각에 문을 닫아 D의 하드 필터가 걸러낸 것은
`closed_saved_place_names`로, 상세 행이나 좌표가 없어 애초에 안 잡힌 것은
`absent_saved_place_names`로 간다. 사용자가 할 수 있는 일이 다르기 때문이다 —
앞엣것은 시간대를 바꾸면 실제로 들어가고, 뒤엣것은 바꿔도 결과가 같다. 한
필드였을 때는 화면이 두 경우에 같은 안내를 했고, 뒤쪽 사용자는 통하지 않는
재시도를 반복했다. 판정은 D의 `excluded_closed_place_ids`를 그대로 쓴다 —
B가 영업시간을 다시 해석하지 않는다.

### 4.3 자르기에서 살아남기 (D-116 정정)

주입에 성공해도 `services/recommendation_pipeline.py`의
`ranked[:recommendation_limit]`가 그대로 잘라냈다. 담아둔 장소는 이번 턴 검색
중심에서 멀어(예: 반경 2km에 실제 5.8km) `_distance_score`가 0이 되고 순위 바닥에
깔리기 때문이다. 인사동에서 4곳을 담고 일정을 요청했더니 1곳만 들어간 실사용
재현으로 드러났다.

첫 방어(D-116 초안)는 상한을 `recommendation_limit + injected_saved_count`로
올리는 것이었는데, **효과도 없고 비용만 늘었다** — `_score_with_measured_routes()`의
`shortlist_limit`이 같은 값을 쓰기 때문에 도보 실측 경로 조회가 함께 부풀었다
(D-113 역행).

확정안은 자르기 자체를 건드리지 않는다.

1. `_missing_place_ids()` — 자른 뒤 빠진 보관함 장소를 찾는다
2. `_narrow_prepared()` — 그 장소들만 다시 점수 계산한다
3. `_with_pinned_recommendations()` — 목록 **끝에** 덧붙인다

상한을 넘지 않고, 실측 조회 대상도 늘지 않는다. 순위는 끝이지만 4.4의 하드 검증이
받아주므로 배치는 보장된다.

### 4.4 배치 보장 (D-114)

후보에 넣는 것만으로는 "담은 곳으로 짜줘"가 안 된다. 채점 순위에서 밀리면 그대로
빠진다.

```python
class SchedulePlanningRequest(BaseModel):
    candidates: list[RecommendationItem]
    must_include_place_ids: list[str] = Field(default_factory=list)
    ...
```

SCHEDULE-07이 정한 철학("LLM 지시 준수보다 구조적 보장을 우선한다")을 그대로
따른다 — 프롬프트로 지시하고, `plan_schedule()`이 응답 직후
`set(must_include) ⊆ {item.place_id}`를 하드 검증한다. 누락되면 **부분 성공 +
안내**로 처리하고 하드 실패하지 않는다(장바구니는 부분 성공이 전체 실패보다 낫다).

기존 `pinned_items`(REJECT_SPECIFIC 전용)는 **재사용하지 않는다**. 그건 order가
이미 정해진 기존 일정 항목을 그 자리에 유지하는 구조라, 순서가 미정인 보관함과
의미가 다르다.

---

## 5. 개수 충돌 규칙

`target_item_range()`(`schedule/schemas.py`)는 `time_available < 120`이면 최대
2개다. 보관함에 7개를 담고 "2시간 코스"를 요청하면 충돌한다.

| 상황 | 처리 |
|------|------|
| 보관함 개수 ≤ 상한 | 전부 넣고, 남는 자리는 기존 후보로 채운다 |
| 보관함 개수 > 상한 | 담은 **순서대로** 상한까지, 나머지는 안내 문구로 알린다 |
| 보관함 개수 > 5 | 담기는 막지 않고, 편성 시점에만 위 규칙을 적용한다 |

점수 순이 아니라 담은 순인 이유는 **왜 빠졌는지 사용자에게 설명할 수 있어야**
하기 때문이다. `items` 순서를 담은 순서로 고정한 것(D-110)이 이 규칙의 전제다.

---

## 6. 기존 네 목록과의 관계

| 목록 | 의미 | 보관함과의 관계 |
|------|------|-----------------|
| `recommended` | 노출됨 | 보관함 후보의 원천 (여기 있는 것만 담을 수 있음) |
| `rejected` | 사용자가 거절 | 거절 기록 시 보관함에서 자동 제거 |
| `closed_excluded` | 폐점으로 걸러냄 | 담아둔 곳이 폐점이면 편성 시 안내 후 제외 |
| `shown_place_ids` | 마지막 run 노출분 | 보관함과 합집합으로 후보 복귀 |

---

## 7. 좌표 문제

`_build_pairwise_distances_km()`는 좌표를 **이번 턴 C 응답**(`tool_context.places`)
에서만 찾고, 못 찾으면 조용히 건너뛴다.

보관함 장소가 이번 턴 검색 반경 밖이면 매칭이 통째로 실패해 LLM이 거리 근거 없이
동선을 짠다 — 강남 장소가 종로 일정의 2번째에 꽂힐 수 있다.

해결: `RecommendedItem`에 `latitude`/`longitude`를 추가하고 `record_recommended()`가
추천 시점 좌표를 함께 저장한다. "B는 place_id만 저장하고 장소 상세를 보관하지
않는다" 원칙의 **네 번째 문서화된 예외**이며, 근거는 SCHEDULE-09에서 `name`을
예외로 넣은 것과 동일하다 — 그 시점 스냅샷이고, 재검색에 의존하면 매번 실패한다.

`record_recommendation()` 호출부가 세 곳이라는 점이 실제 작업량이었다.

| 호출부 | 좌표 출처 |
|--------|-----------|
| `agent_runtime.py` `_run_schedule_branch` | `places` 지역변수가 이미 스코프에 있다 |
| `agent_runtime.py` `_finalize_recommendation_response` | `tool_context`가 인자로 안 들어온다 — 이 함수와 `graph/nodes/pipeline.py`의 시그니처를 함께 고쳐야 했다 |
| `routes/recommendations.py` `_record_shown()` | 없음 — C 컨텍스트를 거치지 않는 독립 엔드포인트라 None으로 남긴다 |

`recommendation_histories.recommended`와 `saved_place_lists.items`는 둘 다 jsonb
배열이라 **마이그레이션이 필요 없다.**

---

## 8. 프론트

- `PlaceCard`에 담기/빼기 토글 (담긴 상태를 시각적으로 구분)
- 화면 하단 고정 바: `보관함 3곳 · [이 장소들로 일정 짜기]`
- CTA는 자유입력이 아니라 **결정적 요청** — `AgentRequest.schedule_from_saved: bool`을
  추가해 `classify_intent()`를 건너뛰고 바로 SCHEDULE로 라우팅한다.
  `clarification_choice`/`travel_origin_override`가 쓰는 패턴 그대로이고, LLM 호출
  한 번을 통째로 절약해 체감 지연에 직접 영향을 준다

**정책**: 보관함이 비어 있지 않으면 **모든 SCHEDULE 턴**에 반영한다(장바구니
은유대로). 하단 바에 개수가 항상 보이므로 놀랄 일이 없고, 비우기도 한 번이다.
대안(CTA로 시작한 턴에만 반영)은 "담아뒀는데 왜 안 들어갔지"가 되기 쉽다.

---

## 9. 구 간 이동 — 남은 병목

"구에서 다른 구로 이동하는 일정"이 함께 요청으로 들어왔다. **구 경계 자체는 이미
장벽이 아니다.**

- 서울 25개 구 전부가 지원 범위다 (`service_area.py`, D-109)
- 장소 검색은 구를 요청에 싣지 않는다. 시도(`"11"`)까지만 좁히고 응답의
  `lDongSignguCd`로 거른다(D-025). `place_search_policy.py` 주석에 이유가 명시돼
  있다 — 구를 요청에 실으면 반경 안에 있는 옆 지원 구 후보가 잘린다. 즉 **의도적으로
  구 간 후보가 섞이도록** 설계돼 있다

실제 병목은 세 개였다.

| # | 병목 | 상태 |
|---|------|------|
| ① | 검색 반경이 이동시간 미지정 시 2km 고정 | 보관함이 우회 (4.2절) |
| ② | 검색 중심점이 하나뿐 | 보관함이 우회 (4.2절) |
| ③ | 이동시간 가정이 실제의 약 3.7배 낙관적 | **남음** |

③은 `place_search_policy.py`에 팀이 남긴 실측(네이버 Directions, 경복궁 기준,
2026-08-20 평일 14시대)에서 나왔다 — 직선거리 기준 실효 속도 평균 5.38km/h로,
가정값 20km/h의 약 1/3.7이다. 종로→강남 11km를 시스템은 33분으로 보지만 실제는 두
시간에 가깝다. 반나절(240분) 예산이 이동만으로 소진되는데 LLM은 그걸 모르고 3~5곳을
배치한다.

가정값 자체는 함부로 못 바꾼다. 반경 산정과 예산 계산이 같은 상수를 공유하도록
설계돼 있어(`place_search_policy.py` 주석) 한쪽만 바꾸면 "사용자가 말한 30분 = 예산
30분"이라는 계약이 깨진다. 처리 방향은 [`schedule-engine.md`](schedule-engine.md)
2단계를 참고한다.

### TP-183과의 관계 — 서로 다른 문제다

| | TP-183 | 이 설계 |
|---|--------|---------|
| 문제 | "강남구에서 추천해줘" — 구 **전체**를 후보 범위로 삼기 | 서로 **다른 구**의 장소가 한 일정에 섞이기 |
| 층위 | 후보 수집(검색 범위) | 일정 편성(이동시간 정직성) |
| 상태 | 세 갈래 중 미결정 | 보관함이 검색 범위를 우회하므로 **의존하지 않는다** |

다만 TP-183의 (a)안(Supabase를 구 코드로 조회)이 지적한 "거리 점수의 분모가
정의되지 않는다"는 문제는 이쪽 ③과 뿌리가 같다. TP-183이 (a)로 결정되면 이 설계의
이동시간 처리도 함께 재검토한다.

---

## 10. 구현 현황

의존 순서: **1 → 2 → 3**, 4는 독립.

| # | 카드 | 핵심 | 상태 |
|---|------|------|------|
| 1 | [Schedule] 장소 보관함 상태와 담기/빼기 API | `SavedPlaceList` · REST 3개 · `SessionContextResponse` 노출 | 완료 (D-110) |
| 2 | [Schedule] 보관함에 담은 장소로 일정을 짠다 | 좌표 스냅샷 + `_revivable_place_ids()` · `must_include_place_ids` 하드 검증 + 개수 충돌 규칙 | 완료 (D-114, PR #306) |
| 3 | [Schedule] 추천 카드에 담기 버튼과 보관함 UI를 붙인다 | 카드 토글 · 하단 바 · `schedule_from_saved` | 완료 |
| — | (버그) 후보 주입 | 편성 직전 상세 재조회 → `tool_context.places` 주입 | 완료 (SCHEDULE-14) |
| — | (버그) 자르기 재부착 | `_missing_place_ids()` + 재점수 + 목록 끝 부착 | 완료 (D-116, PR #315) |
| 4 | [Schedule] 구 간 이동이 포함된 일정을 정직하게 편성한다 | 장거리 구간 실측 경로 + 안내 + 시간 부족 되묻기 | 미착수 |

2번만 A 경계(`agent_runtime.py` 배선 — `_finalize_recommendation_response` 시그니처
변경 포함)에 닿는다. 수정 권한은 받았고 작업 후 소유자에게 공유했다.

---

## 11. 범위 밖

- 자연어 담기("2번 저장해줘") — 인텐트 표면이 늘어난다. V2에서 MODIFY 확장으로 검토
- 계정 단위 영속 — 정식 인증(D-062 Phase 5)에 종속
- 보관함 공유/링크, 복수 보관함
- 다중 검색 중심점 도입 — 보관함이 반경을 우회하므로 불필요. 단 우회는 Supabase에
  상세가 있는 장소에 한한다(4.2절)
- 20km/h 가정값 자체 변경 — 반경 산정과 예산 계산이 같은 값을 공유하는 설계라 한쪽만
  바꾸면 깨진다. 표본을 넓힌 뒤 별도 카드로 다룬다
- `routes/recommendations.py` 경로에 좌표 공급 — C 컨텍스트를 안 거치는 구조라 별개
  문제다
