"""C의 INFO 장소 카드 묶음을 A의 최종 응답 계약으로 변환한다."""

from __future__ import annotations

from app.agent_context.info_schemas import (
    ConcentrationInfoResult,
    DistrictPopulationInfoResult,
    EventInfoResult,
    InfoContextResponse,
    PlaceCard,
    PlaceInfoResult,
    PopulationAgeShareInfo,
    PopulationForecastInfo,
    RealtimeCityInfoResult,
    RealtimeCommercialInfoResult,
    RealtimePopulationInfoResult,
    SeoulRealtimeSummaryInfo,
)
from app.agent_context.info_schemas import (
    RealtimeInfoDetailItem as ContextRealtimeInfoDetailItem,
)
from app.agent_context.service import BUSY_CONGESTION_LEVELS
from app.schemas import (
    ConcentrationForecastBar,
    InfoPlaceCard,
    PlacePhotoItem,
    PopulationForecastBar,
    QuestionType,
    RealtimeInfoDetailItem,
    ReviewSource,
    RoadIncidentCategoryCount,
    SeoulRealtimePaymentCategory,
    SeoulRealtimeSummary,
)
from app.services.runtime.info_display import (
    format_citydata_timestamp,
    format_parking_for_display,
    parse_citydata_timestamp,
)

# 답변 아래 보여줄 출처 링크 수. 근거는 최대 여덟 건까지 검토하지만 화면에 줄줄이
# 걸면 답변보다 링크가 길어진다.
REVIEW_SOURCE_LIMIT = 3

# 서울시 원문 그대로의 인구 혼잡도 단계 — 값이 늘어나지 않는 한 이 4단계다
# (프론트 CONGESTION_HEIGHT와 순서를 맞춘다).
_CONGESTION_LEVEL_RANK = {"여유": 0, "보통": 1, "약간 붐빔": 2, "붐빔": 3}


def _peak_forecast(
    observed_at: str | None, forecasts: list[PopulationForecastInfo]
) -> tuple[PopulationForecastInfo, int] | None:
    """향후 예측 중 가장 붐비는 슬롯과 몇 시간 뒤인지를 고른다.

    과거 추이는 서울시 API가 애초에 제공하지 않아(미래 방향만 응답) 다루지
    않는다. 관측 시각과 예측 시각을 둘 다 실제 파싱해 시간 차를 구한다 —
    슬롯 간격이 항상 정확히 1시간이라고 가정하지 않는다.
    """

    observed = parse_citydata_timestamp(observed_at)
    if observed is None:
        return None

    ranked = [
        (forecast, _CONGESTION_LEVEL_RANK.get(forecast.congestion_level or "", -1))
        for forecast in forecasts
    ]
    ranked = [(forecast, rank) for forecast, rank in ranked if rank >= 0]
    if not ranked:
        return None
    if len({rank for _, rank in ranked}) == 1:
        # 전부 같은 단계면 "가장 붐빈다"고 짚어줄 시간대가 없다.
        return None

    # 최고 단계 중 가장 이른 시각을 고른다.
    best_forecast = None
    best_rank = -1
    best_hours_ahead = None
    for forecast, rank in ranked:
        peak_at = parse_citydata_timestamp(forecast.forecast_at)
        if peak_at is None:
            continue
        hours_ahead = round((peak_at - observed).total_seconds() / 3600)
        is_better = rank > best_rank or (
            rank == best_rank and best_hours_ahead is not None and hours_ahead < best_hours_ahead
        )
        if best_forecast is None or is_better:
            best_forecast, best_rank, best_hours_ahead = forecast, rank, hours_ahead
    if best_forecast is None or best_hours_ahead is None or best_hours_ahead <= 0:
        return None
    if parse_citydata_timestamp(best_forecast.forecast_at) is None:
        return None
    return best_forecast, best_hours_ahead


def _summarize_population_peak(
    observed_at: str | None, forecasts: list[PopulationForecastInfo]
) -> str | None:
    """가장 붐빌 시간대를 카드에 실을 한 줄 요약으로 만든다."""

    peak = _peak_forecast(observed_at, forecasts)
    if peak is None:
        return None
    best_forecast, best_hours_ahead = peak
    peak_at = parse_citydata_timestamp(best_forecast.forecast_at)
    if peak_at is None:
        return None
    level = best_forecast.congestion_level or "알 수 없음"
    return (
        f"{peak_at.hour}시({best_hours_ahead}시간 후)에 가장 붐빌 것으로 예상돼요. "
        f"혼잡정도는 {level}일 것으로 예상돼요."
    )


