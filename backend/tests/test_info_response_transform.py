"""INFO 장소 상세 카드의 C→A 최종 응답 변환을 검증한다."""

from app.agent_context.info_schemas import (
    CommercialPaymentCategoryInfo,
    ConcentrationForecastInfo,
    ConcentrationInfoResult,
    DistrictAreaCongestionInfo,
    DistrictPopulationInfoResult,
    EventInfoResult,
    EventItem,
    InfoContextResponse,
    PlaceCard,
    PlaceInfoResult,
    PlacePhotoItem,
    PopulationAgeShareInfo,
    PopulationForecastInfo,
    RealtimeCityInfoResult,
    RealtimeCommercialInfoResult,
    RealtimeInfoDetailItem,
    RealtimePopulationInfoResult,
    RoadIncidentCategoryCountInfo,
    SeoulRealtimeSummaryInfo,
)
from app.services.runtime.info_response_transform import (
    to_answer_info_place_card,
    to_info_place_card,
)


def _response(
    *, status: str = "success", fields: dict[str, str] | None = None
) -> InfoContextResponse:
    return InfoContextResponse(
        request_id="info-card-test",
        status="success",
        result=PlaceInfoResult(
            status=status,  # type: ignore[arg-type]
            question_type="parking",
            fields=fields or {},
            place_card=PlaceCard(
                place_id="126508",
                place_name="경복궁",
                thumbnail_url="https://example.test/gyeongbokgung.jpg",
                overview="조선 왕조의 법궁이다.",
                operating_hours="09:00~18:00",
                parking="가능",
                parking_fee="무료",
                fee="성인 3,000원",
            ),
        ),
    )


def test_transforms_answer_fields_and_full_card_separately() -> None:
    card = to_info_place_card(_response(fields={"parking": "가능", "parking_fee": "무료"}))

    assert card is not None
    assert card.question_type.value == "parking"
    assert card.answer_fields == {"parking": "가능", "parking_fee": "무료"}
    assert card.overview == "조선 왕조의 법궁이다."
    assert card.operating_hours == "09:00~18:00"
    assert card.thumbnail_url == "https://example.test/gyeongbokgung.jpg"


def test_parking_hides_bus_capacity_from_answer_and_card() -> None:
    response = _response(fields={"parking": "가능 (승용차 240대 / 버스 50대)"})
    result = response.result
    assert isinstance(result, PlaceInfoResult)
    assert result.place_card is not None
    result.place_card.parking = "가능 (승용차 240대 / 버스 50대)"

    card = to_info_place_card(response)

    assert card is not None
    assert card.answer_fields["parking"] == "가능 (승용차 240대)"
    assert card.parking == "가능 (승용차 240대)"


def test_keeps_card_when_requested_field_has_no_data() -> None:
    """질문한 주차 정보가 없더라도 개요 등이 있으면 카드는 보여줄 수 있다."""
    card = to_info_place_card(_response(status="no_data"))

    assert card is not None
    assert card.answer_fields == {}
    assert card.place_name == "경복궁"


def test_location_info_without_c_place_card_still_returns_minimum_card() -> None:
    """C가 주소 조회에서 상세 API를 생략해도 INFO 카드 자체는 항상 보인다."""
    response = InfoContextResponse(
        request_id="no-card",
        status="success",
        result=PlaceInfoResult(
            status="success",
            question_type="location_info",
            requested_place_name="건청궁",
            resolved_place_name="건청궁",
            place_id="126508",
            fields={"address": "서울특별시 종로구 사직로 161"},
        ),
    )

    card = to_info_place_card(response)

    assert card is not None
    assert card.question_type.value == "location_info"
    assert card.place_name == "건청궁"
    assert card.place_id == "126508"
    assert card.answer_fields == {"address": "서울특별시 종로구 사직로 161"}
    assert card.thumbnail_url is None
    assert card.overview is None


