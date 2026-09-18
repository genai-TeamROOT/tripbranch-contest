# TripBranch 패키지별 업무 분담

| 패키지 | 담당자 | 한 줄 정리 |
|---|---|---|
| A | 기민 임 | 사용자를 이해하고 Agent를 실행 |
| B | 태화 이 | 상태와 실행 이력을 기억하고 추적 |
| C | KimJinHyoung | 외부 Tool로 현재 상황을 구성 |
| D | 나종원 | 후보를 판단하고 AI 품질을 검증 |

---

## 패키지 A — Request Intelligence·AI 품질

- **담당자**: 기민 임
- **요약**: 사용자를 이해하고 Agent를 실행
- **AI 핵심**: 사용자 의도·조건 해석, 구조화 출력, Agent 실행
- **구현 중심**: LLM 연동, Prompt Routing, 출력 검증, API·모듈 오케스트레이션
- **주요 복합성**: 불안정한 LLM 출력 처리와 B·C·D 모듈 통합

### 포함 기능
| 기능 ID | 내용 |
|---|---|
| AF-01 | 사용자 의도·조건 해석 |
| AF-02 | 구조화 출력·LLM 안정성 |
| AF-11 | AI 품질 평가 체계 |

### 담당 범위
사용자 원문 → 인텐트·조건 추출 → 구조화 출력 검증 → 오류·모호함 처리 → 기대 결과와 실제 결과 평가

**포함 작업**
- 5개 인텐트 정의와 판별
- 위치·카테고리·시간·날씨 선호 조건 추출
- 누락·모호 조건 탐지
- ADD·REPLACE·REMOVE 해석
- 인텐트별 프롬프트 라우팅
- JSON 출력 검증과 재시도
- 확인이 필요한 상태 반환
- 요청 이해 Fixture
- 인텐트·조건 추출 품질 평가
- 추천·Memory 평가를 위한 공통 평가 기준 관리

> LLM을 직접 다루고, 결과의 정확성까지 책임을 짐

---

## 패키지 B — Agent State·Memory·LLMOps

- **담당자**: 태화 이
- **요약**: 상태와 실행 이력을 기억하고 추적
- **AI 핵심**: 다중 턴 상태, 추천 이력, 실행 추적·재현
- **구현 중심**: 조건 병합, Memory, 버전 Registry, Trace, A/B 결과 관리
- **주요 복합성**: 상태·이력·버전의 일관성과 실험 재현성 유지

### 포함 기능
| 기능 ID | 내용 |
|---|---|
| AF-03 | 다중 턴 조건·상태 관리 |
| AF-04 | 추천 이력·재추천 Memory |
| AF-12 | LLMOps·A/B 실험 |

**한 줄 정리**: 대화가 이어져도 조건과 추천 이력을 일관되게 유지하고, 각 실행의 버전·Trace·실험 결과를 재현 가능하게 관리한다.

### AI 핵심
다중 턴 Agent State / 조건 Memory / 추천·거절 이력 Memory / 재추천 상태 관리 / 실행 식별·Trace / Prompt·Scoring·Variant 버전 관리 / A/B 실행 결과 추적 / 평가 재현성

### 구현 중심
Agent State 모델 / 조건 병합·갱신 모듈 / 추천·거절 이력 모델 / 세션·실행 식별자 / Memory Repository 인터페이스 / Version Registry / Agent Trace Payload / 실행 메타데이터 수집 / 실험 결과 저장·집계 구조 / 동일 실행 조건 재현 기능

### 주요 복잡성
- 여러 턴에 걸친 조건이 누락되거나 중복될 수 있음
- 조건 추가·교체·삭제 순서에 따라 결과가 달라질 수 있음
- 이전 추천과 거절 후보를 정확히 구분해야 함
- 오래된 외부 Context를 현재 정보처럼 사용하지 않아야 함
- Prompt·Scoring·Variant 버전이 실행 결과와 정확히 연결되어야 함
- A/B 결과를 동일한 입력과 Context 기준으로 비교할 수 있어야 함
- DB 저장 실패가 추천 실행을 막지 않도록 해야 함

### 입력
이전 Agent State / 패키지 A가 해석한 조건 변경 연산 / 사용자가 확인한 final_conditions / 추천된 장소 ID / 거절한 장소 ID / Prompt·Scoring·Variant 버전 / LLM·Tool·Scorer 실행 메타데이터

### 출력
현재 Agent State / 병합된 최종 조건 / 이전 추천·거절 ID 목록 / 재추천용 Context 참조 / session_id / run_id / trace_id / 실행 버전 정보 / A/B 실행 결과 연결 정보 / 평가 재현에 필요한 실행 스냅샷

