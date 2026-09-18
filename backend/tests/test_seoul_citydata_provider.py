"""서울시 실시간 상권현황 Provider의 응답 정규화를 검증한다."""

from __future__ import annotations

import httpx
import pytest

from app.providers.seoul_citydata import (
    RealRealtimeCommercialProvider,
    map_realtime_commercial_response,
    map_realtime_parking_response,
    map_realtime_population_response,
    map_realtime_traffic_response,
)


def _payload() -> dict[str, object]:
    return {
        "citydata_cmrcl": {
            "RESULT": {"CODE": "INFO-000", "MESSAGE": "정상 처리되었습니다."},
            "row": [
                {
                    "AREA_NM": "용리단길",
                    "AREA_CD": "POI076",
                    "LIVE_CMRCL_STTS": [
                        {
                            "AREA_CMRCL_LVL": "보통 시간대",
                            "CMRCL_TIME": "2026-08-20 14:00",
                            "CMRCL_RSB": [
                                {
                                    "RSB_LRG_CTGR": "음식·음료",
                                    "RSB_MID_CTGR": "커피·음료",
                                    "RSB_PAYMENT_LVL": "바쁜 시간대",
                                }
                            ],
                        }
                    ],
                }
            ],
        }
    }


def _flat_payload() -> dict[str, object]:
    """2026-08-20 실 API에서 확인한 citydata_cmrcl 응답 형태."""

    return {
        "RESULT": {"resultCode": "INFO-000", "resultMsg": "정상 처리되었습니다."},
        "AREA_NM": "용리단길",
        "AREA_CD": "POI076",
        "LIVE_CMRCL_STTS": {
            "AREA_CMRCL_LVL": "보통 시간대",
            "CMRCL_TIME": "2026-08-20 14:00",
            "CMRCL_RSB": [
                {
                    "RSB_LRG_CTGR": "음식·음료",
                    "RSB_MID_CTGR": "커피·음료",
                    "RSB_PAYMENT_LVL": "바쁜 시간대",
                }
            ],
        },
    }


def test_map_realtime_commercial_response_extracts_cafe_category() -> None:
    result = map_realtime_commercial_response(_payload(), requested_area="POI076")

    assert result.area_name == "용리단길"
    assert result.area_code == "POI076"
    assert result.area_activity_level == "보통 시간대"
    assert result.observed_at == "2026-08-20 14:00"
    assert result.categories[0].middle_category == "커피·음료"
    assert result.categories[0].activity_level == "바쁜 시간대"


def test_map_realtime_commercial_response_keeps_payment_amounts() -> None:
    """결제 건수·금액은 2026-09-05 강남역 실측에서 확인한 필드다."""

    payload = {
        "AREA_NM": "강남역",
        "AREA_CD": "POI014",
        "LIVE_CMRCL_STTS": {
            "AREA_CMRCL_LVL": "보통",
            "CMRCL_TIME": "20260905 1640",
            "AREA_SH_PAYMENT_CNT": "329",
            "AREA_SH_PAYMENT_AMT_MIN": "7900000",
            "AREA_SH_PAYMENT_AMT_MAX": "8000000",
            "CMRCL_RSB": [
                {
                    "RSB_LRG_CTGR": "의료",
                    "RSB_MID_CTGR": "병원",
                    "RSB_PAYMENT_LVL": "한산한",
                    "RSB_SH_PAYMENT_CNT": 11,
                    "RSB_SH_PAYMENT_AMT_MIN": 1300000,
                    "RSB_SH_PAYMENT_AMT_MAX": 1400000,
                }
            ],
        },
    }

    result = map_realtime_commercial_response(payload, requested_area="강남역")

    assert result.payment_count == 329
    assert result.payment_amount_min == 7_900_000
    assert result.payment_amount_max == 8_000_000
    assert result.categories[0].payment_count == 11
    assert result.categories[0].payment_amount_max == 1_400_000


def test_map_realtime_population_response_keeps_headcount_and_age_shares() -> None:
    """현재 인구 구간과 연령대 비율도 같은 응답에 들어 있다(2026-09-05 강남역)."""

    payload = {
        "CITYDATA": {
            "LIVE_PPLTN_STTS": [
                {
                    "AREA_NM": "강남역",
                    "AREA_CONGEST_LVL": "붐빔",
                    "AREA_PPLTN_MIN": "78000",
                    "AREA_PPLTN_MAX": "80000",
                    "PPLTN_RATE_0": "0.8",
                    "PPLTN_RATE_10": "6.3",
                    "PPLTN_RATE_20": "29.0",
                    "PPLTN_RATE_70": "3.4",
                    "PPLTN_TIME": "2026-09-05 16:25",
                    "FCST_YN": "Y",
                    "FCST_PPLTN": [],
                }
            ]
        }
    }

    result = map_realtime_population_response(payload, requested_area="강남역")

    assert result.current_population_min == 78_000
    assert result.current_population_max == 80_000
    # 응답에 없는 구간은 건너뛰고, 있는 것만 어린 연령대부터 순서대로 담는다.
    assert [(share.label, share.rate) for share in result.age_shares] == [
        ("10세 미만", 0.8),
        ("10대", 6.3),
        ("20대", 29.0),
        ("70대 이상", 3.4),
    ]


