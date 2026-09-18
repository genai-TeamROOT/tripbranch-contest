"""detailIntro2 응답의 contenttypeid별 필드명 묶음.

TourAPI는 같은 의미의 값을 유형마다 다른 키로 내려준다(관광지는 `parking`,
음식점은 `parkingfood`). 소비 측이 유형 분기를 하지 않도록 provider가 이 목록을
앞에서부터 훑어 먼저 걸리는 값을 정규화 필드에 담는다.

실 provider(`real_place`)와 fake(`stub`)가 **같은 목록을 쓰는 것이 핵심이라** 모듈로
분리했다. fake가 자기만의 키 목록을 들고 있으면 fake로 검증한 동작이 실 응답에서
재현되지 않는다 — 테스트는 통과하는데 운영에서만 값이 비는 형태가 된다.
"""

from __future__ import annotations

OPERATING_HOURS_KEYS = (
    "usetime",
    "usetimeculture",
    "playtime",
    "usetimeleports",
    "opentime",
    "opentimefood",
    "checkintime",
    "openperiod",
)
REST_DATE_KEYS = (
    "restdate",
    "restdateculture",
    "restdateleports",
    "restdateshopping",
    "restdatefood",
)

# 주차·요금 필드다(D-056, 2026-08-08 실측).
# 축제(15)에는 주차 필드 자체가 없다. 종로구 844건 중 38건이 해당한다.
PARKING_KEYS = (
    "parking",  # 12 관광지
    "parkingculture",  # 14 문화시설
    "parkinglodging",  # 32 숙박
    "parkingshopping",  # 38 쇼핑
    "parkingfood",  # 39 음식점
    "parkingleports",  # 28 레포츠
)
PARKING_FEE_KEYS = (
    "parkingfee",  # 14 문화시설
    "parkingfeeleports",  # 28 레포츠
)

# 축제의 이용요금 필드명이 usetimefestival이다. 이름은 시간처럼 보이지만 내용은
# 요금이라, 이 키를 OPERATING_HOURS_KEYS에 넣으면 영업시간 자리에 "5,000원"이
# 들어간다. 축제 운영시간은 playtime이 담당한다 — 두 목록을 섞지 않는다.
USE_FEE_KEYS = (
    "usefee",  # 14 문화시설
    "usefeeleports",  # 28 레포츠
    "usetimefestival",  # 15 축제 (이름과 달리 요금)
)
DISCOUNT_INFO_KEYS = (
    "discountinfo",  # 14 문화시설
    "discountinfofestival",  # 15 축제
    "discountinfofood",  # 39 음식점
)

# 안내처(전화번호). detailCommon2의 tel은 축제에만 채워지고(표본 35건에서 15는 5/5,
# 나머지 유형은 전부 0/5) 실제 출처는 detailIntro2다 — 같은 표본에서 33건 중 32건.
# 축제(15)는 이 계열이 없어 sponsor1tel을 쓰지만, detailCommon2의 tel이 함께
# 채워지므로 여기서는 다루지 않는다.
INFO_CENTER_KEYS = (
    "infocenter",  # 12 관광지
    "infocenterculture",  # 14 문화시설
    "infocenterleports",  # 28 레포츠
    "infocenterlodging",  # 32 숙박
    "infocentershopping",  # 38 쇼핑
    "infocenterfood",  # 39 음식점
)

# 편의시설. 숙박(32)·축제(15)에는 이 필드들이 없다. 값은 `가능`/`없음`/`있음` 같은
# 단답이고, `없음`은 비어 있는 것과 다르다 — "정보가 없다"가 아니라 "없다고 답했다"다.
BABY_CARRIAGE_KEYS = (
    "chkbabycarriage",  # 12 관광지
    "chkbabycarriageculture",  # 14 문화시설
    "chkbabycarriageleports",  # 28 레포츠
    "chkbabycarriageshopping",  # 38 쇼핑
)
PET_KEYS = (
    "chkpet",  # 12 관광지
    "chkpetculture",  # 14 문화시설
    "chkpetleports",  # 28 레포츠
    "chkpetshopping",  # 38 쇼핑
)
CREDIT_CARD_KEYS = (
    "chkcreditcard",  # 12 관광지
    "chkcreditcardculture",  # 14 문화시설
    "chkcreditcardleports",  # 28 레포츠
    "chkcreditcardshopping",  # 38 쇼핑
    "chkcreditcardfood",  # 39 음식점
)
# 화장실만 유형 구분 없이 한 키다.
RESTROOM_KEYS = ("restroom",)

__all__ = [
    "BABY_CARRIAGE_KEYS",
    "CREDIT_CARD_KEYS",
    "DISCOUNT_INFO_KEYS",
    "INFO_CENTER_KEYS",
    "OPERATING_HOURS_KEYS",
    "PARKING_FEE_KEYS",
    "PARKING_KEYS",
    "PET_KEYS",
    "REST_DATE_KEYS",
    "RESTROOM_KEYS",
    "USE_FEE_KEYS",
]
