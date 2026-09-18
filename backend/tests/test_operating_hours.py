from datetime import UTC, datetime, time

from app.agent_context.mappers import _operating_schedule as serialize_operating_schedule
from app.agent_context.schemas import (
    ContextValue,
    Coordinates,
    PlaceCandidate,
    ProviderMetadata,
    RecommendationContext,
    ResolvedLocation,
)
from app.domain.candidate_mapper import map_context_to_scoring_candidates
from app.domain.operating_hours import (
    DERIVED_CLOSURE_SOURCE_TEXT,
    ClosureRule,
    OperatingAvailability,
    OperatingParseStatus,
    clean_operating_text,
    normalize_operating_schedule,
)
from app.domain.scoring import _is_closed


def test_clean_operating_text_preserves_html_line_breaks_and_entities() -> None:
    value = "<strong>운영시간</strong><br>09:00~18:00&nbsp;&amp; 입장 가능"

    assert clean_operating_text(value) == "운영시간\n09:00~18:00 & 입장 가능"


def test_normalizes_seasonal_hours_last_admission_and_weekly_closure() -> None:
    schedule = normalize_operating_schedule(
        content_type_id="12",
        operating_hours=(
            "[1월~2월] 09:00~17:00 (입장마감 16:00)<br>"
            "[3월~5월] 09:00~18:00 (입장마감 17:00)"
        ),
        rest_date="매주 화요일<br>공휴일과 겹치면 다음 비공휴일 휴무",
    )

    assert schedule.availability is OperatingAvailability.SCHEDULED
    assert schedule.parse_status is OperatingParseStatus.PARTIAL
    assert schedule.rules[0].months == frozenset({1, 2})
    assert schedule.rules[0].time_ranges[0].start == time(9)
    assert schedule.rules[0].time_ranges[0].end == time(17)
    assert schedule.rules[0].last_admission == time(16)
    assert schedule.closure_rules[0].weekdays == frozenset({1})
    assert schedule.warnings


def test_course_without_hours_is_assumed_all_day() -> None:
    schedule = normalize_operating_schedule(
        content_type_id="25",
        operating_hours=None,
        rest_date=None,
    )

    assert schedule.availability is OperatingAvailability.ALL_DAY
    assert schedule.parse_status is OperatingParseStatus.ASSUMED
    assert schedule.assumption_reason == "course_without_operating_hours"


def test_missing_hours_for_non_course_remains_unknown() -> None:
    schedule = normalize_operating_schedule(
        content_type_id="12",
        operating_hours=None,
        rest_date=None,
    )

    assert schedule.availability is OperatingAvailability.UNKNOWN
    assert schedule.parse_status is OperatingParseStatus.UNKNOWN
    assert schedule.assumption_reason is None


def test_parses_multiple_and_cross_midnight_ranges() -> None:
    schedule = normalize_operating_schedule(
        content_type_id="39",
        operating_hours="11:00~15:00, 17:00~익일 02:00",
        rest_date="연중무휴",
    )

    assert len(schedule.rules[0].time_ranges) == 2
    assert schedule.rules[0].time_ranges[1].crosses_midnight is True


def test_24_00_is_end_of_day_not_a_midnight_crossing() -> None:
    """24:00은 당일 종료이므로 자정 통과로 표시하지 않는다.

    time(0)으로 두면 end <= start가 되어 자정 통과로 오분류되고, 당일 구간만
    소비하는 candidate_mapper에서 구간이 통째로 버려진다.
    """
    schedule = normalize_operating_schedule(
        content_type_id="39",
        operating_hours="09:00~24:00",
        rest_date=None,
    )

    assert schedule.rules[0].time_ranges[0].end == time.max
    assert schedule.rules[0].time_ranges[0].crosses_midnight is False


def test_always_open_text_is_all_day() -> None:
    schedule = normalize_operating_schedule(
        content_type_id="12",
        operating_hours="상시 개방※ 우천 시, 안전상의 이유로 출입통제될 수 있음",
        rest_date=None,
    )

    assert schedule.availability is OperatingAvailability.ALL_DAY
    assert schedule.parse_status is OperatingParseStatus.PARSED
    assert schedule.rules == ()


