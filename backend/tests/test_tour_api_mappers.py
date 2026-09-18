"""TourAPI 분류 코드가 내부 어휘로 정확히 변환되는지 고정한다.

category는 C→A 계약에 그대로 실려 나가고, D가 이 값으로 실내외를 판정해 날씨 점수
(가중치 0.40)를 계산한다. 어휘가 어긋나면 조회 조건(PlaceType)과 응답이 왕복하지 않는다.
"""

from __future__ import annotations

import pytest

from app.agent_context.category_rules import PLACE_TYPE_TO_CONTENT_TYPE_ID
from app.providers.mappers import map_tour_api_item, map_tour_api_response
from app.schemas import PlaceType


def _item(content_type_id: str, **overrides: str) -> dict:
    item = {
        "contentid": "126508",
        "title": "경복궁",
        "mapx": "126.9769930325",
        "mapy": "37.5760836609",
        "contenttypeid": content_type_id,
        "addr1": "서울특별시 종로구 사직로 161",
        "lclsSystm1": "HS",
        "lclsSystm2": "HS01",
        "lclsSystm3": "HS010100",
    }
    item.update(overrides)
    return item


@pytest.mark.parametrize(
    ("content_type_id", "expected"),
    [
        ("12", PlaceType.ATTRACTION),
        ("14", PlaceType.CULTURAL_FACILITY),
        ("28", PlaceType.LEISURE),
        ("38", PlaceType.SHOPPING),
        ("39", PlaceType.RESTAURANT),
    ],
)
def test_분류코드가_PlaceType_어휘로_변환된다(
    content_type_id: str, expected: PlaceType
) -> None:
    candidate = map_tour_api_item(_item(content_type_id))
    assert candidate is not None
    assert candidate.category == expected.value


def test_조회조건과_응답어휘가_왕복한다() -> None:
    """PlaceType으로 조회한 결과가 같은 PlaceType으로 돌아와야 한다.

    축제(festival)만 예외다. 조회 어휘에는 남아 있지만 후보에서 빠지므로(D-120)
    왕복하지 않는다 — 이 어긋남을 아는 채로 두는 것이지 빠뜨린 것이 아니다.
    """
    for place_type, content_type_id in PLACE_TYPE_TO_CONTENT_TYPE_ID.items():
        if place_type == PlaceType.FESTIVAL.value:
            continue
        candidate = map_tour_api_item(_item(content_type_id))
        assert candidate is not None
        assert candidate.category == place_type


@pytest.mark.parametrize("content_type_id", ["25", "32"])
def test_LLM이_요청할_수_없는_유형은_후보에서_제외한다(content_type_id: str) -> None:
    """여행코스(25)·숙박(32)은 PlaceType에 없어 조건으로 지정할 수 없다."""
    assert map_tour_api_item(_item(content_type_id)) is None


def test_축제는_끝난_행사를_거를_수_없어_후보에서_제외한다() -> None:
    """places에 행사 기간 컬럼이 없어 종료 여부를 판정할 수 없다(D-120).

    25·32와 달리 조회 어휘(PlaceType.FESTIVAL)에는 남아 있으므로 사유를 나눠 둔다.
    """
    assert map_tour_api_item(_item("15")) is None


def test_알_수_없는_분류코드는_unknown으로_남긴다() -> None:
    """제외 대상과 달리 후보에서 빼지는 않는다 — 새 코드가 생겨도 결과가 비지 않게."""
    candidate = map_tour_api_item(_item("99"))
    assert candidate is not None
    assert candidate.category == "unknown"


def test_세분류_코드를_그대로_싣는다() -> None:
    """대분류만으로는 실내외를 가릴 수 없어 D가 이 값을 쓴다."""
    candidate = map_tour_api_item(_item("12"))
    assert candidate is not None
    assert (candidate.lcls_systm1, candidate.lcls_systm2, candidate.lcls_systm3) == (
        "HS",
        "HS01",
        "HS010100",
    )


def test_응답_변환에서_제외_유형만_빠진다() -> None:
    payload = {
        "response": {
            "body": {
                "items": {
                    "item": [
                        _item("12", contentid="1"),
                        _item("32", contentid="2"),  # 숙박
                        _item("39", contentid="3"),
                        _item("15", contentid="4"),  # 축제
                    ]
                }
            }
        }
    }
    candidates = map_tour_api_response(payload)
    assert [item.place_id for item in candidates] == ["1", "3"]