def test_concentration_and_event_results_also_return_minimum_cards() -> None:
    concentration = to_info_place_card(
        InfoContextResponse(
            request_id="concentration-card",
            status="success",
            result=ConcentrationInfoResult(
                status="success",
                requested_place_name="경복궁",
                resolved_place_name="경복궁",
                forecast_date="2026-08-19",
                concentration_label="보통",
                forecasts=[
                    ConcentrationForecastInfo(
                        forecast_date="2026-08-19",
                        concentration_rate=42.0,
                        concentration_level="normal",
                        concentration_label="보통",
                    ),
                    ConcentrationForecastInfo(
                        forecast_date="2026-08-20",
                        concentration_rate=72.0,
                        concentration_level="crowded",
                        concentration_label="혼잡",
                    ),
                ],
            ),
        )
    )
    event = to_info_place_card(
        InfoContextResponse(
            request_id="event-card",
            status="success",
            result=EventInfoResult(
                status="success",
                requested_place_name="경복궁",
                resolved_place_name="경복궁",
                events=[
                    EventItem(
                        title="경복궁 별빛야행",
                        start_date="2026-08-19",
                        end_date="2026-08-20",
                    )
                ],
            ),
        )
    )

    assert concentration is not None
    assert concentration.answer_fields == {"concentration": "2026-08-19 · 보통"}
    forecast_labels = [
        (item.forecast_date, item.concentration_label)
        for item in concentration.concentration_forecasts
    ]
    assert forecast_labels == [
        ("2026-08-19", "보통"),
        ("2026-08-20", "혼잡"),
    ]
    assert event is not None
    assert event.answer_fields == {"event": "경복궁 별빛야행 (2026-08-19~2026-08-20)"}


def test_realtime_commercial_result_returns_proxy_card() -> None:
    card = to_info_place_card(
        InfoContextResponse(
            request_id="commercial-card",
            status="success",
            result=RealtimeCommercialInfoResult(
                status="success",
                requested_place_name="테스트 카페",
                area_name="용리단길",
                category_label="음식·음료 · 커피·음료",
                commercial_level="바쁜 시간대",
                observed_at="2026-08-20 14:00",
            ),
        )
    )

    assert card is not None
    assert card.question_type.value == "realtime_commercial"
    assert card.place_name == "테스트 카페"
    assert card.answer_fields["상권 지역"] == "용리단길"
    assert card.answer_fields["실시간 활동"] == "바쁜 시간대"
    assert card.answer_fields["기준 시각"] == "8월 20일 14:00"


def test_realtime_population_result_returns_current_level_and_forecasts() -> None:
    card = to_info_place_card(
        InfoContextResponse(
            request_id="population-card",
            status="success",
            result=RealtimePopulationInfoResult(
                status="success",
                requested_place_name="경복궁",
                area_name="광화문·덕수궁",
                proxy_distance_km=0.4,
                current_congestion_level="보통",
                current_congestion_message="사람이 몰려있을 수 있지만 크게 붐비지는 않아요.",
                observed_at="2026-08-20 14:00",
                population_forecasts=[
                    {
                        "forecast_at": "2026-08-20 15:00",
                        "congestion_level": "약간 붐빔",
                        "population_min": 3000,
                        "population_max": 3500,
                    }
                ],
                source_url="https://data.seoul.go.kr/example",
                map_url="https://data.seoul.go.kr/SeoulRtd/map?hotspotNm=test&y=127&x=37",
            ),
        )
    )

    assert card is not None
    assert card.question_type.value == "concentration"
    assert card.answer_fields["실시간 기준 지역"] == "광화문·덕수궁"
    assert card.answer_fields["안내"] == "사람이 몰려있을 수 있지만 크게 붐비지는 않아요."
    assert card.population_current_level == "보통"
    assert card.population_current_message == "사람이 몰려있을 수 있지만 크게 붐비지는 않아요."
    assert card.population_forecasts[0].congestion_level == "약간 붐빔"
    assert card.realtime_source_url == "https://data.seoul.go.kr/example"
    assert card.realtime_map_url is not None
    assert card.realtime_detail_items[0].title == "혼잡도 안내"
    assert "크게 붐비지는 않아요" in card.realtime_detail_items[0].details["안내"]
    # 예측이 하나뿐이라 "가장 붐빈다"고 짚어줄 대비 시간대가 없다.
    assert card.population_peak_forecast_summary is None


def test_realtime_population_result_summarizes_peak_forecast_hour() -> None:
    card = to_info_place_card(
        InfoContextResponse(
            request_id="population-peak-card",
            status="success",
            result=RealtimePopulationInfoResult(
                status="success",
                requested_place_name="경복궁",
                area_name="광화문·덕수궁",
                current_congestion_level="보통",
                observed_at="2026-08-20 14:00",
                population_forecasts=[
                    {"forecast_at": "2026-08-20 15:00", "congestion_level": "보통"},
                    {"forecast_at": "2026-08-20 16:00", "congestion_level": "붐빔"},
                    {"forecast_at": "2026-08-20 17:00", "congestion_level": "약간 붐빔"},
                ],
            ),
        )
    )

    assert card is not None
    assert card.population_peak_forecast_summary == (
        "16시(2시간 후)에 가장 붐빌 것으로 예상돼요. 혼잡정도는 붐빔일 것으로 예상돼요."
    )