def test_map_realtime_population_response_without_headcount_fields() -> None:
    """예전 응답 형태처럼 인구 수가 없어도 혼잡도 단계는 그대로 나온다."""

    payload = {"CITYDATA": {"LIVE_PPLTN_STTS": [{"AREA_CONGEST_LVL": "여유", "FCST_YN": "N"}]}}

    result = map_realtime_population_response(payload, requested_area="경복궁")

    assert result.current_congestion_level == "여유"
    assert result.current_population_min is None
    assert result.age_shares == ()


def test_map_realtime_commercial_response_supports_live_flat_payload() -> None:
    result = map_realtime_commercial_response(_flat_payload(), requested_area="용리단길")

    assert result.area_name == "용리단길"
    assert result.area_code == "POI076"
    assert result.categories[0].middle_category == "커피·음료"


@pytest.mark.asyncio
async def test_real_provider_uses_area_code_without_leaking_key() -> None:
    seen_path = ""

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_path
        seen_path = request.url.path
        return httpx.Response(200, json=_payload())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = RealRealtimeCommercialProvider(api_key="sensitive-key", client=client)
        wrapped = await provider.get_area_commercial_status("POI076")

    assert seen_path.endswith("/citydata_cmrcl/1/1/POI076")
    assert "sensitive-key" in seen_path  # 서울시 API는 인증키를 경로에 요구한다.
    assert wrapped.metadata.status.value == "success"
    assert wrapped.data.categories[0].large_category == "음식·음료"


def _citydata_payload(prk_stts: list[dict[str, object]]) -> dict[str, object]:
    return {"CITYDATA": {"AREA_NM": "교대역", "AREA_CD": "POI999", "PRK_STTS": prk_stts}}


def test_map_realtime_parking_response_labels_public_and_private_types() -> None:
    """PRK_TYPE 코드 → 공영/민영 매핑을 실측(교대역·강남역·홍대) 근거로 검증한다."""

    lots = map_realtime_parking_response(
        _citydata_payload(
            [
                {
                    "PRK_NM": "교대역 동측 공영주차장(구)",
                    "PRK_CD": "1",
                    "PRK_TYPE": "NW",
                    "ADDR": "서울특별시 서초구 서초대로 1",
                },
                {"PRK_NM": "경남 공영주차장(구)", "PRK_CD": "2", "PRK_TYPE": "NS"},
                {"PRK_NM": "하림인터네셔날 빌딩", "PRK_CD": "3", "PRK_TYPE": "BS"},
                {"PRK_NM": "서초세움주차장(민영)", "PRK_CD": "4", "PRK_TYPE": "NP"},
                {"PRK_NM": "코드 모르는 주차장", "PRK_CD": "5", "PRK_TYPE": "ZZ"},
            ]
        )
    )

    by_name = {lot.name: lot for lot in lots}
    assert by_name["교대역 동측 공영주차장(구)"].lot_type == "공영"
    assert by_name["교대역 동측 공영주차장(구)"].address == "서울특별시 서초구 서초대로 1"
    assert by_name["경남 공영주차장(구)"].lot_type == "공영"
    assert by_name["하림인터네셔날 빌딩"].lot_type == "민영"
    assert by_name["서초세움주차장(민영)"].lot_type == "민영"
    assert by_name["코드 모르는 주차장"].lot_type is None


def test_map_realtime_parking_response_dedupes_by_code_keeping_realtime_entry() -> None:
    """이촌한강공원 실측 — 같은 PRK_CD가 두 번 오면 실시간 정보가 있는 쪽을 남긴다."""

    lots = map_realtime_parking_response(
        _citydata_payload(
            [
                {
                    "PRK_NM": "이촌3, 4주차장",
                    "PRK_CD": "1892050",
                    "PRK_TYPE": "BP",
                    "CPCTY": "257",
                    "CUR_PRK_CNT": "",
                    "CUR_PRK_YN": "N",
                },
                {
                    "PRK_NM": "이촌3, 4주차장",
                    "PRK_CD": "1892050",
                    "PRK_TYPE": "BP",
                    "CPCTY": "257",
                    "CUR_PRK_CNT": "0",
                    "CUR_PRK_YN": "Y",
                    "CUR_PRK_TIME": "2025-02-03 09:06:31",
                },
            ]
        )
    )

    assert len(lots) == 1
    assert lots[0].current_available is True
    assert lots[0].current_parked_count == 0
    assert lots[0].available_spaces == 257
    assert lots[0].observed_at == "2025-02-03 09:06:31"