def _peak_forecast_hour_label(
    observed_at: str | None, forecasts: list[PopulationForecastInfo]
) -> tuple[str, str | None] | None:
    """가장 붐빌 시간대를 "오후 5시" 꼴 짧은 라벨과 그때의 단계로 돌려준다.

    서울시 앱의 "오늘의 인기 시간대"와 달리 이건 과거를 포함한 하루 통계가
    아니라 앞으로의 예측이다 — 원본이 미래 12시간만 주기 때문이다(D-090).
    """

    peak = _peak_forecast(observed_at, forecasts)
    if peak is None:
        return None
    best_forecast, _ = peak
    peak_at = parse_citydata_timestamp(best_forecast.forecast_at)
    if peak_at is None:
        return None
    meridiem = "오전" if peak_at.hour < 12 else "오후"
    return f"{meridiem} {peak_at.hour % 12 or 12}시", best_forecast.congestion_level


def _top_age_share(shares: list[PopulationAgeShareInfo]) -> PopulationAgeShareInfo | None:
    """비율이 가장 높은 연령대 한 건. 동률이면 어린 연령대가 이긴다(응답 순서)."""

    return max(shares, key=lambda share: share.rate, default=None)


def _is_not_busier_than_now(peak_level: str | None, current_level: str | None) -> bool:
    """예측 피크가 현재보다 더 붐비지 않는지 본다.

    두 단계 중 하나라도 우리가 모르는 값이면 판단하지 않고 False를 돌려준다 —
    모르는 단계 때문에 있는 정보를 감추지 않는다.
    """

    peak_rank = _CONGESTION_LEVEL_RANK.get(peak_level or "")
    current_rank = _CONGESTION_LEVEL_RANK.get(current_level or "")
    if peak_rank is None or current_rank is None:
        return False
    return peak_rank <= current_rank


def _to_seoul_realtime_summary(
    summary: SeoulRealtimeSummaryInfo | None,
    *,
    observed_at: str | None,
    forecasts: list[PopulationForecastInfo],
    current_level: str | None,
) -> SeoulRealtimeSummary | None:
    """C의 실시간 요약을 카드 계약으로 옮기고, 표시용 라벨만 여기서 만든다."""

    if summary is None:
        return None
    peak = _peak_forecast_hour_label(observed_at, forecasts)
    if peak is not None and _is_not_busier_than_now(peak[1], current_level):
        # 지금이 이미 예측 피크만큼 붐비면 "가장 붐빌 시간대"라고 짚을 시각이
        # 없다. 그대로 두면 강남역처럼 현재 붐빔·예측 최고 약간 붐빔인 지역에서
        # 지금보다 덜 붐비는 시각을 피크로 보여준다(2026-09-05 실측).
        peak = None
    top_age = _top_age_share(summary.age_shares)
    return SeoulRealtimeSummary(
        population_min=summary.population_min,
        population_max=summary.population_max,
        peak_forecast_hour_label=peak[0] if peak is not None else None,
        peak_forecast_level=peak[1] if peak is not None else None,
        top_age_label=top_age.label if top_age is not None else None,
        top_age_rate=top_age.rate if top_age is not None else None,
        commercial_level=summary.commercial_level,
        commercial_observed_at=format_citydata_timestamp(summary.commercial_observed_at),
        payment_count=summary.payment_count,
        payment_amount_min=summary.payment_amount_min,
        payment_amount_max=summary.payment_amount_max,
        top_payment_categories=[
            SeoulRealtimePaymentCategory(
                label=category.label,
                activity_level=category.activity_level,
                payment_count=category.payment_count,
                payment_amount_min=category.payment_amount_min,
                payment_amount_max=category.payment_amount_max,
            )
            for category in summary.top_payment_categories
        ],
    )


# 카드가 "껍데기뿐인가"를 볼 때 내용으로 치지 않는 필드.
#
# 장소명과 좌표는 카드가 무엇에 대한 것인지를 말할 뿐 사용자가 읽을 내용이 아니다.
# question_type도 마찬가지다.
_CARD_SKELETON_FIELDS = frozenset(
    {"question_type", "place_id", "place_name", "latitude", "longitude"}
)


def _has_content(card: InfoPlaceCard) -> bool:
    """카드에 사용자가 읽을 것이 하나라도 있는가."""

    return any(
        value not in (None, [], {}, "")
        for key, value in card.model_dump().items()
        if key not in _CARD_SKELETON_FIELDS
    )


