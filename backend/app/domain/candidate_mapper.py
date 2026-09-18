"""장소 Tool 결과를 ScoringCandidate로 변환한다.

Provider 응답 형태(Real/Fake)에는 독립적이다. 다만 environment_type 세분화
(D-046)를 위해 TourCategoryRegistry(JSON 파일을 프로세스 시작 시 한 번 로드해
조회만 하는 정적 참조 테이블, 부작용·외부 호출 없음)는 예외적으로 참조한다.
TECH-02가 없앤 건 "D가 실행 중에 C의 Tool(날씨·장소 조회 등)을 직접 호출"하는
런타임 의존이고, 이 조회는 그와 성격이 달라 TECH-02 위반이 아니라고 판단했다
(C도 5a3dacc 커밋 메시지에서 이 조회 방식을 직접 지정함). 이 판단 자체를 C에게
확인 요청했고 승인받았다(2026-08-04) — package_D/feature-environment-type-classification.md,
docs/decision-log.md D-046 참고.
"""

from __future__ import annotations

from datetime import datetime, time
from typing import Any

from app.agent_context.schemas import RecommendationContext
from app.domain.models import OperatingHours, ScoringCandidate
from app.domain.ranking_origin import haversine_km, resolve_ranking_origin
from app.providers.tour_category_registry import get_tour_category_registry
from app.schemas import UserConditions

# 대분류(category)만으로는 실내외를 가릴 수 없어(관광지에 고궁과 체험관이, 쇼핑에
# 면세점과 시장이 함께 있다) lcls_systm3(소분류)로 TourCategoryRegistry를 조회해
# (content_type_id, lcls_systm2) 중분류 단위로 판정한다 — D-046.
# 판정 근거: package_D/feature-environment-type-classification.md
_INDOOR_CATEGORIES = {"cultural_facility", "restaurant"}
_OUTDOOR_CATEGORIES = {"attraction"}

_MIDDLE_CATEGORY_ENVIRONMENT: dict[tuple[str, str], str] = {
    # content_type=12 (attraction)
    ("12", "EX02"): "indoor",  # 공예체험
    ("12", "EX03"): "outdoor",  # 농.산.어촌 체험
    ("12", "EX05"): "indoor",  # 웰니스관광
    ("12", "EX06"): "indoor",  # 산업관광
    ("12", "HS01"): "outdoor",  # 역사유적지
    ("12", "HS02"): "outdoor",  # 역사유물
    ("12", "HS04"): "outdoor",  # 안보관광지
    ("12", "NA01"): "outdoor",  # 자연경관(산)
    ("12", "NA02"): "outdoor",  # 자연경관(하천‧해양)
    ("12", "NA03"): "outdoor",  # 자연생태
    ("12", "NA04"): "outdoor",  # 자연공원
    ("12", "NA05"): "outdoor",  # 기타자연관광
    ("12", "VE01"): "outdoor",  # 랜드마크관광
    ("12", "VE03"): "outdoor",  # 도시공원
    ("12", "VE04"): "outdoor",  # 도시.지역문화관광
    # content_type=14 (cultural_facility) — 전부 indoor
    ("14", "VE06"): "indoor",  # 공연시설
    ("14", "VE07"): "indoor",  # 전시시설
    ("14", "VE08"): "indoor",  # 행사시설
    ("14", "VE09"): "indoor",  # 교육시설
    ("14", "VE12"): "indoor",  # 기타문화관광지(서점 등)
    # content_type=28 (leisure)
    ("28", "AC05"): "outdoor",  # 캠핑
    ("28", "LS02"): "outdoor",  # 수상레저스포츠
    ("28", "LS03"): "outdoor",  # 항공레저스포츠
    ("28", "VE12"): "indoor",  # 기타문화관광지(카지노)
    # content_type=38 (shopping)
    ("38", "SH01"): "indoor",  # 백화점
    ("38", "SH03"): "indoor",  # 대형마트
    ("38", "SH04"): "indoor",  # 면세점
    ("38", "SH05"): "indoor",  # 전문매장/상가
    ("38", "SH06"): "outdoor",  # 시장
    ("38", "SH07"): "indoor",  # 기타쇼핑시설
    # content_type=39 (restaurant) — 전부 indoor
    ("39", "FD01"): "indoor",  # 한식
    ("39", "FD02"): "indoor",  # 외국식
    ("39", "FD03"): "indoor",  # 간이음식
    ("39", "FD04"): "indoor",  # 주점
    ("39", "FD05"): "indoor",  # 카페/찻집
}
_WEEKDAY_NAMES = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)