def test_realtime_population_result_omits_peak_summary_without_observed_time() -> None:
    card = to_info_place_card(
        InfoContextResponse(
            request_id="population-no-observed",
            status="success",
            result=RealtimePopulationInfoResult(
                status="success",
                requested_place_name="경복궁",
                area_name="광화문·덕수궁",
                current_congestion_level="보통",
                observed_at=None,
                population_forecasts=[
                    {"forecast_at": "2026-08-20 15:00", "congestion_level": "보통"},
                    {"forecast_at": "2026-08-20 16:00", "congestion_level": "붐빔"},
                ],
            ),
        )
    )

    assert card is not None
    assert card.population_peak_forecast_summary is None


def test_realtime_commercial_area_overall_card_discloses_scope() -> None:
    card = to_info_place_card(
        InfoContextResponse(
            request_id="commercial-area-card",
            status="success",
            result=RealtimeCommercialInfoResult(
                status="success",
                requested_place_name="테스트 카페",
                area_name="용리단길",
                commercial_level="한산한",
                commercial_scope="area_overall",
            ),
        )
    )

    assert card is not None
    assert card.answer_fields["상권 기준"] == "지역 전체 상권 (요청 업종 세부값 미제공)"


def test_realtime_population_card_carries_seoul_realtime_summary() -> None:
    """인구 혼잡도 카드는 같은 citydata 응답의 상권 값까지 함께 싣는다."""

    card = to_info_place_card(
        InfoContextResponse(
            request_id="summary-card",
            status="success",
            result=RealtimePopulationInfoResult(
                status="success",
                requested_place_name="강남역",
                area_name="강남역",
                current_congestion_level="보통",
                observed_at="2026-09-05 16:25",
                population_forecasts=[
                    PopulationForecastInfo(
                        forecast_at="2026-09-05 17:00",
                        congestion_level="붐빔",
                        population_min=76000,
                        population_max=78000,
                    ),
                    PopulationForecastInfo(
                        forecast_at="2026-09-05 18:00",
                        congestion_level="여유",
                        population_min=20000,
                        population_max=22000,
                    ),
                ],
                realtime_summary=SeoulRealtimeSummaryInfo(
                    population_min=78000,
                    population_max=80000,
                    age_shares=[
                        PopulationAgeShareInfo(label="10대", rate=6.3),
                        PopulationAgeShareInfo(label="20대", rate=29.0),
                    ],
                    commercial_level="보통",
                    commercial_observed_at="20260905 1640",
                    payment_count=329,
                    payment_amount_min=7_900_000,
                    payment_amount_max=8_000_000,
                    top_payment_categories=[
                        CommercialPaymentCategoryInfo(
                            label="의료 · 병원",
                            activity_level="한산한",
                            payment_amount_min=1_300_000,
                            payment_amount_max=1_400_000,
                        )
                    ],
                ),
            ),
        )
    )

    assert card is not None
    summary = card.seoul_realtime_summary
    assert summary is not None
    assert (summary.population_min, summary.population_max) == (78_000, 80_000)
    assert (summary.top_age_label, summary.top_age_rate) == ("20대", 29.0)
    # 예측 피크(붐빔)가 현재(보통)보다 붐비므로 짚어준다.
    assert summary.peak_forecast_hour_label == "오후 5시"
    assert summary.peak_forecast_level == "붐빔"
    assert summary.commercial_observed_at == "9월 5일 16:40"
    assert summary.top_payment_categories[0].label == "의료 · 병원"


def test_seoul_realtime_summary_drops_peak_when_now_is_already_busier() -> None:
    """지금이 이미 예측 피크만큼 붐비면 "가장 붐빌 시간대"를 비운다.

    강남역 실측(2026-09-05 16:25)이 현재 붐빔·예측 최고 약간 붐빔이었다 —
    그대로 두면 지금보다 덜 붐비는 시각을 피크라고 보여준다.
    """

    card = to_info_place_card(
        InfoContextResponse(
            request_id="summary-peak",
            status="success",
            result=RealtimePopulationInfoResult(
                status="success",
                requested_place_name="강남역",
                area_name="강남역",
                current_congestion_level="붐빔",
                observed_at="2026-09-05 16:25",
                population_forecasts=[
                    PopulationForecastInfo(
                        forecast_at="2026-09-05 17:00",
                        congestion_level="약간 붐빔",
                        population_min=76000,
                        population_max=78000,
                    ),
                    PopulationForecastInfo(
                        forecast_at="2026-09-05 18:00",
                        congestion_level="보통",
                        population_min=40000,
                        population_max=42000,
                    ),
                ],
                realtime_summary=SeoulRealtimeSummaryInfo(
                    population_min=78000, population_max=80000
                ),
            ),
        )
    )

    assert card is not None
    assert card.seoul_realtime_summary is not None
    assert card.seoul_realtime_summary.peak_forecast_hour_label is None
    # 기존 요약 문장은 이 가드와 무관하게 그대로 둔다.
    assert card.population_peak_forecast_summary is not None


