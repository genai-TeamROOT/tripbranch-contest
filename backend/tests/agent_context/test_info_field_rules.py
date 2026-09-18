"""question_type별 필드 추출 규칙을 실제 TourAPI 응답 모양으로 검증한다.

여기서 쓰는 raw_intro 키 이름은 detailIntro2가 contentTypeId별로 내려주는 실제
필드명이다. 임의의 키로 테스트하면 규칙이 통과해도 실 응답에서는 아무것도
못 뽑는다.
"""

import pytest

from app.agent_context.info_field_rules import (
    clean_text,
    compose_accessible_restroom,
    compose_nursing_room,
    extract_info_fields,
)
from app.agent_context.info_schemas import InfoQuestionType
from app.domain.models import PlaceDetails
from app.providers.stub import FakePlaceProvider


def _details(
    *,
    content_type_id: str = "14",
    overview: str | None = None,
    homepage: str | None = None,
    telephone: str | None = None,
    address: str | None = None,
    operating_hours: str | None = None,
    rest_date: str | None = None,
    raw_intro: dict[str, object] | None = None,
    parking: str | None = None,
    parking_fee: str | None = None,
    fee: str | None = None,
    baby_carriage: str | None = None,
    pet: str | None = None,
    credit_card: str | None = None,
    restroom: str | None = None,
    **barrier_free: str | None,
) -> PlaceDetails:
    return PlaceDetails(
        content_id="126508",
        content_type_id=content_type_id,
        title="경복궁",
        address=address,
        overview=overview,
        homepage=homepage,
        telephone=telephone,
        operating_hours=operating_hours,
        rest_date=rest_date,
        raw_common={},
        raw_intro=raw_intro or {},
        provider="tour_api",
        parking=parking,
        parking_fee=parking_fee,
        fee=fee,
        baby_carriage=baby_carriage,
        pet=pet,
        credit_card=credit_card,
        restroom=restroom,
        **barrier_free,
    )


class TestOperatingHours:
    def test_운영시간과_휴무일을_함께_뽑는다(self) -> None:
        details = _details(operating_hours="09:00~18:00", rest_date="매주 화요일")

        assert extract_info_fields("operating_hours", details) == {
            "operating_hours": "09:00~18:00",
            "rest_date": "매주 화요일",
        }

    def test_없는_필드는_키_자체를_넣지_않는다(self) -> None:
        details = _details(operating_hours="09:00~18:00", rest_date=None)

        assert extract_info_fields("operating_hours", details) == {
            "operating_hours": "09:00~18:00"
        }

    def test_값이_전부_없으면_빈_dict다(self) -> None:
        assert extract_info_fields("operating_hours", _details()) == {}


class TestFee:
    """요금은 provider가 정규화해둔 PlaceDetails.fee에서 읽는다.

    contenttypeid별 키(usefee/usefeeleports/usetimefestival)를 어느 것으로 골랐는지는
    provider의 책임이라 여기서 검증하지 않는다 —
    test_place_details_normalized_fields.py가 그쪽을 덮는다.
    """

    def test_정규화된_요금을_계약_키로_옮긴다(self) -> None:
        details = _details(fee="어른 3,000원")

        assert extract_info_fields("fee", details) == {"fee": "어른 3,000원"}

    def test_요금_값이_없으면_빈_dict다(self) -> None:
        details = _details(parking="가능")

        assert extract_info_fields("fee", details) == {}

    def test_raw_intro만_있으면_뽑지_않는다(self) -> None:
        """옛 경로를 지웠는지 못 박는다.

        두 경로가 함께 살아 있으면 같은 질문이 provider에 따라 다르게 답한다.
        """
        details = _details(raw_intro={"usefee": "어른 3,000원"})

        assert extract_info_fields("fee", details) == {}