def test_always_open_does_not_override_parsed_time_ranges() -> None:
    """부속 시설 시간이 함께 적힌 원문을 통째로 24시간으로 넓히지 않는다."""
    schedule = normalize_operating_schedule(
        content_type_id="12",
        operating_hours="상시 개방 ※ 낙산 전시관 09:00~17:00",
        rest_date=None,
    )

    assert schedule.availability is OperatingAvailability.SCHEDULED
    assert schedule.rules[0].time_ranges[0].end == time(17, 0)


def test_unrecognized_hours_are_partial_and_raw_value_is_preserved() -> None:
    schedule = normalize_operating_schedule(
        content_type_id="12",
        operating_hours="일몰 시간에 따라 변동",
        rest_date=None,
    )

    assert schedule.raw_operating_hours == "일몰 시간에 따라 변동"
    assert schedule.parse_status is OperatingParseStatus.PARTIAL
    assert schedule.availability is OperatingAvailability.UNKNOWN
    assert schedule.warnings


def test_weekly_closure_expands_tilde_weekday_range() -> None:
    """`매주 월요일~목요일`의 사이 요일까지 휴무로 읽는다.

    구분자에 `~`가 없으면 [월]만 남고, 있더라도 나열로 읽으면 [월, 목]이 되어
    화·수요일이 조용히 빠진다.
    """
    schedule = normalize_operating_schedule(
        content_type_id="12",
        operating_hours="09:00~18:00",
        rest_date="매주 월요일~목요일",
    )

    assert schedule.closure_rules[0].weekdays == frozenset({0, 1, 2, 3})


def test_weekly_closure_expands_range_across_week_boundary() -> None:
    schedule = normalize_operating_schedule(
        content_type_id="12",
        operating_hours="09:00~18:00",
        rest_date="매주 토요일~월요일",
    )

    assert schedule.closure_rules[0].weekdays == frozenset({5, 6, 0})


def test_weekly_closure_ignores_trailing_holiday_text() -> None:
    """`공휴일`의 `일`을 요일로 읽지 않는다."""
    schedule = normalize_operating_schedule(
        content_type_id="12",
        operating_hours="09:00~18:00",
        rest_date="매주 토요일~일요일 / 법정공휴일",
    )

    assert schedule.closure_rules[0].weekdays == frozenset({5, 6})


def test_operating_rules_are_split_by_weekday_scope() -> None:
    schedule = normalize_operating_schedule(
        content_type_id="12",
        operating_hours="[일요일~금요일]09:00~18:00[토요일]09:00~13:00",
        rest_date=None,
    )

    assert [rule.weekdays for rule in schedule.rules] == [
        frozenset({6, 0, 1, 2, 3, 4}),
        frozenset({5}),
    ]
    assert schedule.rules[1].time_ranges[0].end == time(13, 0)


def test_weekday_scope_carries_across_lines_until_next_declaration() -> None:
    """요일 선언과 시간 구간이 다른 줄에 오는 원문을 이어서 읽는다."""
    schedule = normalize_operating_schedule(
        content_type_id="12",
        operating_hours="[평일]<br>- 10:00~17:00<br>[토요일]<br>- 10:00~14:00",
        rest_date=None,
    )

    assert [rule.weekdays for rule in schedule.rules] == [
        frozenset({0, 1, 2, 3, 4}),
        frozenset({5}),
    ]


def test_weekday_scope_resets_at_non_weekday_bracket_section() -> None:
    """시설 구획이 바뀌면 앞 구획의 적용 요일을 물려주지 않는다."""
    schedule = normalize_operating_schedule(
        content_type_id="12",
        operating_hours=(
            "[열람실]<br>- 주말 09:00~17:00<br>[자율학습실]<br>- 07:00~22:00"
        ),
        rest_date=None,
    )

    assert schedule.rules[0].weekdays == frozenset({5, 6})
    assert schedule.rules[1].weekdays is None


def test_weekday_in_closure_note_does_not_scope_time_range() -> None:
    """`※` 뒤 휴관 안내의 요일을 운영 요일로 뒤집지 않는다."""
    schedule = normalize_operating_schedule(
        content_type_id="12",
        operating_hours="- 09:00~18:00※ 매주 화요일 휴관",
        rest_date=None,
    )

    assert schedule.rules[0].weekdays is None


def test_weekday_in_break_time_note_does_not_scope_time_range() -> None:
    schedule = normalize_operating_schedule(
        content_type_id="12",
        operating_hours=(
            "11:20~21:00 (주중 브레이크타임 15:00~17:00 / 토요일 브레이크타임 15:30~17:00)"
        ),
        rest_date=None,
    )

    assert [rule.weekdays for rule in schedule.rules] == [None]


