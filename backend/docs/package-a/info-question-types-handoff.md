# INFO question_type 확장 — A 배선 인수인계

| 항목 | 값 |
|---|---|
| 작성 | 2026-08-07 |
| 보내는 쪽 | C (Tool Intelligence·External Context) |
| 받는 쪽 | A (Request Intelligence·Agent Runtime) |
| 관련 결정 | D-054, D-055 |
| 관련 문서 | `docs/design/int-02-info.md`, `docs/design/agent-response-generation.md` §6 |

## 요약

INFO의 `question_type` 8종 중 `concentration` 하나만 실제로 동작하고 있었다. C가
나머지를 처리할 수 있도록 계약과 서비스를 확장했다. **A 배선 3곳이 남아 있어 지금은
사용자에게 여전히 "아직 준비 중이에요"가 나간다.**

C 변경분은 기존 동작을 바꾸지 않는다 — `question_type` 기본값이 `concentration`이라
현재 A 호출부는 그대로 동작한다(전체 회귀 1276 passed).

## C가 지금 돌려주는 것

`InfoContextResponse.result`가 union이 됐다.

```python
result: ConcentrationInfoResult | PlaceInfoResult | EventInfoResult | None
```

- `question_type == "concentration"` → `ConcentrationInfoResult` (기존과 동일, 무변경)
- `question_type == "event"` → `EventInfoResult` (신규)
- 그 외 → `PlaceInfoResult` (신규)

```python
class PlaceInfoResult(BaseModel):
    status: Literal["success", "no_data", "unavailable"]
    question_type: InfoQuestionType
    requested_place_name: str | None    # 사용자가 말한 이름
    resolved_place_name: str | None     # 해석된 정식 명칭 ("종묘 [유네스코 세계유산]")
    place_id: str | None
    fields: dict[str, str]              # 아래 표의 키만 들어온다
    error: ContextError | None
```

`fields`는 값이 있는 키만 담는다. 빈 문자열이나 "정보 없음" 같은 문구를 C가 지어내지
않는다. `fields`가 비면 `status="no_data"`이고, 이때도 `resolved_place_name`은 채워
보낸다("경복궁의 주차 정보는 없어요"처럼 장소를 짚어 안내할 수 있게).

### question_type별 fields 키

| question_type | fields 키 | 비고 |
|---|---|---|
| `operating_hours` | `operating_hours`, `rest_date` | provider 정규화 값 |
| `fee` | `fee` | |
| `parking` | `parking`, `parking_fee` | |
| `facility` | `baby_carriage`, `pet`, `credit_card`, `restroom` | 있는 것만 |
| `location_info` | `address` | 외부 API 호출 없음 |
| `general_info` | `overview`, `homepage` | HTML 태그 제거·공백 정리 완료 |
| `event` | — | 별도 result 타입. 아래 「행사(event) 응답」 참고 |
| `concentration` | — | 기존 `ConcentrationInfoResult` 경로 |

`general_info`의 `overview`는 TourAPI 원문이라 길 수 있다(수백 자). 요약이 필요하면
A 쪽 판단이다 — C는 정제만 하고 자르지 않는다.

## 행사(event) 응답 — D-055

`event`만 결과 모양이 다르다. 행사가 여러 건이라 `fields: dict[str, str]`에 담을 수
없어서다.

```python
class EventItem(BaseModel):
    title: str
    start_date: str          # YYYY-MM-DD
    end_date: str
    address: str | None
    distance_km: float | None    # 대상 장소로부터의 직선거리
    is_direct_match: bool        # 제목에 대상 장소명이 든 행사인가

class EventInfoResult(BaseModel):
    status: Literal["success", "no_data", "unavailable"]
    question_type: Literal["event"]
    requested_place_name / resolved_place_name: str | None
    reference_date: str | None
    events: list[EventItem]      # 최대 5건, (직접 매칭 우선, 가까운 순)
    has_direct_match: bool
    error: ContextError | None
```

### 문구에서 반드시 지켜야 할 것

**`is_direct_match=False`인 행사를 "그 장소의 행사"라고 말하면 사실과 다르다.**

TourAPI에는 장소별 행사 조회가 없어 종로구 행사를 받아 좌표로 거리를 매긴다.
2026-08-07 실측에서 진행 중 6건의 `eventplace`는 `"광화문광장&세종로공원"`,
`"청와대 사랑채 1층"`, `"서울 전역"`처럼 우리 장소명과 형태가 달라 **직접 매칭이
0건**이었다. 즉 실제로는 대부분 "근처 행사"로 나간다.

집중률의 `is_proxy`와 같은 취지다(D-036) — 근처 기준으로 답하되 반드시 고지한다.

```
권장:   "경복궁 근처에서 진행 중인 행사예요. 의정부지 상설 전통문화행사(0.21km), ..."
피할 것: "경복궁에서 의정부지 상설 전통문화행사가 열리고 있어요"
```

`is_direct_match=True`인 건("경복궁 별빛야행")은 그 장소의 행사로 말해도 된다.