def _build_info_place_card(response: InfoContextResponse) -> InfoPlaceCard | None:
    """장소가 확인된 모든 INFO 결과를 카드 묶음으로 AgentResponse에 전달한다.

    C의 ``location_info``·혼잡도·행사 경로는 비용을 아끼기 위해 PlaceDetails를
    조회하지 않아 ``place_card``가 비어 있을 수 있다. 이 경우에도 사용자가 INFO
    답변 아래에서 같은 장소 맥락을 확인할 수 있도록, C가 이미 확정한 장소명과
    답변 사실만으로 최소 카드를 만든다. Overview·썸네일 같은 상세는 C가 제공한
    경우에만 채운다.

    **다만 읽을 것이 하나도 없으면 카드를 만들지 않는다.** 답변이 "확인할 수 없어요"인데
    상세 보기 버튼이 함께 나가고, 눌러 보면 빈 화면이었다. 좌표만 실려 있으면 길찾기
    버튼까지 떠서 "강서구로 길찾기"가 열렸다 — 사용자가 구청에 가려던 것이 아니다
    (2026-09-09: 강서구 운영시간·입장료·편의시설, 식당의 혼잡도).

    **판정을 여기 한 곳에 둔다.** 결과 종류마다 카드 만드는 함수가 여섯 개인데, 각자
    빈 경우를 따로 챙기면 새 종류가 생길 때마다 빠뜨린다. 구 단위 혼잡도는 이미 자기
    함수에서 걸렀지만(areas가 비면 None), 그 방식을 나머지로 넓히는 대신 공통 자리로
    올린다.
    """

    result = response.result
    if isinstance(result, PlaceInfoResult):
        return _to_place_info_card(result)
    if isinstance(result, ConcentrationInfoResult):
        return _to_concentration_card(result)
    if isinstance(result, EventInfoResult):
        return _to_event_card(result)
    if isinstance(result, RealtimeCommercialInfoResult):
        return _to_realtime_commercial_card(result)
    if isinstance(result, RealtimePopulationInfoResult):
        return _to_realtime_population_card(result)
    if isinstance(result, DistrictPopulationInfoResult):
        return _to_district_population_card(result)
    if isinstance(result, RealtimeCityInfoResult):
        return InfoPlaceCard(
            question_type=QuestionType(result.question_type),
            answer_fields=result.fields,
            place_name=result.resolved_place_name or result.requested_place_name,
            realtime_area_name=result.area_name,
            realtime_observed_at=format_citydata_timestamp(result.observed_at),
            realtime_source_url=result.source_url,
            realtime_map_url=result.map_url,
            realtime_detail_items=_to_realtime_detail_items(result.detail_items),
            road_incident_counts=[
                RoadIncidentCategoryCount(label=item.label, count=item.count)
                for item in result.road_incident_counts
            ],
        )
    return None


def to_info_place_card(response: InfoContextResponse) -> InfoPlaceCard | None:
    """모달 상세 조회용. 껍데기라도 그대로 돌려준다.

    `/api/chat/place-details`가 이 함수를 쓴다. 그 경로에서 카드는 읽을거리가 아니라
    **좌표를 실어 나르는 그릇**이다 — 모달이 길찾기를 열려면 목적지가 필요한데, 첫
    응답에 좌표가 없는 카드(혼잡도·행사 등)는 여기서 이름으로 다시 조회해 받는다.
    껍데기를 버리면 그 길이 막힌다.

    채팅 답변에 붙일 카드는 `to_answer_info_place_card()`를 쓴다.
    """

    return _build_info_place_card(response)


def to_answer_info_place_card(response: InfoContextResponse) -> InfoPlaceCard | None:
    """채팅 답변 아래에 붙일 카드. 읽을 것이 없으면 만들지 않는다.

    답이 "확인할 수 없어요"인데 상세 보기 버튼이 함께 나가고, 눌러 보면 빈 화면이었다.
    좌표만 실려 있으면 길찾기 버튼까지 떠서 "강서구로 길찾기"가 열렸다 — 사용자가
    구청에 가려던 것이 아니다(2026-09-09: 강서구 운영시간·입장료·편의시설, 식당의
    혼잡도, 지원 지역 밖 장소의 혼잡도).

    **상세 조회와 갈라 둔다.** 같은 카드라도 두 자리에서 뜻이 다르다 — 여기서는 읽을거리라
    비면 소용이 없지만, 저쪽에서는 좌표 그릇이라 껍데기에도 쓸모가 있다.
    """

    card = _build_info_place_card(response)
    return card if card is not None and _has_content(card) else None