def test_realtime_commercial_card_carries_seoul_realtime_summary() -> None:
    card = to_info_place_card(
        InfoContextResponse(
            request_id="commercial-summary",
            status="success",
            result=RealtimeCommercialInfoResult(
                status="success",
                requested_place_name="테스트 카페",
                area_name="용리단길",
                commercial_level="바쁜 시간대",
                realtime_summary=SeoulRealtimeSummaryInfo(
                    commercial_level="바쁜 시간대",
                    payment_amount_min=1_600_000,
                    payment_amount_max=1_700_000,
                ),
            ),
        )
    )

    assert card is not None
    assert card.seoul_realtime_summary is not None
    assert card.seoul_realtime_summary.payment_amount_max == 1_700_000


def test_card_without_summary_leaves_seoul_realtime_summary_empty() -> None:
    """상권·인구 값이 아예 없는 지역이면 C가 요약을 안 보내고, 카드도 비운다."""

    card = to_info_place_card(
        InfoContextResponse(
            request_id="no-summary",
            status="success",
            result=RealtimePopulationInfoResult(
                status="success",
                requested_place_name="경복궁",
                area_name="경복궁",
                current_congestion_level="여유",
                observed_at="2026-09-05 16:25",
            ),
        )
    )

    assert card is not None
    assert card.seoul_realtime_summary is None


def test_realtime_city_card_keeps_detail_items_and_data_source() -> None:
    card = to_info_place_card(
        InfoContextResponse(
            request_id="realtime-parking-card",
            status="success",
            result=RealtimeCityInfoResult(
                status="success",
                question_type="realtime_parking",
                requested_place_name="경복궁",
                resolved_place_name="경복궁",
                area_name="광화문·덕수궁",
                observed_at="2026-08-20 16:20",
                fields={"테스트 공영주차장": "총 50면 · 현재 20대 주차 · 유료"},
                detail_items=[
                    RealtimeInfoDetailItem(
                        title="테스트 공영주차장",
                        subtitle="총 50면 · 현재 20대 주차 · 유료",
                        details={"거리": "약 200m", "기준 시각": "2026-08-20 16:20"},
                    )
                ],
                source_url="https://data.seoul.go.kr/example",
            ),
        )
    )

    assert card is not None
    assert card.realtime_area_name == "광화문·덕수궁"
    assert card.realtime_observed_at == "8월 20일 16:20"
    assert card.realtime_source_url == "https://data.seoul.go.kr/example"
    # realtime_parking은 road_incident_counts를 안 보낸다 — realtime_traffic 전용.
    assert card.road_incident_counts == []
    assert card.realtime_detail_items[0].details["거리"] == "약 200m"


def test_realtime_traffic_card_carries_road_incident_counts() -> None:
    card = to_info_place_card(
        InfoContextResponse(
            request_id="realtime-traffic-card",
            status="success",
            result=RealtimeCityInfoResult(
                status="success",
                question_type="realtime_traffic",
                requested_place_name="명동",
                resolved_place_name="명동 관광특구",
                area_name="명동 관광특구",
                observed_at="2026-09-06 18:30",
                fields={"도로소통 단계": "서행"},
                road_incident_counts=[
                    RoadIncidentCategoryCountInfo(label="사고/고장", count=0),
                    RoadIncidentCategoryCountInfo(label="공사/집회", count=2),
                    RoadIncidentCategoryCountInfo(label="기상/화재", count=0),
                    RoadIncidentCategoryCountInfo(label="기타", count=0),
                ],
            ),
        )
    )

    assert card is not None
    assert [(item.label, item.count) for item in card.road_incident_counts] == [
        ("사고/고장", 0),
        ("공사/집회", 2),
        ("기상/화재", 0),
        ("기타", 0),
    ]


