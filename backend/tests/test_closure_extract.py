"""LLM이 읽은 휴무를 정규식 결과에 얹는 병합. (TP-231)

**거짓 양성이 거짓 음성보다 나쁘다.** 놓치면 사용자가 헛걸음하지만, 안 쉬는 요일을
쉰다고 담으면 그 장소가 그날 목록에서 통째로 사라진다. 그래서 "모양이 이상하면
버린다"를 여기서 못 박는다.
"""

from __future__ import annotations

import pytest

from app.domain.operating_hours import (
    OperatingAvailability,
    OperatingParseStatus,
    OperatingSchedule,
    normalize_operating_schedule,
)
from app.tools.closure_extract import merge_extracted_closures, needs_extraction


def _schedule(rest: str | None) -> OperatingSchedule:
    return normalize_operating_schedule(
        content_type_id="12", operating_hours="09:00~18:00", rest_date=rest
    )


class Test언제_LLM을_부르나:
    """전부 부르지 않는다 — 비용도 늘고 지어낼 여지만 생긴다."""

    def test_원문이_없으면_안_부른다(self) -> None:
        assert needs_extraction(_schedule(None)) is False

    def test_연중무휴는_안_부른다(self) -> None:
        """쉬는 날이 없다는 뜻이라 뽑을 것이 없다."""
        assert needs_extraction(_schedule("연중무휴")) is False

    def test_정규식이_이미_읽었으면_안_부른다(self) -> None:
        schedule = _schedule("매주 월요일")
        assert schedule.closure_rules != ()
        assert needs_extraction(schedule) is False

    @pytest.mark.parametrize("rest", ["2,4주 일요일", "월 1회 월요일", "일요일"])
    def test_못_읽은_원문은_부른다(self, rest: str) -> None:
        assert needs_extraction(_schedule(rest)) is True


class Test병합:
    _EMPTY = OperatingSchedule(
        raw_operating_hours=None, raw_rest_date=None,
        cleaned_operating_hours=None, cleaned_rest_date=None,
        availability=OperatingAvailability.SCHEDULED, rules=(), closure_rules=(),
        parse_status=OperatingParseStatus.PARSED, assumption_reason=None, warnings=(),
    )

    def test_매주를_담는다(self) -> None:
        merged = merge_extracted_closures(self._EMPTY, {"weekly": ["일"]})
        assert len(merged.closure_rules) == 1
        rule = merged.closure_rules[0]
        assert rule.weekdays == frozenset({6})
        assert rule.week_ordinals is None
        assert rule.uncertain is False

    def test_주차를_담는다(self) -> None:
        merged = merge_extracted_closures(
            self._EMPTY, {"nth_weekday": [{"weekday": "일", "ordinals": [2, 4]}]}
        )
        rule = merged.closure_rules[0]
        assert rule.weekdays == frozenset({6})
        assert rule.week_ordinals == frozenset({2, 4})

    def test_불확실을_담는다(self) -> None:
        merged = merge_extracted_closures(self._EMPTY, {"uncertain_weekdays": ["월"]})
        rule = merged.closure_rules[0]
        assert rule.uncertain is True
        assert rule.week_ordinals is None

    def test_정규식이_읽은_것을_덮지_않는다(self) -> None:
        """겹치면 정규식을 남긴다 — 명확한 형태만 읽으므로 더 믿을 만하다."""
        schedule = _schedule("매주 월요일")
        merged = merge_extracted_closures(schedule, {"weekly": ["월"]})
        assert merged.closure_rules == schedule.closure_rules

    def test_담을_것이_없으면_그대로_돌려준다(self) -> None:
        schedule = _schedule("매주 월요일")
        assert merge_extracted_closures(schedule, {}) is schedule


