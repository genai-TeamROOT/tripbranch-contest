# 프론트엔드 Intent 분류·조건 추출 테스트 화면

`/api/interpret`이 반환하는 Intent 분류·조건 추출(`LLMOutput`) 결과를 프론트에서 바로
확인하기 위한 개발용 테스트 패널이다. 기존 "추천 시작하기" 흐름과는 완전히 별개로 동작한다.

## 실행 방법

```bash
npm run dev
```

- Frontend: http://localhost:5173
- Backend: http://localhost:8000 (`LLM_PROVIDER=real`이어야 실제 Gemini 결과 확인 가능)

홈 화면(`/`) 하단의 **"개발용 Intent · 조건 추출 테스트"** 박스를 사용한다.

## 사용 방법

1. 상단 프리셋 버튼(RECOMMEND / RECOMMEND · 날씨 모호 / MODIFY · 전체 거절 /
   MODIFY · 조건 변경 / GENERAL / OUT_OF_SCOPE) 중 하나를 클릭하면 발화·컨텍스트가
   자동으로 채워진다. 직접 입력해도 된다.
2. MODIFY/COMPARE를 테스트하려면 "이전 추천 이력 있음" 체크박스와 "노출된 장소 수"를
   맞춰야 한다. MODIFY의 조건 변경 확인용으로 "현재 조건" JSON 박스에 그 케이스에
   필요한 필드만 채운다(관계없는 필드를 채우면 LLM이 실제 조건으로 오인할 수 있음).
3. "인텐트/조건 추출 테스트" 버튼을 누르면 `intent`/`status`/되묻기 메시지/
   `modify_type`+`changed_fields`/`out_of_scope` 요약과 `LLMOutput` 원본 JSON 전체가
   그대로 표시된다.

## 관련 코드

- `frontend/src/components/IntentDebugPanel.tsx`
- `frontend/src/api/trip.ts`의 `interpretDebug()`
- 대표 문장 50개를 한 번에 검증하는 배치 스크립트:
  `backend/scripts/test_intent_classification.py` → 결과는
  `backend/test_results/intent_classification_results.csv`,
  `intent_classification_summary.csv`
