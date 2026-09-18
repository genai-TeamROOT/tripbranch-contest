# TripBranch Prompt Library

TripBranch가 모델에 전달하는 정적 지침을 인텐트별로 관리하는 라이브러리입니다. 각 인텐트
폴더의 `HISTORY.md`가 **현재 상태·Draft·승인 이력의 단일 원천**입니다.
`docs/design/prompt-changelog.md`는 이전 안내 문서이며 새 이력을 추가하지 않습니다.

## 빠른 안내

| 확인할 내용 | 파일 |
| --- | --- |
| 현재 활성 슬롯·소유자·의존성 | `<intent>/meta.yaml` |
| 현재 상태·Draft·승인 이력 | `<intent>/HISTORY.md` |
| 실행 가능한 이전 프롬프트 원문 | `<intent>/archive/<slot>__legacy-<version>.md` |
| 과거 기준선의 파일 조합 | `<intent>/archive/variants.json` |
| 단일 턴 실험 | `<intent>/evals/` |
| 다중 턴·크로스 인텐트 회귀 | `backend/test_results/agent_quality/` |

## 폴더 구조와 역할

```text
app/prompts/
├── _shared/                    # 여러 인텐트가 함께 쓰는 모델 지침
├── router/                     # 7개 Intent 분류
├── recommend/                  # RECOMMEND 조건 추출·카드 요약
├── modify/                     # MODIFY 조건 변경·거절 처리
├── info/                       # INFO 질의 추출·사실 기반 답변
├── compare/                    # COMPARE 대상 추출·비교 요약
├── general/                    # GENERAL 주제·답변
├── out_of_scope/               # 범위 밖·안전 분류
├── schedule/                   # 일정 편성·부분 재편성
├── follow_up/                  # 턴이 끝난 뒤 다음 발화 제안
└── synthetic_review/           # TourAPI 기반 합성 리뷰 생성
```

`follow_up/`과 `synthetic_review/`는 인텐트 폴더가 아닙니다 — 인텐트 하나에 속하지 않아
`meta.yaml`에 `intent` 키가 없습니다. 나머지 규칙(슬롯·버전·아카이브)은 동일합니다.

각 인텐트 폴더는 아래 구조를 공통으로 사용합니다.

```text
<intent>/
├── meta.yaml        # 현재 슬롯, 담당자, 활성 관리 버전, 공유 규칙 의존성
├── HISTORY.md        # 현재 상태, Draft, 승인 이력의 단일 원천
├── *.md             # 모델이 읽는 현재 활성 지침
├── evals/           # 담당자가 빠르게 반복할 단일 턴 평가 자산
└── archive/         # 실행 가능한 승인 기준선만 보관
    ├── <slot>__legacy-<version>.md
    └── variants.json # 선택형: 기준선을 재현할 파일 덮어쓰기 조합
```

모델이 읽는 문장과 규칙은 반드시 `.md`에 둡니다. YAML은 사람이 확인하는 메타데이터에만
사용합니다. Jinja2는 사용하지 않으며, 조건 분기·반복·대화 상태·JSON 직렬화는 Python
조립 코드가 맡고 Markdown에는 단순 `{{값}}` 자리표시자만 허용합니다.

## 담당자의 변경 절차

1. 자신의 `<intent>/meta.yaml`에서 담당 슬롯과 공유 규칙 의존성을 확인합니다.
2. 현재 활성 `.md`를 수정합니다. 공유 규칙은 `_shared/`에서만 수정하며 인텐트 폴더에 복사하지 않습니다.
3. 행동에 영향을 주는 변경이면 `<intent>/HISTORY.md`의 `Draft`에 변경 이유와 평가 계획을 먼저 적습니다.
4. 해당 인텐트의 `evals/` 단일 턴 평가와 `backend/test_results/agent_quality/` 다중 턴 회귀를 실행합니다.
5. 평가·리뷰를 통과하면 **바꾸기 전** 모델 입력 원문을
   `archive/<slot>__legacy-<version>.md`에 보관하고, `HISTORY.md` Draft를 승인 이력으로 옮깁니다.
6. 여러 슬롯·공유 규칙을 함께 되돌려야 하면 `archive/variants.json`에 기준선 ID와 덮어쓸
   경로만 기록합니다. 현재 파일을 복제해 새 폴더를 만들지 않습니다.
7. `meta.yaml`의 슬롯 버전도 함께 갱신합니다.

사소한 오탈자·주석 변경은 Archive 기준선을 만들지 않아도 됩니다. 반대로 분류 결과,
구조화 필드, 답변 근거, 페르소나처럼 사용자에게 보이는 행동이 달라지면 이력과 평가 근거를
남깁니다.

## 버전 정책

모든 슬롯 버전은 따옴표로 감싼 `MAJOR.MINOR.PATCH` 형식(예: `"2.1.0"`)으로
`meta.yaml`에 기록합니다. 실행 Trace에도 같은 값이 남습니다. 단순히 `1`, `2`, `3`처럼
정수만 계속 올리지 않습니다.