class Test이상한_값은_버린다:
    """배치 한 건이 실패해 그 장소가 통째로 빠지는 것보다, 그 항목만 없이 저장한다."""

    _EMPTY = Test병합._EMPTY

    @pytest.mark.parametrize(
        "payload",
        [
            {"weekly": ["없는요일"]},
            {"weekly": "일"},                       # 문자열은 목록이 아니다
            {"weekly": [None, 3]},
            {"nth_weekday": [{"weekday": "일"}]},    # 순번이 없다
            {"nth_weekday": [{"ordinals": [2]}]},    # 요일이 없다
            {"nth_weekday": ["문자열"]},
            {"nth_weekday": [{"weekday": "일", "ordinals": [0, 9]}]},  # 범위 밖
            {"uncertain_weekdays": [{"weekday": "월"}]},
        ],
    )
    def test_담기지_않는다(self, payload: dict) -> None:
        assert merge_extracted_closures(self._EMPTY, payload).closure_rules == ()

    def test_한_달에_같은_요일은_다섯_번까지다(self) -> None:
        merged = merge_extracted_closures(
            self._EMPTY, {"nth_weekday": [{"weekday": "일", "ordinals": [1, 5, 6, 99]}]}
        )
        assert merged.closure_rules[0].week_ordinals == frozenset({1, 5})

    def test_같은_요일을_두_번_주면_한_번만_담는다(self) -> None:
        merged = merge_extracted_closures(self._EMPTY, {"weekly": ["일", "일"]})
        assert len(merged.closure_rules) == 1


class Test출처를_남긴다:
    """정규식이 읽은 것과 LLM이 읽은 것을 나중에 갈라 볼 수 있어야 한다."""

    def test_source_text에_llm을_적는다(self) -> None:
        merged = merge_extracted_closures(Test병합._EMPTY, {"weekly": ["일"]})
        assert merged.closure_rules[0].source_text.startswith("llm:")

    def test_정규식_출처는_원문이다(self) -> None:
        schedule = _schedule("매주 월요일")
        assert schedule.closure_rules[0].source_text == "매주 월요일"
        assert not schedule.closure_rules[0].source_text.startswith("llm:")


class Test적재_배선:
    """적재 배치가 추출기를 실제로 부르고, 실패해도 적재를 막지 않는지. (TP-231)"""

    @staticmethod
    def _schedule_with_rest(rest: str):
        return normalize_operating_schedule(
            content_type_id="12", operating_hours="09:00~18:00", rest_date=rest
        )

    @pytest.mark.asyncio
    async def test_추출기가_없으면_정규식_결과_그대로다(self) -> None:
        from app.services.place_sync import _enrich_closures

        schedule = self._schedule_with_rest("2,4주 일요일")
        assert await _enrich_closures(schedule, None) is schedule

    @pytest.mark.asyncio
    async def test_부를_필요가_없으면_안_부른다(self) -> None:
        """`매주 월요일`은 정규식이 이미 읽었다."""
        from app.services.place_sync import _enrich_closures

        class _Never:
            async def extract_closure_rules(self, rest_date):
                raise AssertionError("부를 이유가 없다")

        schedule = self._schedule_with_rest("매주 월요일")
        assert await _enrich_closures(schedule, _Never()) is schedule

    @pytest.mark.asyncio
    async def test_추출_결과가_실제로_담긴다(self) -> None:
        from app.providers.contracts import ProviderSource, provider_result
        from app.services.place_sync import _enrich_closures

        class _Extractor:
            def __init__(self) -> None:
                self.seen: list[str] = []

            async def extract_closure_rules(self, rest_date):
                self.seen.append(rest_date)
                return provider_result(
                    {"nth_weekday": [{"weekday": "일", "ordinals": [2, 4]}]},
                    source=ProviderSource.FAKE_LLM,
                )

        extractor = _Extractor()
        merged = await _enrich_closures(
            self._schedule_with_rest("2,4주 일요일"), extractor
        )
        assert extractor.seen == ["2,4주 일요일"]
        assert merged.closure_rules[0].week_ordinals == frozenset({2, 4})

    @pytest.mark.asyncio
    async def test_추출이_실패해도_적재를_막지_않는다(self) -> None:
        """휴무 하나를 못 읽었다고 그 장소가 통째로 빠지면 안 된다."""
        from app.services.place_sync import _enrich_closures

        class _Broken:
            async def extract_closure_rules(self, rest_date):
                raise RuntimeError("추출 실패")

        schedule = self._schedule_with_rest("2,4주 일요일")
        assert await _enrich_closures(schedule, _Broken()) is schedule

    @pytest.mark.asyncio
    async def test_모양이_이상한_응답도_적재를_막지_않는다(self) -> None:
        from app.providers.contracts import ProviderSource, provider_result
        from app.services.place_sync import _enrich_closures

        class _Weird:
            async def extract_closure_rules(self, rest_date):
                return provider_result("문자열", source=ProviderSource.FAKE_LLM)

        schedule = self._schedule_with_rest("2,4주 일요일")
        assert await _enrich_closures(schedule, _Weird()) is schedule