def test_sunday_scope_survives_adjacent_public_holiday_text() -> None:
    """`일요일 및 공휴일`의 `공휴일`을 휴무 안내로 오인해 일요일 구간을 잃지 않는다."""
    schedule = normalize_operating_schedule(
        content_type_id="12",
        operating_hours="- 월요일~토요일 10:00~18:00<br>- 일요일 및 공휴일 10:00~17:00",
        rest_date=None,
    )

    assert schedule.rules[1].weekdays == frozenset({6})
    assert schedule.rules[1].time_ranges[0].end == time(17, 0)


def test_month_range_scope_is_preserved_without_weekday_declaration() -> None:
    schedule = normalize_operating_schedule(
        content_type_id="12",
        operating_hours="[1월~2월] 09:00~17:00<br>[3월~5월] 09:00~18:00",
        rest_date=None,
    )

    assert [rule.months for rule in schedule.rules] == [
        frozenset({1, 2}),
        frozenset({3, 4, 5}),
    ]
    assert [rule.weekdays for rule in schedule.rules] == [None, None]


def _scoring_candidate_at(operating_hours: str, visit_at: datetime, rest_date: str | None = None):
    """정규화 결과가 D의 운영 판정을 실제로 움직이는지 공개 경로로 확인한다."""
    schedule = normalize_operating_schedule(
        content_type_id="12",
        operating_hours=operating_hours,
        rest_date=rest_date,
    )
    context = RecommendationContext(
        location=ContextValue(
            status="success",
            data=ResolvedLocation(
                requested_query="경복궁",
                resolved_name="경복궁",
                source="query",
                location=Coordinates(latitude=37.5796, longitude=126.9770),
            ),
        ),
        places=ContextValue(
            status="success",
            data=[
                PlaceCandidate(
                    place_id="place-1",
                    name="후보 장소",
                    category="restaurant",
                    location=Coordinates(latitude=37.5806, longitude=126.9770),
                    operating_schedule=serialize_operating_schedule(schedule),
                )
            ],
            provider_metadata=[
                ProviderMetadata(
                    source="fake_place",
                    status="success",
                    retrieved_at=datetime(2026, 7, 24, tzinfo=UTC),
                )
            ],
        ),
    )
    return map_context_to_scoring_candidates(context, visit_at=visit_at)[0]


def test_saturday_short_hours_close_the_candidate_for_scoring() -> None:
    """토요일 13:00에 닫는 장소가 토요일 14:00 추천에서 폐점으로 걸러진다.

    요일을 분리하지 않으면 소비 측이 09:00~18:00 구간을 골라 잔여 240분(운영점수
    만점)으로 추천한다.
    """
    visit_at = datetime(2026, 8, 8, 14, 0)
    candidate = _scoring_candidate_at(
        "[일요일~금요일]09:00~18:00[토요일]09:00~13:00",
        visit_at,
    )

    assert _is_closed(candidate, visit_at) is True
    # 폐점이어도 그날 실제 구간은 남는다 — 카드가 "09:00~13:00"을 표시해야 한다.
    assert candidate.operating_hours is not None
    assert candidate.operating_hours.close_time == time(13, 0)


def test_saturday_short_hours_stay_open_before_closing() -> None:
    candidate = _scoring_candidate_at(
        "[일요일~금요일]09:00~18:00[토요일]09:00~13:00",
        datetime(2026, 8, 8, 10, 0),
    )

    assert candidate.operating_hours is not None
    assert candidate.operating_hours.close_time == time(13, 0)


def test_weekday_scope_keeps_remaining_time_from_the_right_range() -> None:
    """월요일 전용 구간이 토요일 잔여시간을 30분으로 깎지 않는다."""
    candidate = _scoring_candidate_at(
        "- 월요일 11:00~15:00- 화요일~일요일 11:00~20:30",
        datetime(2026, 8, 8, 14, 30),
    )

    assert candidate.operating_hours is not None
    assert candidate.operating_hours.close_time == time(20, 30)


