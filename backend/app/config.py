"""TripBranch 백엔드 환경 설정 진입점.

역할: 환경 변수 기반 설정을 한 곳에서 읽어 서비스와 앱 초기화에 제공한다.
입력: 프로세스 환경 변수와 선택적인 .env 값.
출력: 앱 전역에서 재사용할 Settings 인스턴스.
호출 시점: 앱 부팅 또는 provider/API 키가 필요한 서비스 초기화 시 사용된다.
TODO: 실제 외부 API 연동 시 provider별 캐시 설정을 추가한다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.recommendation_limits import (
    DEFAULT_RECOMMENDATION_CANDIDATE_LIMIT,
    DEFAULT_RECOMMENDATION_RESULT_LIMIT,
    MAX_RECOMMENDATION_CANDIDATE_LIMIT,
    MIN_RECOMMENDATION_LIMIT,
)

ProviderMode = Literal["fake", "real"]
# 장소 후보 "검색"은 항상 PLACE_PROVIDER를 따르고, 후보별 상세·운영정보만 이 값으로
# 출처를 고른다. supabase는 미리 구축된 places 테이블, tour_api는 상세 API 직접 호출.
PlaceDetailsSource = Literal["supabase", "tour_api"]
# Package B의 State 저장소 백엔드. memory는 Phase 1 인메모리, supabase는
# Phase 2 DB 저장소(app/state/supabase_store.py). 서버 재시작 시 상태 보존이
# 필요해지는 시점에 supabase로 전환한다.
StateStoreBackend = Literal["memory", "supabase"]


# backend/.env. 상대경로로 두면 저장소 루트에서 서버를 띄웠을 때 .env를 찾지 못하고
# 오류 없이 전 Provider가 fake로 뜨므로, 실행 위치와 무관하게 같은 파일을 읽는다.
_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


class Settings(BaseSettings):
    # populate_by_name: validation_alias가 붙은 필드(TOUR_API_SERVICE_KEY, 폐지된 LLM
    # 모델 설정)를 테스트에서 필드명으로도 넣을 수 있게 한다. 별칭만 받으면 필드명을 쓴
    # 인자가 extra="ignore"에 조용히 먹혀 기본값인 채로 통과한다.
    model_config = SettingsConfigDict(
        env_file=_ENV_FILE, extra="ignore", env_ignore_empty=True, populate_by_name=True
    )

    app_env: str = "local"

    # 브라우저에서 이 API를 부를 수 있는 출처. 쉼표로 여러 개를 준다.
    #
    # 기본값은 지금까지 코드에 박혀 있던 값 그대로다. 설정으로 뺀 이유는 워킹트리를
    # 여러 개 띄울 때다 — 두 번째 프론트엔드는 5173을 못 쓰므로 다른 포트로 뜨는데,
    # 그러면 교차 출처가 되어 브라우저가 막는다. 백엔드는 멀쩡하고 curl도 되는데
    # 화면만 안 되는 모양이라 원인을 찾기 어렵다.
    cors_allow_origins: str = "http://localhost:5173"

    # Provider selection: 개별 값이 비어 있으면 provider_mode를 공통 기본값으로 사용한다.
    provider_mode: ProviderMode = "fake"
    llm_provider: ProviderMode | None = None
    weather_provider: ProviderMode | None = None
    place_provider: ProviderMode | None = None
    geocoding_provider: ProviderMode | None = None
    local_search_provider: ProviderMode | None = None
    concentration_provider: ProviderMode | None = None
    # 서울시 실시간 데이터는 특정 INFO 질문에서만 필요하고 별도 키를 쓰므로, 공통
    # PROVIDER_MODE=real을 따라 자동 호출하지 않는다. 사용할 환경에서만 명시적으로
    # SEOUL_CITYDATA_PROVIDER=real로 켠다.
    seoul_citydata_provider: ProviderMode = "fake"
    holiday_provider: ProviderMode | None = None
    # 비용이 발생할 수 있으므로 공통 PROVIDER_MODE를 상속하지 않고 명시적으로
    # real을 켤 때만 외부 경로 API를 호출한다.
    #
    # 이동수단마다 벤더가 다르므로(도보 카카오, 자동차 네이버) 하나씩 따로 켠다.
    # 상속 관계를 두지 않는 이유도 같다 — 한 값이 여러 벤더를 켜면, 한쪽 키만 가진
    # 설정이 쓰지도 않는 벤더의 키를 요구하며 부팅에 실패한다. 새 이동수단은 여기에
    # 한 줄씩 추가하고, 기존 이름(TRAVEL_ROUTE_PROVIDER)은 도보 스위치로 유지한다.
    travel_route_provider: ProviderMode = "fake"
    travel_route_driving_provider: ProviderMode = "fake"
    travel_route_transit_provider: ProviderMode = "fake"
    # 직선거리 fallback 예상시간에 쓸 보행속도(m/s).
    walking_speed_mps: float = Field(default=1.2, gt=0)
    # 자동차 fake의 직선거리 예상시간에 쓸 속도(m/s). 20km/h는 반경 산정이 비도보
    # 요청에 쓰는 가정과 같은 값이다(recommendation_transform._OTHER_KM_PER_MIN).
    # fake 값은 채점에 쓰이지 않으므로(STRAIGHT_LINE_ESTIMATE는 걸러진다) 정밀도가
    # 아니라 반경 가정과의 일관성만 맞춘다.
    driving_speed_mps: float = Field(default=5.5, gt=0)
    # 대중교통 fake도 같은 20km/h 가정을 쓴다 — 반경 산정이 비도보 요청을
    # 이동수단으로 가르지 않고 _OTHER_KM_PER_MIN 하나로 처리하기 때문이다.
    transit_speed_mps: float = Field(default=5.5, gt=0)
    travel_route_max_concurrency: int = Field(default=5, ge=1, le=10)
    # SCHEDULE에서 이동수단 미지정·대중교통 명시 구간을 도보로 연결할 최대 예상시간.
    # 이 값을 넘는 구간은 대중교통 추정으로 전환한다. 도보 명시는 전환하지 않는다.
    #
    # TP-217은 계약과 추정 생성기까지만 만들었고, 이 값을
    # `estimate_schedule_travel_edges()`에 넘기는 호출자는 아직 없다. 일정 편성이
    # 이 생성기를 부르기 전(TP-215·TP-216)까지는 값을 바꿔도 동작이 달라지지 않는다.
    schedule_walk_transfer_threshold_min: int = Field(default=20, ge=1, le=120)

    # 한 번의 실측 요청에서 실제 경로 API로 잴 일정 구간 수 상한.
    #
    # 일정 하나가 3~5곳이면 구간은 2~4개다. 기본 12는 유력 일정 3개까지는 그대로
    # 실측된다는 뜻이다. 상한을 넘는 구간은 호출하지 않고 추정값으로 남으므로,
    # 이 값을 올리면 외부 호출 수가 그만큼 늘어난다. 후보 전체 행렬(10곳이면
    # 90간선)을 실측하지 않겠다는 것이 TP-216·TP-217이 함께 정한 방향이다.
    schedule_max_measured_segments: int = Field(default=12, ge=1, le=50)

    # 상세·운영정보 조회 출처. PLACE_PROVIDER=fake이면 Fake Provider가 상세까지
    # 담당하므로 이 값은 무시된다.
    #
    # 기본값을 tour_api에서 supabase로 바꿨다. 추천 경로는 후보 **전량**의 상세를
    # 받아야 순위를 매길 수 있다 — 하드 필터(영업 종료)와 잔여 운영시간 Feature가
    # 운영시간을 요구해서 "상위 5곳만 받기"가 성립하지 않는다. 그래서 이 값이 곧
    # 호출 수를 정한다(안국역 반경 2km 실측, 2026-08-31):
    #
    #   후보 10곳 | supabase 2회 | tour_api 21회(detailCommon2 10 + detailIntro2 10)
    #   후보 30곳 | supabase 2회 | tour_api 61회
    #
    # tour_api는 tour_api_daily_call_limit(오퍼레이션별 1000)을 후보 30 기준 33요청
    # 만에 소진한다. supabase는 places를 content_id 목록으로 한 번에 읽어 후보 수와
    # 무관하게 1회다. validate_provider_config()가 tour_api + 높은 후보 한도 조합을
    # 부팅에서 막는 이유가 이것이다.
    #
    # 신선도 절충은 D-099가 "추천 쪽이 따로 판단할 일"로 남겨둔 것인데, 재보니
    # 문제가 아니었다 — 활성 8,007곳 전량이 상세 조회 30일 이내이고(place_sync_
    # detail_ttl_days와 같은 값) 68%가 7일 이내다. 운영시간·주차·요금은 자주 바뀌는
    # 값이 아니고, TourAPI를 직접 불러도 같은 출처다.
    place_details_source: PlaceDetailsSource = "supabase"

    # Package B State 저장소 백엔드. 기본값은 Phase 1 인메모리다.
    state_store_backend: StateStoreBackend = "memory"

    # 조기 반환 경로(Tool/Scoring 없이 끝나는 턴 — GENERAL·OUT_OF_SCOPE·되묻기 등)를
    # LangGraph 그래프로 태울지 여부(docs/design/langgraph-adoption.md §6.1).
    # 기본 on이지만, 이관은 출력이 같아야 하는 작업이라 문제가 보이면 이 값 하나로
    # 즉시 기존 경로로 되돌린다 — off면 compose_chat_message()를 직접 호출한다.
    use_langgraph_early_return: bool = True

    # 추천 파이프라인(Tool 조회 -> Scoring -> SCHEDULE 편성/추천 마무리)을 그래프로
    # 태울지 여부(3단계). 위와 같은 이유로 되돌릴 스위치를 따로 둔다 — 조기 반환과
    # 파이프라인은 범위가 달라 한쪽만 끄고 싶을 수 있다.
    use_langgraph_pipeline: bool = True

    # INFO의 실시간 지역 조회(주차·지하철·버스·행사·교통·상권, 서울시 폐쇄목록
    # 121/82개)를 LLM 에이전트 루프로 태울지 여부(로드맵 24번, 강의교재 90강
    # ReAct 패턴). 기본 off — 이 provider 최초의 실제 function calling 경로라
    # 검증 전에는 기존 단발 조회(no_data 되묻기)가 안전한 기본값이다. 켜면 no_data
    # 시 곧장 되묻지 않고 LLM이 다른 지역명으로 스스로 재시도한 뒤 문장까지 쓴다.
    agentic_realtime_info: bool = False

    # 취향 근거 벡터 검색 사용 여부. 기본 off인 이유는 임베딩 모델이 선택
    # 의존성(`pip install -e ".[embeddings]"`)이고 서버 프로세스에 상주하기
    # 때문이다 — 실측 RSS 537MB, 적재 9.4초(2026-08-19). 모델을 올릴 수 없는
    # 배포에서도 서버는 떠야 하므로 켜는 쪽을 명시적 선택으로 둔다.
    #
    # **후기·블로그에서 뽑은 데이터를 쓰는 기능 전체의 스위치다**(2026-09-19).
    # 처음에는 임베딩 검색만 막았는데, 같은 출처의 기능이 따로 살아 있어 끈
    # 배포에서도 후기 기반 결과가 사용자에게 나갔다. 끄면 아래가 모두 멈춘다.
    # - 추천 순위의 취향 축: 임베딩 검색(`get_place_evidence_provider`)과 취향 태그
    #   채점(`RealRecommendationProvider._taste_tag_matches_for`). taste 키가 가중치에
    #   아예 없어 순위가 "취향 없음"과 같다. 계정에 저장한 취향도 읽지 않는다
    #   (저장값은 지우지 않는다).
    # - 추천 카드의 `preference_tags`(화면의 "장소별 방문자 취향 태그" 표).
    # - 상세 카드의 `preference_insights`("방문자 후기에 나타난 특징")와
    #   `ai_reason`("AI가 추천하는 이유", POST /chat/place-details/reason) — DB도
    #   LLM도 부르지 않는다.
    # - 후기로 답하는 INFO 질의(아래 `review_answer_enabled`) — 개요 답변으로 바뀐다.
    # - 프론트의 "취향 설정" 메뉴와 /preferences 화면(GET /api/features로 알려준다).
    taste_evidence_enabled: bool = False

    # 후기로 답하는 INFO 질의(question_type=review_opinion)의 스위치.
    # `taste_evidence_enabled`와 따로 두는 이유는 두 기능이 같은 검색을 쓰지만
    # 실패했을 때 사용자가 보는 것이 다르기 때문이다 — 추천은 취향 축이 빠진
    # 순위가 나가고, 이쪽은 답변 자체가 "후기에서 확인하지 못했다"가 된다.
    # 끄면 그 질문도 기존 상세 조회(관광 API 소개글)로 답한다 — 개요 질문
    # (general_info)으로 바꿔 답하므로 "후기에서 확인하지 못했다"고 말하지 않는다.
    # 검색 Provider가 `taste_evidence_enabled`에도 묶여 있어, 그쪽을 끄면 이 값과
    # 무관하게 같은 경로로 간다.
    review_answer_enabled: bool = True

    # IP별 요청 빈도 제한. `/api/chat`은 인증 없이 호출할 수 있고 호출마다 LLM
    # 요금이 발생해서, 공개 배포에서는 그 조합이 그대로 비용 노출이 된다.
    #
    # **기본이 꺼짐인 이유가 둘이다.** 하나는 켜는 쪽을 명시적 선택으로 두는
    # 이 파일의 관례(`taste_evidence_enabled`와 같다). 다른 하나는 테스트다 —
    # 테스트는 같은 클라이언트에서 채팅 요청을 연달아 보내는데, 기본이 켜짐이면
    # 그 테스트들이 429를 맞는다. 공개 배포의 `.env`에서만 켠다.
    rate_limit_enabled: bool = False
    rate_limit_requests: int = 20
    rate_limit_window_seconds: int = 60
    # 돈이 나가는 경로만 막는다. `/api/health`를 넣으면 배포 워크플로우의
    # 헬스체크가 30회 폴링하다 막힌다.
    rate_limit_path_prefixes: str = "/api/chat"

    # 장소 사진 분위기 기능의 스위치. 축 점수 조회(발화 경로)와 사진 최근접
    # 검색(사진 경로)을 함께 켜고 끈다. 기본 off인 이유는 취향 쪽과 같다 —
    # 사진 경로가 SigLIP을 서버 프로세스에 상주시키기 때문이다.
    #
    # 켜도 인코더가 없으면 발화 경로만 돈다. 두 경로를 따로 끄지 않는 이유는
    # 축 점수와 사진 검색이 같은 테이블·같은 축을 쓰기 때문이다 — 하나만 켜서
    # 얻는 게 없다.
    place_mood_enabled: bool = False

    # 사진 검색에서 전체 평균 벡터를 빼고 비교할지(D-115). 사진 경로에만
    # 걸리고 발화 경로(축 점수)와는 무관하다.
    #
    # 기본 on인 이유는 사람 눈가림 채점에서 48.2% → 53.2%로 올랐기 때문이다.
    # 교체된 자리만 보면 빠진 곳 27.5% 대 들어온 곳 45.0%이고, 실제 사용 조건인
    # 직접 찍은 사진에서 44.3% → 51.9%로 개선이 더 크다.
    #
    # **유의성은 0.05 언저리다**(p = 0.0481 / 0.0730 / 0.0812). 질의가 28장뿐이라
    # 표본을 늘려 다시 봐야 한다. 그럼에도 켜 둔 것은 방향이 세 시험에서 모두
    # 같고 나빠진 지표가 없으며, 되돌리려면 이 값만 끄면 되기 때문이다 —
    # 적재된 벡터는 그대로다.
    #
    # 끄면 조회가 빨라진다(184ms → 60ms, 5,465곳 기준). 전체를 훑으므로 장소
    # 수에 정비례한다.
    place_mood_mean_center_enabled: bool = True

    # 사진 검색 순위에서 벡터 유사도와 분위기 축을 섞는 비율(TP-206).
    # 1.0이면 지금처럼 유사도만 본다. 0.5면 반반이다.
    #
    # **1.0이 기본인 이유는 아직 값을 정하지 못했기 때문이다.** 실패한 결과가
    # 모두 축이 가르는 차원에서 갈렸으므로 섞을 값어치는 있어 보이지만, 얼마가
    # 좋은지는 사람 채점으로 정해야 한다. 정하기 전에 켜면 근거 없는 숫자가
    # 서비스에 남는다(유사도 컷을 두지 않은 것과 같은 이유, D-094).
    #
    # 0은 쓸 수 없다 — 축이 다섯 개뿐이라 "애초에 같은 종류인가"를 구분하지 못한다.
    place_mood_axis_weight: float = 1.0

    # 사진 검색 결과를 VLM(Gemini)에게 다시 줄 세우게 할지(D-117, TP-214).
    #
    # **기본 off인 이유는 돈이 들기 때문이다.** 검색 한 번에 16~47원이고, 하루
    # 500회면 월 24만원이다. 품질은 눈가림 채점에서 31.6% → 38.5%로 올랐다
    # (p = 0.171, 후보 12곳 기준). 임베딩만으로 개선하려는 아홉 번의 시도 중
    # 잡음 바닥(2.6%p)을 넘긴 것은 이것과 평균 빼기(D-115)뿐이다.
    #
    # 실패해도 검색은 죽지 않는다 — 임베딩 순서를 그대로 낸다.
    place_mood_rerank_enabled: bool = False

    # 재랭킹에 쓸 모델. 비우면 llm_fast_model_name을 따른다.
    #
    # **flash급이어야 한다.** gemini-3.5-flash-lite로 내리면 개선이 +6.8%p에서
    # +0.9%p로 사라진다(TP-213). 속도는 5.5배 빨랐지만 그 손잡이가 곧 품질을
    # 없애는 손잡이였다.
    place_mood_rerank_model_name: str | None = None

    # VLM에 보낼 후보 수. **보여줄 수(5곳)보다 넉넉해야 한다.**
    #
    # **보내는 수와 보여주는 수가 같으면 이득이 0이다.** 재랭커는 받은 후보의
    # 순서만 바꾸므로 뽑히는 곳 자체가 그대로인데, 쓰는 지표(상위 N칸 중 몇 칸이
    # "비슷하다"인가)는 순서와 무관하다. 실측에서도 뽑힌 곳이 그대로였던 질의
    # 10장의 이득은 정확히 +0.0%였다.
    #
    # **상위 5곳 기준으로 직접 잰 값이다**(TP-214). 질의 39장을 같은 조건에 놓고
    # 후보 수만 바꿨다.
    #
    #     후보 6곳    29.7% → 31.8%   +2.1%p   p = 0.128   겹침 4.6/5
    #     후보 8곳    29.7% → 33.8%   +4.1%p   p = 0.050   겹침 3.9/5
    #     후보 10곳   29.7% → 35.4%   +5.7%p   p = 0.006   겹침 3.3/5
    #
    # 6곳은 겹침이 4.6/5라 거의 바뀌지 않는다 — 여유가 1곳뿐이라 재랭커가 할 일이
    # 없다.
    #
    # **12곳으로 늘릴 근거는 없다.** 상위 3곳 기준으로 네 판을 견주면 순서가
    # 뒤죽박죽이고(6곳 +7.7 · 8곳 +6.0 · 10곳 +10.3 · 12곳 +6.8) 유일한 12곳
    # 실측이 10곳보다 낮았다. 상위 3곳은 한 칸이 33%p라 눈금이 굵어 잡음에
    # 흔들린다 — 같은 조건이어야 할 판들끼리 4.3%p 벌어진다.
    #
    # 토큰은 후보 1곳당 1,083개씩 붙고 고정분이 1,279개다(실측, 사진 해상도는
    # 무관). 문턱까지 걸었을 때 검색당 6곳 16원 · 8곳 21원 · 10곳 26원이다.
    place_mood_rerank_candidate_count: int = 10

    # 1위 유사도가 이 값 미만이면 VLM을 부르지 않는다(TP-214).
    #
    # **방향이 직관과 반대다** — 임베딩이 헤맬 때가 아니라 잘 찾았을 때 부른다.
    # 1위 유사도가 낮다는 것은 DB에 닮은 곳이 아예 없다는 뜻이라, 후보가 전부
    # 안 맞는 곳이고 순서를 바꿔봐야 나아질 것이 없다. 오히려 임베딩이 그나마
    # 낫게 잡아둔 것을 흐트러뜨린다(나빠지는 질의 7장 → 3장).
    #
    #     문턱 없음   호출 100%   35.4%   +5.7%p   p = 0.006   40원
    #     0.500       호출  74%   33.8%   +4.1%p   p = 0.008   30원
    #     0.525       호출  64%   33.8%   +4.1%p   p = 0.008   26원
    #     0.550       호출  54%   32.8%   +3.1%p   p = 0.061   22원
    #
    # **0.525가 0.50과 성적이 같은데 14% 싸다.** 0.55부터는 도움이 되는 질의까지
    # 잘라내 유의성을 잃는다.
    #
    # **이 값은 place_mood_mean_center_enabled가 켜져 있을 때의 눈금이다**(D-115).
    # 평균을 빼지 않으면 유사도 분포가 달라져 이 문턱이 뜻을 잃는다.
    #
    # 질의 39장·채점자 한 명으로 고른 값이라 0.525와 0.50을 통계로 가르지는
    # 못했고(성적이 같다), 싼 쪽을 골랐다. 실사용 로그가 쌓이면 다시 잰다.
    place_mood_rerank_min_top_similarity: float = 0.525

    # 사진 임베딩 모델을 서버 기동 때 미리 올릴지. off면 첫 사진 요청이 적재
    # 시간을 그대로 뒤집어쓴다. place_mood_enabled가 꺼져 있으면 무시된다.
    place_mood_warmup_enabled: bool = False

    # 실시간 인구 혼잡도 조회에서 최근접 대체가 일어났을 때, 실제 해석된 장소
    # 이름을 서울시 API에 한 번 더 직접 던져(probe) "우리 121곳 스냅샷엔 없는데
    # 서울시 API는 지원하는 지역"을 찾아내는 기능의 스위치다(TP-141, D-084).
    # 응답은 바꾸지 않고 개발자 화면 배너로만 알린다. probe 자체가 서울시 API
    # 호출을 하나 더 만들므로, 트래픽이 예상보다 늘면 배포 없이 끌 수 있게 둔다.
    seoul_area_staleness_probe_enabled: bool = True

    # LLMOps 관측(Langfuse) 스위치. **두 개로 나눠 둔 것이 요점이다.**
    # langfuse_enabled는 "전송을 하느냐", langfuse_capture_content는 "발화·응답
    # 원문을 실어 보내느냐"다. 하나로 묶으면 배포 환경에서 지연·토큰만 보고
    # 원문은 빼는 선택을 할 수 없다.
    #
    # 둘 다 기본 off다. 지금은 실사용자가 없어(로컬 개발만) 나가는 게 팀원 자기
    # 발화뿐이지만, 그 조건에서 정한 기본값이 배포 이후까지 살아남으면 남의
    # 발화가 그대로 외부로 나간다. 켜는 쪽을 명시적 선택으로 둔다.
    # 자세한 근거는 package_D/[계획] Langfuse 도입 §6.3.
    langfuse_enabled: bool = False
    langfuse_capture_content: bool = False
    # 세 번째 스위치. `Principal.user_id`(Supabase 신원 토큰의 sub)를 trace에
    # 실을지다. 이걸 켜면 사용자별 비용·지연·실패율이 보이고, 한 사람이 같은 걸
    # 몇 번 다시 물었는지도 보인다.
    #
    # **원문과 별개 축이라 스위치를 나눴다.** capture_content가 꺼져 있어도
    # user_id는 mask를 타지 않는다(trace 속성이다). 즉 "발화는 가리고 신원만
    # 외부에 쌓는" 상태가 실수로 만들어질 수 있어, 묶어두면 오히려 위험하다.
    #
    # 기본 off다 — 개인정보를 외부 SaaS에 올리는 것은 팀 합의가 먼저다.
    # 코드는 먼저 들어가되 켜는 결정은 사람이 한다.
    langfuse_capture_user_id: bool = False
    # 네 번째 스위치. 프롬프트 원문을 레포가 아니라 Langfuse에서 읽을지다.
    # **전송(langfuse_enabled)과 별개 축이다** — 관측을 끄고 프롬프트만 쓰거나
    # 그 반대가 가능해야 한다. 클라이언트는 하나를 공유하되 tracing_enabled로
    # 전송만 따로 끈다(langfuse_tracing._shared_client).
    #
    # 꺼져 있으면 지금까지와 완전히 같다 — 디스크에서만 읽는다. 켜도 Langfuse가
    # 죽거나 프롬프트가 없으면 디스크로 되돌아간다(fallback). 즉 이 스위치는
    # "어디를 먼저 보나"지 "어디에서만 읽나"가 아니다.
    langfuse_prompts_enabled: bool = False
    # 가져온 프롬프트를 몇 초 동안 재사용할지. 만료되면 SDK가 **백그라운드로**
    # 갱신하고 그동안은 기존 값을 즉시 돌려주므로 요청이 막히지 않는다.
    #
    # 0으로 두면 캐시를 아예 안 쓴다 — 호출마다 왕복(실측 중위 51ms)이 붙으므로
    # 디버깅용이지 상시 설정이 아니다.
    langfuse_prompt_cache_ttl_seconds: int = 60
    # 이 서버가 만든 turn에 `developer:<값>` 태그를 붙인다. 팀원 넷이 같은 Langfuse
    # 프로젝트를 공유하면 목록에서 누가 만든 턴인지 구분할 수 없어서 둔다.
    #
    # **`app_env`로 대신하지 않는다.** 그건 관측 라벨이 아니라 기능 게이트다 —
    # `main.py`가 정확히 `"local"`과 비교해서 개발자 Ops 라우터(`/api/dev/*`)를
    # 등록할지 정한다. 사람 이름을 붙이려고 값을 바꾸면 그 라우터가 통째로 사라진다.
    #
    # 기본값은 빈 문자열이고, 그때는 태그를 아예 안 붙인다 — 안 적은 사람의 트레이스가
    # `developer:` 로 오염되지 않아야 필터가 제 역할을 한다.
    langfuse_developer: str = ""
    langfuse_public_key: str = Field(default="", repr=False, exclude=True)
    langfuse_secret_key: str = Field(default="", repr=False, exclude=True)
    # 리전별로 호스트가 다르다. 한국에서는 JP가 지연이 가장 낮다.
    langfuse_base_url: str = "https://jp.cloud.langfuse.com"

    # 짧고 구조화된 판단(의도 분류·조건 추출)에 사용할 모델 묶음. 비용·지연이
    # 중요한 경로라 Lite를 기본으로 두되, 일시적 5xx/타임아웃에는 Flash로 폴백한다.
    llm_fast_model_name: str = "gemini-3.5-flash-lite"
    llm_fast_fallback_model_names: str = "gemini-3.5-flash"

    # 사용자에게 보여 줄 문장·비교·일정처럼 품질 비중이 큰 생성 경로의 모델 묶음.
    # 5xx/타임아웃 시에는 Lite로만 폴백하며, Real→Fake 전환은 하지 않는다(D-042).
    llm_generation_model_name: str = "gemini-3.5-flash"
    llm_generation_fallback_model_names: str = "gemini-3.5-flash-lite"

    # 장소 상세 카드의 "AI가 추천하는 이유" 1~2문장 생성 전용 모델 묶음.
    #
    # **세 번째 티어를 만든 이유**는 이 호출이 fast·generation 어느 쪽 특성도 아니기
    # 때문이다. 판단이 아니라 문장 생성이라 fast(구조화 판단)가 아니고, 사용자가
    # 기다리는 답변 본문이 아니라 카드 클릭 후 한 줄이라 generation의 품질 예산을
    # 쓸 이유도 없다. 사실 근거(태그·후기 문장)를 전부 쥐어주고 말투만 맡기는 작업이다.
    #
    # gemini-3.1-flash-lite가 기본값인 근거는 실측이다(2026-09-09, 한옥카페 선운각
    # 실데이터 1호출): 입력 494 · 출력 64토큰, 지연 1,465ms, 사고 토큰 0. 출력 단가가
    # 3.5-flash-lite의 40% 싸고($1.50 대 $2.50 /1M) 이 호출은 출력 비중이 크다.
    # 두 lite 모두 2027-01-01에 단가가 2배가 되지만 서로의 비율은 그대로다.
    #
    # 폴백은 5xx/타임아웃 대비다. 폴백까지 실패하면 카드는 기존 고정 문장 한 줄만
    # 보여준다 — 이 문장이 없어도 화면이 성립하도록 만들었다(routes/chat.py).
    place_reason_model_name: str = "gemini-3.1-flash-lite"
    place_reason_fallback_model_names: str = "gemini-3.5-flash-lite"
    # 끄면 상세 카드가 기존 고정 문장만 보여준다. LLM 호출이 통째로 사라지므로
    # 비용·지연 회수의 손잡이이자, 문장 품질 문제가 생겼을 때의 즉시 차단 수단이다.
    place_reason_enabled: bool = True

    # 구간 이동수단 판정(judge_travel_modes) 전용 모델 묶음. 일정 구간과 추천 후보가
    # 같은 판정을 쓴다(TP-227).
    #
    # 떼어낸 이유는 generation 스위치 하나가 일정 편성·답변 등 호출 9개를 묶고 있어서,
    # 이 판정에 맞는 모델로 바꾸면 답변 품질까지 함께 바뀌기 때문이다. 비우면 generation
    # 묶음을 그대로 따른다.
    #
    # 기본값 gemini-3.1-flash-lite의 근거는 실측이다(2026-09-15, 34입력 × 5회, 추론
    # MINIMAL). 3.5-flash와 채점 칸 틀림이 0 대 0으로 같고, 흔들림 0 대 2, p50 0.97초 대
    # 1.31초, 1,000호출당 $0.47 대 $2.83이다. test_results/mode_judge_thinking_2026-09-14/,
    # test_results/mode_judge_prompt_ablation_2026-09-15/.
    #
    # **이 판정에만 해당한다.** 같은 모델이 FAST 티어에서는 탈락했다 — 조건 추출은 대등하지만
    # 문맥 없는 첫 턴 `비 와서 실내로 바꿔줘`를 MODIFY로 5/5 분류한다
    # (test_results/classify_gap_2026-09-15/, extract_homonym_2026-09-15/, fast_tier_2026-09-15/).
    #
    # **비워 두지 않고 기본값을 둔 이유는 프롬프트와의 짝 때문이다.** mode_judge.select
    # 1.1.0이 추가한 문장은 3.1-lite의 첫 줄 불일치를 없애지만, 3.5-flash에서는 추천
    # 판정의 흔들림을 2에서 5로 늘린다. 기본값이 비어 있으면 레포 프롬프트를 읽는 환경
    # (LANGFUSE_PROMPTS_ENABLED=false)이 머지 순간 "1.1.0 + generation 모델"이라는 잰 적
    # 없는 나쁜 조합이 된다. 기본값을 3.1-lite로 두면 어느 순서로 배포돼도 조합이
    # "3.1-lite + 1.0.0"(잰 결과: 틀림 0 · 흔들림 0) 아니면 "3.1-lite + 1.1.0"이다.
    mode_judge_model_name: str | None = "gemini-3.1-flash-lite"
    # 비우면 generation 묶음 전체(지금 운영 모델)를 폴백으로 쓴다. 주 모델과 겹치는 이름은
    # 빠진다. `.env`의 빈 값은 설정하지 않은 것으로 읽히므로(env_ignore_empty) 폴백을 끄는
    # 방법은 없다 — 폴백까지 실패하면 호출부가 거리 규칙으로 되돌리므로 끌 이유도 없다.
    mode_judge_fallback_model_names: str | None = None

    # 음성 입력을 텍스트로 바꿀 때 사용할 Gemini 모델. 음성 전사는 채팅 답변 생성과
    # 독립 호출이라, 비용·지연 특성에 맞는 멀티모달 모델을 따로 둔다. gemini-3.5-flash는
    # 2026-08-18 한국어 대표 발화 실측에서 전사를 확인한 기본값이다.
    gemini_audio_model_name: str | None = None

    # 폐지된 단일 모델 설정. 값을 읽어 쓰는 곳은 없고, .env에 남아 있는지 감지하려고만
    # 선언한다 — extra="ignore"라 그냥 지우면 옛 설정이 조용히 안 먹는 상태가 되고,
    # `.env`만 보고 "이 모델로 돌고 있다"고 오판하게 된다. 실제로 역할별 설정이
    # 도입된 뒤 이 두 값은 파싱만 되고 아무도 읽지 않는 상태로 남아 있었다.
    # 검사는 validate_provider_config()에 있다(실패는 첫 요청이 아니라 부팅에서, D-042).
    legacy_llm_model_name: str | None = Field(
        default=None, validation_alias=AliasChoices("LLM_MODEL_NAME")
    )
    legacy_llm_fallback_model_names: str | None = Field(
        default=None, validation_alias=AliasChoices("LLM_FALLBACK_MODEL_NAMES")
    )

    # Only required when the corresponding *_provider above is set to "real".
    llm_api_key: str = Field(default="", repr=False, exclude=True)
    weather_api_key: str = Field(default="", repr=False, exclude=True)
    tour_api_service_key: str = Field(default="", repr=False, exclude=True)
    # Cloud Translation Basic(v2) 호출 전용 키. Gemini/Maps 키와 역할·API 제한을
    # 분리한다. 영어 UI를 쓰지 않는 한국어 요청에는 읽거나 요구하지 않는다.
    google_translate_api_key: str = Field(default="", repr=False, exclude=True)
    seoul_open_data_api_key: str = Field(default="", repr=False, exclude=True)
    naver_map_client_id: str = Field(default="", repr=False, exclude=True)
    naver_map_client_secret: str = Field(default="", repr=False, exclude=True)
    naver_local_search_client_id: str = Field(default="", repr=False, exclude=True)
    naver_local_search_client_secret: str = Field(default="", repr=False, exclude=True)
    kakao_map_rest_api_key: str = Field(default="", repr=False, exclude=True)
    supabase_url: str = ""
    supabase_secret_key: str = Field(default="", repr=False, exclude=True)

    # Real provider HTTP behavior (ignored by fake providers).
    external_api_timeout_seconds: float = 10.0
    external_api_retry_count: int = 2

    # LLM(Gemini) 호출 전용 타임아웃(초). 비어 있으면(기본값) EXTERNAL_API_TIMEOUT_SECONDS를
    # 그대로 쓴다(하위 호환) — 팀이 Gemini 호출 지연 때문에 EXTERNAL_API_TIMEOUT_SECONDS를
    # 25로 올렸다가, TourAPI/Naver/Supabase(장소 상세·상태 저장소 등)까지 같은 값을
    # 물려받아 실패 시 사용자가 그만큼 오래 기다리게 된다는 문제가 논의로 나와 분리했다
    # (2026-08-11). LLM은 구조화 출력 생성 특성상 원래도 오래 걸릴 수 있고 재시도·모델
    # 폴백까지 있어 더 긴 값이 자연스럽지만, Tool/DB 조회는 원래 짧게 끝나야 해서 같은
    # 값을 강제하면 안 된다.
    llm_api_timeout_seconds: float | None = None

    # 관측용 일일 호출 한도. data.go.kr은 오퍼레이션 단위로 한도가 걸리므로
    # (2026-08-07 areaBasedList2 소진) 게이지도 오퍼레이션별로 이 값과 대조한다.
    # 호출을 막는 값이 아니라 개발자 패널 게이지의 기준선이다.
    tour_api_daily_call_limit: int = 1000
    concentration_daily_call_limit: int = 1000

    # 장소 상세 화면에 보여줄 사진 수 상한. 저장소에 적재된 사진과 detailImage2로
    # 즉석 조회한 사진에 같은 값을 쓴다 — 출처에 따라 장수가 달라지면 사용자에게는
    # 같은 화면이 이유 없이 달라 보인다.
    place_photo_display_limit: int = Field(default=10, ge=1, le=20)

    # detailImage2 조회 결과를 프로세스 메모리에 들고 있는 시간(초). 기본 6시간.
    #
    # DB에 쓰지 않기로 해서 이 캐시가 유일한 재사용 수단이다. 없으면 같은 장소를
    # 두 번 열 때 두 번 부른다. 특히 "사진이 없는 장소"가 잦아 그 빈 응답을 반복해서
    # 받는 것이 문제다 — 적재된 5,465곳 중 2,749곳(50%)이 detailImage2가 비어 대표
    # 이미지로 대체된 장소였다(2026-08-31 실측).
    #
    # 서버를 다시 띄우면 사라진다. 신선함은 이 값이 아니라 재적재 주기가 정한다.
    place_photo_api_cache_ttl_seconds: int = Field(default=6 * 60 * 60, ge=0)

    # Recommendation pipeline budgets
    recommendation_result_limit: int = Field(
        default=DEFAULT_RECOMMENDATION_RESULT_LIMIT,
        ge=MIN_RECOMMENDATION_LIMIT,
        le=MAX_RECOMMENDATION_CANDIDATE_LIMIT,
    )
    recommendation_candidate_limit: int = Field(
        default=DEFAULT_RECOMMENDATION_CANDIDATE_LIMIT,
        ge=MIN_RECOMMENDATION_LIMIT,
        le=MAX_RECOMMENDATION_CANDIDATE_LIMIT,
    )

    # Place synchronization policy.
    place_sync_page_size: int = 100
    # 상세조회 동시성과 호출 간 최소 간격(초). 둘 다 TourAPI의 초당 한도를 피하려고
    # 있고, 역할이 다르다 — 동시성은 동시에 떠 있는 요청 수, 간격은 초당 몇 개가
    # 나가는지를 정한다. detailIntro2 응답이 100ms대라 동시성 1에서도 간격이 없으면
    # 초당 8회쯤 나간다(2026-08-10 실측). 그래서 429를 실제로 막는 것은 간격 쪽이다.
    #
    # 기본값을 5 / 0에서 1 / 0.5로 내렸다. 그 조합이 이 서비스키에서 두 번 연속
    # 429를 냈다 — 2026-08-20 중구 892건 중 669건 실패, 2026-08-22 종로구 16건과
    # 중구 2건 실패. 1 / 0.5는 추측이 아니라 중구 892건을 실패 0으로 끝낸 값이다
    # (6분 16초. 5 / 0은 3분 01초였지만 669건을 다시 불러야 했다).
    #
    # 빠르게 돌려야 하면 .env에서 올린다. 기본값은 안전한 쪽에 둔다.
    place_sync_detail_concurrency: int = 1
    place_sync_detail_min_interval_seconds: float = 0.5
    place_sync_detail_ttl_days: int = 30
    place_sync_area_code: str = "11"
    place_sync_district_code: str = "110"

    # 정규식이 못 읽은 휴무 문구를 LLM으로 읽을지. **적재 배치에서만 본다** —
    # 화면 요청은 저장된 파싱 결과를 읽으므로 이 값과 무관하고, 켜도 응답이
    # 느려지지 않는다.
    #
    # **기본 on인 이유는 끄면 지금의 결함이 그대로 남기 때문이다.** `2,4주
    # 일요일`처럼 주차가 섞인 문구를 정규식이 못 읽어, 쉬는 날에도 문 여는
    # 곳으로 채점되는 장소가 399곳 있다(2026-09-03 실측).
    #
    # 돈은 거의 안 든다. 전량을 부르지 않고 **원문이 있는데 정규식이 아무것도
    # 못 읽은 것만** 부른다 — 활성 8,007곳 중 399곳, 고유 문구로는 104종이다.
    # 빠른 모델을 쓰고 사고 예산은 0이다.
    #
    # 실패해도 적재는 죽지 않는다 — 정규식 결과를 그대로 저장한다. 즉 끈 것과
    # 같은 상태로 돌아갈 뿐이다.
    closure_extract_enabled: bool = True

    # Fake-provider-only knobs
    # 기상청 코드 그대로 받는다(D-051) — 4 흐림 / 0 강수 없음이 중립 조합이다.
    fake_weather_sky_code: str = "4"
    fake_weather_precipitation_type: str = "0"
    fake_current_datetime: str = "2026-07-15T14:00:00"

    @model_validator(mode="after")
    def validate_recommendation_limits(self) -> Settings:
        if self.recommendation_result_limit > self.recommendation_candidate_limit:
            raise ValueError(
                "RECOMMENDATION_RESULT_LIMIT은 RECOMMENDATION_CANDIDATE_LIMIT 이하여야 합니다."
            )
        return self

    @property
    def resolved_rate_limit_path_prefixes(self) -> tuple[str, ...]:
        """빈 항목을 뺀 제한 대상 경로 접두사."""
        return tuple(
            prefix.strip()
            for prefix in self.rate_limit_path_prefixes.split(",")
            if prefix.strip()
        )

    @property
    def resolved_cors_allow_origins(self) -> list[str]:
        """빈 항목을 뺀 허용 출처 목록."""
        return [
            origin.strip()
            for origin in self.cors_allow_origins.split(",")
            if origin.strip()
        ]

    @property
    def resolved_llm_provider(self) -> ProviderMode:
        return self.llm_provider or self.provider_mode

    @property
    def resolved_llm_timeout_seconds(self) -> float:
        """LLM_API_TIMEOUT_SECONDS가 없으면 EXTERNAL_API_TIMEOUT_SECONDS로 폴백한다
        (하위 호환 — 기존에 EXTERNAL_API_TIMEOUT_SECONDS만 설정해 쓰던 환경도 그대로
        동작한다)."""
        return self.llm_api_timeout_seconds or self.external_api_timeout_seconds

    @property
    def resolved_llm_fast_models(self) -> list[str]:
        """의도 분류·조건 추출에 사용할 Gemini 시도 순서."""
        return self._model_list(self.llm_fast_model_name, self.llm_fast_fallback_model_names)

    @property
    def resolved_llm_generation_models(self) -> list[str]:
        """사용자 응답·비교·일정 생성에 사용할 Gemini 시도 순서."""
        return self._model_list(
            self.llm_generation_model_name,
            self.llm_generation_fallback_model_names,
        )

    @property
    def resolved_place_reason_models(self) -> list[str]:
        """상세 카드 추천 이유 문장 생성에 사용할 Gemini 시도 순서."""
        return self._model_list(
            self.place_reason_model_name,
            self.place_reason_fallback_model_names,
        )

    @property
    def resolved_mode_judge_models(self) -> list[str]:
        """구간 이동수단 판정에 사용할 Gemini 시도 순서. 미설정 시 generation과 같다."""
        if not self.mode_judge_model_name:
            return self.resolved_llm_generation_models
        if self.mode_judge_fallback_model_names is None:
            models = [self.mode_judge_model_name, *self.resolved_llm_generation_models]
        else:
            models = self._model_list(
                self.mode_judge_model_name, self.mode_judge_fallback_model_names
            )
        # 같은 모델을 두 번 시도하는 것은 폴백이 아니라 재시도다.
        return list(dict.fromkeys(models))

    @property
    def resolved_gemini_audio_model_name(self) -> str:
        """음성 전사용 모델. 미설정 시 빠른 판단 모델 1순위를 재사용한다."""
        return self.gemini_audio_model_name or self.llm_fast_model_name

    @property
    def resolved_place_mood_rerank_model_name(self) -> str:
        """사진 검색 재랭킹용 모델. 미설정 시 빠른 판단 모델 1순위를 재사용한다."""
        return self.place_mood_rerank_model_name or self.llm_fast_model_name

    @staticmethod
    def _model_list(primary: str, fallback_names: str) -> list[str]:
        fallbacks = [name.strip() for name in fallback_names.split(",") if name.strip()]
        return [primary, *fallbacks]

    @property
    def resolved_weather_provider(self) -> ProviderMode:
        return self.weather_provider or self.provider_mode

    @property
    def resolved_place_provider(self) -> ProviderMode:
        return self.place_provider or self.provider_mode

    @property
    def resolved_geocoding_provider(self) -> ProviderMode:
        return self.geocoding_provider or self.provider_mode

    @property
    def resolved_local_search_provider(self) -> ProviderMode:
        return self.local_search_provider or self.provider_mode

    @property
    def resolved_concentration_provider(self) -> ProviderMode:
        return self.concentration_provider or self.provider_mode

    @property
    def resolved_seoul_citydata_provider(self) -> ProviderMode:
        return self.seoul_citydata_provider

    @property
    def resolved_holiday_provider(self) -> ProviderMode:
        return self.holiday_provider or self.provider_mode

    @property
    def resolved_place_details_source(self) -> PlaceDetailsSource:
        """fake 장소 모드에서는 상세도 Fake Provider가 담당한다."""
        if self.resolved_place_provider == "fake":
            return "tour_api"
        return self.place_details_source


settings = Settings()