def test_map_realtime_traffic_response_extracts_avg_road_data() -> None:
    payload = {
        "CITYDATA": {
            "AREA_NM": "이촌한강공원",
            "ROAD_TRAFFIC_STTS": {
                "AVG_ROAD_DATA": {
                    "ROAD_MSG": "해당 장소로 이동·진입하는 도로가 크게 막히지 않아요.",
                    "ROAD_TRAFFIC_IDX": "원활",
                    "ROAD_TRAFFIC_SPD": 32,
                    "ROAD_TRAFFIC_TIME": "2026-08-26 19:15",
                },
                "ROAD_TRAFFIC_STTS": [],
            },
        }
    }

    result = map_realtime_traffic_response(payload)

    assert result is not None
    assert result.level == "원활"
    assert result.average_speed_kmh == 32.0
    assert result.message == "해당 장소로 이동·진입하는 도로가 크게 막히지 않아요."
    assert result.observed_at == "2026-08-26 19:15"


def test_map_realtime_traffic_response_returns_none_when_missing() -> None:
    assert map_realtime_traffic_response({"CITYDATA": {"AREA_NM": "종로"}}) is None


def _traffic_payload(acdnt_cntrl_stts: list[dict[str, object]]) -> dict[str, object]:
    return {
        "CITYDATA": {
            "AREA_NM": "명동 관광특구",
            "ROAD_TRAFFIC_STTS": {
                "AVG_ROAD_DATA": {
                    "ROAD_TRAFFIC_IDX": "서행",
                    "ROAD_TRAFFIC_SPD": 15,
                    "ROAD_TRAFFIC_TIME": "2026-09-06 18:30",
                },
            },
            "ACDNT_CNTRL_STTS": acdnt_cntrl_stts,
        }
    }


def test_map_realtime_traffic_response_counts_incidents_by_category() -> None:
    """실측(2026-09-06, 명동 관광특구)에서 확인한 원문 유형 조합을 그대로 검증한다."""

    payload = _traffic_payload(
        [
            {"ACDNT_TYPE": "공사", "ACDNT_DTYPE": "도로보수"},
            {"ACDNT_TYPE": "집회및행사", "ACDNT_DTYPE": "행사"},
            {"ACDNT_TYPE": "기타", "ACDNT_DTYPE": "기타"},
        ]
    )

    result = map_realtime_traffic_response(payload)

    assert result is not None
    counts = {c.label: c.count for c in result.incident_counts}
    assert counts == {"사고/고장": 0, "공사/집회": 2, "기상/화재": 0, "기타": 1}
    # 4분류 순서가 고정이다 — 지도 화면과 같은 순서로 항상 이 순서를 지킨다.
    assert [c.label for c in result.incident_counts] == [
        "사고/고장",
        "공사/집회",
        "기상/화재",
        "기타",
    ]


def test_map_realtime_traffic_response_classifies_accident_breakdown_weather_fire() -> None:
    """서울시 원문에서 아직 실측하지 못한 유형(사고·고장·기상·화재)도 키워드로 분류한다.

    코드표 API(OA-13312)가 서비스 종료라 전체 값 목록을 직접 조회할 수 없어,
    ITS 국가교통정보센터의 표준 돌발유형 체계를 참고해 분류했다.
    """

    payload = _traffic_payload(
        [
            {"ACDNT_TYPE": "교통사고", "ACDNT_DTYPE": "추돌사고"},
            {"ACDNT_TYPE": "고장차량", "ACDNT_DTYPE": "고장"},
            {"ACDNT_TYPE": "기상특보", "ACDNT_DTYPE": "폭설"},
            {"ACDNT_TYPE": "화재", "ACDNT_DTYPE": "화재"},
        ]
    )

    result = map_realtime_traffic_response(payload)

    assert result is not None
    counts = {c.label: c.count for c in result.incident_counts}
    assert counts == {"사고/고장": 2, "공사/집회": 0, "기상/화재": 2, "기타": 0}


def test_map_realtime_traffic_response_unknown_type_falls_back_to_other() -> None:
    payload = _traffic_payload([{"ACDNT_TYPE": "알수없음", "ACDNT_DTYPE": ""}])

    result = map_realtime_traffic_response(payload)

    assert result is not None
    counts = {c.label: c.count for c in result.incident_counts}
    assert counts["기타"] == 1


def test_map_realtime_traffic_response_always_fills_four_categories_even_with_zero() -> None:
    """돌발상황이 하나도 없는 지역(대다수)도 4분류를 전부 0건으로 채운다.

    값이 없는 분류를 조용히 감추면 "이 지역엔 그 유형이 없다"는 뜻으로 읽혀
    서울시 지도 화면(사고/고장 0건 · 공사/집회 0건 · ...)과 어긋난다.
    """

    result = map_realtime_traffic_response(_traffic_payload([]))

    assert result is not None
    assert [c.count for c in result.incident_counts] == [0, 0, 0, 0]