def map_context_to_scoring_candidates(
    context: RecommendationContext,
    *,
    visit_at: datetime,
    conditions: UserConditions | None = None,
) -> tuple[ScoringCandidate, ...]:
    """A–C 공개 Context를 D의 ScoringCandidate 목록으로 변환한다.

    공휴일 Context는 이번 변환에서 사용하지 않는다. 정기 휴무 요일과 운영시간은
    장소별 ``operating_schedule``만 사용하고, 실제 폐점 제외는 Scoring이 수행한다.
    """

    location_value = context.location
    places_value = context.places
    if (
        location_value is None
        or location_value.status not in {"success", "partial"}
        or location_value.data is None
        or places_value is None
        or places_value.status not in {"success", "partial"}
        or not places_value.data
    ):
        return ()

    # 거리는 사용자 기준으로 잰다 — 후보를 모은 중심(검색 기준점)과 줄을 세우는
    # 기준점은 다르다(TP-112, ranking_origin.py). 사용자 위치를 모르면 검색
    # 기준점으로 돌아간다.
    origin = (resolve_ranking_origin(context, conditions) or location_value.data).location
    source = (
        places_value.provider_metadata[0].source if places_value.provider_metadata else "unknown"
    )
    return tuple(
        ScoringCandidate(
            place_id=place.place_id,
            name=place.name,
            category=place.category,
            environment_type=_environment_type(place.category, place.lcls_systm3),
            distance_km=haversine_km(
                origin.latitude,
                origin.longitude,
                place.location.latitude,
                place.location.longitude,
            ),
            operating_hours=_operating_hours_from_context(
                place.operating_schedule,
                visit_at,
            ),
            raw_source=source,
            # 무장애 조건이 없는 요청에서는 None이다. 값이 있으면 그대로 옮기고
            # 여기서 판정하지 않는다 — 원문을 읽어 내린 판정은 저장소에 있고,
            # D가 다시 해석하면 같은 장소에 두 규칙이 생긴다.
            accessibility_verdicts=place.accessibility_verdicts,
        )
        for place in places_value.data
    )


def _environment_type(category: str, lcls_systm3: str | None) -> str:
    """중분류(lcls_systm2) 기준으로 판정하고, 소분류 코드가 없거나 Registry에
    없으면(과거 fixture 등) 대분류(category) 기준 최소 매핑으로 폴백한다."""
    if lcls_systm3:
        found = get_tour_category_registry().get_by_small_code(lcls_systm3)
        if found is not None:
            return _MIDDLE_CATEGORY_ENVIRONMENT.get(
                (found.content_type_id, found.lcls_systm2), "unknown"
            )

    normalized = category.strip().lower()
    if normalized in _INDOOR_CATEGORIES:
        return "indoor"
    if normalized in _OUTDOOR_CATEGORIES:
        return "outdoor"
    return "unknown"


def _operating_hours_from_context(
    schedule: dict[str, Any] | None,
    visit_at: datetime,
) -> OperatingHours | None:
    """직렬화된 운영 규칙에서 방문일의 대표 구간을 선택한다.

    지금 영업 중인 구간이 없더라도 실제 시간 구간은 보존한다. 이전에는 폐점/정기
    휴무를 ``00:00~00:00`` 내부 표식으로 바꿨는데, Scoring 이후 추천 카드가 이
    표식을 그대로 표시해 "00:00~00:00 (현재 운영시간 아님)"으로 보였다.

    다만 시각만으로는 정기 휴무를 전달할 수 없다 — 휴관일에도 평소 구간
    (09:00~18:00)은 방문 시각을 포함하므로, ``_remaining_minutes()``는 잔여
    360분을 돌려주고 폐점 판정이 통째로 뚫린다. 그래서 휴무 여부는 시각을 지우는
    대신 ``OperatingHours.is_regular_closure``로 함께 실어 보낸다.
    """

    if not schedule or schedule.get("availability") == "unknown":
        return None
    # 휴무는 운영 구간과 독립이다 — 24시간 영업(all_day)도 정기 휴무일이 있을 수 있다.
    regular_closure = _is_regular_closure(schedule, visit_at)
    uncertain_closure = has_uncertain_closure(schedule, visit_at)
    if schedule.get("availability") == "all_day":
        return OperatingHours(
            open_time=time.min,
            close_time=time.max,
            is_regular_closure=regular_closure,
            has_uncertain_closure=uncertain_closure,
        )

    raw_rules = schedule.get("rules")
    rules = (
        raw_rules
        if isinstance(raw_rules, list) and raw_rules
        else [{"months": None, "weekdays": None, "time_ranges": schedule.get("time_ranges")}]
    )
    fallback_range: tuple[time, time] | None = None
    for rule in rules:
        if not isinstance(rule, dict) or not _rule_applies(rule, visit_at):
            continue
        ranges = _context_time_ranges(rule.get("time_ranges"))
        if not ranges:
            continue
        if fallback_range is None:
            fallback_range = ranges[0]
        current_time = visit_at.time()
        active = next(
            (
                (open_time, close_time)
                for open_time, close_time in ranges
                if open_time <= current_time < close_time
            ),
            None,
        )
        if active is not None:
            return OperatingHours(
                open_time=active[0],
                close_time=active[1],
                is_regular_closure=regular_closure,
                has_uncertain_closure=uncertain_closure,
            )

    # 정기 휴무·영업 전·영업 종료 후에도 카드에는 실제 운영 구간을 표시한다.
    # Scoring은 영업 전/후는 remaining_minutes가 None인 것으로, 정기 휴무는
    # is_regular_closure로 판별해 폐점 필터 또는 "운영시간 무시" 경고를 건다.
    if fallback_range is not None:
        return OperatingHours(
            open_time=fallback_range[0],
            close_time=fallback_range[1],
            is_regular_closure=regular_closure,
            has_uncertain_closure=uncertain_closure,
        )
    if regular_closure:
        # 요일 열거에서 빠져 유도한 휴무는 그날 구간 자체가 원문에 없다. 표시할
        # 시각은 없지만 폐점인 것은 확실하므로, `None`(운영시간 미확인 — 폐점으로
        # 걸러지지 않는다)으로 두지 않고 길이 0 구간에 휴무 표식을 실어 보낸다.
        # 표시 측은 이 구간을 시각으로 쓰지 않는다(`_operating_hours_display`).
        return OperatingHours(
            open_time=time.min,
            close_time=time.min,
            is_regular_closure=True,
        )
    return None


