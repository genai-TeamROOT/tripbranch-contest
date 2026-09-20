"""TripBranch 백엔드 API의 요청/응답 스키마 정의.

역할: Pydantic 모델로 API 계약과 프론트엔드가 기대하는 데이터 형태를 고정한다.
입력: 라우터로 들어온 원시 JSON payload와 서비스가 반환하는 dict/model 값.
출력: 검증된 요청 모델, 직렬화 가능한 응답 모델, 공통 오류 모델.
호출 시점: FastAPI 요청 검증, 응답 직렬화, 서비스/테스트 타입 확인 때 사용된다.
TODO: 실제 도메인 확정 후 문자열 카테고리와 날씨 값은 Enum으로 좁힌다.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.domain.travel_route import TravelMode
from app.state.service import StateApplyResponse


class HealthResponse(BaseModel):
    status: str


class FeaturesResponse(BaseModel):
    """화면이 켜고 끌 기능 목록(GET /api/features).

    **설정값을 그대로 노출하지 않고 화면에 필요한 판정만 싣는다.** 지금은 후기·블로그
    데이터를 쓰는 기능 묶음 하나다(`settings.taste_evidence_enabled`). 꺼져 있으면
    화면이 "취향 설정" 메뉴와 /preferences를 숨긴다.
    """

    taste_enabled: bool


class ErrorBody(BaseModel):
    code: str
    message: str
    retryable: bool = False
    details: object | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody


class TranscriptionResponse(BaseModel):
    """음성 입력을 Gemini로 전사한 결과.

    전사 텍스트는 이 응답 이후 프론트 입력창에만 채워진다. AgentRequest로 바로
    전달하지 않으므로 사용자가 오인식된 고유명사를 확인·수정한 뒤 기존 채팅 흐름으로
    전송할 수 있다.
    """

    text: str = Field(min_length=1)
    elapsed_ms: int = Field(ge=0)
    model: str


class PhotoSimilarPlace(BaseModel):
    """사진 검색 결과 한 곳."""

    content_id: str
    title: str
    similarity: float
    # 평균에 쓴 사진 수. 1이면 detailImage2가 비어 대표 이미지 한 장으로 대체된
    # 장소이고 벡터가 그 한 장에 좌우된다(D-087). 화면이 신뢰도를 낮춰 보이거나
    # 걸러낼 수 있게 그대로 싣는다.
    photo_count: int
    address: str | None = None
    image_url: str | None = None


class PhotoSimilarPlacesResponse(BaseModel):
    """올린 사진과 분위기가 닮은 장소 목록.

    **유사도는 순위를 위한 값이지 "얼마나 닮았다"의 눈금이 아니다.** 사진끼리의
    경계값을 아직 재지 않아 컷 없이 상위 N곳을 그대로 준다(D-094). 화면에
    백분율로 표시하지 않는다.
    """

    places: list[PhotoSimilarPlace]
    # 어디를 중심으로 찾았는지. 화면이 "{center_name} 주변에서 분위기가 닮은
    # 곳이에요"로 보여준다. 지역명 없이 좌표로만 찾았으면 "현재 위치"다.
    center_name: str
    # 이 검색이 속한 대화. **화면이 세션 없이 보내도 채워져서 돌아온다** — 홈에서
    # 발화 없이 사진부터 올리면 이 응답이 그 대화의 시작이라 서버가 여기서
    # 발급한다. 화면은 이 값을 받아 이후 턴을 같은 대화로 이어야 한다.
    session_id: str
    # 하드 필터를 통과해 사진 검색에 넘어간 후보 수. 0이면 "닮은 곳이 없다"가
    # 아니라 "볼 곳 자체가 없었다"라 화면 문구가 달라져야 한다.
    candidate_count: int
    # 후보 상한(500)에 걸려 잘린 수. 0이 아니면 반경을 좁히는 편이 낫다.
    truncated_count: int = 0
    elapsed_ms: int = Field(ge=0)


class PlaceSearchCandidate(BaseModel):
    """위치 설정 화면의 장소 검색 결과 한 곳."""

    name: str
    address: str | None = None
    road_address: str | None = None
    category: str | None = None
    # 좌표가 없는 후보는 검색 위치로 쓸 수 없어 라우터가 이미 빼고 보낸다.
    # 그래서 여기서는 optional이 아니다.
    latitude: float
    longitude: float


class PlaceSearchResponse(BaseModel):
    """검색어로 찾은 서울 안의 장소 목록."""

    places: list[PlaceSearchCandidate]
    # 좌표는 있는데 서울 밖이라 뺀 수. 0인지 아닌지에 따라 화면 문구가 갈린다 —
    # 0이면 "찾은 곳이 없어요"이고, 0이 아니면 "서울 지역만 검색할 수 있어요"다.
    # 하나로 뭉치면 사용자가 오타를 고쳐야 할지 지역을 바꿔야 할지 알 수 없다.
    outside_service_area_count: int = Field(default=0, ge=0)


class InterpretedConditions(BaseModel):
    location_query: str
    preferred_categories: list[str]
    weather_condition: str | None
    search_radius_km: float


class RecommendationRequest(InterpretedConditions):
    session_id: str | None = None
    run_id: str | None = None
    # 하위 호환용. session_id가 있으면 B가 조회한 값으로 대체된다.
    shown_place_ids: list[str] = Field(default_factory=list)


class TasteEvidenceQuote(BaseModel):
    """취향 검색이 찾은 근거 문장 한 건 — 개발자 디버그 화면용."""

    text: str
    similarity: float


class PreferenceTagSummary(BaseModel):
    """추천 카드에 표시할 장소별 취향 태그 요약."""

    code: str
    label: str
    mention_count: int = Field(ge=0)
    # 이번 발화에서 요청한 취향과 실제 장소 태그가 일치할 때만 true다.
    # 없는 태그를 화면에 만들어내지 않고, 보유 태그의 정렬·강조에만 사용한다.
    is_query_match: bool = False


class PreferenceTagScoreDetail(BaseModel):
    """D가 한 요청 태그를 후보군 안에서 상대 채점한 계산 근거."""

    code: str
    label: str | None = None
    positive_document_count: int = Field(ge=0)
    negative_document_count: int = Field(ge=0)
    candidate_max_positive_document_count: int = Field(ge=0)
    relative_score: float = Field(ge=0, le=1)


class ImageAttribution(BaseModel):
    """사진과 함께 화면에 그려야 하는 출처.

    Google Maps Platform 정책은 사진을 보여줄 때 작성자를 밝히고, 사용자가
    `source_uri`로 원본 사진을 Google 지도에서 볼 수 있게 하라고 요구한다.
    `provider`를 따로 두는 이유는 정책이 "Google Maps"라는 이름을 밝히라고
    하기 때문이다 — 작성자 이름만으로는 어디서 온 사진인지 알 수 없다.
    """

    provider: str = "Google Maps"
    author_name: str
    author_uri: str | None = None
    source_uri: str | None = None


class RecommendationItem(BaseModel):
    place_id: str
    name: str
    category: str
    # TourAPI 신분류 중분류명(한식·전시시설·면세점). `category`가 대분류 코드
    # (`restaurant`·`shopping`)라 화면에 그대로 쓰기 어려워 함께 내려준다.
    #
    # **None이 정상 값이다.** 카드 조회가 실패하면 붙지 않고(썸네일과 같은 이유로
    # 추천 자체는 그대로 살린다), 화면은 그때 `category`로 되돌아간다. 다만 실측에서
    # 추천 후보 7,448건이 모두 소분류 코드로 해석됐다 — 해석 실패 0건, 라벨 41종
    # (2026-09-08, places 스냅샷).
    category_label: str | None = None
    distance_km: float
    remaining_minutes: int | None
    # 그 후보에 실제로 적용된 당일 운영 구간("09:00~18:00"). 프론트가
    # remaining_minutes만으로는 "언제부터"를 표시할 수 없어 함께 내려준다.
    # 운영시간 미확인 후보는 None이다.
    operating_hours_display: str | None = None
    # 실측 경로로 잰 값. 이 값이 있으면 거리 Feature 점수도 직선거리가 아니라 이
    # 소요시간으로 계산된 것이다. 조회에 실패했거나 그 이동수단의 경로 Provider가
    # 아직 없으면 세 필드 모두 None이고, 그때는 distance_km(직선거리)가 유일한
    # 거리 정보다. travel_mode는 어떤 이동수단으로 잰 값인지를 말한다 — 프론트가
    # "도보 이동"인지 다른 수단인지 스스로 추측하지 않게 하려고 함께 내려준다.
    travel_distance_m: int | None = None
    travel_duration_seconds: int | None = None
    travel_mode: TravelMode | None = None
    environment_type: str
    recommendation_reason: str
    explanations: list[str]
    warnings: list[str]
    score: float
    feature_scores: dict[str, float | None]
    weights_used: dict[str, float]
    # 취향 검색이 찾은 근거 문장 전부(유사도 내림차순). taste가 0이어도 검색 자체가
    # 실패한 것과 근거를 못 찾은 것을 구분할 수 있게 항상 채운다 — 빈 리스트면
    # 컷을 넘는 근거가 없었다는 뜻이다. 서비스 화면에는 원문을 직접 노출하지 않되,
    # RECOMMEND/MODIFY 말풍선 생성에는 후보별 일부 문장을 제한적으로 전달할 수 있다.
    taste_evidence: list[TasteEvidenceQuote] = Field(default_factory=list)
    # 태그가 취향 순위에 반영된 경우에만 0~1 값이 있다. 임베딩 유사도와 달리
    # 후보군 안의 positive_document_count 최댓값으로 나눈 상대 점수다.
    taste_tag_score: float | None = Field(default=None, ge=0, le=1)
    taste_tag_label: str | None = None
    taste_tag_documents: int = Field(default=0, ge=0)
    taste_tag_details: list[PreferenceTagScoreDetail] = Field(default_factory=list)
    # 취향 결합점수 계산 과정. embedding_similarity는 환산 전 평균 코사인
    # 유사도이고, embedding_score는 0.43~0.65 구간을 0~1로 편 값이다.
    taste_embedding_similarity: float | None = None
    taste_embedding_score: float | None = Field(default=None, ge=0, le=1)
    taste_combined_score: float | None = Field(default=None, ge=0, le=1)
    # 개발자 패널의 전체 후보 목록에서 원래 D 순위를 보존한다.
    scoring_rank: int | None = Field(default=None, ge=1)
    # 리뷰·블로그 원문은 보내지 않고 장소별 상위 태그와 문서 단위 언급 수만
    # 서비스 화면에 노출한다. 태그 미수집 장소는 빈 배열이다.
    preference_tags: list[PreferenceTagSummary] = Field(default_factory=list)
    # D의 파이프라인은 이미지를 조회하지 않는다 — A가 응답 조립 단계에서 C의
    # RecommendationCardTool(원래 COMPARE용, place_id 배치 조회)로 채워 넣는다
    # (TECH-02: D가 C의 Tool을 직접 부르지 않는다). 채우지 못한 장소는 None이고,
    # 프론트는 그 경우 자리표시 칩을 그린다.
    image_url: str | None = None
    # image_url이 404일 때 프론트가 대신 그릴 주소(places.first_image_url).
    #
    # 주소가 살아 있는지는 여기서 확인하지 않는다 — 추천 한 번에 카드가 5장이라 매
    # 요청마다 외부 확인이 5~10건 붙고 응답이 그만큼 늦어진다. 두 주소를 다 내려보내고
    # 실패한 카드에서만 프론트가 두 번째를 부른다(PlaceThumbnail).
    #
    # 필요한 이유는 image_url이 가리키는 작은 썸네일(firstimage2)만 관광공사 서버에서
    # 사라지는 장소가 있어서다 — 아현시장이 그렇다. 그 장소도 원본(firstimage)은 살아
    # 있고, 상세 카드는 원본을 먼저 고르기 때문에 사진이 나온다. 추천 카드만 비어 보인다.
    image_url_fallback: str | None = None
    # image_url이 Google Places에서 온 경우에만 채워진다(관광공사 이미지가 하나도
    # 없던 장소). **값이 있으면 화면이 반드시 출처를 함께 그려야 한다** — Google
    # 정책이 사진에 작성자 표기와 원본 링크를 요구한다. 지킬 수 없으면 사진 자체를
    # 쓸 수 없으므로, 서버가 출처 없는 사진은 아예 싣지 않는다.
    image_attribution: ImageAttribution | None = None


class TravelOriginToggle(BaseModel):
    """비차단형 전환 제안(D-071). travel_origin이 판정되지 않았고 사용자 위치와
    검색 기준점이 실제로 다를 때만 채워진다 — "안국역 근처에 10분"처럼 발화가
    출발점을 확정하지 않은 요청에서, 답을 먼저 준 뒤 "안국역 기준으로 다시
    보기" 같은 원탭 전환을 조건부로 제안한다. 조사로 이미 확정된 요청
    (travel_origin이 채워진 요청)에는 만들지 않는다 — 되물을 이유가 없다.
    """

    alternative_origin: TravelOrigin
    alternative_origin_name: str


class ExcludedScoringCandidate(BaseModel):
    """하드 필터에서 점수 계산 전에 제외된 후보의 개발자용 요약."""

    place_id: str
    name: str
    category: str
    distance_km: float
    reason: str


class RecommendationResponse(BaseModel):
    recommendations: list[RecommendationItem]
    unverified_recommendations: list[RecommendationItem]
    # 이번 답변에 전환 제안이 있으면 채워진다. 프론트는 이 값이 있을 때만
    # "OO 기준으로 다시 보기" 버튼을 노출한다.
    travel_origin_toggle: TravelOriginToggle | None = None
    elapsed_ms: float = Field(
        ge=0,
        description="추천 파이프라인 시작부터 응답 조립 완료까지의 총 처리시간(ms)",
    )
    # 결과가 0건이고 그 이유가 전부 폐점 후보 제외였을 때만 True. A가 이 값으로
    # "운영중이 아닌 곳도 볼래요" 되묻기를 띄울지 판단한다(recommendation_pipeline.py).
    excluded_all_closed: bool = False
    # 이번 회차에 D의 하드 필터(_is_closed)가 폐점이라 걸러낸 후보 id 전체.
    # excluded_all_closed와 달리 결과가 0건이 아니어도(일부만 폐점) 채워진다.
    # A가 이 값을 B(상태 저장소)에 기록해 다음 회차 후보 수집 시 제외 목록에
    # 반영한다 — 그러지 않으면 노출 이력이 없는 폐점 후보가 매 회차 다시 수집된다
    # (TP-82, docs/design/... 참고). LLM이 생성하지 않고 D가 결정적으로 채운다.
    excluded_closed_place_ids: list[str] = Field(default_factory=list)
    # 사용자에게 보여 주는 5곳과 별개인 개발자 진단용 목록. scoring_candidates는
    # 하드 필터를 통과해 실제 점수를 받은 전체 후보이고, excluded는 점수 계산 전에
    # 빠진 후보라 탈락 사유만 갖는다. 사용자 화면은 두 필드를 렌더링하지 않는다.
    scoring_candidates: list[RecommendationItem] = Field(default_factory=list)
    scoring_excluded_candidates: list[ExcludedScoringCandidate] = Field(default_factory=list)


class ScheduleItem(BaseModel):
    """일정에 포함된 장소 1건. (docs/design/int-07-schedule.md 6.2절)"""

    order: int
    place_id: str
    place_name: str
    estimated_arrival: str
    estimated_duration_min: int
    travel_to_next_min: int | None
    reason: str
    # LLM이 생성하지 않는다 — 프롬프트가 항상 빈 배열로 두라고 지시하고,
    # app.schedule.planner가 estimated_arrival과 후보의 operating_hours_display를
    # 대조해 최종적으로 결정적으로 채운다("구조적 보장 우선" 원칙, basis_note와
    # 같은 이유). 폐점 시각이 지난 도착 예정 스탑을 사용자에게 알리는 용도다
    # (docs/design/int-07-schedule.md 9절, "폐점 스탑 감지" 항목 해소).
    warnings: list[str] = Field(default_factory=list)
    # 다음 장소까지의 이동을 무엇으로 어떻게 잰 값인지. (TP-216)
    #
    # travel_to_next_min만으로는 화면이 이동수단을 알 수 없어 "도보 이동 약 N분"으로
    # 고정 표기해 왔는데, 도보 예상시간이 임계값을 넘는 구간은 편성 단계에서
    # 대중교통으로 전환된다(tools/schedule_travel._select_mode) — 그 구간까지 도보로
    # 적히면 화면이 사실과 다른 말을 한다. RecommendationItem.travel_mode를 함께
    # 내려주는 것과 같은 이유다("프론트가 스스로 추측하지 않게 한다").
    # 일정 화면 카드에 쓸 장소 사진. 후보(RecommendationItem)가 이미 들고 있는 값을
    # 편성 단계에서 그대로 옮겨 담는다 — 화면이 나중에 장소별로 다시 조회하지 않게
    # 하려는 것이다. `/chat/place-details`로도 사진을 얻을 수는 있지만 그 경로는
    # INFO 전체(이름 재해석 + 외부 조회 + 취향 인사이트)를 타므로 정류장 수만큼
    # 부르면 일정을 열 때마다 외부 호출이 그 수만큼 나간다.
    #
    # 부분 재편성의 pinned 항목은 후보에 없어서, 편성 전에 장소 DB에서 사진을 다시
    # 조회해 채운다(agent_runtime._with_pinned_images). 조회하지 못한 장소는 None이고,
    # 프론트는 그때 자리표시를 그린다.
    image_url: str | None = None
    # image_url이 404일 때 대신 그릴 주소. 추천 카드와 같은 규칙이다
    # (RecommendationItem.image_url_fallback 주석 참고).
    image_url_fallback: str | None = None
    # Google Places에서 온 사진이면 출처가 함께 온다. 일정 카드도 같은 사진을
    # 그리므로 같은 표기 의무를 진다(RecommendationItem.image_attribution 참고).
    image_attribution: ImageAttribution | None = None
    # 도보로 이어지는 묶음 번호. 같은 번호끼리 한 묶음으로 그린다. (TP-243)
    #
    # **항목 배열 모양은 그대로 두고 번호만 얹는다.** saved_schedules.payload와
    # session_messages에 옛 모양 스냅샷이 쌓여 있어서 복원 경로가 두 모양을 다
    # 읽어야 하기 때문이다 — 그룹 구조로 바꾸지 않기로 이미 결정했다. 기본값이
    # None이라 이 필드가 없는 옛 스냅샷도 그대로 읽힌다.
    #
    # 묶음 판정은 `budget.cluster_ids_in_order()`가 방문 순서의 이웃 구간만 보고
    # 한다. 묶이지 않은 자리는 None이다.
    cluster_id: int | None = None
    travel_to_next_mode: TravelMode | None = None
    # 그 값이 경로 API 실측인지(True) 직선거리 추정인지(False).
    #
    # ScheduleTravelEdge는 source(provider 이름)와 confidence(high/low) 두 필드로 같은
    # 사실을 말하지만, 화면에 필요한 것은 "실측이냐"는 한 비트뿐이라 여기서 좁힌다 —
    # 어느 provider가 답했는지는 사용자에게 의미가 없고 관측 지표에서 본다
    # (schedule_travel_measured_ratio). 추정 구간에서 시간을 숨기지는 않는다:
    # 일정은 이동시간 없이 성립하지 않으므로 값을 보여주고 추정임을 함께 밝힌다
    # (추천 카드는 실측이 없으면 시간을 아예 말하지 않는다 — 거기서는 거리만으로도
    # 카드가 성립하기 때문이고, 같은 규칙을 일정에 그대로 옮길 수 없다).
    #
    # 이동정보를 아예 못 구한 구간(좌표 없음 → 시간표 폴백 15분)은 mode가 None이고
    # 이 값도 False다. 화면은 mode가 None인 것으로 그 경우를 구분한다.
    travel_to_next_measured: bool = False


class ScheduleBudgetStatus(StrEnum):
    """편성 결과가 사용자가 말한 활동 가능 시간을 지켰는지. (TP-238)

    판정 자체는 app.schedule.budget.classify_budget()이 내린다. **이 열거형이
    app.schedule이 아니라 여기 있는 이유**는 ScheduleResult가 이 타입을 직접
    참조하기 때문이다 — app.schemas가 app.schedule을 import하면 순환이 된다
    (app.schedule.duration이 이미 app.schemas.PlaceType을 읽는다). SCHEDULE-02가
    ScheduleItem을 app.schemas에 둔 것과 같은 이유다.
    """

    WITHIN = "within"
    OVER = "over"
    UNDER = "under"


class ScheduleResult(BaseModel):
    """일정 편성 모듈(app.schedule)의 최종 출력. AgentResponse.schedule에 실린다.

    basis_note는 LLM이 생성하지 않고 A/일정편성모듈이 visit_at 값을 넣어
    고정 템플릿으로 채운다 — 근거 데이터(운영시간·날씨)가 단일 시각 기준이라
    뒷 순서 스탑에는 부정확할 수 있다는 걸 사용자에게 알리는 안내 문구다.
    (docs/design/int-07-schedule.md 6.2.1절)
    """

    items: list[ScheduleItem]
    total_duration_min: int
    route_summary: str
    basis_note: str
    # 보관함에 담겨 있었지만 이번 일정에 넣지 못한 장소 이름 (SCHEDULE-12).
    # 담은 개수가 활동 가능 시간이 허용하는 항목 수 상한(budget.derive_item_range())을
    # 넘었거나, LLM이 재시도 후에도 포함 지시를 지키지 못한 경우에 채워진다.
    # 사용자에게 조용히 빠뜨리지 않고 말풍선으로 알리기 위한 값이라, 화면 문구를
    # 조립하는 쪽(response_composer)이 읽는다. 빈 리스트가 정상이다.
    omitted_saved_place_names: list[str] = Field(default_factory=list)
    # 담겨 있었지만 이번 턴 후보 목록에 아예 없어서 편성 대상이 되지 못한 장소
    # 이름 (SCHEDULE-12). omitted_saved_place_names와 사유가 정반대다 — 이쪽은
    # 시간을 늘리거나 다른 곳을 빼도 들어가지 않는다. 후보 수집(C) 단계에서
    # 안 잡힌 것이라 편성 조건을 바꿔도 결과가 같기 때문이다. 두 사유를 한
    # 리스트에 섞으면 화면이 "시간을 늘려보라"는 잘못된 안내를 하게 된다.
    #
    # **영업시간으로 걸러진 것은 여기 넣지 않는다**(TP-236) — 그쪽은
    # closed_saved_place_names로 간다. 예전에는 둘이 합쳐져 있어서 화면이
    # "문을 닫는 시간이거나 장소 정보를 못 찾은 경우"라는 한 문장으로 뭉갰고,
    # 뒤에 붙는 "시간대를 바꾸면 들어갈 수도 있어요"가 절반에게는 통하지 않는
    # 안내였다. 여기 남는 것은 장소 상세를 못 가져왔거나 좌표가 없는 경우이고,
    # 그쪽은 시간대를 어떻게 바꿔도 결과가 같다.
    absent_saved_place_names: list[str] = Field(default_factory=list)
    # 담겨 있었지만 **방문 시각에 영업하지 않아** D의 하드 필터(_is_closed)가
    # 걸러내서 후보에 못 들어온 장소 이름 (TP-236).
    #
    # A가 RecommendationResponse.excluded_closed_place_ids와 보관함 id를
    # 교집합해 채운다 — D가 이미 결정적으로 계산해 둔 값이라 B가 영업시간을
    # 다시 해석하지 않는다. 다시 해석하면 같은 장소를 두고 D의 판정과 화면의
    # 안내가 갈릴 수 있다.
    #
    # absent_saved_place_names에서 갈라 둔 이유는 **사용자가 할 수 있는 일이
    # 확정적이기 때문**이다. 이쪽은 시간대를 바꾸면 실제로 들어간다. 저쪽은
    # 바꿔도 같다. over_capacity_place_names를 따로 둔 것과 같은 기준이다.
    closed_saved_place_names: list[str] = Field(default_factory=list)
    # 담겨 있었지만 **항목 수 상한**(budget.derive_item_range()의 max)을 넘겨 이번 편성 대상에서
    # 잘린 장소 이름 (TP-223). 담은 순서로 뒤에서부터 잘린다.
    #
    # omitted_saved_place_names와 갈라 둔 이유는 사유가 다르고 사용자가 할 수 있는 일이
    # 다르기 때문이다 — 이쪽은 "보관함에서 다른 곳을 빼면 들어간다"가 확정적으로 참이고,
    # 저쪽은 다시 요청하면 들어갈 수도 있다는 정도다. 예전에는 planner가 두 사유를 한
    # 리스트에 합쳐 넘겨서 화면이 "시간을 늘리거나 다른 곳을 빼고"라는 한 문장으로
    # 뭉뚱그렸고, 사용자는 자기 경우가 어느 쪽인지 알 수 없었다.
    over_capacity_place_names: list[str] = Field(default_factory=list)
    # 보관함에 없었는데 편성에 새로 들어간 장소 이름 (TP-223).
    #
    # 빈 자리를 다른 후보로 채우는 것은 설계된 동작이다(prompts/schedule/plan.md —
    # "남는 자리를 다른 후보로 채우세요"). 다만 "이 장소들로 일정 짜기" 버튼을 누른
    # 사용자에게는 담지 않은 곳이 말없이 끼어든 것으로 보여 버그로 신고됐다(TP-223).
    # 동작을 바꾸는 대신 그 사실을 말하기로 했다.
    #
    # 보관함을 쓰지 않은 턴(must_include가 비어 있음)에는 채우지 않는다 — 그때는 모든
    # 장소가 "새로 찾은 곳"이라 알릴 내용이 아니다.
    added_place_names: list[str] = Field(default_factory=list)
    # 이번 요청에서 일정에 넣을 수 있었던 항목 수 상한 (TP-239).
    #
    # **화면이 이 값을 다시 계산할 수 없어서 실어 보낸다.** 예전에는 버킷 상수라
    # 활동 가능 시간만 있으면 어디서든 같은 답이 나왔다(1~2 / 2~4 / 3~5곳). 지금은
    # 체류 최소값과 이번 후보들의 실제 거리로 계산하므로, 후보를 모르는
    # response_composer는 "한 번에 n곳까지만" 문구의 n을 만들 수 없다.
    #
    # 부분 재편성에서는 None이다 — 그때 개수는 유지 항목과 교체 대상이 정하므로
    # 상한이 관여하지 않는다. 이 필드가 없던 시절의 스냅샷도 None이다.
    item_capacity: int | None = Field(default=None, ge=1)
    # 요청한 활동 가능 시간을 지켰는지에 대한 판정 (TP-238).
    #
    # **판정을 한 곳에서만 내리기 위한 필드다.** 예전에는 response_composer가
    # total_duration_min과 요청 시간을 직접 빼서 두 번 판정했다 — 라벨을 요청값으로
    # 쓸지 한 번, 초과 안내를 붙일지 또 한 번. 편성 쪽에는 목표가 아예 없었다.
    # 지금은 planner가 체류시간을 예산에 맞춘 뒤 그 결과를 판정해 여기 싣고,
    # 화면과 지표가 같은 값을 읽는다.
    #
    # 사용자가 시간을 말하지 않은 턴에서는 None이다 — 판정할 것이 없다.
    # **기본값이 None인 이유는 그것만이 아니다**: saved_schedules.payload와
    # session_messages에 이 필드가 없던 시절의 스냅샷이 쌓여 있어서, 기본값이
    # 없으면 지난 대화·저장한 일정을 여는 복원 경로가 통째로 깨진다.
    time_budget_status: ScheduleBudgetStatus | None = None
    elapsed_ms: float = Field(
        ge=0,
        description="일정 편성 파이프라인 시작부터 응답 조립 완료까지의 총 처리시간(ms)",
    )


class PlaceCandidate(BaseModel):
    """장소 API 원본 응답을 정규화한 공통 후보 모델.

    역할: 어떤 장소 API(TourAPI, 카카오 등)를 쓰든 Mapper가 이 모양으로
    변환해서 Recommendation Service에 넘긴다. Service는 이 모델만 알면 되고
    원본 API 응답 구조를 몰라도 된다.
    """

    place_id: str
    content_type_id: str | None = None
    lcls_systm1: str | None = None
    lcls_systm2: str | None = None
    lcls_systm3: str | None = None
    name: str
    category: str
    latitude: float
    longitude: float
    address: str | None = None
    operating_hours: str | None = None
    raw_source: str = Field(description="어떤 provider가 만든 후보인지 (예: 'tour_api')")


# === LLM Output Schema ===
#
# 아래는 docs/design/conditions-schema.md(§2, §4)와 docs/design/llm-output-schema.md(§3~8)의
# Pydantic 초안을 프로젝트 컨벤션(StrEnum)에 맞춰 옮긴 것. 필드 의미·예시는 두 문서를 참조.
# StatedWeather는 app.domain.models.WeatherCondition(good/neutral/bad, API 날씨)과 이름이
# 겹치지 않도록 사용자 발화 날씨(rain/snow/hot/cold/good)를 가리키는 이름으로 분리했다.


class Intent(StrEnum):
    RECOMMEND = "RECOMMEND"
    SCHEDULE = "SCHEDULE"
    INFO = "INFO"
    MODIFY = "MODIFY"
    COMPARE = "COMPARE"
    GENERAL = "GENERAL"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"


class InteractionMode(StrEnum):
    """사용자가 지금 무엇을 하고 있는지. Intent와 **직교하는 별개 축**이다.

    (docs/design/conversational-layer.md) 상황은 Intent 중 하나가 아니다 —
    "비 오는데 실내 카페 추천해줘"는 RECOMMEND면서 동시에 상황이고, "지쳤는데
    경복궁 지금 붐벼?"는 INFO면서 동시에 상황이다. GENERAL 하위 주제로 넣으면
    이런 발화를 잘못 처리하고, 이미 두 역할(정체성 질문·상식 폴백)을 지고 있는
    GENERAL이 만능 라벨이 된다.

    값을 둘로만 시작하는 것은 의도적이다. 모드를 처음부터 5개씩 만들면 인텐트
    라벨에서 피하려던 과부하를 다른 필드에서 그대로 반복하게 된다. 실제로 구분이
    필요해지는 시점에 늘린다.

    실측(2026-08-30, scripts/test_situational_utterances.py)으로 확인한 현재
    동작: 상황 발화는 한 곳으로 떨어지지 않고 흩어진다 — "다리를 다쳤어"와
    "아 비 오네"는 RECOMMEND(조건 없이 조용히 검색을 시작한다), "너무 지친다"와
    "오늘 진짜 되는 일이 없네"는 OUT_OF_SCOPE/unrelated(거절), "아 오늘
    휴관이래"는 GENERAL. 이 축이 없으면 상황을 알아채는 일 자체가 불가능하다.
    """

    DIRECT_REQUEST = "direct_request"
    SITUATIONAL = "situational"


class OutputStatus(StrEnum):
    COMPLETE = "complete"
    NEEDS_CLARIFICATION = "needs_clarification"


class ModifyType(StrEnum):
    REJECT_ALL = "REJECT_ALL"
    REJECT_SPECIFIC = "REJECT_SPECIFIC"
    CHANGE_CONDITION = "CHANGE_CONDITION"


class WeatherIntent(StrEnum):
    AVOID = "AVOID"
    ENJOY = "ENJOY"
    NO_MENTION = "NO_MENTION"
    IGNORE = "IGNORE"


class ConcentrationIntent(StrEnum):
    """weather_intent와 동일 패턴. concentration-conditions.md §2.1 참고.

    null/IGNORE는 weather_intent와 달리 하드 필터에 관여하지 않으므로
    needs_clarification을 유발하지 않는다.
    """

    AVOID = "AVOID"
    SEEK = "SEEK"
    IGNORE = "IGNORE"


class StatedWeather(StrEnum):
    RAIN = "rain"
    SNOW = "snow"
    HOT = "hot"
    COLD = "cold"
    GOOD = "good"


class Transport(StrEnum):
    WALK = "walk"
    PUBLIC = "public"
    CAR = "car"


class TravelOrigin(StrEnum):
    """이동시간의 출발점 판정. search_center(사실, 어디를 말했는가)와 분리된
    축이다 — "이번 요청에서 그 지명을 어떻게 쓸까"라는 판정만 담는다.

    "안국역에서/까지 10분"처럼 조사가 출발점을 확정하는 발화만 SEARCH_CENTER로
    채운다. "안국역 근처/주변" 같은 목적지 언급이나 조사가 없는 발화는 비워
    둔다(None) — D-067 기본값(사용자 위치 우선, 없으면 검색 기준점)이 그대로
    적용된다. USER_LOCATION은 추출 단계에서는 쓰지 않는다 — 답을 먼저 준 뒤
    "내 위치 기준으로 다시 보기" 전환 버튼(비차단형 되묻기)이 생기면 그
    전환에 쓸 자리로 미리 마련해 둔 값이다.
    """

    USER_LOCATION = "user_location"
    SEARCH_CENTER = "search_center"


class Environment(StrEnum):
    INDOOR = "indoor"
    OUTDOOR = "outdoor"
    ANY = "any"


class Companion(StrEnum):
    SOLO = "solo"
    COUPLE = "couple"
    FRIEND = "friend"
    PARENT = "parent"
    CHILD = "child"
    PET = "pet"


class CompareCriteria(StrEnum):
    TIME = "time"
    # "가까워?"/"거리 차이?"도 여기로 합친다(2026-08-21) — 직선거리 하나만 답하는
    # 것보다, 실제 이동 경로(도보/자동차/대중교통) 소요시간과 실측 거리를 함께
    # 보여주는 쪽이 "이동이 얼마나 용이한지"라는 실제 질문 의도에 더 가깝다.
    TRAVEL_TIME = "travel_time"
    OVERALL = "overall"


class OutOfScopeCategory(StrEnum):
    HARMFUL = "harmful"
    UNRELATED = "unrelated"
    ROLE_REQUEST = "role_request"
    PROMPT_INJECTION = "prompt_injection"


class Severity(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class GeneralTopic(StrEnum):
    SERVICE_IDENTITY = "service_identity"
    TRAVEL_TIP = "travel_tip"
    SEASON_INFO = "season_info"
    AREA_INFO = "area_info"
    PLACE_KNOWLEDGE = "place_knowledge"
    PLANNING_TIP = "planning_tip"
    FOOD_CULTURE = "food_culture"
    TRANSPORT_INFO = "transport_info"


class QuestionType(StrEnum):
    OPERATING_HOURS = "operating_hours"
    FEE = "fee"
    PARKING = "parking"
    FACILITY = "facility"
    EVENT = "event"
    LOCATION_INFO = "location_info"
    GENERAL_INFO = "general_info"
    # 후기·평판을 묻는 질문("뭐가 맛있대?", "카공하기 좋대?"). 관광 API에 없는 것을
    # 묻기 때문에 place_embeddings의 블로그·리뷰 문장으로 답한다. 설비 자체를 묻는
    # 질문은 전언 말투가 붙어도 FACILITY다 - 답하는 데이터가 다르다.
    REVIEW_OPINION = "review_opinion"
    CONCENTRATION = "concentration"
    # 서울시 실시간 도시데이터의 지역·업종별 카드 소비 활동. 특정 매장 자체의
    # 혼잡도가 아니라, 매장 좌표와 가까운 제공 상권의 대체 정보다.
    REALTIME_COMMERCIAL = "realtime_commercial"
    REALTIME_PARKING = "realtime_parking"
    # 공영/시영을 명시한 질문은 GetParkingInfo의 구 단위 최신 대수 경로를 쓴다.
    REALTIME_PUBLIC_PARKING = "realtime_public_parking"
    REALTIME_SUBWAY = "realtime_subway"
    REALTIME_BUS = "realtime_bus"
    REALTIME_EVENT = "realtime_event"
    REALTIME_TRAFFIC = "realtime_traffic"
    # "근처에 화장실 있어?"처럼 주변 공중화장실 위치를 찾는 질문. 그 장소 안에
    # 화장실이 있는지 묻는 건 FACILITY다 — 답하는 데이터가 다르다(전자는 서울시
    # 공중화장실 목록, 후자는 그 관광지의 편의시설 안내).
    PUBLIC_TOILET = "public_toilet"


class PlaceContext(StrEnum):
    EXPLICIT = "explicit"
    FROM_RECOMMENDATION = "from_recommendation"
    FROM_CONVERSATION = "from_conversation"


class PlaceType(StrEnum):
    ATTRACTION = "attraction"
    CULTURAL_FACILITY = "cultural_facility"
    FESTIVAL = "festival"
    LEISURE = "leisure"
    SHOPPING = "shopping"
    RESTAURANT = "restaurant"


class PlaceTag(StrEnum):
    # attraction 하위
    PARK = "공원"
    PALACE = "궁궐"
    MOUNTAIN = "산"
    BEACH = "해변"
    LAKE = "호수"
    VALLEY = "계곡"
    VIEWPOINT = "전망대"
    THEME_PARK = "테마파크"
    ZOO = "동물원"
    ARBORETUM = "수목원"
    TEMPLE = "사찰"
    FORTRESS = "성곽"
    VILLAGE = "마을"
    TRAIL = "둘레길"
    TRADITIONAL_EXPERIENCE = "전통체험"
    CRAFT_EXPERIENCE = "공예체험"
    WELLNESS = "웰니스"
    # cultural_facility 하위
    MUSEUM = "박물관"
    ART_GALLERY = "미술관"
    LIBRARY = "도서관"
    PERFORMANCE_HALL = "공연장"
    SCIENCE_MUSEUM = "과학관"
    EXHIBITION_HALL = "전시관"
    # festival 하위
    FESTIVAL = "축제"
    EXHIBITION = "전시회"
    PERFORMANCE = "공연"
    CONCERT = "콘서트"
    # shopping 하위
    MARKET = "시장"
    SHOPPING_MALL = "쇼핑몰"
    DUTY_FREE = "면세점"
    DEPARTMENT_STORE = "백화점"
    # restaurant 하위
    RESTAURANT = "식당"
    KOREAN_FOOD = "한식"
    JAPANESE_FOOD = "일식"
    CHINESE_FOOD = "중식"
    WESTERN_FOOD = "양식"
    CAFE = "카페"
    TEA_HOUSE = "찻집"
    BAR = "주점"
    SNACK = "분식"


class UserConditions(BaseModel):
    """conditions-schema.md §2의 필드. LLM이 사용자 발화에서 추출한 값만 담는다.

    §2가 명명한 "15개"는 taste_query(2026-08-19)·travel_origin(2026-08-22)
    이전 기준이라 지금은 그보다 많다 — 개수 자체보다 §2의 필드 정의를 최신으로
    맞춰 참고한다.
    """

    current_location: str | None = None
    search_center: str | None = None
    place_types: list[PlaceType] = Field(default_factory=list)
    place_tags: list[PlaceTag] = Field(default_factory=list)
    weather: StatedWeather | None = None
    weather_intent: WeatherIntent | None = None
    concentration_intent: ConcentrationIntent | None = None
    transport: Transport | None = None
    max_travel_time: int | None = Field(
        default=None,
        ge=0,
        description="분(minute) 단위 정수. 사용자가 시간(hour) 단위로 말했으면 60을 곱해 "
        "환산한 값을 넣는다(예: '5시간' -> 300). 숫자만 그대로 옮기지 않는다.",
    )
    # "안국역에서/까지 10분"처럼 조사가 출발점을 확정할 때만 SEARCH_CENTER.
    # "근처/주변"이나 미언급은 비워 둔다 — D-067 기본값이 그대로 적용된다.
    travel_origin: TravelOrigin | None = None
    time_available: int | None = Field(
        default=None,
        ge=0,
        description="분(minute) 단위 정수. 사용자가 시간(hour) 단위로 말했으면 60을 곱해 "
        "환산한 값을 넣는다(예: '5시간' -> 300). 숫자만 그대로 옮기지 않는다.",
    )
    environment: Environment | None = None
    companion: Companion | None = None
    budget: str | None = None
    exclude_tags: list[str] = Field(default_factory=list)
    special_requirements: list[str] = Field(default_factory=list)
    # 요구된 무장애 편의(app.domain.models.AccessibilityNeed의 9개 값). weather_intent와
    # 같은 이유로 Literal이 아닌 list[str]이다 — C(app.agent_context.schemas.UserConditions)가
    # A보다 먼저 이 필드를 열어 뒀으므로, 어휘를 늘려도 요청 전체가 깨지지 않는다.
    # "휠체어"는 wheelchair_access, "유모차"는 stroller_access로 분리해서 넣는다 — 같은
    # 원문이라도 통로 폭·흙길 판정이 갈린다. "유모차 끌고 갈 만한 곳"은 stroller_access +
    # infant_facilities 두 값으로 채운다.
    accessibility_needs: list[str] = Field(default_factory=list)
    # 취향 발화 원문. 벡터 검색(search_place_evidence) 질의로 쓴다.
    # special_requirements와 분리한 이유는 그 필드가 "기타 전부"를 받아
    # 일정·교통 조건이 섞이고, 그대로 임베딩하면 취향이 아닌 문장이 근거를
    # 찾아내기 때문이다(실측 2026-08-19: "3시간 안에 다녀올 수 있는 곳"이
    # 유사도 0.523으로 진짜 취향 발화보다 높게 나왔다).
    taste_query: str | None = None

    @field_validator("current_location", "search_center", mode="before")
    @classmethod
    def _blank_to_none(cls, value: str | None) -> str | None:
        """공백/빈 문자열은 None으로 낮춘다.

        A-C Context Contract v0 §4.4: "빈 문자열과 공백 문자열은 허용하지 않는다."
        C로 넘어가기 전에 A 쪽에서 미리 차단한다.
        """
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @field_validator("max_travel_time", "time_available", mode="before")
    @classmethod
    def _zero_to_none(cls, value: object) -> object:
        """0은 "시간 제한 없음"이 아니라 None으로 정규화한다.

        "시간 제한 없음"은 max_travel_time=0이 아니라 None으로 표현하기로
        확정됐다. mode="before"라 Field(ge=0) 검사보다 먼저 실행되므로,
        음수는 이 함수를 그대로 통과해 여전히 ValidationError로 막힌다.
        C(app.agent_context.schemas.UserConditions)의 max_travel_time/
        time_available은 Field(gt=0)이라 0을 애초에 거부하는데, 이 정규화
        덕분에 A에서 C로 0이 넘어갈 일 자체가 없어진다.
        """
        return None if value == 0 else value


class RecommendPayload(BaseModel):
    conditions: UserConditions


class InfoPayload(BaseModel):
    place_name: str | None = None
    place_context: PlaceContext
    question_type: QuestionType
    specific_question: str | None = None
    visit_time: str | None = None
    """YYYY-MM-DD. question_type == CONCENTRATION일 때만 사용 (concentration-conditions.md §3.2)."""


class ModifyPayload(BaseModel):
    """llm-output-schema.md 초안 + 구현 시 확정 사항(§10 #3) 반영.

    문서 초안은 `condition_changes: Partial<UserConditions> | null`만 정의하지만,
    구조화 출력에서는 UserConditions의 모든 필드가 항상 채워지므로 "언급 안 해서 유지"와
    "명시적으로 null로 해제"를 값만으로 구분할 수 없다(§10 #3이 "구현 시 확정"으로 남긴 지점).
    `changed_fields`에 실제로 변경(Update/Remove)된 UserConditions 필드명만 명시하고,
    나머지는 condition_changes에 어떤 값이 있든 Keep으로 처리한다.

    `_clear_unlisted_fields`가 이 불변식을 생성 시점에 구조적으로 강제한다 — LLM이
    changed_fields 밖 필드에 값을 채워 보내도(예: 호출자가 current_conditions에 실제
    null이 아닌 값을 실어 보내서 LLM이 그 값을 그대로 carry-forward한 경우) 여기서
    null/빈 배열로 정리되므로, 이 필드를 나중에 직접 읽는 소비자가 생겨도 안전하다.

    `target_indices`는 SCHEDULE-09(부분 수정)에서 추가됐다. `modify_type ==
    REJECT_SPECIFIC`일 때만 의미가 있으며, COMPARE의 `ComparePayload.targets`와
    같은 1-indexed 순번 표현이다("all" 같은 전체 지정은 없다 — 전체 거절은
    REJECT_ALL이 이미 담당한다). REJECT_ALL/CHANGE_CONDITION일 때는 빈 배열이다.
    """

    modify_type: ModifyType
    condition_changes: UserConditions | None = None
    changed_fields: list[str] = Field(default_factory=list)
    target_indices: list[int] = Field(default_factory=list)

    @model_validator(mode="after")
    def _clear_unlisted_fields(self) -> ModifyPayload:
        if self.condition_changes is None:
            return self

        allowed = set(self.changed_fields)
        updates: dict[str, object] = {}
        for name, field_info in UserConditions.model_fields.items():
            if name in allowed:
                continue  # changed_fields에 있는 필드는 절대 건드리지 않는다.
            empty = (
                field_info.default_factory()  # type: ignore[call-arg]
                if field_info.default_factory is not None
                else field_info.default
            )
            if getattr(self.condition_changes, name) != empty:
                updates[name] = empty

        if updates:
            self.condition_changes = self.condition_changes.model_copy(update=updates)
        return self


class ComparePayload(BaseModel):
    targets: Literal["all"] | list[int]
    criteria: CompareCriteria


class ComparisonItem(BaseModel):
    """C의 비교 결과를 A가 LLM 요약·응답 표시용으로 정규화한 항목.

    추천 시점 Feature 스냅샷의 수치 자체는 B가 보관하고, C가 place_id를 사람이
    읽을 수 있는 장소명으로 해석해 이 모델로 반환한다. 이 모델은 C의 Tool 계약을
    중복 정의하려는 것이 아니라, A가 LLM에 넘길 수 있는 공개 비교 사실의 최소
    집합이다.

    latitude/longitude·travel_* 필드는 TRAVEL_TIME 전용(2026-08-21, TP-105/106
    실측 연결). C는 좌표만 사실 그대로 전달하고(우열 판정 없음, 기존 원칙 유지),
    A가 그 좌표로 도보·자동차·대중교통 세 경로를 모두 실측해 travel_* 값을
    채운다 — distance_km/remaining_minutes(추천 시점 스냅샷 재사용, D-050/
    int-04-compare.md §13)와는 출처가 달라 별도 필드로 둔다. 수단별로 값을
    나누는 이유: "도보 15분/자동차 4분/대중교통 10분"처럼 사용자가 자기 상황에
    맞는 수단을 골라 볼 수 있어야 한다 — 하나로 합치면 그 선택지가 사라진다.
    수단 중 조회에 실패하거나 provider가 없는 것은 None으로 남는다.
    """

    place_id: str
    place_name: str
    rank: int = Field(ge=1)
    distance_km: float | None = Field(default=None, ge=0)
    remaining_minutes: int | None = Field(default=None, ge=0)
    environment_type: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    travel_distance_km: float | None = Field(default=None, ge=0)
    travel_walking_minutes: int | None = Field(default=None, ge=0)
    travel_driving_minutes: int | None = Field(default=None, ge=0)
    travel_transit_minutes: int | None = Field(default=None, ge=0)


class ComparisonResult(BaseModel):
    """COMPARE 답변 생성에 쓰는 검증된 사실 데이터.

    LLM은 이 모델에 담긴 값만 문장으로 바꾸며, 순위·점수·운영 상태를 새로
    계산하거나 추정하지 않는다.
    """

    criteria: CompareCriteria
    items: list[ComparisonItem] = Field(min_length=1)


class SituationKind(StrEnum):
    """GENERAL 상황 발화(interaction_mode=situational)에서 감지한 상황 종류.

    (docs/design/conversational-layer.md 3단계) 닫힌 목록이다 — 여기 없는 값은
    "상황이지만 우리가 실행 가능한 도움이 없다"는 뜻으로 다룬다(VAGUE).

    이 값에서 실제로 무엇을 제안할지(actions/조건 override/버튼 문구)는 프롬프트가
    아니라 코드(app.services.interpret.situational_offers)가 정한다 — "닫힌 목록"을
    프롬프트 규칙으로만 두면 코드와 프롬프트가 언젠가 어긋난다. LLM은 상황을
    분류하기만 하고, 무엇을 할 수 있는지는 절대 스스로 정하지 않는다.
    """

    FATIGUE = "fatigue"  # 지침, 다리·발 통증
    BAD_WEATHER = "bad_weather"  # 비, 더위, 추위, 바람
    CLOSED_OR_CROWDED = "closed_or_crowded"  # 휴관, 혼잡
    COMPANION_DIFFICULTY = "companion_difficulty"  # 동행(아이·어르신 등)이 힘들어함
    VAGUE = "vague"  # 막연한 답답함 — 실행 가능한 제안이 없다


class GeneralPayload(BaseModel):
    topic: GeneralTopic
    original_question: str
    # interaction_mode가 situational일 때만 채워진다. direct_request 턴(예: "서울
    # 여행 팁")에서는 상황이 없으므로 None이다.
    situation: SituationKind | None = None


class OutOfScopePayload(BaseModel):
    category: OutOfScopeCategory
    severity: Severity


class MissingField(BaseModel):
    field: str
    reason: str


class AmbiguousField(BaseModel):
    field: str
    user_input: str
    candidates: list[str]
    reason: str


class ClarificationOption(BaseModel):
    """되묻기에 붙는 버튼 하나. 프론트가 그대로 렌더링하고, 클릭 시 id를 그대로
    돌려보내면 서버가 결정적으로 처리한다(clarification_choice) — classify_intent()를
    다시 태우지 않는다. docs/design/clarification-options.md 3절."""

    id: str
    label: str
    resolved_intent: Intent


class ClarificationPayload(BaseModel):
    missing_fields: list[MissingField] = Field(default_factory=list)
    ambiguous_fields: list[AmbiguousField] = Field(default_factory=list)
    message: str
    options: list[ClarificationOption] = Field(default_factory=list)
    # 오케스트레이터가 분류 이전에 선제 차단으로 만드는 되묻기(예: 케이스 4/5의
    # "처음부터 다시")는 missing_fields/ambiguous_fields가 비어 있어 agent_runtime의
    # _llm_clarification_code()가 세션에 남길 코드를 이 값으로 명시한다. None이면
    # 기존처럼 missing/ambiguous_fields에서 코드를 유도한다(하위 호환).
    code: str | None = None


class LLMOutput(BaseModel):
    """모든 Intent가 담기는 단일 envelope (llm-output-schema.md §3)."""

    intent: Intent
    status: OutputStatus
    # Intent와 직교하는 축(대화층 2단계). 분류 단계가 정한 값을 그대로 옮겨
    # 담기만 한다 — 이 envelope는 판단하지 않는다.
    interaction_mode: InteractionMode = InteractionMode.DIRECT_REQUEST
    recommend: RecommendPayload | None = None
    info: InfoPayload | None = None
    modify: ModifyPayload | None = None
    compare: ComparePayload | None = None
    general: GeneralPayload | None = None
    out_of_scope: OutOfScopePayload | None = None
    clarification: ClarificationPayload | None = None


class SessionState(BaseModel):
    """Package B가 관리하는 세션 상태 스냅샷.

    프론트엔드는 session_id만 보관하면 되고, 조건과 이력은 서버가 들고 있다.
    run_id는 /api/recommendations 요청에 실어 보내야 조건 변경 기록과
    추천 이력이 같은 실행으로 묶인다.
    """

    session_id: str
    run_id: str
    session_created: bool
    condition_version: int
    condition_changed: bool
    user_conditions: UserConditions
    shown_place_ids: list[str] = Field(default_factory=list)
    excluded_place_ids: list[str] = Field(default_factory=list)
    gps_expired: bool = True
    weather_expired: bool = True


class InterpretResponse(BaseModel):
    """/api/interpret 응답. 해석 결과와 세션 상태를 함께 반환한다."""

    output: LLMOutput
    state: SessionState


class IntentClassificationResult(BaseModel):
    """1단계 LLM 호출(Intent 분류) 전용 최소 스키마. 문서에 없는 신규 모델.

    OUT_OF_SCOPE는 1단계 판정만으로 차단에 필요한 정보가 다 나오므로
    category/severity를 여기서 함께 받아 2단계(조건 추출) 호출을 생략한다.
    """

    intent: Intent
    # Intent와 나란한 별개 축. 기본값이 DIRECT_REQUEST라 이 필드를 안 채우는
    # 모델·스텁도 지금까지와 똑같이 동작한다.
    interaction_mode: InteractionMode = InteractionMode.DIRECT_REQUEST
    out_of_scope_category: OutOfScopeCategory | None = None
    out_of_scope_severity: Severity | None = None


class ConversationTurnView(BaseModel):
    """프롬프트에 넘길 최근 대화 한 턴. (대화층 1단계)

    B가 보관하는 ConversationTurn은 어시스턴트 쪽을 원문이 아니라 재료(intent/
    question_type/장소명/제안)로 들고 있다. 그 재료를 사람이 읽는 한 줄로 조립하는
    건 A의 책임이고(agent_runtime), 조립은 순수 함수라 LLM 호출이 늘지 않는다.

    **이 값은 신뢰할 수 없는 입력이다.** 프롬프트에 실을 때 system_instruction
    문자열에 치환하면 사용자가 쓴 글이 시스템 지시문 내부에 박힌다 — 반드시
    대화 내용(contents) 자리에 역할을 나눠 넣어야 한다.
    """

    user_input: str
    assistant_summary: str | None = None


class InterpretRequest(BaseModel):
    user_input: str = Field(..., min_length=1)

    # 세션 식별자. 없으면 B가 첫 apply()에서 발급한다.
    session_id: str | None = None
    # 브라우저에서 확보한 "위도,경도". api_context.gps_location과 동일 포맷.
    device_location: str | None = None

    # 아래 5개는 라우터가 B의 세션 컨텍스트로 채운다.
    # 호출자가 보낸 값은 무시되며, 하위 호환을 위해 필드만 유지한다.
    has_previous_recommendation: bool = False
    shown_place_count: int = Field(default=0, ge=0)
    current_conditions: UserConditions | None = None
    # 직전 턴이 되묻기로 끝났는지(B의 SessionContextResponse.pending_clarification 그대로)와
    # 그 되묻기가 어떤 Intent의 턴이었는지(SessionContextResponse.last_intent). SCHEDULE
    # 되묻기 답변이 새 MODIFY 요청으로 오분류되는 걸 막기 위해 classify_intent()까지
    # 전달한다(D-059) — RECOMMEND는 우선순위 fallback이라 이 정보 없이도 대체로 맞지만,
    # SCHEDULE은 키워드가 있어야만 선택되는 명시적 분류라 fallback이 없다.
    pending_clarification: str | None = None
    last_intent: str | None = None
    # SCHEDULE-09 후속(이름 지목): 현재 노출된 항목의 이름을 rank 순으로 담는다.
    # "두가헌 레스토랑은 빼줘"처럼 순번이 아니라 이름으로 REJECT_SPECIFIC 대상을
    # 지목할 때 MODIFY 추출기가 이름→순번을 매칭하는 데 쓴다. 이름이 없는 항목은
    # 빈 문자열로 채워 인덱스(=순번-1)가 어긋나지 않게 한다.
    shown_place_names: list[str] = Field(default_factory=list)
    # 직전 INFO 상세 카드에서 프론트가 보존한 장소명. "여기/이곳/거기"처럼
    # 추천 목록이 아닌 대화 속 장소를 가리키는 INFO 발화의 해소 후보로만 쓴다.
    # 상태 계약에 새 필드를 추가하지 않고도, 현재 대화 화면이 이미 받은 카드 정보를
    # 다음 턴의 해석에 재사용할 수 있게 한다.
    conversation_place_name: str | None = None
    # 최근 대화(오래된 것이 앞). 라우터가 B의 SessionContextResponse.recent_turns를
    # 조립해 채운다 — 호출자가 보낸 값은 무시된다(위 5개 필드와 같은 원칙).
    recent_turns: list[ConversationTurnView] = Field(default_factory=list)
    # 직전 턴이 INFO 되묻기(장소명 없음)로 끝났을 때 그때 이미 파악한 질문 정보.
    # 라우터가 B의 SessionContextResponse.pending_info_context로 채운다 — 호출자가
    # 보낸 값은 무시된다(위 5개 필드와 같은 원칙). 자유 텍스트로 장소명만 답해도
    # extract_info_query()가 이 값을 참고해 question_type/specific_question을
    # 이어받을 수 있게 한다.
    pending_info_question_type: str | None = None
    pending_info_specific_question: str | None = None
    pending_info_visit_time: str | None = None


# === Agent Runtime (A-03) ===
#
# Agent Runtime(app.services.runtime.agent_runtime)이 쓰는 요청/응답 모델. Tool 결과
# (C)는 app.agent_context.schemas.AgentContextResponse/RecommendationContext로
# 이미 계약이 확정됐다(A-C Context Contract v0). D(Recommendation)는 아직 확정 전이라
# AgentResponse는 여전히 임시 모델이다 — 계약이 확정되면 필드가 바뀔 수 있다.


# AgentRequest.recent_follow_ups의 상한.
#
# 개수: 직전 턴에 보여준 세 개.
#
# **막으려는 것은 "세 개가 전부 방금 본 것"이다.** 세 개면 직전 턴의 버튼 전부를 덮으므로
# 그 경우가 구조적으로 불가능해진다. 두 턴 이상 전의 문구가 하나씩 돌아오는 것은 남지만,
# 그건 사용자가 "또 이거네"라고 느끼는 것과 강도가 다르다.
#
# **넓히면 버튼이 마른다.** 한 장소를 두고 이어가는 대화에서 그 장소로 이어갈 질문은 원래
# 예닐곱 개뿐이라(운영시간·주차·혼잡도·행사·도로·편의시설·이동시간), 목록이 길어지면 그
# 공간을 통째로 덮어버린다. 모델이 규칙을 어겨서가 아니다 — 같은 조건을 다섯 번 돌려 모델
# 원본을 봤더니 다섯 번 다 한 개였고, _clean()이 버린 것은 없었다.
#
# 같은 5턴 대화(마포구 추천 -> 난지한강공원 운영시간·주차·편의시설·혼잡도)로 잰 값이다.
#   상한  3 -> 버튼 3/3/3/3/3, 반복 4건 (모두 두 턴 이상 전 문구)
#   상한  6 -> 버튼 3/3/3/1/1, 반복 2건
#   상한 10 -> 버튼이 0이 되는 턴이 생김
#
# **측정 전에 두 가지를 먼저 확인한다.** 이 값을 한 번 열로 올렸다가 되돌렸는데, 그때 근거로
# 삼은 측정이 (1) LANGFUSE_PROMPTS_ENABLED가 켜져 있어 레포가 아닌 원격 프롬프트로 돌았고
# (2) 아예 다른 워크트리에서 뜬 서버를 향하고 있었다. 프롬프트 출처와 서버의 작업 디렉터리를
# 먼저 본다.
#
# 세 개는 직전 턴에 보여준 버튼 전부다 — 사용자가 방금 보고 지나친 것들이라 되풀이될
# 확률이 가장 높고, 그만큼만 부탁하면 모델이 실제로 지킨다.
#
# 길이: 버튼 문구의 상한(follow_up_suggester.MAX_LABEL_LENGTH)과 같은 값이다. 그보다 긴
# 문자열은 이 슬롯이 만든 문구일 수 없으므로 비교 대상이 아니다. 두 곳에서 같은 값을
# 쓰지만 상수를 공유하지는 않는다 — 저쪽이 schemas를 가져다 쓰는 방향이라 반대로는 못 건다.
MAX_RECENT_FOLLOW_UPS = 3
MAX_RECENT_FOLLOW_UP_LENGTH = 40


class AgentRequest(BaseModel):
    """run_agent()의 입력. has_previous_recommendation 등은 더 이상 호출자가 넣지 않는다 —
    Runtime이 B의 SessionContextResponse에서 직접 계산한다."""

    user_input: str = Field(..., min_length=1)
    # 화면 표시는 영어여도 Runtime·B의 누적 조건 계약은 한국어로 유지한다. 라우터가
    # language="en" 요청만 Cloud Translation으로 한국어화한 사본을 Runtime에 넘긴다.
    # 매 턴 함께 보내므로 B 세션 스키마를 넓히지 않고도 언어를 바꿀 수 있다.
    language: Literal["ko", "en"] = "ko"
    session_id: str | None = None
    device_location: str | None = None  # "위도,경도" 문자열, api_context.gps_location과 동일 포맷
    # 위치 설정 화면에서 사용자가 직접 고른 검색 위치의 이름(예: "안국역").
    # device_location과 다른 값이다 — 그쪽은 "사용자가 지금 있는 곳"이고 이쪽은
    # "어디를 기준으로 찾을지"다. 화면이 세션마다 들고 있다가 매 턴 함께 보낸다.
    #
    # 이번 턴 발화가 검색 위치를 말하지 않았을 때에만 조건에 채운다 — 화면에
    # 안국역이 설정돼 있어도 "성수동 카페 알려줘"는 성수동이 맞다
    # (agent_runtime._apply_selected_search_center).
    #
    # 좌표가 아니라 이름을 받는 이유는, 이름을 좌표로 바꾸는 경로가 이미
    # ResolveLocationTool 하나로 정리돼 있어서다. 좌표를 직접 받으면 검색 기준점을
    # 정하는 길이 두 개가 된다.
    selected_search_center: str | None = None
    # 위치 설정 화면에서 사용자가 직접 정한 출발지의 이름(예: "안국역").
    # selected_search_center와 다른 질문의 답이다 — 이쪽은 "사용자가 어디 있는가"라
    # 이동시간을 재는 시작점이 되고, 저쪽은 "어디 주변을 찾을까"다(D-067이 둘을
    # 분리한 이유). 화면이 같은 목록에서 둘 중 무엇으로 쓸지 물어 정한다.
    #
    # 발화가 출발지를 말했으면("나 지금 혜화역인데") 그쪽이 이긴다. 비어 있으면
    # 기기 좌표(device_location)가 그대로 사용자 위치가 된다.
    selected_current_location: str | None = None
    # 직전 INFO 카드의 장소명. 현재 화면이 "여기/이곳"을 보낼 때에만 A가 INFO
    # from_conversation 해소 후보로 사용한다.
    conversation_place_name: str | None = None
    # 되묻기 버튼 클릭 시 ClarificationOption.id를 그대로 echo. user_input에는 버튼
    # label을 채워 보내되(채팅 이력 표시용) 라우팅은 이 필드만으로 결정한다 —
    # classify_intent()를 다시 태우지 않는다(docs/design/clarification-options.md 3절).
    clarification_choice: str | None = None
    # "OO 기준으로 다시 보기" 비차단형 전환 버튼 클릭(D-071, TravelOriginToggle).
    # user_input에는 버튼 label을 채워 보내되(채팅 이력 표시용) 라우팅은 이
    # 필드만으로 결정한다 — clarification_choice와 같은 이유로
    # classify_intent()/extract_recommend_conditions()를 다시 태우지 않는다.
    # 직전 턴 조건을 그대로 재사용해 travel_origin만 이 값으로 덮어써 재실행한다.
    travel_origin_override: TravelOrigin | None = None
    # 보관함 하단 바의 "이 장소들로 일정 짜기" 클릭(SCHEDULE-12 카드 3).
    # user_input에는 버튼 label을 채워 보내되(채팅 이력 표시용) 라우팅은 이
    # 필드만으로 결정한다 — clarification_choice/travel_origin_override와 같은
    # 이유로 classify_intent()/extract_*_conditions()를 다시 태우지 않는다.
    # 보관함이 비어 있으면 평소 경로로 폴백한다(런타임이 판정).
    schedule_from_saved: bool = False
    # 개발자용 채팅(/dev-chat) 전용 디버그 스위치. True면 이번 턴은 폐점 후보도
    # 항상 채점에 포함한다 — no_data_closed 되묻기 자체를 재현/우회하려고 매번
    # 버튼을 누르지 않고 강제로 켤 수 있게 한다(실사용 피드백, 2026-08-13).
    # 세션 상태(ignore_operating_hours_until)는 건드리지 않는다 — 이 턴에만
    # 적용되는 일회성 오버라이드다.
    debug_ignore_operating_hours: bool = False
    # 화면이 최근에 후속 질문 버튼으로 보여준 문구(오래된 것이 앞). 같은 문구를
    # 다시 권하지 않으려고 받는 제외 목록이다.
    #
    # **서버가 채울 수 없어서 화면이 보낸다.** B가 보관하는 recent_turns에는 사용자가
    # 실제로 한 말만 남는다. 버튼으로 보여줬는데 누르지 않은 문구는 어디에도 안 남아서,
    # 서버만으로는 "이미 권했다"를 알 방법이 없다. 화면이 유일한 출처다.
    #
    # 그래서 ApiContext.recent_turns 계열과 규칙이 반대다 — 저쪽은 라우터가 채우고
    # 호출자가 보낸 값을 무시하지만, 이쪽은 화면이 보낸 값을 그대로 쓴다.
    #
    # **신뢰할 수 없는 입력이다.** 이 문구는 후속 질문 제안 프롬프트에 실린다. 개수와
    # 길이를 아래 검증기가 자르되, 형식이 어긋나도 422로 turn을 실패시키지 않는다 —
    # 버튼 중복을 막자고 대화를 끊을 이유가 없다.
    recent_follow_ups: list[str] = Field(default_factory=list)

    @field_validator("recent_follow_ups", mode="before")
    @classmethod
    def _trim_recent_follow_ups(cls, value: object) -> list[str]:
        """제외 목록을 프롬프트에 실어도 되는 크기로 자른다. 어긋난 입력은 버린다."""

        if not isinstance(value, list):
            return []
        trimmed = [
            stripped
            for stripped in (item.strip() for item in value if isinstance(item, str))
            if stripped and len(stripped) <= MAX_RECENT_FOLLOW_UP_LENGTH
        ]
        return trimmed[-MAX_RECENT_FOLLOW_UPS:]


class LLMCallMetadata(BaseModel):
    """한 번의 Gemini 호출에서 실제로 시도·응답한 모델 기록.

    개발자용 Agent Runtime Audit에서만 실행 경로를 확인하는 용도다. 사용자 발화나
    프롬프트 본문은 포함하지 않아, 관측용 메타데이터가 입력 내용을 추가 노출하지 않는다.
    """

    operation: str
    attempted_models: list[str]
    served_model: str | None = None
    # 개발자용 Audit에서 Intent 분류·조건 추출 호출별 지연을 보여주기 위한 값이다.
    # 기존에 저장된 실행 이력과의 호환을 위해 누락 가능하게 둔다.
    latency_ms: int | None = None
    # 토큰 사용량. B의 LLMOps Trace(token_usage)와 Langfuse 비용 화면이 같은 값을
    # 쓴다. 실패하거나 usage_metadata가 없는 응답에서는 None이다 — 0으로 채우면
    # "안 썼다"와 "모른다"가 구분되지 않는다.
    # thoughts_tokens는 Gemini 3.x 계열의 사고 토큰이다. 과금 대상인데
    # candidates_token_count에 안 잡혀서 따로 세지 않으면 비용이 과소 집계된다.
    # cached_tokens는 입력 중 캐시에서 읽힌 몫이다. Gemini는 2.5 이상에서 자동
    # 캐싱이 기본으로 켜져 있고 캐시된 입력은 정가의 10분의 1로 과금되는데,
    # prompt_token_count에는 캐시분까지 합쳐서 들어온다 — 이 값을 따로 세지
    # 않으면 "적중했는데 비싸게 계산"하거나 "한 번도 적중 안 했는데 싸다고
    # 착각"하는 것을 구분할 수 없다(2026-09-09).
    input_tokens: int | None = None
    output_tokens: int | None = None
    thoughts_tokens: int | None = None
    cached_tokens: int | None = None
    total_tokens: int | None = None
    # 같은 모델에 대해 타임아웃·429·5xx로 다시 시도한 횟수(0 = 첫 시도에서 끝남).
    # latency_ms가 유독 크게 보일 때 "모델이 느렸다"와 "타임아웃 후 재시도가
    # 조용히 성공했다"를 구분하는 값이다 — 재시도가 성공하면 로그도 안 남고
    # attempted_models도 안 늘어나 겉보기엔 아무 흔적이 없다(D-076 검토 후속).
    # 스트리밍 호출(stream_*)은 모델별 재시도 없이 바로 다음 모델로 넘어가므로
    # 항상 0이다. 기존 저장된 실행 이력에는 없을 수 있어 누락을 허용한다.
    retry_count: int | None = None


class LLMExecutionMetadata(BaseModel):
    """한 Agent 요청 안에서 발생한 LLM 호출들의 모델 사용 이력."""

    calls: list[LLMCallMetadata] = Field(default_factory=list)


class ToolProviderDebug(BaseModel):
    """C가 한 번의 Context 수집에서 실제로 호출한 Provider 하나의 기록."""

    source: str
    status: str
    retrieved_at: str | None = None


class ToolContextItemDebug(BaseModel):
    """RecommendationContext의 항목(location/weather/places/holidays) 하나의 상태.

    fetched=False는 C가 그 항목을 아예 조회하지 않았다는 뜻이다(예: 발화에 날씨가
    이미 있어 조회를 생략한 경우). 조회했는데 실패한 것과 구분된다.
    """

    key: str
    fetched: bool
    status: str | None = None
    error_code: str | None = None
    warning_codes: list[str] = Field(default_factory=list)
    item_count: int | None = None


class CandidateConcentrationDebug(BaseModel):
    """개발자용 Audit 전용: 후보 한 건의 혼잡도가 어디서 온 값인지.

    건수만 세면 "5건 중 3건이 근사치"까지만 알고 어느 후보가 어디서 빌렸는지는
    모른다. 근사치의 타당성은 "어느 장소에서 얼마나 떨어진 값인가"로 판단하므로
    후보별로 남긴다.
    """

    place_id: str
    name: str
    status: str
    is_proxy: bool = False
    # 값을 빌려온 실제 장소와 후보로부터의 거리. is_proxy=False면 둘 다 None.
    proxy_place_name: str | None = None
    proxy_distance_km: float | None = None


class LocationDebug(BaseModel):
    """개발자용 Audit 전용: 이번 턴에 쓰인 위치 하나가 무엇이었는지.

    name은 ResolvedLocation.resolved_name이 아니라 requested_query다. resolved_name은
    지오코딩으로 풀리면 도로명 주소가 되어 표시용으로 쓸 수 없다고 C의 계약
    (agent_context/schemas.py::ResolvedLocation)이 명시한다.

    source는 그 좌표가 어디서 왔는지다. 검색 위치·사용자 위치는 C의
    ResolvedLocation.source("query" / "device_gps")를 그대로 옮기고, 경로 시작점은
    다음 둘 중 하나를 추가로 쓴다.

    - "search_center": 사용자 위치를 몰라 검색 위치로 대체한 경우다
      (domain/ranking_origin.py::resolve_ranking_origin). 사용자가 자기 위치라고
      말한 적 없는 좌표가 시작점이 된 상태라, 거리·경로 표기가 사실과 어긋나는지
      화면에서 바로 가려내야 한다 — 진짜 "대체"다.
    - "travel_origin_override": 사용자 위치를 알면서도 발화가 조사로 출발점을
      확정해("안국역에서 10분", D-071) 검색 위치를 골랐다. 값이 사실과 어긋난
      게 아니라 사용자가 그렇게 말한 것이므로 위 경고 대상이 아니다. 이 둘을
      구분하지 않으면 정상 동작인 후자까지 "위치를 몰라서 대체됨"으로 잘못
      경고하게 된다.
    """

    # device_gps로 온 좌표에는 부를 이름이 없다 — C의 requested_query가 "gps_location"
    # 이라는 자리표시자이므로 그대로 실으면 지명처럼 보인다. 그 경우 None으로 두고
    # 표시는 소비 측이 좌표로 처리한다.
    name: str | None = None
    source: Literal["query", "device_gps", "search_center", "travel_origin_override"]
    latitude: float
    longitude: float


class StaleAreaProbeDebug(BaseModel):
    """우리 지역 목록엔 없지만 서울시 API는 실제로 지원하는 지역을 찾았을 때만
    채워진다(TP-141, D-084). 응답(추천 판정)에는 영향을 주지 않는 감시 전용
    필드다 — 우리 스냅샷이 서울시 라이브 목록보다 뒤처지기 시작했다는 신호다.
    """

    probed_area_name: str
    probed_area_code: str | None = None
    # 지금 실제로 대신 답한 지역과 그 거리. 개발자 화면 배너가 "OO은 목록에
    # 없어서 대신 XX(0.85km) 값으로 답했다"는 문구를 만드는 데 쓴다.
    matched_area_name: str
    matched_area_distance_km: float


class ToolExecutionDebug(BaseModel):
    """개발자용 Audit 전용: A→C 호출 한 단계가 실제로 무엇을 했는지.

    llm_execution과 같은 성격의 관측 전용 필드다 — 추천 판정에는 쓰이지 않으며,
    이 값이 없다고 해서 흐름이 달라지지 않는다. 특히 providers[].source는 실제로
    응답을 만든 Provider가 Real인지 Stub인지 드러내므로, D-042(Real 실패 시 Fake로
    자동 전환하지 않는다)가 지켜지고 있는지 화면에서 바로 확인하는 수단이 된다.
    """

    operation: Literal[
        "context_fetch",
        "info_concentration",
        "info_realtime_commercial",
        "info_realtime_population",
        "info_realtime_citydata",
        "candidate_enrichment",
        "compare_fetch",
    ] = "context_fetch"
    request_id: str
    status: str
    latency_ms: int | None = None
    providers: list[ToolProviderDebug] = Field(default_factory=list)
    context_items: list[ToolContextItemDebug] = Field(default_factory=list)
    rule_versions: dict[str, str] = Field(default_factory=dict)
    resolved_location_name: str | None = None
    resolved_location_address: str | None = None
    # 이번 턴의 위치 세 갈래. 셋은 서로 다를 수 있고, 다른 것 자체가 관측 대상이다
    # (TP-112: 후보를 **모으는** 중심과 후보를 **줄 세우는** 기준점은 다르다).
    # route_origin.source가 "search_center"면 사용자 위치를 몰라 검색 위치로 대체한
    # 턴이다. context_fetch(RECOMMEND)에서만 채워진다 — INFO/COMPARE는 C의 위치
    # 해석을 거치지 않고 A가 기기 GPS로 직접 경로를 조회한다(agent_runtime.py).
    search_location: LocationDebug | None = None
    user_location: LocationDebug | None = None
    route_origin: LocationDebug | None = None
    error_code: str | None = None
    clarification_code: str | None = None
    is_proxy: bool | None = None
    # info_realtime_population 전용. is_proxy가 true일 때만 의미가 있다 — 대체가
    # 안 일어났으면 애초에 확인할 게 없다. TP-141/D-084 참고.
    stale_area_detected: StaleAreaProbeDebug | None = None
    candidate_status_counts: dict[str, int] = Field(default_factory=dict)
    # candidate_enrichment 전용. 매핑 없는 후보가 다수라(활성 844건 중 매핑 100건)
    # 근사치가 섞이는 게 정상 상태인데, 상태 집계만 보면 직접 조회한 값과 빌려온
    # 값이 "success 5건"으로 같아 보인다. 건수는 이 목록에서 세면 되므로 따로
    # 두지 않는다 — 같은 사실의 출처가 둘이면 어긋난다.
    candidate_concentration: list[CandidateConcentrationDebug] = Field(default_factory=list)


class PreferenceEvidenceQuote(BaseModel):
    polarity: str
    text: str
    source_type: str
    source_url: str | None = None


class PlacePreferenceInsight(BaseModel):
    """상세 카드에 표시할 장소별 취향 태그와 대표 후기 근거."""

    code: str
    label: str
    mention_count: int = Field(ge=0)
    positive_document_count: int = Field(ge=0)
    negative_document_count: int = Field(ge=0)
    evidence: list[PreferenceEvidenceQuote] = Field(default_factory=list)


class ReviewSource(BaseModel):
    """후기 답변의 근거가 된 문장 하나와 그 출처.

    **문장을 함께 싣는다.** 링크만 주면 사용자가 "무슨 근거로 이렇게 답했나"를
    확인하려고 블로그를 열어 긴 글에서 해당 대목을 직접 찾아야 한다. 답변 아래에
    인용으로 보여주고, 링크는 더 읽고 싶을 때 쓴다.
    """

    text: str
    # 원문 글 주소. 초기 적재분 일부는 링크가 없어 인용만 보여준다.
    url: str | None = None
    # 네이버 블로그인지 구글 리뷰인지. 화면이 "네이버 블로그" 같은 라벨을 붙인다.
    source_type: str | None = None
    published_at: str | None = None


class InfoPlaceCard(BaseModel):
    """INFO 장소 상세 카드용 A의 최종 응답 모델.

    C의 ``PlaceInfoResult.fields``는 사용자가 물어본 정보가 실제로 있었는지를
    판정하는 용도이고, 이 모델은 그와 별개로 펼쳐서 보여줄 장소 전체 정보다.
    따라서 ``answer_fields``를 카드의 상세 필드와 합치지 않는다.
    """

    question_type: QuestionType
    answer_fields: dict[str, str] = Field(default_factory=dict)
    place_id: str | None = None
    place_name: str | None = None
    # 목적지 좌표. 프론트가 지도 앱 길찾기 딥링크(출발=현재 위치, 도착=이 좌표)를
    # 만드는 데 쓴다. C의 destination_coordinates에서 오며, 좌표를 못 얻은 카드
    # 타입(혼잡도/행사 등)은 None이라 프론트에서 버튼을 숨긴다.
    latitude: float | None = None
    longitude: float | None = None
    thumbnail_url: str | None = None
    # 여러 장 보기용 사진 목록. 비어 있어도 thumbnail_url은 따로 있을 수 있어
    # 프론트는 둘을 함께 본다 — 사진 목록이 있는 장소가 전체의 30%뿐이다.
    photos: list[PlacePhotoItem] = Field(default_factory=list)
    overview: str | None = None
    operating_hours: str | None = None
    rest_date: str | None = None
    parking: str | None = None
    parking_fee: str | None = None
    fee: str | None = None
    baby_carriage: str | None = None
    pet: str | None = None
    credit_card: str | None = None
    restroom: str | None = None
    homepage: str | None = None
    # 무장애 여행 정보(D-077). C의 ``PlaceCard``에서 그대로 옮겨온 값이고, 프론트는
    # 편의시설 표와 분리된 "무장애 정보" 구획으로 그린다.
    #
    # 값이 없으면 None이다. 소비 측은 None인 항목의 줄 자체를 그리지 않는다 —
    # 이 원문은 있으면 적고 없으면 비우는 식이라 빈 값을 "없음"으로 읽으면 있는
    # 시설을 없다고 말하게 된다.
    #
    # ``stroller_rental``이 차면 위 ``baby_carriage``가 비고, 비면 반대다. 두 값이
    # 같은 사실을 말하는데 서로 어긋나 C가 하나만 골라 보낸다.
    accessible_restroom: str | None = None
    accessible_parking: str | None = None
    elevator: str | None = None
    visual_guide: str | None = None
    wheelchair_rental: str | None = None
    nursing_room: str | None = None
    seating: str | None = None
    stroller_rental: str | None = None
    guide_dog: str | None = None
    # 후기에서 추출한 취향 태그·대표 근거. 상세 모달 요청에서만 채운다.
    preference_insights: list[PlacePreferenceInsight] = Field(default_factory=list)
    # 후기로 답한 턴에서 그 답의 근거가 된 글. 답변 본문은 링크를 말하지 않고
    # 화면이 이 목록으로 "출처"를 그린다. 링크가 없는 근거는 담지 않는다 —
    # 걸 곳이 없으면 출처로 보여줄 것도 없다.
    review_sources: list[ReviewSource] = Field(default_factory=list)
    population_current_level: str | None = None
    population_current_message: str | None = None
    population_observed_at: str | None = None
    # 향후 예측 중 가장 붐빌 시간대 요약("N시 후 가장 붐빌 것으로 예상돼요").
    # 과거 추이는 서울시 API가 제공하지 않아 다루지 않는다.
    population_peak_forecast_summary: str | None = None
    population_forecasts: list[PopulationForecastBar] = Field(default_factory=list)
    concentration_forecasts: list[ConcentrationForecastBar] = Field(default_factory=list)
    # 서울시 도시데이터는 관광 상세 DB가 아닌 지역 단위 실시간 데이터다. 기본 카드에는
    # 질문에 대한 요약만 두고, 모달은 이 목록으로 추가 항목·이미지·원문 링크를 표시한다.
    realtime_area_name: str | None = None
    realtime_observed_at: str | None = None
    realtime_source_url: str | None = None
    realtime_map_url: str | None = None
    realtime_detail_items: list[RealtimeInfoDetailItem] = Field(default_factory=list)
    # 서울시 실시간 인구·상권 공통 요약. 실시간 인구 혼잡도(concentration)와 실시간
    # 상권(realtime_commercial) 카드에만 싣는다 — 두 유형만 서울시 citydata를 이미
    # 호출하므로 추가 호출 없이 채울 수 있다.
    seoul_realtime_summary: SeoulRealtimeSummary | None = None
    # 도로 위 돌발상황 4분류 진행 건수(realtime_traffic 전용). 0건 포함 항상 4개다 —
    # 값이 없는 분류를 조용히 감추면 "이 지역엔 그 유형이 없다"는 뜻으로 읽힌다.
    road_incident_counts: list[RoadIncidentCategoryCount] = Field(default_factory=list)


class RoadIncidentCategoryCount(BaseModel):
    """도로 위 돌발상황 한 분류의 진행 중 건수(사고/고장·공사/집회·기상/화재·기타)."""

    label: str
    count: int = Field(ge=0)


class SeoulRealtimePaymentCategory(BaseModel):
    """최근 10분 결제 금액 상위 업종 한 건. 금액 단위는 원이고 구간으로만 온다."""

    label: str
    activity_level: str | None = None
    payment_count: int | None = None
    payment_amount_min: int | None = None
    payment_amount_max: int | None = None


class SeoulRealtimeSummary(BaseModel):
    """서울시 실시간 도시데이터에서 뽑은 인구·상권 요약 블록.

    인구 구획과 상권 구획은 서로 독립이다 — 서울시가 상권을 121곳 중 82곳에만
    제공하므로(D-084) 경복궁처럼 상권 값이 전부 비는 지역이 있다. 프론트는 값이
    없는 구획을 통째로 감춘다.

    현재 혼잡도 단계·기준 시각은 카드의 ``population_current_level`` /
    ``population_observed_at``에 이미 있어 여기서 중복해 두지 않는다.
    """

    population_min: int | None = None
    population_max: int | None = None
    # 예측 중 가장 붐비는 시간대("오후 5시")와 그때의 단계. 과거 추이는 서울시
    # API가 제공하지 않아 "오늘의 인기 시간대"가 아니라 앞으로의 예측이다.
    peak_forecast_hour_label: str | None = None
    peak_forecast_level: str | None = None
    top_age_label: str | None = None
    top_age_rate: float | None = None
    commercial_level: str | None = None
    commercial_observed_at: str | None = None
    payment_count: int | None = None
    payment_amount_min: int | None = None
    payment_amount_max: int | None = None
    top_payment_categories: list[SeoulRealtimePaymentCategory] = Field(default_factory=list)


class PopulationForecastBar(BaseModel):
    forecast_at: str
    congestion_level: str | None = None
    population_min: int | None = None
    population_max: int | None = None


class ConcentrationForecastBar(BaseModel):
    """관광지 집중률 API의 일 단위 예측을 카드 차트에 전달한다."""

    forecast_date: str
    concentration_rate: float = Field(ge=0)
    concentration_level: str
    concentration_label: str


class PlacePhotoItem(BaseModel):
    """장소 상세 화면에 여러 장으로 보여줄 사진 한 장.

    C의 ``PlacePhotoItem``을 그대로 옮긴 값이다. 목록 순서가 곧 보여줄 순서이고,
    첫 번째가 가장 대표적이다.
    """

    url: str
    image_name: str | None = None


class RealtimeInfoDetailItem(BaseModel):
    """실시간 INFO 상세 모달에 표시하는 서울시 데이터 항목."""

    title: str
    subtitle: str | None = None
    details: dict[str, str] = Field(default_factory=dict)
    thumbnail_url: str | None = None
    external_url: str | None = None
    # 항목별 길찾기용 좌표. 공중화장실처럼 목록의 각 항목이 목적지가 되는
    # 카드에서 채운다. 주소만 있는 항목(일부 민영주차장)은 없는 채로 둔다 —
    # 프론트가 주소 검색으로 폴백한다.
    latitude: float | None = None
    longitude: float | None = None


class RecommendationPlaceDetailRequest(BaseModel):
    """추천 카드 클릭으로 여는 장소 상세조회 요청.

    대화 발화가 아니므로 LLM·세션 상태를 거치지 않는다. ``place_name``은 C의 기존
    INFO 상세조회 입력이고, ``place_id``는 이름 해석이 다른 장소로 빗나가지 않았는지
    A가 응답을 대조하는 기준이다. 추천 카드처럼 클릭 대상의 id를 아는 경우에만
    채운다 — 혼잡도·행사 INFO 카드는 id 없이 이름으로 조회하며, 그때는 대조를
    건너뛴다(원래 이름으로 해석된 장소라 이름 재해석이 일관된다).
    """

    place_id: str | None = Field(default=None, max_length=100)
    place_name: str = Field(min_length=1, max_length=200)

    @field_validator("place_id", "place_name")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("장소 정보는 비어 있을 수 없습니다.")
        return normalized


class RecommendationPlaceDetailResponse(BaseModel):
    """추천 카드 상세 모달이 소비하는 단건 PlaceDetails 조회 결과."""

    status: Literal["success", "no_data", "unavailable"]
    requested_place_id: str | None = None
    place_card: InfoPlaceCard | None = None


class PlaceReasonRequest(BaseModel):
    """상세 카드의 "AI가 추천하는 이유" 문장만 따로 만드는 요청.

    **상세조회와 나눈 별도 호출이다.** 한 응답에 묶으면 문장 생성에 드는 1~2초가
    주소·운영시간·사진 전체의 대기 시간이 된다 — 부가 문장 하나 때문에 카드가
    통째로 늦는 것이 훨씬 나쁘다. 화면은 상세 카드를 먼저 그리고, 이 호출의
    응답이 오면 비워 둔 자리에 문장을 채운다.

    **``place_id``가 필수다.** 문장의 근거(취향 태그·후기)를 저장소에서 다시
    읽기 때문이다. 화면이 방금 받은 태그를 되돌려 받으면 조회 한 번을 아끼지만,
    화면이 준 문장을 그대로 LLM 입력으로 믿는 경로가 생긴다 — 그 대신 짧은
    저장소 조회를 한 번 더 한다. ``place_id``가 없는 카드(혼잡도·행사 INFO)는
    애초에 이 절 자체가 없어 화면이 이 호출을 하지 않는다.

    ``category_label``은 문장이 장소 종류를 잘못 말하지 않게 넘기는 분류
    라벨("카페/전통찻집")이다. 이 경로가 읽는 ``InfoPlaceCard``에는 분류 필드가
    없어서(관광 상세는 분류를 안 싣는다) 카드를 이미 들고 있는 화면에서 받는다.
    """

    place_id: str = Field(min_length=1, max_length=100)
    place_name: str = Field(min_length=1, max_length=200)
    category_label: str | None = Field(default=None, max_length=100)
    # 이번 추천에서 **사용자 취향과 일치한** 태그 코드들. 화면이 이미 들고 있는
    # `RecommendationItem.preference_tags[].is_query_match`가 그대로 여기로 온다.
    #
    # **코드만 받는다.** 위에서 "화면이 준 값을 LLM 입력으로 믿지 않는다"고 한
    # 원칙은 그대로다 — 이 코드는 저장소가 준 태그 중 어느 것을 먼저 말할지
    # 고르는 데만 쓰고, 문장에 실리는 라벨과 후기는 전부 저장소 조회 결과다.
    # 비어 있으면(취향 발화도 저장 취향도 없는 턴) 예전처럼 언급 수 순서다.
    matched_preference_codes: list[str] = Field(default_factory=list, max_length=10)

    @field_validator("matched_preference_codes")
    @classmethod
    def normalize_codes(cls, value: list[str]) -> list[str]:
        seen: set[str] = set()
        codes: list[str] = []
        for code in value:
            normalized = code.strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            codes.append(normalized[:100])
        return codes

    @field_validator("place_id", "place_name")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("장소 정보는 비어 있을 수 없습니다.")
        return normalized


class PlaceReasonResponse(BaseModel):
    """ "AI가 추천하는 이유" 문장 단건 생성 결과.

    **None이 정상 값이다.** 취향 태그가 없는 장소이거나, 설정이 꺼졌거나
    (PLACE_REASON_ENABLED), 생성이 실패한 경우 전부 None이다. 화면은 그때 카드가
    이미 들고 있는 고정 문장(`recommendation_reason`) 한 줄만 보여주므로, 이 값이
    없어도 절이 성립한다 — 그래서 이 호출은 실패해도 상세 카드를 건드리지 않는다.
    """

    ai_reason: str | None = None


class AgentResponse(BaseModel):
    """TODO(D 계약 확정 시 필드 변경 가능): Agent Runtime의 임시 최종 응답.

    recommendations는 RECOMMEND/MODIFY이고 status가 complete일 때만 채워진다(그 외에는
    None — Tool/Recommendation 단계 자체를 건너뛰었다는 뜻).
    schedule은 SCHEDULE이고 status가 complete일 때만 채워진다(docs/design/
    int-07-schedule.md 7절) — recommendations와 동시에 채워지지 않는다.
    message는 사용자에게 보여줄 챗봇 말풍선 텍스트다(docs/design/agent-response-
    generation.md 참고) — 카드(recommendations)·일정(schedule) 상세는 이 문장에
    다시 풀어쓰지 않는다.
    """

    llm_output: LLMOutput
    state: StateApplyResponse
    recommendations: RecommendationResponse | None = None
    schedule: ScheduleResult | None = None
    # COMPARE에서 C가 이름으로 보강한 추천 시점 Feature 스냅샷. 사용자 말풍선은
    # 이를 바탕으로 A의 LLM이 만들며, 개발자 Audit은 원본 비교 사실도 확인할 수 있다.
    comparison: ComparisonResult | None = None
    # INFO의 장소 상세 질의에서만 채운다. 질문 답변(fields)과 펼침 카드 정보는
    # 목적이 달라 InfoPlaceCard.answer_fields와 카드 상세를 분리해 보존한다.
    info_place_card: InfoPlaceCard | None = None
    # 근처 주차장/공영주차장처럼 서로 짝인 실시간 주차 질문을 하나 물으면 다른 쪽도
    # 이어서 조회해 둘째 카드로 붙인다(TP-115). info_place_card 바로 뒤, 두 번째
    # 말풍선 묶음으로 순차 표시된다 — message_footnote와 달리 카드 자체다.
    secondary_info_place_card: InfoPlaceCard | None = None
    message: str
    # message 본문에 넣기엔 긴 부가 정보 — 지금은 서비스 지역 밖 안내에서 지원 구
    # 목록을 여기 담는다. 화면은 이 필드가 있으면 본문 아래 작고 옅은 글씨로 보여준다
    # (D-085). 본문에 목록을 그대로 이어붙이면 구가 늘 때마다 문장이 길어지는데,
    # 그 성장을 본문과 분리된 각주 쪽에서만 받게 한다.
    message_footnote: str | None = None
    # 개발자용 Audit에서 1차 Intent/2차 추출 호출의 실제 Gemini 모델·폴백 경로를
    # 확인한다. Fake LLM 등 실행 메타데이터를 제공하지 않는 구현체에서는 None이다.
    # 이 턴 뒤에 버튼으로 보여줄 다음 발화 후보(0~3개). 버튼을 누르면 이 문구가
    # **그대로 user_input으로 재전송된다** — 되묻기 버튼(ClarificationPayload.options)이
    # id로 Intent를 못 박는 것과 다르다. 그쪽은 서버가 모르는 것을 물어 결정적으로
    # 분기하는 자리이고, 이쪽은 답변이 이미 끝난 뒤 다음 발화를 깔아주는 자리라
    # 사용자가 직접 입력한 것과 같은 경로를 타야 한다.
    # 만들지 못했거나 만들 게 없으면 빈 목록이고, 화면은 버튼을 띄우지 않는다.
    suggested_follow_ups: list[str] = Field(default_factory=list)
    llm_execution: LLMExecutionMetadata | None = None
    # 개발자용 Audit에서 C가 실제로 호출한 Provider·항목별 상태를 확인한다.
    # C 단계에 도달하지 못한 요청(LLM 실패, needs_clarification 등)에서는 None이다.
    tool_execution: ToolExecutionDebug | None = None
    # 한 요청 안에서 C가 여러 번 호출될 수 있으므로, 감사 패널은 이 목록을 우선 사용한다.
    # tool_execution은 이전 개발자 클라이언트 호환을 위해 첫/주요 호출을 계속 제공한다.
    tool_executions: list[ToolExecutionDebug] = Field(default_factory=list)
    # 이 턴을 기록한 Langfuse trace의 id. 관측이 꺼져 있으면 None이다(기본값).
    # **`state.trace_id`와 다른 값이다** — 그쪽은 run 내부 한 단계를 가리키는 B의
    # 식별자이고, 이건 턴 하나에 대응하는 관측 trace다. 그래서 이름을 `trace_id`로
    # 줄이지 않는다.
    # 평가 스크립트가 골드셋 케이스와 trace를 잇는 유일한 통로다. session_id로
    # 대신할 수 없다 — 세션은 LLM 단계 뒤에 발급돼서 첫 턴 trace에는 안 붙는다.
    langfuse_trace_id: str | None = None
