# place_preference_evidence 데이터 딕셔너리

## 개요

`public.place_preference_evidence`는 장소 상세 카드에서 보여 줄 대표 후기 문장입니다. 원문 전체를 저장하지 않고, 각 장소·취향 태그·판정별로 최대 두 문장만 보관합니다.

| 필드 | 타입 | NULL 허용 | 정의 | 값 예시 | 활용 예시 |
| --- | --- | --- | --- | --- | --- |
| `content_id` | text | 아니오 | `places`와 연결하는 장소 식별자입니다. | `126499` | 상세 카드의 장소를 식별합니다. |
| `preference_code` | text | 아니오 | 연결된 취향 태그 코드입니다. | `walk` | 태그별 근거를 묶습니다. |
| `polarity` | text | 아니오 | 문장 판정값입니다. | `positive` | 긍정 후기와 주의 언급을 구분합니다. |
| `evidence_rank` | smallint | 아니오 | 같은 장소·태그·판정 안의 대표 문장 순서입니다. | `1` | 각 판정별 최대 2개만 표시합니다. |
| `document_id` | text | 아니오 | 후기·게시글 단위 식별자입니다. | `doc_ebc...` | 같은 문서의 중복 문장을 막습니다. |
| `source_evidence_id` | text | 아니오 | 전처리 결과의 근거 문장 식별자입니다. | `evi_ae5...` | 재생성·감사 시 원본 근거를 대조합니다. |
| `evidence_text` | text | 아니오 | 사용자에게 보여 줄 전처리된 후기 문장입니다. | `나무 그늘이 있어 걷기 좋아요.` | 상세 카드의 대표 근거로 표시합니다. |
| `source_type` | text | 아니오 | 근거 출처 유형입니다. | `google_review` | Google 리뷰·네이버 블로그를 구분합니다. |
| `source_url` | text | 예 | 원문으로 이동하는 링크입니다. | `https://blog.naver.com/...` | 사용자가 출처를 확인할 수 있게 합니다. |
| `match_strength` | smallint | 아니오 | 태그 사전과 문장의 매칭 강도입니다. | `3` | 대표 문장 우선순위 선정에 사용합니다. |
| `extraction_version` | text | 아니오 | 추출 규칙·사전 버전입니다. | `place-preference-1.0.0` | 재추출 결과를 구분합니다. |
| `created_at` | timestamptz | 아니오 | 최초 적재 시각입니다. | `2026-08-31T10:00:00+09:00` | 적재 시점을 확인합니다. |
| `updated_at` | timestamptz | 아니오 | 마지막 갱신 시각입니다. | `2026-08-31T10:00:00+09:00` | 재적재 여부를 확인합니다. |

## 사용 시 유의사항

- `polarity`는 규칙 기반 판정값이므로 사실 판정이나 별점이 아닙니다.
- 화면에서는 `negative`와 `mixed`를 단정적인 부정 평가가 아닌 “주의가 필요한 언급”으로 표현합니다.
- 대표 문장은 서로 다른 `document_id`에서 우선 선정합니다.