def test_photos_reach_the_final_card_in_order() -> None:
    """상세 화면이 여러 장을 그리려면 순서까지 그대로 넘어와야 한다."""
    response = InfoContextResponse(
        request_id="info-card-photos",
        status="success",
        result=PlaceInfoResult(
            status="success",
            question_type="general_info",
            fields={"overview": "조선 왕조의 법궁이다."},
            place_card=PlaceCard(
                place_id="126508",
                place_name="경복궁",
                thumbnail_url="https://example.test/gyeongbokgung.jpg",
                photos=[
                    PlacePhotoItem(
                        url="https://tong.visitkorea.or.kr/126508-1.jpg",
                        image_name="경복궁 (1)",
                    ),
                    PlacePhotoItem(url="https://tong.visitkorea.or.kr/126508-2.jpg"),
                ],
            ),
        ),
    )

    card = to_info_place_card(response)

    assert card is not None
    assert [photo.url for photo in card.photos] == [
        "https://tong.visitkorea.or.kr/126508-1.jpg",
        "https://tong.visitkorea.or.kr/126508-2.jpg",
    ]
    assert card.photos[0].image_name == "경복궁 (1)"
    assert card.photos[1].image_name is None
    # 대표 이미지는 사진 목록과 별개로 남는다.
    assert card.thumbnail_url == "https://example.test/gyeongbokgung.jpg"


def test_card_without_photos_has_empty_list() -> None:
    """사진 목록이 없는 장소가 대부분이다.

    None이 아니라 빈 목록이어야 소비 측이 장수만 세면 된다.
    """
    card = to_info_place_card(_response(fields={"parking": "가능"}))

    assert card is not None
    assert card.photos == []


def test_무장애_아홉_항목을_카드로_이월한다() -> None:
    """C가 채운 값이 A 응답에서 조용히 사라지지 않는지 본다.

    이 저장소가 반복해 온 사고 유형이다 — 계약 모델을 필드별로 다시 조립하는
    자리에서 새 필드 하나만 빠지면, 테스트는 통과하는데 화면에서는 그 줄이
    영영 비어 있다.
    """
    response = _response()
    result = response.result
    assert isinstance(result, PlaceInfoResult)
    assert result.place_card is not None
    result.place_card.accessible_restroom = "장애인 화장실 있음(1층)"
    result.place_card.accessible_parking = "장애인 주차구역 2면"
    result.place_card.elevator = "엘리베이터 있음"
    result.place_card.visual_guide = "점자블록 있음 / 음성안내기 대여 가능"
    result.place_card.wheelchair_rental = "대여가능(2대, 안내데스크)"
    result.place_card.nursing_room = "수유실 있음(2층) / 기저귀교환대 있음"
    result.place_card.seating = "의자식 테이블 있음"
    result.place_card.stroller_rental = "대여가능(10대)"
    result.place_card.guide_dog = "보조견 동반 가능함"

    card = to_info_place_card(response)

    assert card is not None
    assert card.accessible_restroom == "장애인 화장실 있음(1층)"
    assert card.accessible_parking == "장애인 주차구역 2면"
    assert card.elevator == "엘리베이터 있음"
    assert card.visual_guide == "점자블록 있음 / 음성안내기 대여 가능"
    assert card.wheelchair_rental == "대여가능(2대, 안내데스크)"
    assert card.nursing_room == "수유실 있음(2층) / 기저귀교환대 있음"
    assert card.seating == "의자식 테이블 있음"
    assert card.stroller_rental == "대여가능(10대)"
    assert card.guide_dog == "보조견 동반 가능함"


def test_무장애_정보가_없는_장소는_전부_None이다() -> None:
    """전체 8,060곳 중 무장애 원문이 있는 곳은 1,229곳(15%)뿐이다."""
    card = to_info_place_card(_response())

    assert card is not None
    assert card.accessible_restroom is None
    assert card.elevator is None
    assert card.visual_guide is None
    assert card.nursing_room is None
    assert card.seating is None
    assert card.stroller_rental is None
    assert card.guide_dog is None


def _district_response(*areas: tuple[str, str, str | None]) -> InfoContextResponse:
    return InfoContextResponse(
        request_id="district-card",
        status="success",
        result=DistrictPopulationInfoResult(
            status="success",
            district_name="종로구",
            areas=[
                DistrictAreaCongestionInfo(
                    area_name=name, congestion_level=level, message=message
                )
                for name, level, message in areas
            ],
            observed_at="2026-09-09 14:00",
        ),
    )