### 담당 범위
- 이전 조건 유지 / 신규 조건 추가 / 기존 조건 교체 / 조건 삭제
- 추천·거절 장소 이력 관리 / 중복 추천 방지용 제외 ID 관리 / "다른 곳" 요청의 재추천 상태 관리
- 요청·실행·Trace 식별자 생성
- Prompt·Template 버전 관리 / Scoring·가중치 버전 관리 / Variant 버전 관리
- 지연 시간·토큰·오류 유형 기록 / Agent 실행 Trace
- A/B 실행 결과 저장·집계 / 동일 입력·Context·버전 재현 지원

### 주요 산출물
Agent State Schema / 조건 병합 모듈 / 추천·거절 이력 모델 / Memory 인터페이스 / 세션·실행·Trace 식별 구조 / Prompt·Scoring·Variant Registry / Agent Trace Payload / 실행 메타데이터 모델 / 실험 결과 저장 인터페이스 / A/B 결과 집계 구조 / 재현성 검증 기능

### 책임 경계
**하는 일**
- 패키지 A가 해석한 변경 연산을 실제 State에 적용한다.
- 상태·이력·실행 버전을 저장하고 반환한다.
- 실험 실행 결과를 연결하고 재현 가능하게 관리한다.

**하지 않는 일**
- 사용자 자연어의 의미를 직접 해석하지 않는다.
- 외부 API를 호출하거나 현재 Context를 생성하지 않는다.
- 추천 점수나 품질 합격 기준을 결정하지 않는다.
- Prompt Variant의 내용과 품질 기준을 단독으로 결정하지 않는다.

**경계 예시**
- A: `REMOVE category=cafe` 해석 → B: State에서 category=cafe 제거
- D: 좋은 추천 결과의 평가 기준 정의 → B: 어떤 Variant와 버전으로 실행됐는지 기록
- C: 현재 외부 Context 생성 → B: 해당 Context의 실행 참조와 fetched_at 기록

### 난이도 판단
**높음** — Memory와 상태는 모든 후속 실행에 영향을 준다. 여기에 버전·Trace·A/B 재현성까지 포함되므로 데이터 일관성과 백엔드 구조 설계 난이도가 높다.

---

## 패키지 C — Tool Intelligence·External Context

- **담당자**: KimJinHyoung
- **요약**: 외부 Tool로 현재 상황을 구성
- **AI 핵심**: Tool Planning, 실시간 외부 Context, 부분 실패 대응
- **구현 중심**: 위치·날씨·장소 Tool, 병렬 실행, Context Builder, Fallback
- **주요 복합성**: 다양한 외부 API 연동과 불완전·실패 데이터 처리

### 포함 기능
| 기능 ID | 내용 |
|---|---|
| AF-06 | Tool Planning·실행 제어 |
| AF-07 | 위치·날씨·장소 Tool |
| AF-08 | Context Builder·Tool Fallback |

**한 줄 정리**: 확정 조건에 필요한 외부 Tool을 선택·실행하고, 실시간 위치·날씨·장소 정보를 Agent가 사용할 Context로 구성한다.

### AI 핵심
Tool Planning / Tool-using Agent / Tool 실행 오케스트레이션 / External Context Engineering / 실시간 정보 재조회 / 부분 실패 대응 / Degraded Context / 외부 근거 데이터 제공

### 구현 중심
Tool Registry·Factory / Stub·실제 Tool 교체 / Geocoding Provider / KMA Weather Provider / Place Search·Detail Provider / 운영시간 Mapper / 순차·병렬 Tool 실행 / 중복 호출 방지 / Context Builder / Timeout·빈 결과·부분 실패 처리 / 현재 데이터 재조회 / 외부 데이터 상태·출처·조회 시각 관리

### 주요 복잡성
- Tool마다 요청·응답 구조와 오류 형태가 다름
- 지오코딩 결과가 날씨·장소 조회의 선행 조건이 됨
- 날씨와 장소 조회는 병렬 실행이 가능함
- 일부 Tool이 실패해도 추천을 계속할 수 있어야 함
- 현재 정보와 과거 Context가 섞이지 않아야 함
- 운영시간 형식과 장소 정보가 불완전할 수 있음
- 실제 Provider와 Stub이 같은 인터페이스를 지켜야 함
- 외부 API Timeout·빈 응답·쿼터 문제를 처리해야 함

### 입력
패키지 B에서 확정된 현재 조건 / 위치 문자열 또는 좌표 / 검색 반경 / 필요한 외부 Context 필드 / 과거 Context와 fetched_at / 이전 Tool 실행 상태 / Provider 설정

### 출력
정규화된 위치 Context / 현재 날씨 Context / 장소 후보 목록 / 장소 상세·운영시간 원본 데이터 / 거리 계산에 필요한 좌표 / Tool별 성공·실패 상태 / 데이터 출처 / fetched_at / 사용 불가능한 필드 목록 / 후보 부족 판단에 필요한 데이터 / Degraded Context

