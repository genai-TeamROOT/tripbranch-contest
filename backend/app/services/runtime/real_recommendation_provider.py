"""RecommendationProvider Protocol을 만족하는 실제 D(Recommendation) 호출 구현체.

역할: C가 만든 RecommendationContext를 D의 공개 진입점
(app.services.recommendation_pipeline.run_recommendation_pipeline_from_context())에
그대로 넘겨 실제 추천 결과를 받는다. D 내부(candidate_mapper/scoring/evidence/
explanation)는 이 진입점 하나만 거쳐 호출하고 직접 import하지 않는다.
AppError는 여기서 잡지 않고 그대로 전파한다 — RecommendationProvider Protocol의
반환 타입에 에러 variant가 없고, app.main의 전역 exception_handler(AppError)가
처리하도록 하는 게 기존 코드베이스 관례(run_recommendation_pipeline())와 일치한다.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from datetime import datetime
from zoneinfo import ZoneInfo

from app.agent_context.enrichment_schemas import CandidateEnrichmentResponse
from app.config import settings
from app.domain.models import PlaceEvidenceMatch, PreferenceTag, PreferenceTagMatch
from app.domain.scoring import match_preference_tags
from app.domain.travel_route import TravelRoute
from app.providers.place_evidence import PlaceEvidenceProvider
from app.repositories.supabase_places import SupabasePlaceRepository
from app.schemas import (
    ConcentrationIntent,
    PlaceTag,
    PlaceType,
    PreferenceTagSummary,
    RecommendationItem,
    RecommendationResponse,
    UserConditions,
)
from app.services.recommendation_pipeline import (
    PreparedRecommendationResult,
    merge_prepared_recommendations,
    prepare_recommendation_from_context,
    rerank_with_co_visited,
    rerank_with_concentration,
    resolve_origin_name,
    resolve_weather_condition,
    score_prepared_recommendation,
)
from app.services.runtime.context_schemas import RecommendationContext
from app.services.runtime.recommendation_transform import (
    to_search_radius_km,
    to_search_radius_speed_km_per_min,
)
from app.tools.recommendation_cards import RecommendationCardTool

_KST = ZoneInfo("Asia/Seoul")
logger = logging.getLogger(__name__)

_RECOMMENDATION_LIMIT = 5

_COMPANION_PREFERENCE_CODES = {
    "solo": "alone",
    "couple": "date",
    "friend": "with_friends",
    "parent": "with_parents",
    "child": "with_kids",
}

# LLM이 만든 taste_query는 자연어이므로 DB 코드와 직접 비교할 수 없다. 공백·기호를
# 없앤 뒤 대표 표현을 찾는다. 동행은 위 구조화 companion 값을 우선 사용한다.
_REQUESTED_PREFERENCE_ALIASES: dict[str, tuple[str, ...]] = {
    "date": ("데이트", "연인", "커플", "소개팅"),
    "with_parents": ("부모님", "어르신", "엄마랑", "아빠랑", "효도"),
    "with_friends": ("친구와", "친구랑", "친구들", "우정"),
    "with_kids": ("아이와", "아이랑", "아이들", "아기와", "아기랑", "어린이", "키즈"),
    "alone": ("혼자", "나홀로", "혼밥", "혼술", "혼카페"),
    "photo_spot": ("사진찍", "포토존", "인생샷", "인생사진", "셀카"),
    "good_view": ("전망", "경치", "풍경", "조망", "야경", "뷰좋"),
    "healing": ("힐링", "휴식", "쉬기좋", "여유", "편안", "재충전"),
    "quiet": ("조용", "한적", "고요", "차분", "붐비지", "북적이지"),
    "experience": ("체험", "원데이", "클래스", "워크숍", "공방"),
    "night_visit": ("야간", "밤에", "밤산책", "저녁데이트", "일몰", "노을"),
    "indoor": ("실내", "비오는날", "비올때", "날씨상관", "우천"),
    "walk": ("산책", "걷기", "둘레길", "트레킹", "거닐"),
    "cozy": ("아늑", "포근", "아기자기", "정겨", "오붓"),
    "unique": ("이색", "독특", "색다른", "유니크", "특이"),
    "nature": ("자연", "숲", "녹지", "나무", "초록"),
    "culture_art": ("문화예술", "전시", "미술", "공연", "박물관", "갤러리"),
    "food_exploration": ("맛집투어", "먹거리", "디저트", "미식", "식도락"),
    "conversation": ("대화하기", "수다떨", "이야기하기"),
    "comfortable_seating": ("좌석편", "앉기편", "의자편"),
    "trendy_hotspot": ("핫플", "인스타", "트렌디", "힙한"),
    "group_gathering": ("모임하기", "단체모임", "회식", "단체로"),
    "spacious": ("넓고쾌적", "넓은곳", "공간넓"),
    "music_atmosphere": ("음악듣", "음악감상", "라이브음악", "LP음악"),
    "good_value": ("가성비", "가격좋", "저렴"),
    "study": ("카공", "공부하기", "스터디"),
    "work": ("작업하기", "노트북하기", "업무하기"),
    "private": ("프라이빗", "개인실", "룸있는", "독립공간"),
    "seasonal_visit": ("봄에", "여름에", "가을에", "겨울에", "단풍", "벚꽃"),
    "long_stay": ("오래머물", "장시간", "오래있"),
    "reading": ("책읽", "독서하기"),
    "picnic": ("피크닉", "돗자리", "도시락먹"),
    "lively": ("활기찬", "신나는", "북적이는", "생동감"),
}


def _compact_preference_query(value: str | None) -> str:
    return "".join(character for character in (value or "").lower() if character.isalnum())


def _requested_preference_codes(conditions: UserConditions) -> tuple[str, ...]:
    requested: list[str] = []
    companion = str(conditions.companion.value) if conditions.companion is not None else ""
    if code := _COMPANION_PREFERENCE_CODES.get(companion):
        requested.append(code)
    query = _compact_preference_query(conditions.taste_query)
    for code, aliases in _REQUESTED_PREFERENCE_ALIASES.items():
        if code not in requested and any(alias.lower() in query for alias in aliases):
            requested.append(code)
    return tuple(requested)


def _to_preference_tags(rows: Sequence[Mapping[str, object]]) -> tuple[PreferenceTag, ...]:
    """저장소의 취향 태그 행을 채점용 도메인 값으로 변환한다.

    추천 카드용 저장소 계약은 dict 행을 반환한다. 채점 계층까지 그 표현을
    흘려보내지 않고 여기서 숫자 기본값과 빈 코드를 정리한다. 일부 필드가 없는
    오래된 테스트·데이터는 0으로 다뤄 가산점을 만들지 않는다.
    """
    converted: list[PreferenceTag] = []
    for row in rows:
        code = str(row.get("preference_code") or "").strip()
        if not code:
            continue
        try:
            confidence = float(row.get("confidence") or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        try:
            positive_documents = int(row.get("positive_document_count") or 0)
        except (TypeError, ValueError):
            positive_documents = 0
        try:
            negative_documents = int(row.get("negative_document_count") or 0)
        except (TypeError, ValueError):
            negative_documents = 0
        converted.append(
            PreferenceTag(
                code=code,
                label=str(row.get("preference_label") or code),
                confidence=confidence,
                positive_documents=max(0, positive_documents),
                negative_documents=max(0, negative_documents),
            )
        )
    return tuple(converted)


# 단어 하나짜리 질의("조용한")는 문장형 리뷰 텍스트와 임베딩이 잘 안 맞는다.
# place_tag도 place_type도 모르는 요청의 마지막 폴백 — 아예 안 붙이는 것보다는
# 낫다(실측: 경복궁 카페 46곳에서 "조용한" 2곳 컷 통과 → "조용한 곳" 10곳).
_GENERIC_TASTE_SUFFIX = "곳"

# place_tag가 비었을 때 쓸 place_type의 한국어 라벨.
#
# "식당"/"레스토랑"처럼 넓은 유형을 말한 발화는 place_tags가 비고 place_types만
# 채워진다(태그는 한식·카페 같은 세분류만 있다). 그때 일반 접미어로 떨어지면
# 개선 효과를 거의 못 받아서, 유형명을 대신 붙인다.
#
# **라벨은 후보를 여러 개 실측해서 골랐다**(2026-08-23, 경복궁 반경 3km,
# 질의 "조용한", 컷 0.43 통과 수 / 매칭 수. 원자료는
# test_results/taste_query_enrichment.csv):
#
#   attraction         곳 19/168 → 관광지 63/168 (명소 58, 볼거리 49)
#   cultural_facility  곳 17/171 → 문화시설 36/171 (전시 48, 박물관 62)
#   shopping           곳  7/412 → 상점 26/414  (쇼핑 10, 쇼핑몰 8)
#   restaurant         곳 17/262 → 맛집 118/262 (음식점 57, 식당 46)
#
# **통과 수가 제일 큰 라벨을 그냥 고르지는 않았다.** cultural_facility는
# "박물관"(62건)이 수치상 제일 높은데도 "문화시설"(36건)을 택했다 — 인용문을
# 열어보니 "박물관"은 조용함이 아니라 박물관다움("알찬 박물관!", "퀄리티가
# 괜찮은 박물관")을 끌어왔다. 문화시설의 **일부 하위종**이라 도서관·갤러리
# 후보를 박물관 쪽으로 잘못 당긴다. 반면 restaurant의 "맛집"은 음식점 전반에
# 두루 쓰이는 리뷰 단어라 같은 왜곡이 없었다 — 인용문이 "조용하고 디저트도
# 맛있는", "한적한 공간", "고요한 안식처"처럼 조용함을 그대로 짚었다.
#
# festival·leisure는 **일부러 뺐다.** 효과가 없거나 오히려 나빠서다
# (축제 곳 1/32 → 축제 0/32 · 행사 0/32, 레저는 후보 8곳 전부 어느 라벨로도 0).
# 여기 없는 유형은 일반 접미어로 떨어진다.
_PLACE_TYPE_TASTE_LABELS: dict[PlaceType, str] = {
    PlaceType.ATTRACTION: "관광지",
    PlaceType.CULTURAL_FACILITY: "문화시설",
    PlaceType.SHOPPING: "상점",
    PlaceType.RESTAURANT: "맛집",
}

# 질의에 붙이면 **오히려 손해**인 place_tag. 위 place_type 표에서 festival을
# 뺀 것과 대칭이다 — 태그 경로에는 그 장치가 없어서 "축제"가 그대로 붙고 있었다.
#
# **실측(2026-08-24, 활성 2,220곳 종로·중구·용산, 취향 축 6종.**
# scripts/measure_taste_tag_enrichment.py, 원자료
# test_results/taste_tag_enrichment.csv). 각 축에서 "<취향> 곳" 대비
# "<취향> 축제"의 컷(0.43) 통과 수:
#
#   조용한 1→0 · 감성적인 18→11 · 빈티지하고 레트로한 분위기 10→6
#   분위기 좋은 20→12 · 데이트하기 좋은 19→13 · 혼자 가기 좋은 4→1
#
# **6축 전부 감소한 태그는 21개 중 축제뿐이다.** 인용문을 보면 이유가 보인다 —
# 관련 근거가 실재하는데("요란한 불꽃놀이나 큰 사운드는 없지만... 창경궁 특유의
# 조용한" 0.3765, "사람이 치이지 않고... 고궁의 고즈넉함" 0.3441) "축제"라는
# 단어가 유사도를 끌어내려 전부 컷 아래로 밀어낸다.
#
# **"공연장"도 넣으려다 실측으로 철회했다.** "조용한" 한 축의 인용문만 보고
# (좌석·동선 후기가 통과) 왜곡이라 판단했는데, 6축으로 재니 0/6 손해였다
# (조용한 1→17, 혼자 가기 좋은 1→26, 데이트하기 좋은 16→37). 한 축의 인용문은
# 태그를 빼는 근거로 부족하다. "양식"은 2/6(감성적인 15→5, 빈티지 6→4)이라
# 일관된 손해가 아니어서 제외하지 않았다.
#
# 취향 축은 전부 실 LLM(gemini-3.5-flash-lite)이 실제로 뽑은 taste_query
# 문자열이다. "저렴한"은 budget 필드로 가서 taste_query가 되지 않으므로 축에서
# 뺐다 — 시스템이 만들지 않는 질의로 재면 안 된다.
_TASTE_QUERY_EXCLUDED_TAGS: frozenset[PlaceTag] = frozenset({PlaceTag.FESTIVAL})


def _enrich_taste_query(conditions: UserConditions) -> str:
    """검색 질의에 장소 유형을 붙여 문장형 리뷰 텍스트와 임베딩이 더 잘
    맞게 만든다.

    실측(2026-08-23, 경복궁 반경 3km 카페 46곳 중 근거가 있는 45곳,
    `search_place_evidence` p_min_similarity=0.0): "조용한"은 컷(0.43) 통과
    2/45곳(평균 0.31)뿐인데 "조용한 카페"로 place_tag를 붙이면 38/45곳(평균
    0.48)으로 뛴다. 종로 4개 지점 전부에서 같은 폭으로 재현된다
    (1~2곳 → 24~38곳). 컷을 낮추는 대신 이 방법을 쓰는 이유는 장소 유형이
    하드 필터 단계에서 이미 확정된 값이라 새 정보를 만드는 게 아니고, 컷을
    낮출 때처럼 관련 없는 약한 매치를 끌어들이는 부작용도 없기 때문이다.

    붙일 말은 좁은 것부터 고른다 — place_tag(카페·박물관 등 세분류)가 있으면
    그걸 쓰고, 없으면 place_type의 라벨(`_PLACE_TYPE_TASTE_LABELS`), 그것도
    없으면 일반 접미어다. 단 `_TASTE_QUERY_EXCLUDED_TAGS`에 든 태그는 붙여봐야
    손해라 걸러내고, 남는 태그가 없으면 그대로 다음 단계로 내려간다 — 태그를
    걸러낸 것이 하드 필터에는 영향을 주지 않는다(후보는 이미 확정돼 있다).

    **발화 전체를 그대로 넣는 안은 실측으로 기각했다.** taste는 순위 축이라
    "얼마나 통과하나"가 아니라 "취향으로 갈라내나"가 본질인데, 발화 전체는
    후자를 못 한다 — 취향 단어만 뺀 중립 질의와의 순위상관이 0.88~0.97로,
    "고즈넉한"을 넣든 빼든 같은 곳이 뽑힌다. 블로그 리뷰가 "○○ 근처 ○○
    추천해요" 형식이라 위치·요청 어투가 그대로 매칭돼 취향 형용사가 묻힌다.
    자세한 수치와 다른 대안(유형 기준선 차감 등)은 package_D
    "[기록] 취향 근거 RAG 검색과 Scoring 반영.md" 9절에 있다.

    **단 동행 표현은 이 기각에 해당하지 않는다.** "아이들이랑 가기 좋은" 같은
    동행 문구는 `taste_query`에 그대로 남긴다(`recommend.extract` 2.4.0). 위
    기각은 위치·요청 어투("○○ 근처 추천해요")가 매칭되는 발화 **전체** 얘기고,
    동행은 리뷰가 실제로 장소를 서술할 때 쓰는 말이라 성격이 다르다 — 인용문에
    "부모님 모시고 가기 좋아요", "아이랑 가기 좋은 식당이고 좌식 방이 있어서"가
    실재한다. 위 문단이 상위권 기준으로 검증된 적 없는 ρ에 기대 이 안까지 함께
    기각하는 것으로 읽혀 실제로 한 번 기각됐으므로(TP-128), 상위 5곳 기준으로
    다시 쟀다: 취향이 묻힌 조합 0/6, 컷 통과 수는 6/6 감소하지만 순위 축에서
    통과 수는 판정 지표가 아니다(위 첫 문단과 같은 이유). TP-137, 원자료
    `test_results/taste_companion_cofill.csv`,
    `scripts/measure_taste_condition_dominance.py --scope companion`.
    """
    usable_tags = [tag for tag in conditions.place_tags if tag not in _TASTE_QUERY_EXCLUDED_TAGS]
    if usable_tags:
        return f"{conditions.taste_query} {' '.join(usable_tags)}"
    type_labels = [
        label
        for place_type in conditions.place_types
        if (label := _PLACE_TYPE_TASTE_LABELS.get(place_type)) is not None
    ]
    if type_labels:
        return f"{conditions.taste_query} {' '.join(type_labels)}"
    return f"{conditions.taste_query} {_GENERIC_TASTE_SUFFIX}"


def _log_taste_matches(
    query: str, candidate_count: int, matches: dict[str, PlaceEvidenceMatch]
) -> None:
    """취향 검색이 무엇을 찾았는지 한 줄로 남긴다.

    "taste가 0으로 나온다"는 관찰만으로는 검색이 아예 실패한 것인지, 컷을 넘는
    근거가 없어서 0점이 된 것인지 구분이 안 된다 — 이 로그로 그 둘을 가른다.

    **인용문은 찍지 않는다.** 같은 내용이 `RecommendationItem.taste_evidence`로
    응답에 실려 개발자 디버그 화면에 나오므로 로그로 반복하면 중복이다. 여기
    남기는 건 화면에 안 나오는 값(실제로 나간 **보강된 질의**와 후보 수)뿐이다.
    """
    logger.info(
        "취향 근거 검색: 질의=%r 후보=%d곳 → 매칭 %d곳%s",
        query,
        candidate_count,
        len(matches),
        "" if matches else " (컷 0.43 이상 근거 없음)",
    )


class RealRecommendationProvider:
    """RecommendationProvider Protocol 구현체 — D의 공개 진입점만 호출한다."""

    # 클래스 기본값으로 둔다. 이 클래스를 상속해 __init__을 부르지 않는
    # 테스트 대역(tests/test_agent_runtime.py)이 있어, 인스턴스 속성으로만
    # 두면 그쪽이 깨진다.
    _place_evidence: PlaceEvidenceProvider | None = None
    _preference_tags: SupabasePlaceRepository | None = None
    _recommendation_cards: RecommendationCardTool | None = None

    def __init__(
        self,
        place_evidence: PlaceEvidenceProvider | None = None,
        preference_tags: SupabasePlaceRepository | None = None,
        recommendation_cards: RecommendationCardTool | None = None,
    ) -> None:
        """취향 근거 Provider는 선택이다.

        None이면 채점이 taste Feature를 아예 쓰지 않는다. 임베딩 모델이 선택
        의존성이고 서버 상주 비용(실측 RSS 537MB)이 있어, 모델을 올릴 수 없는
        배포에서도 추천은 그대로 동작해야 하기 때문이다.

        도보 경로와 달리 A가 조회해 넘기지 않고 D가 직접 부른다 — 취향 발화는
        `conditions.taste_query`로 이미 도착해 있고, 이 값을 쓰는 곳이 D의 점수
        계산뿐이라 A가 중계할 이유가 없다.

        recommendation_cards(C의 RecommendationCardTool, 원래 COMPARE 전용)는
        썸네일만 빌려 쓴다(TECH-02: D는 C의 Tool을 직접 못 부르니 A가 대신
        불러 D의 결과에 병합한다). None이면 이미지 없이 추천한다.
        """
        self._place_evidence = place_evidence
        self._preference_tags = preference_tags
        self._recommendation_cards = recommendation_cards

    def merge_prepared(
        self,
        results: Sequence[PreparedRecommendationResult],
    ) -> PreparedRecommendationResult:
        return merge_prepared_recommendations(results)

    async def prepare(
        self,
        conditions: UserConditions,
        context: RecommendationContext,
        excluded_place_ids: list[str],
        *,
        visit_at: datetime,
        ignore_operating_hours: bool = False,
    ) -> PreparedRecommendationResult:
        return await prepare_recommendation_from_context(
            context,
            conditions=conditions,
            visit_at=visit_at,
            shown_place_ids=frozenset(excluded_place_ids),
            ignore_operating_hours=ignore_operating_hours,
        )

    async def score_prepared(
        self,
        conditions: UserConditions,
        prepared: PreparedRecommendationResult,
        *,
        travel_routes: tuple[TravelRoute, ...] = (),
        limit: int = _RECOMMENDATION_LIMIT,
        # 계정에 저장해 둔 취향으로 만든 질의. **발화에 취향이 있어도 값이 있다** —
        # 부딪히는 칩만 빠진 뒤에 온다(`domain/saved_preference.py`). conditions에
        # 합치지 않는 이유는 protocols.py의 같은 인자 주석 참고.
        saved_taste_query: str | None = None,
    ) -> RecommendationResponse:
        # 임베딩 검색은 태그 인식 여부와 무관하게 항상 실행한다. 사전에 있는 취향
        # (혼밥 → alone 등)은 후보군 상대 태그 점수도 만들고, D가 임베딩을 기본으로
        # 태그를 가산한다. 어느 한쪽 근거만 있는 후보도 취향 점수를 받을 수 있다.
        taste_tag_matches = await self._taste_tag_matches_for(conditions, prepared)
        taste_matches = await self._taste_matches_for(
            conditions, prepared, saved_taste_query=saved_taste_query
        )
        response = await score_prepared_recommendation(
            prepared,
            search_radius_km=to_search_radius_km(conditions),
            recommendation_limit=limit,
            # 실측을 요청 단위로 버리던 판정(옛 `_measured_routes_for()`)은
            # 없앴다. 그것은 "반경을 만든 속도와 실측한 이동수단의 속도가 같은
            # 요청에서만 쓸 수 있다"는 제약 때문이었는데, 예산이 더 이상 측정
            # 수단을 보지 않으므로(D-118) 어떤 수단으로 잰 값이든 같은 자로
            # 채점된다.
            travel_routes=travel_routes,
            travel_budget_speed_km_per_min=to_search_radius_speed_km_per_min(conditions),
            taste_matches=taste_matches,
            taste_tag_matches=taste_tag_matches,
        )
        response = await self._with_preference_tags(response, conditions)
        return await self._with_thumbnails(response)

    async def _with_thumbnails(self, response: RecommendationResponse) -> RecommendationResponse:
        """추천 결과에 C의 RecommendationCardTool로 조회한 썸네일을 붙인다.

        실패해도(조회 실패·설정 없음) 추천 자체는 그대로 유지한다 —
        _with_preference_tags와 같은 원칙이다. 찾지 못한 장소는 image_url이
        None으로 남고, 프론트는 그 경우 자리표시 칩을 그린다.
        """
        if self._recommendation_cards is None:
            return response
        items = [*response.recommendations, *response.unverified_recommendations]
        try:
            result = await self._recommendation_cards.get_cards([item.place_id for item in items])
        except Exception:
            logger.exception("추천 썸네일 조회 실패 — 이미지 없이 추천한다")
            return response

        # 폴백 주소도 함께 옮긴다. 작은 썸네일(firstimage2)만 관광공사 서버에서 사라진
        # 장소가 있어(아현시장 등 2% 안팎) 프론트가 실패했을 때 원본으로 갈아탄다.
        #
        # **분류 라벨도 같은 조회에서 나온다**(2026-09-08). 카드가 이미
        # `category_label`(TourAPI 신분류 중분류명 — 한식·전시시설)을 계산해 담고
        # 있는데 여기서 버리고 있었다. 응답의 `category`는 대분류 코드라
        # (`restaurant`·`shopping`) 화면에 영어가 그대로 찍혔다. **추가 조회는
        # 없다** — 이미 도는 get_cards()에서 필드 하나를 더 꺼낼 뿐이다.
        cards = {card.content_id: card for card in result.cards}

        def attach(item: RecommendationItem) -> RecommendationItem:
            card = cards.get(item.place_id)
            if card is None:
                return item
            update: dict[str, object] = {}
            # 썸네일은 둘 다 있거나 둘 다 없다 — 대표 이미지가 없으면 폴백도 없다.
            if card.thumbnail_url is not None:
                update["image_url"] = card.thumbnail_url
                update["image_url_fallback"] = card.fallback_thumbnail_url
            # 라벨은 썸네일과 독립이다. 사진 없는 장소(실측 20%)에도 분류는 있으므로
            # 위 조건에 묶으면 그 장소들이 라벨을 잃는다.
            if card.category_label is not None:
                update["category_label"] = card.category_label
            if not update:
                return item
            return item.model_copy(update=update)

        return response.model_copy(
            update={
                "recommendations": [attach(item) for item in response.recommendations],
                "unverified_recommendations": [
                    attach(item) for item in response.unverified_recommendations
                ],
            }
        )

    async def _with_preference_tags(
        self, response: RecommendationResponse, conditions: UserConditions
    ) -> RecommendationResponse:
        """추천 결과에 DB의 장소별 취향 태그를 붙인다. 실패해도 추천은 유지한다.

        **취향 스위치가 꺼져 있으면 붙이지 않는다**(`taste_evidence_enabled`).
        태그는 후기·블로그를 집계한 값이라 임베딩 검색과 같은 출처다 — 스위치가
        임베딩만 끄고 이쪽을 남기면 화면의 "장소별 방문자 취향 태그" 표가 계속
        뜬다. 빈 목록이 스키마의 "없음"이고(`preference_tags` 기본값), 화면은
        태그가 하나도 없으면 표 메시지를 만들지 않는다(frontend agentMessages.ts).

        저장소(`self._preference_tags`)를 None으로 주입하는 식으로 끄지 않은 이유는
        같은 저장소를 보관함 주입이 함께 쓰기 때문이다(agent_runtime.run_agent).
        """
        if self._preference_tags is None or not settings.taste_evidence_enabled:
            return response
        items = [*response.recommendations, *response.unverified_recommendations]
        try:
            tags_by_place = await self._preference_tags.find_preference_tags(
                [item.place_id for item in items]
            )
        except Exception:
            logger.exception("장소 취향 태그 조회 실패 — 태그 없이 추천한다")
            return response

        requested_codes = _requested_preference_codes(conditions)
        requested_rank = {code: index for index, code in enumerate(requested_codes)}

        def attach(item: RecommendationItem) -> RecommendationItem:
            rows = list(tags_by_place.get(item.place_id, ()))
            rows.sort(
                key=lambda row: (
                    0 if str(row.get("preference_code") or "") in requested_rank else 1,
                    requested_rank.get(str(row.get("preference_code") or ""), 99),
                    int(row.get("display_rank") or 999),
                )
            )
            summaries = [
                PreferenceTagSummary(
                    code=str(row.get("preference_code") or ""),
                    label=str(row.get("preference_label") or ""),
                    mention_count=int(row.get("mention_count") or 0),
                    is_query_match=str(row.get("preference_code") or "") in requested_rank,
                )
                for row in rows[:5]
            ]
            return item.model_copy(update={"preference_tags": summaries})

        return response.model_copy(
            update={
                "recommendations": [attach(item) for item in response.recommendations],
                "unverified_recommendations": [
                    attach(item) for item in response.unverified_recommendations
                ],
            }
        )

    async def _taste_tag_matches_for(
        self,
        conditions: UserConditions,
        prepared: PreparedRecommendationResult,
    ) -> dict[str, PreferenceTagMatch] | None:
        """발화에서 뽑은 사전 취향 코드로 후보의 취향 태그를 맞춘다.

        None을 돌려주면 호출부가 기존 임베딩 경로로 떨어진다. 그 경우는 둘이다 —
        발화에 사전 코드가 없거나(자유 표현 "빈티지한 분위기"), 태그 저장소가
        붙어 있지 않을 때다.

        **채점 대상 후보 전원의 태그를 읽는다.** `_with_preference_tags()`는
        채점이 끝난 뒤 노출되는 5곳 남짓만 읽지만, 순위를 이 값으로 정하려면
        후보 30곳이 다 필요하다. 같은 배치 조회라 왕복 횟수는 그대로다.

        조회 실패는 추천을 막지 않는다 — 취향은 순위를 다듬는 축이지 후보를
        만드는 축이 아니라서, 실패하면 임베딩 경로로 내려가는 편이 낫다
        (`_taste_matches_for()`와 같은 원칙).

        **취향 스위치가 꺼져 있으면 태그를 읽지 않고 None이다.** 스위치는 원래
        임베딩 검색만 막았는데(`get_place_evidence_provider`), 이 경로가 따로 살아
        있어 꺼도 "혼자"·"조용한" 같은 발화에서 taste Feature가 켜지고 가중치가
        바뀌었다. None이면 채점이 taste 키 자체를 만들지 않으므로(scoring.py
        `uses_taste`) 순위가 "취향 없음"과 같아진다 — 빈 dict를 주면 Feature가
        켜진 채 전원 0점이 되어 다른 축의 가중치가 줄어드니 None이어야 한다.
        """
        if self._preference_tags is None or not settings.taste_evidence_enabled:
            return None
        requested_codes = _requested_preference_codes(conditions)
        if not requested_codes:
            return None

        place_ids = [item.candidate.place_id for item in prepared.preparation.eligible_candidates]
        if not place_ids:
            return None

        try:
            tags_by_place = await self._preference_tags.find_preference_tags(place_ids)
        except Exception:
            logger.exception("취향 태그 조회 실패 — 임베딩 근거로 채점한다")
            return None

        converted_tags = {
            place_id: _to_preference_tags(tags_by_place.get(place_id, ()))
            for place_id in place_ids
        }
        max_positive_documents_by_code = {
            code: max(
                (
                    tag.positive_documents
                    for tags in converted_tags.values()
                    for tag in tags
                    if tag.code == code
                ),
                default=0,
            )
            for code in requested_codes
        }
        matches = {
            place_id: match_preference_tags(
                place_id,
                requested_codes,
                converted_tags[place_id],
                max_positive_documents_by_code=max_positive_documents_by_code,
            )
            for place_id in place_ids
        }
        matched = sum(1 for match in matches.values() if match.score > 0)
        logger.info(
            "취향 태그 채점: 코드=%s 후보=%d곳 → 일치 %d곳",
            ",".join(requested_codes),
            len(place_ids),
            matched,
        )
        return matches

    async def _taste_matches_for(
        self,
        conditions: UserConditions,
        prepared: PreparedRecommendationResult,
        *,
        saved_taste_query: str | None = None,
    ) -> dict[str, PlaceEvidenceMatch] | None:
        """취향 근거를 하드 필터 통과 후보 범위 안에서만 찾는다.

        None을 돌려주면 채점이 taste Feature를 쓰지 않는다. 빈 dict는 "취향을
        말했는데 근거를 못 찾았다"라 Feature가 켜지고 모든 후보가 0점이 된다 —
        둘을 구분해야 취향 미언급 요청의 가중치가 바뀌지 않는다.

        검색 실패는 추천 전체를 막지 않는다. 취향은 순위를 다듬는 축이지
        후보를 만드는 축이 아니라서, 실패하면 취향 없이 채점하는 편이 낫다.

        **계정에 저장해 둔 취향은 발화 뒤에 이어 붙인다.** 무엇을 붙일지는
        `domain/saved_preference.py`가 이미 정해서 `saved_taste_query`에 담아
        보낸다 — 발화와 겹치거나 부딪히는 칩은 거기서 빠진 뒤에 온다. 발화가
        없으면 이 값이 질의 전체가 된다.
        """
        if self._place_evidence is None:
            return None
        query_source = conditions.taste_query or saved_taste_query
        if not query_source:
            return None

        place_ids = [item.candidate.place_id for item in prepared.preparation.eligible_candidates]
        if not place_ids:
            return None

        # 저장값은 이미 취향 라벨만 모은 문자열이라 발화 질의처럼 장소 유형을
        # 덧붙이는 보강(`_enrich_taste_query`)을 태우지 않는다. 그 보강은 "조용한"
        # 같은 **한 단어 발화**가 임베딩에 안 걸리는 것을 고치려던 것인데(2/45 →
        # 38/45), 저장값은 보통 칩 3~5개가 이어져 있어 이미 문맥이 있다.
        #
        # **발화가 앞이다.** 벡터 하나로 합쳐 검색하므로 순서가 점수를 가르지는
        # 않지만, 로그에 남는 질의를 읽을 때 사용자가 방금 한 말이 먼저 보인다.
        spoken_query = _enrich_taste_query(conditions) if conditions.taste_query else None
        enriched_query = " ".join(part for part in (spoken_query, saved_taste_query) if part)
        try:
            result = await self._place_evidence.search(enriched_query, place_ids)
        except Exception:
            logger.exception("취향 근거 검색 실패 — 취향 없이 채점한다")
            return None
        _log_taste_matches(enriched_query, len(place_ids), result.data)
        return result.data

    async def recommend(
        self,
        conditions: UserConditions,
        context: RecommendationContext,
        excluded_place_ids: list[str],
        limit: int = _RECOMMENDATION_LIMIT,
        ignore_operating_hours: bool = False,
    ) -> RecommendationResponse:
        prepared = await self.prepare(
            conditions,
            context,
            visit_at=datetime.now(_KST),
            excluded_place_ids=excluded_place_ids,
            ignore_operating_hours=ignore_operating_hours,
        )
        return await self.score_prepared(conditions, prepared, limit=limit)

    async def rerank_with_concentration(
        self,
        conditions: UserConditions,
        context: RecommendationContext,
        first_pass: RecommendationResponse,
        concentration: CandidateEnrichmentResponse,
    ) -> RecommendationResponse:
        """D-040/D-051: 2차 Scoring. A는 concentration_intent가 AVOID/SEEK일 때만 이
        메서드를 호출한다(agent_runtime.py의 `_CONCENTRATION_RANK_INTENTS` 게이트) —
        그 외 값이 들어오면 방향을 정할 수 없으므로 AVOID(한적한 곳 선호)로 취급한다.

        날씨 판정은 1차 `recommend()`와 동일하게 `resolve_weather_condition()`으로
        여기서 다시 계산한다 — 호출부(agent_runtime.py)가 C의 옛 3단계 판정을 직접
        읽어 넘기던 방식(`to_weather_condition()`)은 1차와 2차의 판정이 갈라질 수
        있어 폐기했다. `context`는 1차 호출에 쓴 것과 같은 RecommendationContext여야
        한다.
        """
        seek = conditions.concentration_intent is ConcentrationIntent.SEEK
        weather_condition, weather_reason = resolve_weather_condition(context, conditions)
        return await rerank_with_concentration(
            first_pass,
            weather_condition,
            concentration,
            seek=seek,
            weather_reason=weather_reason,
            # 날씨 판정과 같은 이유로 context에서 뽑는다 — 안 넘기면 2차가 거리
            # 결측을 비례 재분배해 1차와 다른 가중치를 쓴다(D-119 후속, 1.9.0).
            district_scoped=context.district_scope is not None,
            # 날씨 판정과 같은 이유로 여기서 context에서 다시 뽑는다 — 1차와 2차가
            # 같은 기준점 이름을 써야 근거 문장이 갈리지 않는다.
            origin_name=resolve_origin_name(context, conditions),
        )

    async def rerank_with_co_visited(
        self,
        conditions: UserConditions,
        context: RecommendationContext,
        first_pass: RecommendationResponse,
        co_visited_pairs: Sequence[tuple[str, str]],
    ) -> RecommendationResponse:
        """D-092: RECOMMEND 2차 Scoring에 place_associations 기반 co-visit을
        반영한다. `rerank_with_concentration()`과 같은 이유로 날씨 판정만 여기서
        다시 계산하고(1차와 판정이 갈리지 않게), 점수 자체는
        `recommendation_pipeline.rerank_with_co_visited()`가 새로 얹는다.
        """
        weather_condition, weather_reason = resolve_weather_condition(context, conditions)
        return await rerank_with_co_visited(
            first_pass,
            co_visited_pairs,
            weather_condition,
            weather_reason=weather_reason,
            origin_name=resolve_origin_name(context, conditions),
            # `rerank_with_concentration()`과 같은 이유다(D-119 후속, 1.9.0).
            district_scoped=context.district_scope is not None,
        )


__all__ = ["RealRecommendationProvider"]