def _to_place_info_card(result: PlaceInfoResult) -> InfoPlaceCard:
    """상세 조회 유무와 관계없이 장소 정보 INFO 카드를 만든다."""

    card = result.place_card or PlaceCard(
        place_id=result.place_id,
        place_name=result.resolved_place_name or result.requested_place_name,
    )
    return InfoPlaceCard(
        question_type=QuestionType(result.question_type),
        answer_fields={
            key: format_parking_for_display(value) if key == "parking" else value
            for key, value in result.fields.items()
        },
        place_id=card.place_id,
        place_name=card.place_name,
        latitude=(
            result.destination_coordinates.latitude
            if result.destination_coordinates
            else None
        ),
        longitude=(
            result.destination_coordinates.longitude
            if result.destination_coordinates
            else None
        ),
        thumbnail_url=card.thumbnail_url,
        photos=[
            PlacePhotoItem(url=photo.url, image_name=photo.image_name)
            for photo in card.photos
        ],
        overview=card.overview,
        operating_hours=card.operating_hours,
        rest_date=card.rest_date,
        parking=format_parking_for_display(card.parking),
        parking_fee=card.parking_fee,
        fee=card.fee,
        baby_carriage=card.baby_carriage,
        pet=card.pet,
        credit_card=card.credit_card,
        restroom=card.restroom,
        homepage=card.homepage,
        # 무장애 아홉 항목(D-077). C가 이미 원문 정리·합성을 끝낸 값이라 여기서는
        # 그대로 옮기기만 한다 — 손대면 같은 원문이 답변과 카드에서 갈린다.
        accessible_restroom=card.accessible_restroom,
        accessible_parking=card.accessible_parking,
        elevator=card.elevator,
        visual_guide=card.visual_guide,
        wheelchair_rental=card.wheelchair_rental,
        nursing_room=card.nursing_room,
        seating=card.seating,
        stroller_rental=card.stroller_rental,
        guide_dog=card.guide_dog,
        review_sources=_to_review_sources(result),
    )


def _to_review_sources(result: PlaceInfoResult) -> list[ReviewSource]:
    """답변 근거가 된 문장을 같은 글당 한 번씩만 남긴다.

    한 글에서 여러 문장이 뽑히는 일은 검색 단계에서 이미 막혀 있지만, 초기 적재분은
    `document_id`가 없어 같은 URL이 두 번 올 여지가 있다. **링크가 없어도 담는다** —
    인용은 그 자체로 근거이고, 링크는 더 읽고 싶을 때 쓰는 것이다.
    """
    sources: list[ReviewSource] = []
    seen: set[str] = set()
    for item in result.review_evidence:
        text = item.text.strip()
        if not text:
            continue
        url = (item.source_url or "").strip()
        if url:
            if url in seen:
                continue
            seen.add(url)
        sources.append(
            ReviewSource(
                text=text,
                url=url or None,
                source_type=item.source_type,
                published_at=item.published_at,
            )
        )
        if len(sources) >= REVIEW_SOURCE_LIMIT:
            break
    return sources


def _to_concentration_card(result: ConcentrationInfoResult) -> InfoPlaceCard | None:
    """혼잡도 결과도 장소가 확인됐을 때 최소 카드로 보여준다."""

    place_name = result.requested_place_name or result.resolved_place_name
    if place_name is None:
        return None

    value_parts = [part for part in (result.forecast_date, result.concentration_label) if part]
    return InfoPlaceCard(
        question_type=QuestionType.CONCENTRATION,
        answer_fields={"concentration": " · ".join(value_parts)} if value_parts else {},
        place_name=place_name,
        concentration_forecasts=[
            ConcentrationForecastBar(
                forecast_date=forecast.forecast_date,
                concentration_rate=forecast.concentration_rate,
                concentration_level=forecast.concentration_level,
                concentration_label=forecast.concentration_label,
            )
            for forecast in result.forecasts
        ],
    )