def _is_regular_closure(schedule: dict[str, Any], visit_at: datetime) -> bool:
    """방문일이 정기 휴무 요일인지 본다.

    `closure_rules`에는 원문에 적힌 휴무(`매주 월요일`)와 운영 요일 열거에서
    빠진 요일로 유도한 휴무가 함께 들어온다(`operating_hours.py`).

    `week_ordinals`가 있으면 **그 달의 몇 번째 그 요일인지**까지 본다
    (`2,4주 일요일`). 없으면 매주다 — 이 키를 모르는 기존 행도 그 뜻으로 읽힌다.

    `uncertain`인 규칙은 **휴무로 치지 않는다.** 주기 휴무인 것은 알지만 몇 번째인지
    원문에 없는 경우인데(`월 1회 월요일`), 매주로 치면 안 쉬는 주에 후보가 통째로
    사라진다. 그 사실은 경고로 따로 알린다(`uncertain_closure_note()`).
    """
    weekday = _WEEKDAY_NAMES[visit_at.weekday()]
    ordinal = _weekday_ordinal(visit_at)
    for rule in _closure_rules(schedule):
        if rule.get("uncertain"):
            continue
        weekdays = rule.get("weekdays")
        if not isinstance(weekdays, list) or weekday not in weekdays:
            continue
        ordinals = rule.get("week_ordinals")
        if isinstance(ordinals, list) and ordinal not in ordinals:
            continue
        return True
    return False


def _closure_rules(schedule: dict[str, Any]) -> list[dict[str, Any]]:
    rules = schedule.get("closure_rules")
    if not isinstance(rules, list):
        return []
    return [rule for rule in rules if isinstance(rule, dict)]


def _weekday_ordinal(visit_at: datetime) -> int:
    """그 달의 몇 번째 그 요일인가(1부터).

    "그 달 N번째 주에 속한 요일"로 세지 않는다 — 그러면 주 경계 정의를 끌어들여야
    하고 1일의 요일에 따라 답이 흔들린다(ClosureRule.week_ordinals 주석 참고).
    """
    return (visit_at.day - 1) // 7 + 1


def has_uncertain_closure(schedule: dict[str, Any], visit_at: datetime) -> bool:
    """방문 요일에 "몇 번째인지 모르는" 주기 휴무가 걸려 있는지 본다.

    걸려 있으면 그날 문을 닫을 수도 있지만 확인할 수 없다. 후보는 살리고 경고만
    붙인다 — 소비 측이 카드에 안내를 그린다.
    """
    weekday = _WEEKDAY_NAMES[visit_at.weekday()]
    return any(
        rule.get("uncertain")
        and isinstance(rule.get("weekdays"), list)
        and weekday in rule["weekdays"]
        for rule in _closure_rules(schedule)
    )


def _rule_applies(rule: dict[str, Any], visit_at: datetime) -> bool:
    months = rule.get("months")
    if isinstance(months, list) and visit_at.month not in months:
        return False
    weekdays = rule.get("weekdays")
    return not (isinstance(weekdays, list) and _WEEKDAY_NAMES[visit_at.weekday()] not in weekdays)


def _context_time_ranges(value: object) -> tuple[tuple[time, time], ...]:
    if not isinstance(value, list):
        return ()
    result: list[tuple[time, time]] = []
    for item in value:
        if not isinstance(item, dict) or item.get("crosses_midnight") is True:
            continue
        try:
            open_time = time.fromisoformat(str(item["open_time"]))
            close_time = time.fromisoformat(str(item["close_time"]))
        except (KeyError, TypeError, ValueError):
            continue
        if open_time <= close_time:
            result.append((open_time, close_time))
    return tuple(result)