### 담당 범위
- 필요한 Tool 선택 / 이미 있는 정보에 따른 Tool 생략
- Tool Registry·Factory / Stub·실제 Provider 교체
- Geocoding Tool / 위경도·KMA 격자 변환 / Weather Tool / Place Search Tool / Place Detail Tool / 운영시간 조회·Mapping
- Tool 선행 관계 관리 / 순차·병렬 실행 / 중복 호출 방지
- 위치·날씨·장소 결과 병합 / 현재 정보 재조회 / 과거·현재 Context 구분
- Timeout·빈 결과·부분 실패 처리(날씨 실패 시 날씨 없는 Context, 일부 장소 실패 시 성공 후보 유지)
- 후보 개수와 외부 데이터 상태 반환

### 주요 산출물
Tool Planner / Tool Registry·Factory / Geocoding Tool / Weather Tool / Place Search·Detail Tool / 외부 응답 Mapper / Tool Orchestrator / Agent Context Schema / Context Builder / 현재 정보 재조회 구조 / Tool Fallback 정책 / Degraded Context / Provider·Mapper 테스트 / Stub·실제 Provider 계약 테스트

### 책임 경계
**하는 일**
- 어떤 외부 Tool이 필요한지 결정한다.
- Tool을 실제로 실행하고 외부 사실을 수집한다.
- 수집한 결과를 하나의 Agent Context로 조립한다.

**하지 않는 일**
- 사용자 요청의 의미를 직접 판단하지 않는다.
- 조건을 Agent State에 저장하지 않는다.
- 날씨·거리·영업시간의 최종 추천 점수를 결정하지 않는다.
- 후보의 최종 순위를 결정하거나 추천 문장을 확정하지 않는다.
- 전체 Agent API 응답을 조립하지 않는다.

**경계 예시**
- C가 제공: 현재 날씨 / 장소 좌표 / 운영시간 원본 / 데이터 상태와 조회 시각 / 후보 개수
- D가 결정: 날씨 적합도 점수 / 거리 점수 / 영업시간 점수 / 후보 제외와 최종 순위

### 난이도 판단
**높음** — 외부 API 연결뿐 아니라 Tool Planning, 병렬 실행, 부분 실패, 현재 정보 갱신, Context Engineering까지 포함한다. 단순 API 담당보다 훨씬 넓은 Tool-using Agent 책임 영역이다.

---

## 패키지 D — Recommendation Intelligence·AI Quality

- **담당자**: 나종원
- **요약**: 후보를 판단하고 AI 품질을 검증
- **AI 핵심**: 후보 판단, 추천 순위, Evidence, AI 품질 평가
- **구현 중심**: 후보 정규화, Feature, Scoring, Fixture, 평가 Runner
- **주요 복합성**: 데이터 부족 상황의 일관된 추천 판단과 품질 검증

### 포함 기능
| 기능 ID | 내용 |
|---|---|
| AF-09 | 추천 후보 데이터 처리 |
| AF-10 | 추천 Scoring·결과 설명 |
| AF-11 | AI 품질 평가 체계 |

**한 줄 정리**: 외부 후보를 추천 가능한 데이터로 가공하고 점수·순위를 결정하며, 결과가 기대 품질을 충족하는지 평가한다.

### AI 핵심
추천 후보 정규화 / 추천 Feature Engineering / Rule 기반 후보 제외 / 가중치 Scoring / 추천 순위 결정 / 추천 Evidence / 사용자용 결과 설명 기준 / AI 품질 평가 / Fixture·Evaluation Runner / 최종 품질 판정

### 구현 중심
후보 공통 모델 / 카테고리 Mapping / 거리·남은 영업시간 계산 / 날씨 적합도 Feature / 후보 제외 모듈 / Scorer / 날씨 미제공 가중치 재분배 / 후보 부족 판단 / Evidence Payload / 추천 설명 생성 입력 / 고정 Fixture / 평가 Runner / 영역별 품질 지표·판정 로직

### 주요 복잡성
- 여러 Provider의 장소 정보를 같은 기준으로 정규화해야 함
- 영업시간·날씨·거리 값이 없거나 불확실할 수 있음
- Rule·가중치 변경이 추천 순위 전체에 영향을 줌
- 이전 추천·거절 후보를 빠짐없이 제외해야 함
- 추천 이유가 실제 Score·Evidence와 일치해야 함
- 요청 이해·Memory·Tool·추천 품질을 한 평가 체계로 검증해야 함
- A/B 결과에서 품질과 지연·비용을 함께 판단해야 함

### 입력
패키지 C의 Agent Context / 장소 후보 원본·정규화 중간 데이터 / 패키지 B의 이전 추천·거절 ID / 사용자 확정 조건 / 추천 가중치·점수 구간 / Scoring 버전 / 각 패키지의 Fixture 실행 결과

