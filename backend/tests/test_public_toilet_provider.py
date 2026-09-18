from app.providers.public_toilet import map_public_toilet_response


def test_map_public_toilet_response_normalizes_real_payload_shape() -> None:
    toilets = map_public_toilet_response(
        {
            "mgisToiletPoi": {
                "list_total_count": 4447,
                "row": [
                    {
                        # 실제 응답은 OBJECTID를 283814.0처럼 실수로 준다.
                        "OBJECTID": 283814.0,
                        "ADDR_NEW": "서울특별시 종로구 인사동길 35-4",
                        "ADDR_OLD": "서울특별시 종로구 관훈동 196-10",
                        "COORD_X": 126.98563,
                        "COORD_Y": 37.57432,
                        "CONTS_NAME": "인사동마루 신관 개방화장실",
                        "GU_NAME": "종로구",
                        "TEL_NO": "02-2148-2383",
                        "VALUE_01": "민간개방|",
                        "VALUE_02": "상시(24시간)|",
                        "VALUE_04": "남자|여자|",
                        "VALUE_05": "남자|여자|",
                        "VALUE_08": "근생시설|",
                        "VALUE_09": "인사동마루",
                    },
                    # 좌표가 없는 행. "근처"를 거리로만 답하므로 버려야 한다.
                    {
                        "OBJECTID": 999999.0,
                        "CONTS_NAME": "좌표 없는 화장실",
                        "COORD_X": None,
                        "COORD_Y": None,
                    },
                ],
            }
        }
    )

    assert len(toilets) == 1
    toilet = toilets[0]
    # 실수 표기의 소수점을 떼어 안정적인 키로 만든다.
    assert toilet.toilet_id == "283814"
    assert toilet.latitude == 37.57432
    assert toilet.longitude == 126.98563
    assert toilet.district == "종로구"
    assert toilet.open_hours_raw == "상시(24시간)|"
    assert toilet.location_type == "근생시설|"


def test_map_public_toilet_response_falls_back_for_missing_name() -> None:
    toilets = map_public_toilet_response(
        {
            "mgisToiletPoi": {
                "row": [
                    {"OBJECTID": "1", "COORD_X": 126.9, "COORD_Y": 37.5, "CONTS_NAME": "   "}
                ]
            }
        }
    )

    assert toilets[0].name == "공중화장실"


def test_map_public_toilet_response_tolerates_unexpected_shape() -> None:
    # 서비스 키가 없거나 row가 리스트가 아니면 예외가 아니라 빈 결과다.
    assert map_public_toilet_response({}) == ()
    assert map_public_toilet_response({"mgisToiletPoi": {"row": "nope"}}) == ()
