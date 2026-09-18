"""Provider/Tool이 공유하는 내부 도메인 모델.

역할: 외부 API(Naver Geocoding, KMA 단기예보, TourAPI 등) 응답을 라우터/서비스가
직접 다루지 않도록, provider 구현체가 반드시 이 타입으로 변환해서 반환하게 한다.
`ScoringCandidate`처럼 특정 provider 출력이 아니라 여러 Tool 결과가 조합되어
채워지는 Scoring 입력 모델도 이 모듈에 둔다.
이 모듈은 FastAPI/Pydantic/HTTP 라이브러리를 import하지 않는다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, time
from enum import StrEnum
from typing import Any

from app.domain.operating_hours import OperatingSchedule


class WeatherCondition(StrEnum):
    GOOD = "good"
    NEUTRAL = "neutral"
    BAD = "bad"


@dataclass(frozen=True)
class WeatherForecastSlot:
    forecast_for: datetime
    sky_code: str | None
    precipitation_type: str | None
    temperature_celsius: float | None = None


@dataclass(frozen=True)
class WeatherForecastResult:
    latitude: float
    longitude: float
    grid_x: int
    grid_y: int
    slots: tuple[WeatherForecastSlot, ...]
    provider: str


@dataclass(frozen=True)
class OperatingHours:
    """하루 운영시간 구간 (개장~마감).

    v1은 `open_time <= close_time`인 당일 운영만 다룬다. 자정을 넘기는
    운영시간(예: 22:00~02:00)은 범위 밖이며 `TBD`다.

    `is_regular_closure`는 "이 구간은 평소 운영시간이지만 방문일은 정기 휴무"라는
    뜻이다. 휴무일에 시각을 `00:00~00:00`으로 지워 표시하던 방식을 대체한다 —
    그 표식은 폐점을 전달하는 대신 표시할 시간까지 함께 없애서, 추천 카드에
    "00:00~00:00"이 그대로 노출됐다. 시각과 휴무 여부를 한 객체에 같이 두면
    둘이 어긋난 조합이 만들어지지 않는다.
    """

    open_time: time
    close_time: time
    is_regular_closure: bool = False
    # 방문 요일에 "몇 번째인지 모르는" 주기 휴무가 걸려 있다(`월 1회 월요일`).
    #
    # **`is_regular_closure`와 다르다.** 저쪽은 그날 확실히 닫는다는 뜻이라 폐점으로
    # 걸러지지만, 이쪽은 **닫을 수도 있는데 확인할 수 없다**는 뜻이다. 후보는
    # 살리고 경고만 붙인다 — 매주로 치면 안 쉬는 주에 멀쩡한 장소가 사라지고,
    # 무시하면 휴무일에 추천이 나간다. 둘 다 아닌 자리가 이것이다.
    has_uncertain_closure: bool = False


@dataclass(frozen=True)
class GeocodeResult:
    query: str
    resolved_name: str
    latitude: float
    longitude: float
    candidate_count: int = 1
    administrative_district: str | None = None
    # 후보가 여럿일 때 되묻기 버튼에 쓸 이름들. 대표 한 곳(resolved_name)만으로는
    # "어느 쪽이냐"를 물을 수 없어서 함께 나른다(TP-182).
    #
    # **시도를 뗀 짧은 이름이다** ("서울특별시 종로구 익선동"이 아니라 "종로구
    # 익선동"). 버튼 글자가 곧 다음 검색어라 혼자서도 찾아져야 하는데, 실측하면
    # 시도를 떼도 후보 1건으로 확정된다(2026-09-03: "익선동" 2건 → "종로구 익선동"
    # 1건, "연희동" 2건 → "서대문구 연희동" 1건). 반대로 동 이름만 남기면 다시
    # 여럿이 되어 되묻기가 반복된다.
    candidate_labels: tuple[str, ...] = ()


@dataclass(frozen=True)
class ConcentrationForecast:
    """관광지의 날짜별 상대 집중률 예측 한 건."""

    place_name: str
    forecast_date: str | None
    concentration_rate: float | None
    raw_data: Mapping[str, object]


@dataclass(frozen=True)
class ConcentrationResult:
    """지역·관광지 조건으로 조회한 집중률 예측 결과."""

    area_code: str
    district_code: str
    requested_place_name: str | None
    forecasts: tuple[ConcentrationForecast, ...]
    provider: str


@dataclass(frozen=True)
class RealtimeCommercialCategory:
    """서울시 실시간 상권현황의 업종별 소비 활동 지표 한 건."""

    large_category: str | None
    middle_category: str | None
    activity_level: str | None
    # 최근 10분 결제 건수와 결제 금액 범위(원). 서울시는 금액을 구간으로만 준다.
    payment_count: int | None = None
    payment_amount_min: int | None = None
    payment_amount_max: int | None = None


@dataclass(frozen=True)
class RealtimeCommercialResult:
    """서울시 주요 장소 한 곳의 실시간 상권현황 응답."""

    area_name: str
    area_code: str | None
    area_activity_level: str | None
    observed_at: str | None
    categories: tuple[RealtimeCommercialCategory, ...]
    provider: str
    # 지역 전체의 최근 10분 결제 건수·금액 범위(원, 신한카드 내국인 기준).
    payment_count: int | None = None
    payment_amount_min: int | None = None
    payment_amount_max: int | None = None


@dataclass(frozen=True)
class PopulationForecastSlot:
    """서울시 도시데이터가 제공하는 시간대별 인구 혼잡도 예측 한 건."""

    forecast_at: str
    congestion_level: str | None
    population_min: int | None
    population_max: int | None


@dataclass(frozen=True)
class PopulationAgeShare:
    """실시간 인구의 연령대별 비율 한 건(``PPLTN_RATE_*``)."""

    label: str
    rate: float


@dataclass(frozen=True)
class RealtimePopulationResult:
    """서울시 주요 장소 단위의 현재·향후 인구 혼잡도."""

    area_name: str
    area_code: str | None
    current_congestion_level: str | None
    current_congestion_message: str | None
    observed_at: str | None
    forecast_available: bool
    forecasts: tuple[PopulationForecastSlot, ...]
    provider: str
    # 현재 인구 지표 범위(명). 서울시는 예측(FCST_PPLTN)과 같은 방식으로 현재값도
    # 구간(AREA_PPLTN_MIN/MAX)으로만 준다 — 단일 실측치가 아니다.
    current_population_min: int | None = None
    current_population_max: int | None = None
    # 응답에 실린 순서(0세~70대) 그대로 보존한다. 비율 합은 100이다.
    age_shares: tuple[PopulationAgeShare, ...] = ()


@dataclass(frozen=True)
class RealtimeParkingLot:
    name: str
    latitude: float | None
    longitude: float | None
    capacity: int | None
    current_parked_count: int | None
    current_available: bool
    paid: bool | None
    observed_at: str | None
    # 공영주차장은 정적 카탈로그, 도시데이터는 응답이 주소를 줄 때만 채운다.
    # 주소가 없는 민영 항목에 지오코딩을 추가하지 않아 실시간 조회 비용을 늘리지 않는다.
    address: str | None = None
    # PRK_CD. 같은 주차장이 PRK_STTS에 중복으로 올 때 병합용 키로 쓴다.
    code: str | None = None
    # PRK_TYPE(NW/NS/BS/NP)을 "공영"/"민영"으로 정리한 값. 모르는 코드는 None.
    lot_type: str | None = None
    # 총 주차 대수와 현재 주차 대수를 모두 받으면 계산한다. GetParkingInfo와
    # 도시데이터 PRK_STTS 모두 같은 방식으로 채울 수 있다.
    available_spaces: int | None = None


@dataclass(frozen=True)
class MunicipalParkingStatus:
    """서울시 시영·공영주차장 API(GetParkingInfo)의 최신 현황 한 건.

    좌표는 API에 없으므로 ``municipal_parking_lots`` 카탈로그에서 별도로 보강한다.
    ``is_live``는 '최근 20분 안에 실시간 주차 대수가 제공됐는지'를 뜻하며, 빈자리가
    있다는 뜻이 아니다.
    """

    code: str
    name: str
    address: str | None
    district: str | None
    capacity: int | None
    current_parked_count: int | None
    observed_at: str | None
    paid: bool | None
    is_live: bool


@dataclass(frozen=True)
class StoredMunicipalParkingLot:
    """한 번 지오코딩해 Supabase에 보관하는 공영주차장 정적 카탈로그 행."""

    code: str
    name: str
    address: str | None
    district: str | None
    latitude: float | None
    longitude: float | None
    capacity: int | None
    paid: bool | None


@dataclass(frozen=True)
class PublicToilet:
    """서울시 공중화장실 위치정보(mgisToiletPoi) 한 곳.

    주차장과 달리 실시간 값이 없어 모델이 하나다 — API가 주는 것도, 우리가
    보관하는 것도 같은 정적 정보다. 좌표는 원본에 이미 들어 있어(실측 4,447건
    전부) 지오코딩 단계가 없다.

    ``open_hours_raw``는 자치구 담당자가 손으로 적은 값을 그대로 보관한다.
    ``상시(24시간)``·``정시(09:00~18:00)``·``기타|05:00~익일01:00``처럼 형식이
    제각각이고 11%는 시각 자체가 없어(``정시(영업시작~종료)``), 해석은 조회
    시점에 ``app.public_toilet_hours``가 맡고 실패하면 이 원문을 그대로 보여준다.
    """

    toilet_id: str
    name: str
    address_new: str | None
    address_old: str | None
    latitude: float
    longitude: float
    district: str | None
    tel: str | None
    # 공공개방 / 민간개방. 민간개방은 건물주가 시민에게 열어준 화장실이다.
    open_type: str | None
    open_hours_raw: str | None
    # `남자|여자` 같은 파이프 구분 원문. 개수가 아니라 있고 없음만 알려준다.
    restroom_status: str | None
    accessible_status: str | None
    amenities: str | None
    safety_signs: str | None
    # 지하철 / 공공시설 / 공원 및 하천변 / 근생시설 등.
    location_type: str | None
    manager: str | None


@dataclass(frozen=True)
class RealtimeSubwayArrival:
    station_name: str
    line: str | None
    direction: str | None
    destination: str | None
    arrival_seconds: int | None
    arrival_message: str | None


@dataclass(frozen=True)
class RealtimeBusStop:
    name: str
    ars_id: str | None
    latitude: float | None
    longitude: float | None


@dataclass(frozen=True)
class RealtimeCityEvent:
    name: str
    period: str | None
    place: str | None
    thumbnail_url: str | None
    url: str | None


@dataclass(frozen=True)
class RoadIncidentCategoryCount:
    """도로 위 돌발상황(citydata의 ACDNT_CNTRL_STTS) 한 분류의 진행 중 건수.

    서울시 원문(``ACDNT_TYPE``)은 개별 사건 단위라 종류가 세분화돼 있다
    (예: "공사", "집회및행사", "기타"). TOPIS가 지도에서 쓰는 표준 4분류
    (사고/고장·공사/집회·기상/화재·기타)로 묶어서 보여준다 — 이 표준 코드표
    API(OA-13312)는 서비스 종료라 직접 조회할 수 없어, 원문 유형명 키워드로
    분류한다(``_ROAD_INCIDENT_CATEGORY_KEYWORDS``).
    """

    label: str
    count: int


@dataclass(frozen=True)
class RoadTrafficStatus:
    """지역 인근 도로의 평균 소통 현황(citydata의 ROAD_TRAFFIC_STTS.AVG_ROAD_DATA)."""

    level: str | None
    average_speed_kmh: float | None
    message: str | None
    observed_at: str | None
    # 4분류 전부를 항상 채운다(0건 포함) — 서울시 지도 화면과 같은 방식이라
    # 조용히 사라지는 분류가 없어야 "이 지역엔 그 유형이 없다"는 뜻으로 읽힌다.
    incident_counts: tuple[RoadIncidentCategoryCount, ...] = ()


@dataclass(frozen=True)
class RealtimeCityDataResult:
    """상권 활동과 인구 혼잡도를 같은 서울시 주요 장소 기준으로 묶는다."""

    commercial: RealtimeCommercialResult | None
    population: RealtimePopulationResult | None
    parking_lots: tuple[RealtimeParkingLot, ...] = ()
    subway_arrivals: tuple[RealtimeSubwayArrival, ...] = ()
    bus_stops: tuple[RealtimeBusStop, ...] = ()
    events: tuple[RealtimeCityEvent, ...] = ()
    road_traffic: RoadTrafficStatus | None = None


@dataclass(frozen=True)
class PlaceCategoryFilter:
    """TourAPI 장소 목록 조회에 사용할 선택적 분류 코드 묶음."""

    content_type_id: str | None = None
    lcls_systm1: str | None = None
    lcls_systm2: str | None = None
    lcls_systm3: str | None = None

    def __post_init__(self) -> None:
        values = (
            self.content_type_id,
            self.lcls_systm1,
            self.lcls_systm2,
            self.lcls_systm3,
        )
        if any(value is not None and not value.strip() for value in values):
            raise ValueError("분류 코드는 비어 있는 문자열일 수 없습니다.")
        if self.lcls_systm2 and not self.lcls_systm1:
            raise ValueError("lcls_systm2 사용 시 lcls_systm1이 필요합니다.")
        if self.lcls_systm3 and not (self.lcls_systm1 and self.lcls_systm2):
            raise ValueError("lcls_systm3 사용 시 lcls_systm1과 lcls_systm2가 필요합니다.")


@dataclass(frozen=True)
class PlaceDetails:
    """TourAPI의 공통·소개 상세 응답을 정규화한 장소 상세정보."""

    content_id: str
    content_type_id: str
    title: str | None
    address: str | None
    overview: str | None
    homepage: str | None
    telephone: str | None
    operating_hours: str | None
    rest_date: str | None
    raw_common: Mapping[str, object]
    raw_intro: Mapping[str, object]
    provider: str
    operating_schedule: OperatingSchedule | None = None
    # 주차·요금은 operating_hours와 같은 취급이다 — provider가 contenttypeid별 키를
    # 이미 훑어 하나로 정규화해둔다. 소비 측(info_field_rules)은 raw_intro에서
    # 원본 키를 다시 찾지 않고 이 필드를 읽는다.
    #
    # raw_intro에서 읽던 옛 경로는 남기지 않았다. 두 경로를 함께 두면 같은 질문이
    # provider에 따라 다르게 답한다.
    parking: str | None = None
    parking_fee: str | None = None
    fee: str | None = None
    baby_carriage: str | None = None
    pet: str | None = None
    credit_card: str | None = None
    restroom: str | None = None
    # 장소 카드 이미지. 캐시 경로(hybrid/supabase_place_details.py)는 원본 크기
    # firstimage(first_image_url)를 우선하고, 없으면 작은 썸네일 firstimage2로
    # 대체한다(2026-08-13) — 필드명은 thumbnail_url이지만 실제로는 대부분 원본
    # 크기 이미지가 들어온다. 실측 844건 중 169건(20%)은 이미지가 없어 None이
    # 정상 값이다 — 소비 측이 이미지 영역을 숨기는 근거다.
    thumbnail_url: str | None = None
    # 무장애 여행 정보(place_barrier_free, D-077). 무장애 목록에 등록된 장소만
    # 행이 있어 대부분의 장소에서는 전부 None이다 — 4개 구 실측 커버리지가 19%다.
    #
    # 두 가지 함정이 있다.
    #   1. wheelchair_rental_raw는 휠체어 **대여** 여부다. 휠체어로 들어갈 수 있는지가
    #      아니다. 출입 가능 여부는 approach_route_raw·entrance_access_raw가 답한다.
    #   2. approach_route_raw(접근로)와 entrance_access_raw(주출입구)는 원문에서 서로
    #      뒤바뀐 장소가 있다(가나아트센터). 한쪽만 읽어 판정하지 않는다.
    approach_route_raw: str | None = None
    entrance_access_raw: str | None = None
    elevator_raw: str | None = None
    accessible_restroom_raw: str | None = None
    accessible_parking_raw: str | None = None
    braille_block_raw: str | None = None
    braille_promotion_raw: str | None = None
    audio_guide_raw: str | None = None
    guide_dog_raw: str | None = None
    wheelchair_rental_raw: str | None = None
    stroller_rental_raw: str | None = None
    nursing_room_raw: str | None = None
    infant_family_etc_raw: str | None = None
    public_transport_raw: str | None = None
    disability_etc_raw: str | None = None


@dataclass(frozen=True)
class LocalSearchPlace:
    """Naver Local Search가 반환한 장소·업체 후보 한 건."""

    name: str
    address: str | None
    road_address: str | None
    category: str | None
    latitude: float | None
    longitude: float | None


@dataclass(frozen=True)
class TourPlaceRecord:
    """TourAPI 지역 기반 목록의 장소 한 건."""

    content_id: str
    content_type_id: str
    title: str
    address: str | None
    latitude: float | None
    longitude: float | None
    area_code: str
    district_code: str
    lcls_systm1: str | None
    lcls_systm2: str | None
    lcls_systm3: str | None
    source_modified_at: datetime | None
    # 목록 응답이 그대로 주는 이미지 URL(firstimage/firstimage2). 상세조회가 없어도
    # 채워지므로 detail_fetched_at이 아니라 list_fetched_at 주기를 따른다(D-056).
    first_image_url: str | None = None
    thumbnail_url: str | None = None


@dataclass(frozen=True)
class TourPlacePage:
    """TourAPI 지역 기반 목록의 페이지 메타데이터와 장소 목록."""

    page_no: int
    num_of_rows: int
    total_count: int
    places: tuple[TourPlaceRecord, ...]


@dataclass(frozen=True)
class PlaceOperatingDetails:
    """TourAPI 소개 상세 응답에서 가져온 운영시간·휴무일·주차·요금 원문."""

    content_id: str
    content_type_id: str
    operating_hours_raw: str | None
    rest_date_raw: str | None
    # 주차·요금은 운영시간과 같은 detailIntro2 응답에서 온다(D-056). 추가 호출은 없다.
    # 축제(15)에는 주차 필드가 없고, 요금 필드는 14·15·28에만 있어 대부분 None이다.
    parking_info_raw: str | None = None
    parking_fee_raw: str | None = None
    use_fee_raw: str | None = None
    discount_info_raw: str | None = None
    # 안내처 원문. 전화번호의 실제 출처는 detailCommon2의 tel이 아니라 이쪽이다 —
    # tel은 축제(15)에만 채워진다. 축제는 여기가 비고 tel이 채워진다.
    info_center_raw: str | None = None
    # 편의시설. `없음`은 빈 값과 다르다 — "없다고 답한" 것이므로 그대로 담는다.
    baby_carriage_raw: str | None = None
    pet_raw: str | None = None
    credit_card_raw: str | None = None
    restroom_raw: str | None = None


@dataclass(frozen=True)
class PlaceBarrierFreeDetails:
    """무장애 여행 정보(KorWithService2/detailWithTour2) 원문(D-077).

    detailIntro2와 다른 서비스라 호출도 따로 나간다. 응답 필드 28개 중 채움률 5%를
    넘긴 15개만 담는다(2026-08-25 실측, 4개 구 427건 기준). 담지 않는 13개는
    수어 안내·큰활자 안내물처럼 427건 중 한 자리 수만 채워진 필드들이다.

    필드 이름은 응답 키가 아니라 의미를 따른다. 응답 키를 그대로 쓰면 두 필드가
    이름과 반대로 읽힌다 — `wheelchair`는 휠체어 대여이지 출입이 아니고, `exit`는
    출구가 아니라 주출입구다.
    """

    content_id: str
    # 휠체어 접근. 접근로(route)와 출입구(exit)를 나눈 필드인데 작성자가 뒤바꿔 넣은
    # 사례가 있어, 접근 가능 여부는 두 값을 함께 읽어야 한다.
    approach_route_raw: str | None = None
    entrance_access_raw: str | None = None
    elevator_raw: str | None = None
    accessible_restroom_raw: str | None = None
    accessible_parking_raw: str | None = None
    braille_block_raw: str | None = None
    braille_promotion_raw: str | None = None
    audio_guide_raw: str | None = None
    guide_dog_raw: str | None = None
    wheelchair_rental_raw: str | None = None
    stroller_rental_raw: str | None = None
    nursing_room_raw: str | None = None
    infant_family_etc_raw: str | None = None
    public_transport_raw: str | None = None
    disability_etc_raw: str | None = None

    def has_any_value(self) -> bool:
        """값이 하나라도 있는가.

        무장애 목록에 있어도 15개가 전부 빈 장소가 496건 중 60건이다. 목록에
        있었다는 사실과 값이 있다는 사실은 다르다.
        """
        return any(
            value is not None
            for field_name, value in vars(self).items()
            if field_name != "content_id"
        )


class AccessibilityNeed(StrEnum):
    """요청이 요구할 수 있는 무장애 편의. `place_barrier_free`의 컬럼 묶음과 1:1이다.

    이름은 대개 **"무엇이 필요한가"**를 가리킨다. 다만 단차 접근만은 대상으로
    나눈다 — 휠체어와 유모차는 같은 컬럼을 읽지만 판정이 갈리기 때문이다(각 값의
    주석 참고). 나머지는 누가 요구하든 같은 값을 본다.

    "유모차 끌고 갈 만한 곳"은 STROLLER_ACCESS와 INFANT_FACILITIES 두 값으로
    온다 — 유모차를 끌고 들어갈 수 있는지와 수유실·기저귀교환대가 있는지는 다른
    질문이다. INFANT_FACILITIES만 요구하면 유아 시설은 있는데 계단으로 올라가야
    하는 곳이 섞인다(유아 시설이 있는 275곳 중 24곳에 단차 정보가 없다).

    각 값이 어느 컬럼을 읽는지는 저장소가 아니라 **RPC의 판정 블록**이 정한다
    (`search_places_barrier_free`). 판정을 한 곳에 모아 두려는 것이므로 여기에
    컬럼 목록을 복제하지 않는다.
    """

    # 단차 없이 들어갈 수 있는가. 두 값이 **같은 컬럼을 읽지만 판정이 다르다** —
    # 계단은 휠체어만 막고(들어 올릴 수 없다), 흙·자갈은 바퀴가 작은 유모차에 더
    # 불리하며, 좁은 통로는 유모차만 접어서 지날 수 있다. 원문은 휠체어 기준으로
    # 쓰여 있어(477문장 중 253개가 휠체어·전동 스쿠터를 언급, 유모차는 0건) 유모차
    # 판정은 그 서술로부터 따로 내려야 한다.
    #
    # **지금은 두 값이 같은 결과를 낸다.** 원문을 읽어 가르는 판정이 아직 없기
    # 때문이다(TP-204). 어휘를 먼저 나눈 이유는 A가 조건 추출을 붙이기 전이라
    # 지금이 가장 싼 시점이어서다.
    WHEELCHAIR_ACCESS = "wheelchair_access"
    STROLLER_ACCESS = "stroller_access"

    ACCESSIBLE_RESTROOM = "accessible_restroom"
    ACCESSIBLE_PARKING = "accessible_parking"
    VISUAL_GUIDE = "visual_guide"
    # 수유실·기저귀교환대·유모차 대여. "유모차를 끌고 갈 수 있는가"가 아니라
    # "아기와 있을 때 필요한 시설이 있는가"다 — 앞은 STROLLER_ACCESS가 답한다.
    INFANT_FACILITIES = "infant_facilities"

    # 오래 걷기 힘든 동행(노인 등)에게 쓸모 있는 값들. 휠체어 사용자 전용이 아니다.
    #
    # WHEELCHAIR_RENTAL은 휠체어 **대여**다. 휠체어로 들어갈 수 있는지가 아니다 —
    # TourAPI의 `wheelchair` 응답 키가 대여를 뜻한다.
    # SEATING_AVAILABLE은 의자식(입식) 테이블이다. 좌식이 아니라는 뜻이다.
    WHEELCHAIR_RENTAL = "wheelchair_rental"
    SEATING_AVAILABLE = "seating_available"
    LOW_FLOOR_TRANSIT = "low_floor_transit"


class AccessibilityVerdict(StrEnum):
    """무장애 원문을 사람이 읽어 내린 접근 가능 판정.

    원본은 `supabase/data/barrier_free_sentence_verdicts.csv`이고, 장소마다
    펼친 값이 `place_barrier_free`의 판정 컬럼이다. 한 장소가 여러 문장을
    가지면 **가장 나쁜 것**이 그 장소의 판정이다 — 접근로가 가능이어도
    주출입구가 불가면 못 들어간다.

    판정이 붙는 어휘는 셋뿐이다(휠체어·유모차·시각안내). 나머지 여섯은 아직
    원문 규칙으로 거른다.
    """

    # 들어갈 수단이 있다. 리프트도 보조출입구도 수단이고, 불편하다는 말은
    # 못 들어간다는 뜻이 아니다.
    POSSIBLE = "possible"
    # 들어가긴 하지만 못 가는 구역이 남는다. **후보에서 빼지 않는다** — 팔각정
    # 하나 못 간다고 추천에서 빼는 것은 과하다. 대신 답변이 그 사실을 말한다.
    PARTIAL = "partial"
    # 아예 들어갈 수 없다. 후보에서 뺀다.
    IMPOSSIBLE = "impossible"


@dataclass(frozen=True)
class DistrictPlaceRow:
    """구 단위 후보 조회가 돌려주는 장소 한 건.

    `BarrierFreePlaceRow`와 같은 자리를 채우되 `distance_km`이 없다. 구 단위 요청은
    기준점이 없어 거리를 잴 대상이 없기 때문이다(D-119).

    운영시간은 담지 않는다. 다른 후보 경로와 같은 이유다 — 뒤이은 상세 보완이
    채우므로 여기서 미리 넣으면 같은 값을 두 경로가 서로 다른 규칙으로 만든다.
    """

    content_id: str
    title: str
    address: str | None
    latitude: float
    longitude: float
    content_type_id: str | None
    lcls_systm1: str | None
    lcls_systm2: str | None
    lcls_systm3: str | None
    first_image_url: str | None


@dataclass(frozen=True)
class BarrierFreePlaceRow:
    """무장애 후보 검색(`search_places_barrier_free`)이 돌려주는 장소 한 건.

    `places`에서 읽은 값이라 TourAPI 목록 조회가 주는 것과 같은 자리를 채운다 —
    provider가 이 값을 `PlaceCandidate`로 옮기면 그 뒤 경로(상세 보완·병합·조립)는
    후보가 어디서 왔는지 몰라도 된다.

    운영시간은 담지 않는다. TourAPI 후보도 검색 단계에서는 비워 두고 뒤이은 상세
    보완이 채우므로, 여기서 미리 채우면 같은 값을 두 경로가 서로 다른 규칙으로
    만들게 된다.
    """

    content_id: str
    title: str
    address: str | None
    latitude: float
    longitude: float
    content_type_id: str | None
    lcls_systm1: str | None
    lcls_systm2: str | None
    lcls_systm3: str | None
    first_image_url: str | None
    distance_km: float
    # 원문을 사람이 읽어 내린 판정. 후보에 남았다는 것은 impossible이 아니라는
    # 뜻이지 possible이라는 뜻은 아니다 — partial도 후보로 남긴다. 그래서 값을
    # 함께 올려 답변이 "일부 구역은 접근이 어렵다"고 말할 수 있게 한다.
    #
    # 판정표가 없는 여섯 어휘(화장실·주차장·유아·대여·좌석·저상버스)는 여기
    # 없다. RPC가 아직 원문 규칙으로 거르므로 후보에 있다는 것 말고는 할 말이 없다.
    wheelchair_access_verdict: AccessibilityVerdict | None = None
    stroller_access_verdict: AccessibilityVerdict | None = None
    visual_guide_verdict: AccessibilityVerdict | None = None


@dataclass(frozen=True)
class PlaceCommonDetails:
    """detailCommon2에서만 얻을 수 있는 값. places 동기화 대상이 아니다.

    overview는 표본 35건 실측(2026-08-10)에서 100%(평균 326자), homepage는 63%
    채워진다. telephone(`tel`)은 축제(15)에만 있고 나머지 유형은 detailIntro2의
    안내처가 출처다 — 그쪽은 places가 캐시한다.
    """

    content_id: str
    overview: str | None
    homepage: str | None
    telephone: str | None


@dataclass(frozen=True)
class StoredPlaceLocation:
    """저장된 TourAPI 장소의 검색 중심점 해석용 최소 정보."""

    content_id: str
    title: str
    address: str | None
    latitude: float
    longitude: float
    # TourAPI 법정동 코드의 시군구 부분(lDongSignguCd, 종로구 "110"). 집중률 조회는
    # 구를 지정해야 하고 API가 그 값으로 엄격하게 거른다 - 중구 장소를 종로구로
    # 물으면 0건이 온다(D-095). 집중률 API의 signguCd는 시도까지 붙인 5자리라
    # 넘기기 전에 concentration_signgu_code()로 바꾼다.
    district_code: str | None = None
    # 집중률 응답에서 장소를 골라낼 때 대조할 정식 명칭.
    concentration_name: str | None = None
    # tAtsNm에 넣을 검색어 목록. 앞에서부터 시도하고 결과가 나오면 멈춘다(D-057).
    # 비어 있으면 concentration_name을 그대로 쓴다.
    concentration_search_keys: tuple[str, ...] = ()


@dataclass(frozen=True)
class PlaceEvidenceSnippet:
    """취향 근거 한 조각 — 블로그·리뷰 원문에서 뽑은 문장 하나.

    `search_place_evidence` RPC가 장소당 최대 `p_match_count`개를 유사도 내림차순
    으로 돌려준다. 같은 글에서 두 문장이 뽑히지 않도록 RPC가 url 단위로 대표
    1건만 남긴다.
    """

    source_text: str
    source_url: str | None
    similarity: float
    published_at: datetime | None
    # 무엇에서 나온 문장인가(naver_post / google_review / tour_overview). 후기로 답하는
    # 경로가 관광 안내문(tour_overview)을 근거에서 빼는 데 쓴다 - 그건 이미 기존 INFO
    # 경로가 쓰는 텍스트이고 출처로 걸 링크도 없다. 추천 채점은 이 값을 보지 않는다.
    source_type: str | None = None
    # 같은 글에서 나온 조각을 묶는 키. RPC가 글당 한 문장만 남기므로 서로 다른 글에서
    # 왔는지 세는 데 쓴다.
    document_id: str | None = None


@dataclass(frozen=True)
class PlaceEvidenceMatch:
    """한 장소의 취향 근거 검색 결과.

    `avg_similarity`는 그 장소에서 살아남은 조각들의 유사도 평균이다 — 점수
    Feature의 입력 후보가 되는 값이지만, **0~1 점수로 어떻게 펼지는 아직 정하지
    않았다.** 실제 분포를 재본 뒤 정한다(계획 문서 2단계).
    """

    content_id: str
    place_title: str
    avg_similarity: float
    snippets: tuple[PlaceEvidenceSnippet, ...]


@dataclass(frozen=True)
class PreferenceTag:
    """`place_preference_tags` 한 행 중 채점이 보는 값만 추린 것.

    `confidence`는 카드 표시·분석용 원자료로 보존한다. 추천 순위의 태그 점수는
    요청마다 후보군 안의 `positive_documents` 최댓값으로 정규화한다.
    """

    code: str
    label: str
    confidence: float
    positive_documents: int
    negative_documents: int


@dataclass(frozen=True)
class PreferenceTagScoreDetail:
    """요청 태그 하나의 후보군 상대 점수 계산 근거."""

    code: str
    label: str | None
    positive_documents: int
    negative_documents: int
    candidate_max_positive_documents: int
    score: float


@dataclass(frozen=True)
class PreferenceTagMatch:
    """한 장소가 요청 취향과 얼마나 맞는지. 태그 기반 taste 점수의 입력이다.

    `score`는 요청 코드 **전부**에 대한 평균이다. "혼밥 + 조용한"처럼 둘을 말했는데
    한쪽만 맞는 곳은 절반만 받는다 — 둘 다 맞기를 요구하면(교집합) 걸리는 곳이
    거의 없어진다. 후보 30곳에서 한 축이 맞는 비율이 평균 7.1%였다(같은 실측).

    `label`/`documents`는 점수가 아니라 근거 문장용이다 — 맞은 태그 중 가장 강한
    하나를 그대로 들고 간다("후기 5건에서 '혼자 가기 좋은' 이야기가 나와요").
    """

    content_id: str
    score: float
    label: str | None = None
    documents: int = 0
    details: tuple[PreferenceTagScoreDetail, ...] = ()


@dataclass(frozen=True)
class PlaceMoodProfile:
    """장소 사진에서 뽑은 분위기 축 점수 한 벌.

    `place_mood_vectors.axis_scores`를 그대로 담는다. 적재 때 미리 계산해 둔
    값이라 조회 경로에는 벡터 연산이 없다 — 발화에 분위기 표현이 있으면 이
    점수로 정렬만 하면 된다.

    **점수의 부호를 임계값으로 쓰지 않는다.** 특히 `세월`은 종로 631곳 중
    양수가 24곳뿐일 만큼 한쪽으로 쏠려 있어 "0보다 크면 새것"이 성립하지
    않는다. 순위는 정확하므로 정렬에만 쓴다(D-087).
    """

    content_id: str
    # 축 이름 → −1~1 점수. 키는 영문이고 부호는 `+` 쪽을 가리킨다 — calm이
    # 양수면 조용한 쪽이다. 지금 켠 축은 indoor·calm·traditional·warm_toned·
    # weathered 다섯이지만
    # 축을 켜고 끄는 일이 잦아 고정 필드로 두지 않는다.
    axis_scores: Mapping[str, float]
    # 평균에 쓴 사진 수. 1이면 detailImage2가 비어 대표 이미지 한 장으로 대체된
    # 장소이고, 그 한 장이 간판만 찍혔으면 장소가 아니라 그 사진을 대표한다.
    # 종로 631곳 중 170곳(27%)이 여기 해당한다.
    photo_count: int


@dataclass(frozen=True)
class PlaceMoodMatch:
    """사진 한 장으로 찾은 "분위기가 닮은 장소" 한 건.

    `similarity`는 올린 사진의 벡터와 장소 벡터의 코사인 유사도다. 양쪽 다
    길이 1로 정규화돼 있어 내적이 곧 유사도다.

    **얼마부터 "닮았다"인지는 아직 정하지 않았다.** 축 점수 쪽은 사람 정답표
    77곳으로 AUC를 쟀지만(D-087), 사진끼리의 유사도 컷은 표본이 없다. 재기
    전까지는 절대값이 아니라 순위만 쓴다.
    """

    content_id: str
    similarity: float
    profile: PlaceMoodProfile
    # 검색 중심에서의 거리. 반경으로 좁혀 부른 경우에만 채워진다.
    distance_km: float | None = None


@dataclass(frozen=True)
class PlacePhoto:
    """장소 사진 한 장. 상세 화면에 여러 장을 보여주기 위한 값이다.

    출처는 `place_image_embeddings`다. 분위기 임베딩을 적재하면서 함께 담아 둔
    원본 주소(`origin_url`)이고, TourAPI `detailImage2`가 준 순서를 그대로
    가지고 있다. 사진 파일 자체는 저장하지 않으므로 이 주소가 유일한 경로다.

    **한 장뿐인 장소가 절반이 넘는다.** 5,465곳 중 3,074곳이고, 그중 2,749곳은
    이 주소가 `places.first_image_url`과 같다 — `detailImage2`가 비어 대표
    이미지 한 장으로 대체된 장소라 갤러리로 보여줄 것이 없다(2026-08-31 실측).
    소비 측은 장수를 세어 한 장이면 지금처럼 한 장만 그린다.
    """

    content_id: str
    # 장소 안에서 몇 번째 사진인가. 1이 가장 대표적이다 — 관광공사가 대표성 높은
    # 사진을 앞에 주고, 적재가 그 순서를 바꾸지 않았다.
    photo_order: int
    url: str
    # detailImage2의 imgname. 대부분 장소명이지만 무장애 실측 사진처럼 성격이
    # 다른 사진이 이름으로 드러나는 경우가 있어(예: MouseRabbit_출입구자동문)
    # 걸러낼 단서로 함께 나른다. 지금은 화면에서 쓰지 않는다.
    image_name: str | None = None


@dataclass(frozen=True)
class StoredPlaceDetail:
    """요청 시 저장소에서 읽어오는 장소 상세·운영정보 한 건.

    운영시간은 정규화 결과가 아니라 TourAPI 원문(``*_raw``)을 그대로 담는다 —
    소비 시점에 normalize_operating_schedule()로 다시 정규화하므로 TourAPI를
    직접 호출하는 경로와 동일한 결과가 보장된다.
    """

    content_id: str
    content_type_id: str
    title: str | None
    address: str | None
    operating_hours_raw: str | None
    rest_date_raw: str | None
    detail_fetch_status: str
    detail_fetched_at: datetime | None
    source_modified_at: datetime | None
    # COMPARE의 TRAVEL_TIME 실측 연결(2026-08-21)이 쓴다 — A가 이 좌표로 실측 경로를
    # 조회한다. C는 좌표만 그대로 전달하고 우열은 판정하지 않는다.
    latitude: float | None = None
    longitude: float | None = None
    # 아래 필드는 추천 카드 조립(app.tools.recommendation_cards)이 쓴다. 분류 코드는
    # 카테고리 라벨을, 주차·이미지는 카드 배지와 썸네일을 채운다(D-056).
    lcls_systm1: str | None = None
    lcls_systm2: str | None = None
    lcls_systm3: str | None = None
    parking_info_raw: str | None = None
    parking_fee_raw: str | None = None
    # 이미지는 detail_fetched_at이 아니라 list_fetched_at 주기를 따른다 — 상세조회가
    # 실패한 장소에서도 채워져 있을 수 있다.
    first_image_url: str | None = None
    thumbnail_url: str | None = None
    # INFO 상세 질의가 캐시만으로 요금·전화번호·편의시설을 답하기 위한 원문.
    use_fee_raw: str | None = None
    info_center_raw: str | None = None
    baby_carriage_raw: str | None = None
    pet_raw: str | None = None
    credit_card_raw: str | None = None
    restroom_raw: str | None = None
    # 적재 배치가 저장한 파싱 결과와 그때의 파서 버전(위 docstring 참고).
    operating_schedule_raw: Mapping[str, Any] | None = None
    operating_parser_version: str | None = None
    # 무장애 여행 정보(place_barrier_free, D-077). 무장애 목록에 등록된 장소만
    # 행이 있어 대부분의 장소에서는 전부 None이다 — 4개 구 실측 커버리지가 19%다.
    #
    # 두 가지 함정이 있다.
    #   1. wheelchair_rental_raw는 휠체어 **대여** 여부다. 휠체어로 들어갈 수 있는지가
    #      아니다. 출입 가능 여부는 approach_route_raw·entrance_access_raw가 답한다.
    #   2. approach_route_raw(접근로)와 entrance_access_raw(주출입구)는 원문에서 서로
    #      뒤바뀐 장소가 있다(가나아트센터). 한쪽만 읽어 판정하지 않는다.
    approach_route_raw: str | None = None
    entrance_access_raw: str | None = None
    elevator_raw: str | None = None
    accessible_restroom_raw: str | None = None
    accessible_parking_raw: str | None = None
    braille_block_raw: str | None = None
    braille_promotion_raw: str | None = None
    audio_guide_raw: str | None = None
    guide_dog_raw: str | None = None
    wheelchair_rental_raw: str | None = None
    stroller_rental_raw: str | None = None
    nursing_room_raw: str | None = None
    infant_family_etc_raw: str | None = None
    public_transport_raw: str | None = None
    disability_etc_raw: str | None = None


@dataclass(frozen=True)
class StoredPlaceState:
    """상세 재조회와 활성화 여부 판단에 필요한 기존 장소 상태."""

    content_id: str
    source_modified_at: datetime | None
    detail_fetched_at: datetime | None
    detail_fetch_status: str
    operating_parser_version: str
    operating_hours_raw: str | None
    rest_date_raw: str | None
    is_active: bool
    inactive_reason: str | None


@dataclass(frozen=True)
class HolidayEntry:
    """한국천문연구원 공휴일 API 응답 한 건."""

    date: str
    name: str
    kind: str | None
    sequence: int | None
    is_holiday: bool
    raw_data: Mapping[str, object]


@dataclass(frozen=True)
class HolidayResult:
    """연·월 조건으로 조회한 공휴일 결과."""

    year: int
    month: int | None
    entries: tuple[HolidayEntry, ...]
    provider: str

    @property
    def holidays(self) -> tuple[HolidayEntry, ...]:
        """응답 중 isHoliday=Y인 항목만 반환한다."""
        return tuple(entry for entry in self.entries if entry.is_holiday)


@dataclass(frozen=True)
class ScoringCandidate:
    """Scoring v1의 입력 후보 공통 모델 (Candidate Model v1).

    역할: 특정 Provider나 Tool 출력 형태에 종속되지 않는 정규화된 후보 표현.
    `PlaceCandidate`(Provider 산출물)에 Tool Context가 채워 넣은 판단 근거
    (운영 상태, 실내외 구분, 거리)를 더해 Scoring이 필요로 하는 형태로 만든
    것이다. C-01 Tool 계약이 확정되기 전에는 이 모델에 맞춘 응답 샘플/Stub
    데이터로 Scoring을 개발하고, Tool 완성 후에는 Tool 출력 → 이 모델로의
    변환만 새로 작성하면 된다.

    `category`는 표시용 메타데이터로만 보존한다 (Scoring v1은 카테고리를
    가중치 계산에 사용하지 않고, place_type/place_tag 1차 하드 필터가 이미
    처리했다고 전제한다). `operating_hours`가 `None`이면 운영시간 자체를
    확인하지 못한 상태(미검증)이고, 폐점 여부는 Scoring이 `now`와
    `operating_hours`를 비교해 최종 하드 필터로 직접 판정한다.
    """

    place_id: str
    name: str
    category: str
    environment_type: str  # "indoor" | "outdoor" | "unknown"
    distance_km: float
    operating_hours: OperatingHours | None
    raw_source: str = "unknown"
    # 무장애 조건이 붙은 요청에서만 채워진다. 열쇠는 요구된 어휘, 값은
    # `possible`·`partial` 중 하나다(`AccessibilityVerdict`).
    #
    # **후보에 있다는 것이 온전히 갈 수 있다는 뜻은 아니다.** 들어갈 수는 있는데
    # 못 가는 구역이 남는 곳(`partial`)도 후보로 남기기 때문이다 — 팔각정 하나
    # 못 간다고 추천에서 빼는 것은 과하다. 그래서 이 값을 읽어 경고 한 줄로
    # 알린다(`scoring.py`의 `_accessibility_warnings`).
    #
    # **채점에는 쓰지 않는다.** `partial`이 "덜 좋은 곳"이 아니라 "한 군데를 못
    # 가는 곳"이라, 점수를 깎으면 갈 수 있는 좋은 장소를 뒤로 미는 셈이 된다.
    # 사용자에게 필요한 것은 순위 조정이 아니라 그 사실을 아는 것이다.
    #
    # `impossible`은 오지 않는다. 저장소 조회가 이미 후보에서 뺐다.
    accessibility_verdicts: Mapping[str, str] | None = None