def _to_event_card(result: EventInfoResult) -> InfoPlaceCard | None:
    """행사 INFO도 확정된 장소명을 중심으로 최소 카드를 보여준다.

    realtime_event 카드와 같은 가로 스크롤 사진 카드로 그리도록, event 항목도
    realtime_detail_items(제목/부제/썸네일) 모양으로 옮긴다 — 프론트가 이미
    그 모양으로 PlaceCardRow를 그리는 컴포넌트를 갖고 있다.
    """

    place_name = result.resolved_place_name or result.requested_place_name
    if place_name is None:
        return None

    event_lines = [
        f"{event.title} ({event.start_date}~{event.end_date})" for event in result.events
    ]
    return InfoPlaceCard(
        question_type=QuestionType.EVENT,
        answer_fields={"event": "\n".join(event_lines)} if event_lines else {},
        place_name=place_name,
        realtime_detail_items=[
            RealtimeInfoDetailItem(
                title=event.title,
                subtitle=f"{event.start_date}~{event.end_date}",
                thumbnail_url=event.image_url,
            )
            for event in result.events
        ],
    )


def _to_realtime_commercial_card(
    result: RealtimeCommercialInfoResult,
) -> InfoPlaceCard | None:
    """개별 매장 대신 조회한 지역·업종 상권 활동을 최소 INFO 카드로 보인다."""

    place_name = result.resolved_place_name or result.requested_place_name
    if place_name is None:
        return None

    scope_label = (
        "요청 업종"
        if result.commercial_scope != "area_overall"
        else "지역 전체 상권 (요청 업종 세부값 미제공)"
    )
    fields = {
        key: value
        for key, value in {
            "상권 지역": result.area_name,
            "상권 기준": scope_label,
            "업종": result.category_label,
            "실시간 활동": result.commercial_level,
            "기준 시각": format_citydata_timestamp(result.observed_at),
        }.items()
        if value is not None
    }
    return InfoPlaceCard(
        question_type=QuestionType.REALTIME_COMMERCIAL,
        answer_fields=fields,
        place_name=place_name,
        population_current_level=result.population_current_level,
        population_observed_at=format_citydata_timestamp(result.population_observed_at),
        population_forecasts=[
            PopulationForecastBar(
                forecast_at=forecast.forecast_at,
                congestion_level=forecast.congestion_level,
                population_min=forecast.population_min,
                population_max=forecast.population_max,
            )
            for forecast in result.population_forecasts
        ],
        realtime_area_name=result.area_name,
        realtime_observed_at=format_citydata_timestamp(result.observed_at),
        realtime_source_url=result.source_url,
        realtime_detail_items=_to_realtime_detail_items(result.detail_items),
        seoul_realtime_summary=_to_seoul_realtime_summary(
            result.realtime_summary,
            observed_at=result.population_observed_at,
            forecasts=result.population_forecasts,
            current_level=result.population_current_level,
        ),
    )


def _to_realtime_population_card(
    result: RealtimePopulationInfoResult,
) -> InfoPlaceCard | None:
    """현재 인구 혼잡도와 12시간 예측을 concentration 카드에 함께 싣는다."""

    place_name = result.resolved_place_name or result.requested_place_name
    if place_name is None:
        return None

    fields = {
        key: value
        for key, value in {
            "실시간 기준 지역": result.area_name,
            "현재 인구 혼잡도": result.current_congestion_level,
            "기준 시각": format_citydata_timestamp(result.observed_at),
            "안내": result.current_congestion_message,
        }.items()
        if value is not None
    }
    return InfoPlaceCard(
        question_type=QuestionType.CONCENTRATION,
        answer_fields=fields,
        place_name=place_name,
        population_current_level=result.current_congestion_level,
        population_current_message=result.current_congestion_message,
        population_observed_at=format_citydata_timestamp(result.observed_at),
        population_peak_forecast_summary=_summarize_population_peak(
            result.observed_at, result.population_forecasts
        ),
        population_forecasts=[
            PopulationForecastBar(
                forecast_at=forecast.forecast_at,
                congestion_level=forecast.congestion_level,
                population_min=forecast.population_min,
                population_max=forecast.population_max,
            )
            for forecast in result.population_forecasts
        ],
        realtime_area_name=result.area_name,
        realtime_observed_at=format_citydata_timestamp(result.observed_at),
        realtime_source_url=result.source_url,
        realtime_map_url=result.map_url,
        realtime_detail_items=(
            [
                RealtimeInfoDetailItem(
                    title="혼잡도 안내",
                    subtitle=result.current_congestion_level,
                    details={"안내": result.current_congestion_message},
                )
            ]
            if result.current_congestion_message is not None
            else []
        ),
        seoul_realtime_summary=_to_seoul_realtime_summary(
            result.realtime_summary,
            observed_at=result.observed_at,
            forecasts=result.population_forecasts,
            current_level=result.current_congestion_level,
        ),
    )