def test_weekday_not_listed_in_operating_hours_is_derived_as_closure() -> None:
    """요일을 열거한 원문에서 빠진 요일은 정기 휴무로 유도한다.

    북촌문화센터 원문 — 휴무 필드가 비어 있고 월요일만 열거에서 빠져 있다.
    """
    schedule = normalize_operating_schedule(
        content_type_id="12",
        operating_hours=(
            "- 화요일 / 목요일~금요일 09:00~18:00<br>"
            "- 수요일 09:00~20:00<br>"
            "- 토요일~일요일 09:00~17:00"
        ),
        rest_date=None,
    )

    assert len(schedule.closure_rules) == 1
    assert schedule.closure_rules[0].weekdays == frozenset({0})
    assert schedule.closure_rules[0].source_text == DERIVED_CLOSURE_SOURCE_TEXT


def test_derived_closure_excludes_the_candidate_for_scoring() -> None:
    """유도한 휴무가 소비 측 폐점 판정까지 이어진다."""
    visit_at = datetime(2026, 8, 3, 10, 0)
    candidate = _scoring_candidate_at(
        "- 화요일 / 목요일~금요일 09:00~18:00<br>- 수요일 09:00~20:00<br>"
        "- 토요일~일요일 09:00~17:00",
        visit_at,
    )

    assert candidate.operating_hours is not None
    assert candidate.operating_hours.is_regular_closure is True
    assert _is_closed(candidate, visit_at) is True


def test_declared_rest_day_closes_the_candidate_but_keeps_the_usual_hours() -> None:
    """휴관일에도 평소 구간은 카드에 남기고, 폐점 판정만 건다.

    휴무 요일을 보지 않으면 평소 구간(09:00~18:00)이 방문 시각을 포함해 잔여
    360분으로 읽히고, 휴관일 장소가 그대로 추천된다.
    """
    visit_at = datetime(2026, 8, 3, 12, 0)  # 월요일
    candidate = _scoring_candidate_at("09:00~18:00", visit_at, rest_date="매주 월요일")

    assert _is_closed(candidate, visit_at) is True
    assert candidate.operating_hours is not None
    assert candidate.operating_hours.open_time == time(9, 0)
    assert candidate.operating_hours.close_time == time(18, 0)


def test_same_place_stays_open_on_a_non_rest_day() -> None:
    """휴무 요일이 다른 요일까지 닫아버리지 않는다(대조군)."""
    visit_at = datetime(2026, 8, 4, 12, 0)  # 화요일
    candidate = _scoring_candidate_at("09:00~18:00", visit_at, rest_date="매주 월요일")

    assert _is_closed(candidate, visit_at) is False
    assert candidate.operating_hours is not None
    assert candidate.operating_hours.is_regular_closure is False


def test_all_day_place_is_still_closed_on_its_rest_day() -> None:
    """24시간 영업도 정기 휴무일에는 닫는다 — 휴무는 운영 구간과 독립이다."""
    visit_at = datetime(2026, 8, 3, 12, 0)  # 월요일
    candidate = _scoring_candidate_at("상시 개방", visit_at, rest_date="매주 월요일")

    assert _is_closed(candidate, visit_at) is True


def test_no_closure_is_derived_when_a_rule_covers_every_weekday() -> None:
    """요일 없는 규칙이 하나라도 있으면 전 요일을 덮으므로 유도하지 않는다."""
    schedule = normalize_operating_schedule(
        content_type_id="12",
        operating_hours="[열람실]<br>- 주말 09:00~17:00<br>[자율학습실]<br>- 07:00~22:00",
        rest_date=None,
    )

    assert schedule.closure_rules == ()


def test_derived_closure_does_not_duplicate_declared_rest_date() -> None:
    schedule = normalize_operating_schedule(
        content_type_id="12",
        operating_hours="- 월요일~금요일 07:00~20:00<br>- 토요일 10:00~14:00",
        rest_date="매주 일요일",
    )

    assert [rule.weekdays for rule in schedule.closure_rules] == [frozenset({6})]
    assert schedule.closure_rules[0].source_text == "매주 일요일"


class Test주기_휴무를_담는_자리:
    """`2,4주 일요일`·`월 1회 월요일`을 담을 자리. (TP-231)

    이 카드는 자리만 만든다 — 채우는 것은 다음 카드의 LLM 추출이다. 그래서 여기
    테스트는 "담으면 판정이 따라오는가"와 "안 담으면 예전과 같은가"를 본다.
    """

    def test_기본값은_매주다(self) -> None:
        """이 필드를 모르는 기존 데이터가 그대로 매주로 읽혀야 한다."""
        rule = ClosureRule(weekdays=frozenset({0}), source_text="매주 월요일")
        assert rule.week_ordinals is None
        assert rule.uncertain is False