`status="no_data"`는 "종로구에 오늘 진행 중인 행사가 없다"이지 장소를 못 찾은 게
아니다 — `resolved_place_name`은 채워져 있다.

## A가 해야 할 배선 3곳

### 1. `services/runtime/info_context_transform.py`

`to_info_context_request()`가 `question_type`을 안 넘긴다(계약이 concentration
고정이던 시절 그대로). 두 필드를 추가한다.

```python
return InfoContextRequest(
    request_id=request_id,
    place_name=info.place_name,
    place_context=info.place_context.value,
    question_type=info.question_type.value,   # 추가
    specific_question=info.specific_question,  # 추가
    visit_time=info.visit_time,
)
```

함수 docstring의 "question_type이 concentration이 아닌 InfoPayload로 호출하지
않는다" 전제도 함께 지워야 한다.

`app.schemas.QuestionType`과 `InfoQuestionType`은 값이 일치한다(8종 동일).

### 2. `services/runtime/agent_runtime.py`

INFO 게이트가 `CONCENTRATION`으로 한정돼 있다. 그 조건만 빼면 INFO 전체가 C를 탄다.

```python
if (
    llm_output.status is OutputStatus.COMPLETE
    and llm_output.intent is Intent.INFO
    and llm_output.info is not None
    # and llm_output.info.question_type is QuestionType.CONCENTRATION  ← 제거
    and hasattr(tool_provider, "fetch_info_context")
):
```

`hasattr` 방어는 그대로 두면 된다.

### 3. `services/runtime/response_composer.py`

여기가 배선만으로 안 끝나는 유일한 곳이다. `compose_info_concentration_message()`는
`concentration_label`/`forecast_date`/`is_proxy`를 직접 읽어 문장을 만든다. 상세
응답을 그대로 넣으면 그 필드들이 전부 `None`이라 `_TOOL_UNAVAILABLE_MESSAGE`로
떨어진다.

`result` 타입으로 분기하는 진입점을 앞에 두는 형태를 제안한다.

```python
if isinstance(response.result, EventInfoResult):
    return compose_event_info_message(response)   # 신규
if isinstance(response.result, PlaceInfoResult):
    return compose_place_info_message(response)   # 신규
return compose_info_concentration_message(response)  # 기존, 무변경
```

C 쪽 제안은 **고정 템플릿**이다. 집중률을 고정 템플릿으로 둔 이유(정확성이 걸린
문제라 LLM 스타일링을 넣지 않는다)가 요금·운영시간·주차에도 그대로 적용된다.
`general_info`처럼 원문이 긴 것만 LLM 요약을 붙일지는 A 판단 영역이라 C가 정하지
않았다.

`status`별로 필요한 문구:

| status | 상황 |
|---|---|
| `success` | `fields`를 문장으로 |
| `no_data` | 장소는 찾았지만 그 질문에 답할 값이 없음 |
| `unsupported` | (현재 발생하지 않음 — 8종 모두 지원) |
| `unavailable` | TourAPI 장애 / Tool 미배선 |
| `needs_clarification` | 장소명 없음·모호 (기존 `_CLARIFICATION_TEMPLATES` 재사용 가능) |

마지막 줄의 `_NOT_YET_SUPPORTED_MESSAGE`는 COMPARE용으로 남겨둔다.

## 확인 방법

C 단독 회귀는 끝나 있다.

배선 전에도 C 응답을 직접 볼 수 있다. `--json`을 주면 A가 받을 계약 원본이 그대로
찍힌다.

```bash
cd backend
python -m scripts.try_info_context                      # 경복궁 8종
python -m scripts.try_info_context 창덕궁 event
python -m scripts.try_info_context 경복궁 fee --json

# 외부 API 없이(반복 확인용)
PLACE_PROVIDER=fake python -m scripts.try_info_context 경복궁 event
```

회귀 테스트:

```bash
cd backend
python -m pytest tests/agent_context/test_info_field_rules.py \
                 tests/agent_context/test_info_place_detail.py \
                 tests/agent_context/test_info_event.py \
                 tests/test_festival_provider.py -v
python -m pytest -q          # 1276 passed / 22 skipped
python -m ruff check app tests
```

A 배선 후에는 실제 TourAPI로 한 번 확인하는 게 좋다 — Fake는 `raw_intro`와 행사
목록을 흉내 낸 값이라 실 응답의 유형별 키 누락까지는 잡지 못한다.

```bash
RUN_REAL_PROVIDER_TESTS=true python -m pytest -m smoke -v -s
```

## 알려진 한계

- `event`는 종로구 등록 행사가 25건뿐이라 표본이 작고, 직접 매칭이 드물다(위 참고).
- 상세 질의는 TourAPI를 직접 호출한다(D-054). INFO 응답에 외부 API 지연이 붙고
  TourAPI 일일 한도를 소비한다. `location_info`만 호출 없이 답한다
  (`operating_hours`도 호출한다 — 이유는 D-054 참고).
- `facility`의 `restroom`은 쇼핑(38) 유형에만 있는 키라 대부분의 장소에서 비어 있다.
