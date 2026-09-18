# RecommendationContext Fixture 사용 가이드

이 폴더의 JSON은 C가 반환하는 정규화된 `AgentContextResponse` 샘플이다. D는 실제
Provider나 A Runtime 없이 이 파일을 읽어 `context`를 추천 파이프라인에 전달할 수
있다.

> **주의:** 이 데이터는 반복 검증을 위해 고정한 테스트 Fixture이며 실제 최신
> 장소·날씨·운영정보를 나타내지 않는다. 운영 환경의 사용자 응답이나 데이터 적재에
> 사용하지 않는다.

## 빠른 검증

백엔드 디렉터리에서 다음 테스트를 실행한다.

```bash
python -m pytest tests/agent_context/test_recommendation_context_fixtures.py -v
```

기존 A–C Mock Fixture 검증까지 함께 실행하려면 다음 명령을 사용한다.

```bash
python -m pytest \
  tests/agent_context/test_mock_fixtures.py \
  tests/agent_context/test_recommendation_context_fixtures.py -v
```

## D에서 사용하는 방법

아래 코드는 별도의 Python 실행 파일을 새로 만들라는 의미가 아니다. D의 기존
테스트 코드에서 Fixture를 불러와 추천 파이프라인에 전달할 때 참고하는 사용
예시다.

Fixture 결과를 바로 눈으로 확인하려면 별도 파일을 만들지 말고 프로젝트에 포함된
inspection 테스트를 실행한다.

```bash
python -m pytest \
  tests/agent_context/test_recommendation_context_fixture_inspection.py \
  -q -s
```

아래 Python 예시는 D가 자체 품질·회귀 테스트를 작성할 때 필요한 부분만 기존
테스트에 옮겨 사용한다.

```python
import json
from datetime import datetime
from pathlib import Path

from app.agent_context.schemas import AgentContextResponse
from app.services.recommendation_pipeline import (
    run_recommendation_pipeline_from_context,
)

fixture_path = Path(
    "tests/fixtures/agent_context/partial_weather_unavailable.json"
)
payload = json.loads(fixture_path.read_text(encoding="utf-8"))
response = AgentContextResponse.model_validate(payload)

result = await run_recommendation_pipeline_from_context(
    response.context,
    visit_at=datetime.fromisoformat("2026-08-15T11:00:00+09:00"),
    search_radius_km=2.0,
)
```

Fixture의 `AgentContextResponse.status`가 `success`, `partial`, `no_data`인 경우에만
`context`가 D 테스트 입력으로 사용된다. `needs_clarification`, `unsupported`,
`unavailable` 응답은 A에서 흐름을 종료하거나 재질문하므로 D에 전달하지 않는다.

## 실행 인자

| 인자 | 설명 | Fixture 포함 여부 |
|---|---|---|
| `context` | C가 정규화한 추천 Context | 포함 |
| `visit_at` | 운영시간 판단 기준 시각 | 미포함, 테스트가 지정 |
| `search_radius_km` | C가 장소를 수집할 때 사용한 실제 반경 | 미포함, 테스트가 지정 |
| `shown_place_ids` | 이전에 노출한 장소 ID | 미포함, D가 지정 |
| `rejected_place_ids` | 사용자가 거절한 장소 ID | 미포함, D가 지정 |
| `recommendation_limit` | 최대 추천 결과 수 | 미포함, D가 지정 |

`search_radius_km`은 거리 점수 정규화에 사용되므로 실제 후보 수집 반경과 같은 값을
사용해야 한다.

## Fixture 선택 기준

- 일반 추천 경로: `success.json`
- 악천후 환경 유형 비교: `success_bad_weather.json`
- 운영시간·폐점 처리: `success_operating_schedule.json`
- 날씨 API 장애: `partial_weather_unavailable.json`
- 일부 운영정보 누락: `partial_place_details.json`
- 날씨 조건 미사용: `missing_weather.json`
- 운영정보 전체 누락: `missing_operating_hours.json`
- 후보 수 부족: `insufficient_candidates.json`
- 장소 검색 결과 없음: `no_place_candidates.json`

## 수정 규칙

- Provider 원본 응답을 넣지 않고 C 계약으로 정규화된 필드만 사용한다.
- 필드 이름은 Python과 JSON 모두 `snake_case`를 사용한다.
- `retrieved_at`, `forecast_for`에는 timezone을 포함한다.
- 상태가 `success` 또는 `partial`이면 `data`가 필요하다.
- `no_data`에는 `null` 또는 빈 목록만 허용한다.
- `unavailable`에는 `data` 대신 `error`가 필요하다.
- 추천 점수나 기대 순위는 이 JSON에 추가하지 않는다.
- Fixture 추가·수정 후 위의 검증 테스트를 실행한다.

상세한 설계와 책임 경계는
`docs/design/recommendation-context-fixtures.md`를 참고한다.