### 출력
정규화된 추천 후보 / 후보별 Feature / 제외된 후보와 제외 이유 / 후보별 Score / 최종 추천 순위 / 추천 Evidence / 추천 이유 생성 입력 / 후보 부족 상태 / Fixture 기대 결과 / 영역별 평가 결과 / 품질 합격·실패 판정 / Variant 품질 비교 결과

### 담당 범위
- 외부 장소 후보 정규화 / 카테고리 Mapping / 거리·반경 Feature / 남은 영업시간 Feature / 날씨 적합도 Feature / 카테고리 우선순위 Feature
- 폐업·영업 종료 제외 / 반경 밖 후보 제외 / 이전 추천·거절 후보 제외
- 카테고리·영업시간·날씨·거리 점수 계산 / 가중치 합산 / 날씨 미제공 시 가중치 재분배 / 운영시간 미확인 후보 처리 / 후보 부족 판단
- Evidence 생성 / 추천 설명의 근거·문장 기준
- 정상·누락·모호 Fixture / 다중 턴·재추천 Fixture / Tool 실패 Fixture / 평가 Runner
- 요청 이해·Memory·Tool·추천 품질 평가 / Variant 품질 비교와 최종 선정 기준

### 주요 산출물
추천 후보 공통 모델 / Feature 생성 규칙 / 후보 제외 규칙 / Scorer v1 / 가중치·점수 구간 정의 / 날씨 미제공 규칙 / 추천 결과·Score·Evidence Schema / 추천 설명 생성 기준 / 고정 Fixture 세트 / 평가 Runner / 품질 지표 / 영역별 평가 리포트 / Variant 비교·선정 기준

### 책임 경계
**하는 일**
- 어떤 후보가 추천 가능한지 판단한다.
- 후보 점수와 순위를 결정한다.
- 무엇이 좋은 AI 결과인지 평가 기준을 정의한다.
- 평가 Runner로 결과를 판정한다.

**하지 않는 일**
- 외부 API를 직접 호출하지 않는다.
- 사용자의 인텐트와 조건을 직접 추출하지 않는다.
- Agent State와 실행 Trace를 저장하지 않는다.
- 각 Variant의 Runtime 실행을 직접 소유하지 않는다.
- 내부 추론 과정이나 Chain-of-Thought를 저장·노출하지 않는다.

**경계 예시**
- D: Fixture·기대 결과·품질 기준 정의 → A·B·C: 자기 영역의 실제 실행 결과 제공
- D: 기대 결과와 실제 결과 비교·판정 → B: 평가 실행의 버전·Trace·결과 저장·재현 관리
- **추천 설명 경계**: D는 추천 순위와 Evidence를 결정 / A 또는 LLM Runtime은 D가 제공한 Evidence를 사용자 문장으로 변환 / LLM은 추천 순위를 새로 결정하거나 Evidence에 없는 이유를 생성하지 않음

### 난이도 판단
**높음** — 후보 가공, Rule·Scoring, 추천 근거, 전체 AI 평가를 함께 책임진다. 구현량이 크지만 Agent Runtime과 외부 Tool 구현은 제외되어 있고, 추천 판단과 품질 검증이라는 하나의 책임으로 일관되게 묶여있다.

---

## 참고: 패키지 외 신규 기능 — 일정 편성(SCHEDULE)

- **내용**: INT-07 SCHEDULE 인텐트의 일정 편성 로직(`app/schedule/`). 후보+조건을
  입력받아 LLM으로 방문 순서·시간 배분을 판단해 반환하는 상태 비의존 모듈.
- **패키지 A/B/C/D와 나란한 지위(별도 letter)를 부여하지 않는다** — 기능 하나만을
  위한 모듈이라 그 정도 무게가 필요하지 않음. 설계 근거는
  `docs/design/int-07-schedule.md` 6.0절 참고.
- 상태 저장소에 접근하지 않고 A가 호출하는 방식은 D 호출과 동일한 구조(입력→계산된
  출력)를 따른다. B 코드·계약 문서는 이 기능으로 인해 변경되지 않는다.

---

## 참고: 경계 관계 요약

- **A → B**: A가 해석한 조건 변경 연산(ADD/REPLACE/REMOVE)을 B가 실제 State에 반영
- **B → C**: B가 확정한 현재 조건을 바탕으로 C가 외부 Tool 실행
- **C → D**: C가 구성한 Agent Context(위치·날씨·장소)를 D가 Feature·Scoring에 활용
- **D → A/LLM**: D가 결정한 추천 순위·Evidence를 A 또는 LLM Runtime이 사용자 문장으로 변환
- **B (공통)**: 모든 패키지의 Prompt·Scoring·Variant 버전과 실행 Trace, A/B 실험 결과를 저장·관리