class TestParking:
    def test_주차와_주차요금을_각각_뽑는다(self) -> None:
        details = _details(parking="주차 가능", parking_fee="무료")

        assert extract_info_fields("parking", details) == {
            "parking": "주차 가능",
            "parking_fee": "무료",
        }

    def test_주차요금만_없으면_주차만_뽑는다(self) -> None:
        details = _details(parking="10대 가능")

        assert extract_info_fields("parking", details) == {"parking": "10대 가능"}

    def test_HTML이_섞인_원문도_정리한다(self) -> None:
        details = _details(parking="가능<br>요금 (30분 1,500원)")

        assert extract_info_fields("parking", details) == {
            "parking": "가능 요금 (30분 1,500원)"
        }

    def test_raw_intro만_있으면_뽑지_않는다(self) -> None:
        details = _details(raw_intro={"parkingculture": "주차 가능"})

        assert extract_info_fields("parking", details) == {}


class TestFacility:
    """편의시설도 provider 정규화 필드에서 읽는다(D-060).

    유형별 키(chkbabycarriageculture/chkcreditcardfood 등) 선택은 provider 책임이라
    test_place_details_normalized_fields.py가 덮는다.
    """

    def test_편의시설_항목을_모두_뽑는다(self) -> None:
        details = _details(
            baby_carriage="가능", pet="불가", credit_card="가능", restroom="있음"
        )

        assert extract_info_fields("facility", details) == {
            "baby_carriage": "가능",
            "pet": "불가",
            "credit_card": "가능",
            "restroom": "있음",
        }

    def test_일부만_있으면_있는_것만_뽑는다(self) -> None:
        details = _details(pet="불가")

        assert extract_info_fields("facility", details) == {"pet": "불가"}

    def test_없음도_값으로_낸다(self) -> None:
        """`없음`은 빈 값과 다르다 — "정보가 없다"가 아니라 "없다고 답했다"다."""
        details = _details(baby_carriage="없음")

        assert extract_info_fields("facility", details) == {"baby_carriage": "없음"}

    def test_raw_intro만_있으면_뽑지_않는다(self) -> None:
        details = _details(raw_intro={"chkpetculture": "불가"})

        assert extract_info_fields("facility", details) == {}


class TestLocationInfo:
    def test_주소와_전화번호를_뽑는다(self) -> None:
        details = _details(address="서울 종로구 사직로 161", telephone="02-3700-3900")

        assert extract_info_fields("location_info", details) == {
            "address": "서울 종로구 사직로 161",
            "telephone": "02-3700-3900",
        }


class TestGeneralInfo:
    def test_개요와_홈페이지를_뽑는다(self) -> None:
        details = _details(
            overview="조선의 법궁이다.", homepage="http://www.royalpalace.go.kr"
        )

        assert extract_info_fields("general_info", details) == {
            "overview": "조선의 법궁이다.",
            "homepage": "http://www.royalpalace.go.kr",
        }


class TestEvent:
    def test_event는_이_경로에서_아무것도_뽑지_않는다(self) -> None:
        # searchFestival2 별도 연동이 필요해 호출부가 unsupported로 걸러낸다.
        details = _details(raw_intro={"usefee": "무료"})

        assert extract_info_fields("event", details) == {}