def test_district_card_groups_areas_by_congestion_level() -> None:
    """같은 등급은 항목 하나로 묶는다.

    서울시가 주는 안내 문구는 같은 등급이면 글자까지 같다. 지역마다 항목을 두면
    종로구처럼 한 등급에 열 곳이 몰릴 때 같은 문장이 열 번 반복됐다.
    """

    card = to_info_place_card(
        _district_response(
            ("경복궁", "약간 붐빔", "붐빈다고 느낄 수 있어요."),
            ("인사동", "약간 붐빔", "붐빈다고 느낄 수 있어요."),
            ("혜화역", "보통", "크게 붐비지는 않아요."),
        )
    )

    assert card is not None
    items = card.realtime_detail_items
    assert [item.title for item in items] == ["약간 붐빔 2곳", "보통 1곳"]
    assert items[0].details == {"지역": "경복궁, 인사동"}
    # 안내 문구는 등급마다 한 번만.
    assert items[0].subtitle == "붐빈다고 느낄 수 있어요."


def test_district_card_puts_the_guidance_in_the_subtitle() -> None:
    """긴 안내 문장은 details가 아니라 subtitle에 둔다.

    details는 화면에서 두 칸 격자로 그려져(RealtimeDetailEntries) 문장이 절반 폭에
    갇히면 어색하게 접힌다. 제목 아래 한 줄로 흐르는 subtitle이 문장에 맞다.
    """

    card = to_info_place_card(_district_response(("경복궁", "붐빔", "많이 붐벼요.")))

    assert card is not None
    assert "안내" not in card.realtime_detail_items[0].details


def test_district_card_has_no_source_link() -> None:
    """서울 열린데이터광장 페이지는 데이터셋 설명이지 사용자가 읽을 화면이 아니다.

    목록 아래 링크 하나가 붙으면 "여기서 더 볼 수 있다"로 읽히는데 눌러 보면 그렇지
    않다. 지도 미리보기가 함께 있는 한 곳짜리 카드는 사정이 다르므로 그쪽은 그대로 둔다.
    """

    card = to_info_place_card(_district_response(("경복궁", "붐빔", None)))

    assert card is not None
    assert card.realtime_source_url is None
    assert card.realtime_map_url is None
    assert card.population_forecasts == []


def test_card_without_anything_to_read_is_not_made() -> None:
    """답이 "확인할 수 없어요"인데 상세 보기 버튼이 함께 나가지 않는다.

    장소명과 좌표만 있는 카드는 껍데기다. 열어 보면 빈 화면이고, 좌표 때문에 길찾기
    버튼까지 떠서 "강서구로 길찾기"가 열렸다 — 사용자가 구청에 가려던 것이 아니다.
    """

    card = to_answer_info_place_card(
        InfoContextResponse(
            request_id="empty",
            status="success",
            result=PlaceInfoResult(
                status="success",
                question_type="operating_hours",
                requested_place_name="강서구",
                resolved_place_name="서울특별시 강서구",
                fields={},
            ),
        )
    )

    assert card is None


def test_card_with_one_readable_field_survives() -> None:
    """읽을 것이 하나라도 있으면 만든다. 껍데기만 걸러내는 것이지 카드를 아끼는 게 아니다."""

    card = to_answer_info_place_card(
        InfoContextResponse(
            request_id="one-field",
            status="success",
            result=PlaceInfoResult(
                status="success",
                question_type="operating_hours",
                requested_place_name="경복궁",
                resolved_place_name="경복궁",
                fields={"operating_hours": "09:00~18:00"},
            ),
        )
    )

    assert card is not None
    assert card.answer_fields == {"operating_hours": "09:00~18:00"}


def test_detail_lookup_keeps_the_bare_card() -> None:
    """상세 조회 경로는 껍데기라도 그대로 받는다.

    거기서 카드는 읽을거리가 아니라 좌표를 실어 나르는 그릇이다 — 모달이 길찾기를
    열려면 목적지가 필요한데, 첫 응답에 좌표가 없는 카드는 이름으로 다시 조회해 받는다.
    껍데기를 버리면 그 길이 막힌다.
    """

    response = InfoContextResponse(
        request_id="detail",
        status="success",
        result=PlaceInfoResult(
            status="success",
            question_type="general_info",
            requested_place_name="창덕궁",
            resolved_place_name="창덕궁",
            fields={},
        ),
    )

    assert to_info_place_card(response) is not None
    assert to_answer_info_place_card(response) is None