def _to_district_population_card(
    result: DistrictPopulationInfoResult,
) -> InfoPlaceCard | None:
    """구 하나를 통째로 물었을 때의 혼잡도 카드.

    **12시간 예측 막대와 지도를 채우지 않는다.** 둘 다 지역 한 곳을 전제로 그린다 —
    종로구 14곳의 예측을 한 그래프에 겹칠 수 없고, 지도도 어느 지역 것을 띄울지 정할
    수 없다. 값을 비우면 화면이 그 자리를 아예 그리지 않는다(조건부 렌더링).

    지역 목록은 `realtime_detail_items`에 담는다. 공중화장실·주차장 목록이 이미 쓰는
    자리라 화면을 새로 만들지 않아도 된다.
    """

    if not result.areas:
        return None

    # 세는 규칙은 답변 문장과 같아야 한다 — 말풍선은 "2곳", 카드는 "13곳"이면
    # 같은 화면이 서로 다른 말을 한다(response_composer._BUSY_LEVELS).
    busy = [area for area in result.areas if area.congestion_level in BUSY_CONGESTION_LEVELS]
    fields = {
        "실시간 기준 지역": f"{result.district_name} {len(result.areas)}곳",
        "지금 붐비는 곳": f"{len(busy)}곳" if busy else "없음",
    }
    if result.observed_at:
        fields["기준 시각"] = format_citydata_timestamp(result.observed_at) or ""
    if result.unavailable_area_count:
        # 못 본 곳을 숨기지 않는다 — "14곳 중"이라고 해놓고 12곳만 보여주면
        # 사용자는 두 곳이 어디로 갔는지 알 수 없다.
        fields["조회 실패"] = f"{result.unavailable_area_count}곳"

    return InfoPlaceCard(
        question_type=QuestionType.CONCENTRATION,
        answer_fields={key: value for key, value in fields.items() if value},
        place_name=result.district_name,
        realtime_area_name=result.district_name,
        realtime_observed_at=format_citydata_timestamp(result.observed_at),
        # **출처 링크를 붙이지 않는다.** 서울 열린데이터광장 페이지는 데이터셋 설명과
        # 신청 안내이지 사용자가 읽을 혼잡도 화면이 아니다. 구 단위 답은 지역 목록이
        # 본문이라 그 목록 아래 링크가 하나 붙으면 "여기서 더 볼 수 있다"로 읽히는데,
        # 눌러 보면 그렇지 않다. 한 곳짜리 카드는 지도 미리보기가 함께 있어 사정이
        # 다르므로 그쪽은 그대로 둔다.
        realtime_detail_items=_district_congestion_items(result),
    )


def _district_congestion_items(
    result: DistrictPopulationInfoResult,
) -> list[RealtimeInfoDetailItem]:
    """등급 하나를 항목 하나로 묶는다.

    **지역마다 한 항목씩 두지 않는다.** 같은 등급이면 서울시가 주는 안내 문구가 글자
    하나까지 같아서, 종로구처럼 한 등급에 열 곳이 몰리면 같은 문장이 열 번 반복됐다
    (2026-09-09). 읽을 것이 늘지 않는데 화면만 길어진다.

    긴 안내 문장은 `subtitle`에 둔다. `details`는 두 칸 격자로 그려져(모달의
    RealtimeDetailEntries) 문장이 절반 폭에 갇히면 어색하게 접힌다 — 제목 아래 한 줄로
    흐르는 `subtitle`이 문장에 맞다.
    """

    grouped: dict[str, list[str]] = {}
    messages: dict[str, str | None] = {}
    for area in result.areas:
        grouped.setdefault(area.congestion_level, []).append(area.area_name)
        messages.setdefault(area.congestion_level, area.message)

    return [
        RealtimeInfoDetailItem(
            title=f"{level} {len(names)}곳",
            subtitle=messages.get(level),
            details={"지역": ", ".join(names)},
        )
        for level, names in grouped.items()
    ]


def _to_realtime_detail_items(
    items: list[ContextRealtimeInfoDetailItem],
) -> list[RealtimeInfoDetailItem]:
    """C의 실시간 도시데이터 상세 항목을 최종 응답 스키마로 옮긴다."""

    return [
        RealtimeInfoDetailItem(
            title=item.title,
            subtitle=item.subtitle,
            details=item.details,
            thumbnail_url=item.thumbnail_url,
            external_url=item.external_url,
            latitude=item.latitude,
            longitude=item.longitude,
        )
        for item in items
    ]