class TestFakeProviderCarriesIntro:
    """Fake의 raw_intro가 비면 추출 로직이 한 줄도 안 돈 채 테스트가 통과한다.

    이 저장소에서 반복된 "조용한 fake" 실패 유형이다. Fake가 소비 측이 실제로
    읽는 키를 계속 들고 있는지 여기서 못 박는다 — 아래가 깨지면 Fake를 고쳐야지
    테스트를 지우면 안 된다.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("content_id", "content_type_id", "question_type"),
        [
            ("fake-museum-1", "14", "fee"),
            ("fake-museum-1", "14", "parking"),
            ("fake-museum-1", "14", "facility"),
            ("fake-cafe-1", "39", "parking"),
            ("fake-cafe-1", "39", "facility"),
        ],
    )
    async def test_fake_상세로도_필드가_비지_않는다(
        self,
        content_id: str,
        content_type_id: str,
        question_type: InfoQuestionType,
    ) -> None:
        details = (
            await FakePlaceProvider().get_details(content_id, content_type_id)
        ).data

        assert extract_info_fields(question_type, details) != {}


class TestCleanText:
    def test_HTML_태그를_공백으로_바꾼다(self) -> None:
        # <br>을 그냥 지우면 앞뒤 문장이 붙어버린다.
        assert clean_text("조선의 법궁<br>경복궁입니다.") == "조선의 법궁 경복궁입니다."

    def test_HTML_엔티티를_풀어준다(self) -> None:
        assert clean_text("어른 &amp; 어린이") == "어른 & 어린이"

    def test_연속_공백을_하나로_줄인다(self) -> None:
        assert clean_text("  09:00 ~   18:00  ") == "09:00 ~ 18:00"

    def test_빈_문자열은_None이다(self) -> None:
        assert clean_text("   ") is None
        assert clean_text("<br>") is None

    def test_문자열이_아니면_None이다(self) -> None:
        assert clean_text(None) is None
        assert clean_text(3000) is None


class TestBarrierFree:
    """무장애 값(place_barrier_free, D-077)이 facility로 나가는 규칙.

    분류 규칙(prompts/info/question_type_rules.md)이 이미 "휠체어 가능?"을
    facility로 보내고 있어 새 question_type을 만들지 않았다.
    """

    def test_접근로_주출입구_승강기를_한_키로_잇는다(self) -> None:
        details = _details(
            approach_route_raw="출입구까지 턱이 없어 휠체어 접근 가능함",
            entrance_access_raw="주출입구는 경사로가 있어 휠체어 접근 가능함",
            elevator_raw="엘리베이터 있음",
        )

        fields = extract_info_fields("facility", details)

        assert fields["wheelchair_access"] == (
            "출입구까지 턱이 없어 휠체어 접근 가능함"
            " / 주출입구는 경사로가 있어 휠체어 접근 가능함"
            " / 엘리베이터 있음"
        )

    def test_조각이_하나면_구분자를_붙이지_않는다(self) -> None:
        details = _details(entrance_access_raw="주출입구는 턱이 없어 휠체어 접근 가능함")

        fields = extract_info_fields("facility", details)

        assert fields["wheelchair_access"] == "주출입구는 턱이 없어 휠체어 접근 가능함"

    def test_세_값이_모두_없으면_키가_생기지_않는다(self) -> None:
        """무장애 목록에 없는 장소가 대부분이다(4개 구 실측 커버리지 19%)."""
        fields = extract_info_fields("facility", _details(restroom="있음"))

        assert "wheelchair_access" not in fields
        assert fields == {"restroom": "있음"}

    def test_원문의_HTML_태그를_정리한다(self) -> None:
        """무장애 원문에는 <br/>이 섞여 있다. 합치기 전에 정리해야 한다."""
        details = _details(
            approach_route_raw="접근로 이용이 쉬움<br />경사로 있음",
            public_transport_raw="대중교통 이용 가능<br/>저상버스 운행",
        )

        fields = extract_info_fields("facility", details)

        assert fields["wheelchair_access"] == "접근로 이용이 쉬움 경사로 있음"
        assert fields["public_transport"] == "대중교통 이용 가능 저상버스 운행"

    def test_휠체어_대여는_출입과_다른_키로_나간다(self) -> None:
        """TourAPI의 `wheelchair`는 출입이 아니라 대여다.

        두 값이 한 키로 섞이면 "휠체어로 들어갈 수 있나요"라는 질문에 대여 여부로
        답하게 된다.
        """
        details = _details(
            entrance_access_raw="주출입구는 턱이 없어 휠체어 접근 가능함",
            wheelchair_rental_raw="대여 가능(1대/안내데스크)",
        )

        fields = extract_info_fields("facility", details)

        assert fields["wheelchair_access"] == "주출입구는 턱이 없어 휠체어 접근 가능함"
        assert fields["wheelchair_rental"] == "대여 가능(1대/안내데스크)"

    def test_일반_화장실과_장애인_화장실을_함께_낸다(self) -> None:
        """앞은 detailIntro2, 뒤는 detailWithTour2에서 온 값이라 뜻이 다르다."""
        details = _details(
            restroom="있음", accessible_restroom_raw="장애인 화장실 있음(남녀 분리)"
        )

        fields = extract_info_fields("facility", details)

        assert fields["restroom"] == "있음"
        assert fields["accessible_restroom"] == "장애인 화장실 있음(남녀 분리)"

    def test_다른_question_type에는_나가지_않는다(self) -> None:
        """무장애 값은 facility 질문의 답이다. 주차 질문에 섞이면 안 된다."""
        details = _details(
            parking="가능 (54대)", accessible_parking_raw="장애인 주차장 있음(9면)"
        )

        assert extract_info_fields("parking", details) == {"parking": "가능 (54대)"}

    def test_유모차는_무장애_값이_있으면_그것만_낸다(self) -> None:
        """두 출처가 같은 사실을 말하는데 값이 서로 반대인 장소가 있다.

        서울공예박물관은 detailIntro2가 "없음", 무장애가 "대여가능(10대)"이다.
        둘 다 내면 카드에 모순된 두 줄이 나란히 보인다.
        """
        details = _details(
            baby_carriage="없음", stroller_rental_raw="대여가능(10대)"
        )

        fields = extract_info_fields("facility", details)

        assert fields["stroller_rental"] == "대여가능(10대)"
        assert "baby_carriage" not in fields

    def test_무장애_값이_없으면_기존_유모차_값을_그대로_낸다(self) -> None:
        """무장애 정보가 없는 장소가 대부분이라 이 경로가 기본이다."""
        fields = extract_info_fields("facility", _details(baby_carriage="가능"))

        assert fields == {"baby_carriage": "가능"}


class TestBarrierFreeCleanup:
    """무장애 원문에만 있는 두 가지 잡음을 다듬는다.

    적재 쪽(`providers/tour_barrier_free.py`)은 원문을 그대로 저장하기로 정해
    두었으므로 여기가 정리하는 자리다.
    """

    def test_출처_표시를_뗀다(self) -> None:
        """뜻이 없는 꼬리다. 원문 15종을 통틀어 835곳에 붙어 있다."""
        details = _details(accessible_restroom_raw="장애인 화장실 있음_무장애 편의시설")

        fields = extract_info_fields("facility", details)

        assert fields["accessible_restroom"] == "장애인 화장실 있음"

    def test_분류명이_달라도_출처_표시로_본다(self) -> None:
        """`_시각장애인 편의시설`(208건)·`_무장애 편의정보`(8건)도 같은 꼬리다."""
        details = _details(
            braille_block_raw="점자블록 있음(주요시설 앞)_시각장애인 편의시설",
            accessible_parking_raw="장애인 주차구역 2면_무장애 편의정보",
        )

        fields = extract_info_fields("facility", details)

        assert fields["braille_block"] == "점자블록 있음(주요시설 앞)"
        assert fields["accessible_parking"] == "장애인 주차구역 2면"

    def test_출처_표시_뒤에_이어지는_설명은_남긴다(self) -> None:
        """꼬리 뒤에 설명이 붙어 오는 값이 3곳 있다. 앞말과 붙지 않게 띄운다."""
        details = _details(
            accessible_parking_raw=(
                "장애인 주차구역 있음_무장애 편의시설지상 공터에 주차하는 것이 더 편리함"
            )
        )

        fields = extract_info_fields("facility", details)

        assert fields["accessible_parking"] == (
            "장애인 주차구역 있음 지상 공터에 주차하는 것이 더 편리함"
        )

    def test_붙어_온_두_문장을_나눈다(self) -> None:
        """구분자 없이 이어 붙은 원문이 있다(실측 18건).

        `<br/>`이 아니라 아예 구분자가 없어 태그 정리만으로는 갈라지지 않는다.
        """
        details = _details(infant_family_etc_raw="영유아거치대 있음기저귀교환대 있음")

        fields = extract_info_fields("facility", details)

        assert fields["infant_family_etc"] == "영유아거치대 있음 / 기저귀교환대 있음"

    def test_문장_끝의_있음은_그대로_둔다(self) -> None:
        """뒤에 이어지는 말이 없으면 나눌 자리도 없다."""
        details = _details(guide_dog_raw="보조견 동반 가능함")

        fields = extract_info_fields("facility", details)

        assert fields["guide_dog"] == "보조견 동반 가능함"


class TestBarrierFreeItemCleanup:
    """항목 단위 정리(TP-248).

    2026-09-07 `place_barrier_free` 1,229행 실측을 근거로 넣은 규칙들이다.
    화면이 편의시설을 줄 단위로 읽히게 그리므로, 항목 구분이 어긋나면 그 줄이
    통째로 읽기 어려워진다.
    """

    def test_파이프_구분자를_쉼표로_맞춘다(self) -> None:
        """같은 뜻인데 어떤 곳은 쉼표, 어떤 곳은 파이프다(실측 6곳)."""
        details = _details(accessible_restroom_raw="손잡이|등받이|비상 호출벨")

        fields = extract_info_fields("facility", details)

        assert fields["accessible_restroom"] == "손잡이, 등받이, 비상 호출벨"

    def test_있음이_아닌_자리의_붙음도_나눈다(self) -> None:
        """앞말이 숫자나 괄호로 끝나면 기존 `있음` 패턴이 비켜간다."""
        details = _details(accessible_restroom_raw="출입구 인근 1개소슬라이딩손잡이,비상 호출벨")

        fields = extract_info_fields("facility", details)

        # 원문이 쓰던 쉼표 간격은 건드리지 않는다 — `"1,2m이상"`처럼 숫자 사이
        # 쉼표가 있어서, 간격을 맞추려다 값을 바꾸게 된다.
        assert fields["accessible_restroom"] == "출입구 인근 1개소 / 슬라이딩 / 손잡이,비상 호출벨"

    def test_사전에_없는_말은_붙은_채로_둔다(self) -> None:
        """규칙을 넓히면 고유명사를 자른다.

        `"서울역버스환승센터강우규의거터"`는 실제 정류장 이름이라, 예쁘게 나누면
        사용자가 안내판에서 그 이름을 못 찾는다. 확실한 것만 끊는다.
        """
        details = _details(public_transport_raw="서울역버스환승센터강우규의거터(7번 승강장)")

        fields = extract_info_fields("facility", details)

        assert fields["public_transport"] == "서울역버스환승센터강우규의거터(7번 승강장)"

    def test_뜻_없는_있음_조각을_뺀다(self) -> None:
        """항목을 나누다 남은 찌꺼기다. 무엇이 있다는 것인지가 없다."""
        details = _details(accessible_restroom_raw="손잡이|등받이| 있음")

        fields = extract_info_fields("facility", details)

        assert fields["accessible_restroom"] == "손잡이, 등받이"

    def test_값이_있음_하나뿐이면_그대로_둔다(self) -> None:
        """그때는 찌꺼기가 아니라 그것이 답이다."""
        details = _details(guide_dog_raw="있음")

        fields = extract_info_fields("facility", details)

        assert fields["guide_dog"] == "있음"

    def test_원래_쓰던_구분자를_바꾸지_않는다(self) -> None:
        """항목을 쪼갠 뒤 다시 이어 붙이면 원문의 `/`가 쉼표로 바뀐다.

        `"대여 가능(1대/안내데스크)"`가 `"대여 가능(1대, 안내데스크)"`가 되던
        회귀다. 지울 때는 그 자리만 도려내고 나머지는 원문 그대로 둔다.
        """
        details = _details(wheelchair_rental_raw="대여 가능(1대/안내데스크)")

        fields = extract_info_fields("facility", details)

        assert fields["wheelchair_rental"] == "대여 가능(1대/안내데스크)"


class TestInfantItemMove:
    """장애인 화장실 원문에 적힌 영유아 설비를 "수유·기저귀" 줄로 옮긴다.

    실측 25곳에서 겹치고, 그중 14곳은 두 줄에 같은 말이 두 번 나온다. 다만 그냥
    지우면 두 필드가 모두 비어 있던 8곳에서 정보가 통째로 사라진다.
    """

    def test_답변_경로는_영유아_키로_받는다(self) -> None:
        """받는 자리가 없으면 화장실에서 뗀 항목이 그대로 사라진다."""
        details = _details(
            accessible_restroom_raw="장애인 화장실 있음, 손잡이, 영유아 거치대",
        )

        fields = extract_info_fields("facility", details)

        assert fields["accessible_restroom"] == "장애인 화장실 있음, 손잡이"
        assert fields["infant_family_etc"] == "영유아 거치대"

    def test_상세_카드는_수유_기저귀_줄로_받는다(self) -> None:
        """카드에는 영유아 전용 줄이 없어 "수유·기저귀"가 받는다."""
        details = _details(
            accessible_restroom_raw="장애인 화장실 있음, 손잡이, 영유아 거치대",
        )

        assert compose_accessible_restroom(details) == "장애인 화장실 있음, 손잡이"
        assert compose_nursing_room(details) == "영유아 거치대"

    def test_수유실만_있으면_뒤에_잇는다(self) -> None:
        """수유실 값이 있어도 기저귀 갈 곳이 있다는 사실은 따로 필요하다."""
        details = _details(
            accessible_restroom_raw="장애인 화장실 있음, 영유아 거치대 있음",
            nursing_room_raw="수유실 있음",
        )

        assert compose_accessible_restroom(details) == "장애인 화장실 있음"
        assert compose_nursing_room(details) == "수유실 있음 / 영유아 거치대 있음"

    def test_이미_같은_말이_있으면_더하지_않는다(self) -> None:
        """옮기는 이유가 중복을 없애는 것이라, 여기서 다시 겹치면 뜻이 없다."""
        details = _details(
            accessible_restroom_raw="장애인 화장실 있음, 기저귀 교환대",
            infant_family_etc_raw="기저귀 교환대",
        )

        fields = extract_info_fields("facility", details)

        assert fields["accessible_restroom"] == "장애인 화장실 있음"
        assert fields["infant_family_etc"] == "기저귀 교환대"

    def test_문장_안의_단어는_건드리지_않는다(self) -> None:
        """`"유아숲"`은 공원 이름이다. 단어로 지우면 위치 안내가 잘린다.

        항목 전체가 영유아 설비일 때만 떼어낸다.
        """
        details = _details(
            accessible_restroom_raw="장애인 전용 화장실 있음(공용화장실, 유아숲 맞은편)",
        )

        fields = extract_info_fields("facility", details)

        assert fields["accessible_restroom"] == "장애인 전용 화장실 있음(공용화장실, 유아숲 맞은편)"
        assert "infant_family_etc" not in fields

    def test_괄호_안의_항목은_두고_온다(self) -> None:
        """마지막 항목만 떼면 여는 괄호가 닫히지 않는다.

        중복이 남는 편이 문장이 깨지는 것보다 낫다.
        """
        details = _details(
            accessible_restroom_raw="장애인 화장실 있음 (점자표지판, 손잡이, 영유아 거치대)",
        )

        fields = extract_info_fields("facility", details)

        assert (
            fields["accessible_restroom"]
            == "장애인 화장실 있음 (점자표지판, 손잡이, 영유아 거치대)"
        )
