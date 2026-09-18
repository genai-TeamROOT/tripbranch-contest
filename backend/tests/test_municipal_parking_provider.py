from app.providers.municipal_parking import map_municipal_parking_response


def test_map_municipal_parking_response_keeps_live_status_and_current_count() -> None:
    lots = map_municipal_parking_response(
        {
            "GetParkingInfo": {
                "row": [
                    {
                        "PKLT_CD": "10001",
                        "PKLT_NM": "세종로 공영주차장(시)",
                        "ADDR": "서울특별시 종로구 세종대로 189",
                        "GU_NM": "종로구",
                        # 실제 응답은 1260.0처럼 정수형 실수를 준다.
                        "TPKCT": 1260.0,
                        "NOW_PRK_VHCL_CNT": 698.0,
                        "NOW_PRK_VHCL_UPDT_TM": "2026-08-27 11:24:53",
                        "PRK_STTS_YN": "1",
                        "PAY_YN": "Y",
                    },
                    {
                        "PKLT_CD": "10002",
                        "PKLT_NM": "정보 미연계 공영주차장",
                        "PRK_STTS_YN": "0",
                        "TPKCT": "50",
                    },
                ]
            }
        },
        requested_district="종로구",
    )

    assert lots[0].code == "10001"
    assert lots[0].is_live is True
    assert lots[0].current_parked_count == 698
    assert lots[0].observed_at == "2026-08-27 11:24:53"
    assert lots[1].is_live is False
    assert lots[1].current_parked_count is None