| 변경 수준 | 올리는 자리 | 예시 | 기준 |
| --- | --- | --- | --- |
| 큰 정책·계약 변화 | MAJOR | `2.3.4` → `3.0.0` | Intent 판별 기준, 출력 스키마, 데이터 근거처럼 기존 평가 기준을 새로 잡아야 하는 변화 |
| 규칙·예시 추가 또는 판단 보정 | MINOR | `2.0.4` → `2.1.0` | 특정 발화·예외 처리를 추가하거나 분류/추출 결과가 달라지는 변화 |
| 문구·오탈자·모호성 보정 | PATCH | `2.1.0` → `2.1.1` | 의도한 판단·출력 계약은 유지하면서 지침 표현만 명확하게 하는 변화 |

행동에 영향을 주지 않는 주석·문서 설명만의 수정은 버전을 올리지 않습니다. MINOR 이상을
올릴 때는 기존 모델 입력 원문을 Archive에 보관하고 `HISTORY.md`에 변경·평가 근거를
기록합니다. PATCH라도 실제 모델 입력 문장을 바꾸면 `HISTORY.md`에는 반드시 남깁니다.

## Trace 표기 방식

실행 기록(Trace)의 `prompt_version`은 예전엔 어떤 슬롯이 바뀌어도 그대로인 고정 문자열
하나였습니다. 지금은 **그 턴에 실제로 쓰인 슬롯들의 버전을 조합**해서 남깁니다.

```
agent-interpret-prompts-1.0.16            (이전 — 어느 프롬프트가 실제로 쓰였는지 알 수 없음)
router.classify@2.0.0+info.extract@3.0.0  (현재 — 이 턴이 쓴 슬롯과 버전이 그대로 보임)
```

**조합 방식**: `<슬롯 이름>@<버전>`을 `+`로 이어붙입니다.

- 분류(`router.classify`)는 모든 턴에서 항상 돌아 항상 포함됩니다.
- 그 뒤에 인텐트별 슬롯이 하나 더 붙습니다 — INFO 턴이면 `info.extract`, COMPARE 턴이면
  `compare.extract` 식입니다. 어느 인텐트가 어느 슬롯을 쓰는지는 `registry.py`의
  `INTENT_SLOTS`가 정합니다.
- 각 슬롯의 버전은 그 슬롯 `meta.yaml`의 값을 그대로 읽어옵니다 — 담당자가 자기 인텐트
  폴더만 고쳐도 이 값이 자동으로 따라 바뀝니다.
- 과거 기준선(`TRIPBRANCH_PROMPT_VARIANT`)으로 실행 중이면 뒤에 `+variant:<ID>`가 더 붙어
  "옛 프롬프트로 낸 기록"임을 표시합니다.

자세한 배경과 결정 근거는 `docs/decision-log.md`의 D-065를 참고하세요.

## 과거 기준선으로 서버·평가 실행하기

Archive 원문은 설명용 코드 블록이 아니라 현재 템플릿과 같은 형식의 실제 모델 입력입니다.
`TRIPBRANCH_PROMPT_VARIANT`를 **서버 시작 전에** 지정하면 `variants.json`에 정의된 파일만
현재 파일 대신 로드합니다. 서버가 시작된 뒤에는 같은 세션의 재현성을 위해 기준선을 바꾸지
않습니다.

```bash
cd backend
TRIPBRANCH_PROMPT_VARIANT=router-context@legacy-1.0.0 \
  .venv/bin/python -m uvicorn app.main:app --port 8000
```

다른 터미널에서 같은 기준선 ID를 결과에 명시해 다중 턴 분류 회귀를 실행합니다.

```bash
cd backend
.venv/bin/python -m scripts.evaluate_agent_quality \
  --split dev \
  --prompt-variant router-context@legacy-1.0.0
```

`--prompt-variant`은 평가 클라이언트가 서버 설정을 바꾸지 않습니다. 반드시 실행 중인 서버의
환경변수와 같은 값을 전달해야 하며, 결과의 `summary.json`, `report.md`, `history.csv`에 남아
현재 기준선과 별도로 비교됩니다. 과거 기준선이 선택된 서버의 Trace `prompt_version`에도
`+<기준선 ID>`가 붙습니다.

## 공유 규칙과 경계

`_shared/`는 페르소나·서비스 범위·안전·사실성뿐 아니라 RECOMMEND와 MODIFY가 함께 쓰는
예산·날씨 의도·혼잡도 의도·실내외 규칙, MODIFY와 COMPARE가 함께 쓰는 노출 장소 목록 형식도
관리합니다. 공유 규칙 변경은 `_shared/HISTORY.md`에 원문 이력을 기록하고, 영향을 받는
인텐트의 `HISTORY.md`에는 영향 항목만 연결합니다.

인텐트 간 규칙 충돌, UserConditions·AgentResponse 계약 변경, Tool·Scoring 데이터 계약 변경은
프롬프트 담당자 단독으로 확정하지 않습니다. 소유 패키지 담당자와 합의하고 설계 결정은
`docs/decision-log.md`에 별도로 남깁니다.

## 평가와 RAG

- `evals/`는 빠른 단일 턴 실험용이며 머지의 단독 근거가 아닙니다.
- 다중 턴·되묻기·인텐트 전환 회귀는 기존 `backend/test_results/agent_quality/`를 머지 기준으로 사용합니다.
- RAG를 쓰는 슬롯의 평가 결과에는 모델·프롬프트 기준선 외에 인덱스 버전과 데이터 기준 시각도
  기록합니다. 같은 프롬프트라도 RAG 인덱스가 달라지면 결과를 직접 비교할 수 없습니다.
