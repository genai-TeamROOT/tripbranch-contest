"""조건 병합 엔진.

계약 문서: 2.3~2.9절, 5.5절
"""


from app.state.merge import merge_conditions
from app.state.operations import Operation, validate_all
from app.state.schema import UserConditions

SESSION_ID = "sess_test"
RUN_ID = "run_test"


def merge(before: UserConditions, raw_ops: list[dict], reset: str | None = None):
    """검증을 거쳐 병합한다. 테스트 편의용 헬퍼."""
    valid, ignored = validate_all([Operation(**o) for o in raw_ops])
    result = merge_conditions(
        before, valid, session_id=SESSION_ID, run_id=RUN_ID, reset_scope=reset
    )
    return result, ignored


# ---------------------------------------------------------------- 계약 2.9절

class TestContractExamples:
    """계약 문서 2.9절 적용 예시를 그대로 테스트로 고정한다."""

    def test_예시1_조건_추가(self):
        before = UserConditions(place_types=["restaurant"])
        result, _ = merge(
            before, [{"op": "Add", "field": "special_requirements", "value": ["주차"]}]
        )

        assert result.conditions.special_requirements == ["주차"]
        assert result.conditions.place_types == ["restaurant"]
        assert result.changed is True

    def test_예시2_대분류_교체와_태그_정리(self):
        """place_tags 정리는 A가 Remove 연산을 함께 보낸다.

        B는 자동 정리를 수행하지 않는다. (계약 2.2절)
        """
        before = UserConditions(
            place_types=["cultural_facility", "restaurant"],
            place_tags=["박물관", "카페"],
        )
        result, _ = merge(
            before,
            [
                {
                    "op": "Update",
                    "field": "place_types",
                    "value": ["cultural_facility", "shopping"],
                },
                {"op": "Remove", "field": "place_tags", "value": ["카페"]},
            ],
        )

        assert result.conditions.place_types == ["cultural_facility", "shopping"]
        assert result.conditions.place_tags == ["박물관"]
        assert result.changed is True
        assert len(result.change_logs) == 2

    def test_예시3_조건_해제(self):
        before = UserConditions(budget="free", environment="indoor")
        result, _ = merge(
            before,
            [
                {"op": "Remove", "field": "budget"},
                {"op": "Update", "field": "environment", "value": "any"},
            ],
        )

        assert result.conditions.budget is None
        assert result.conditions.environment == "any"
        assert result.changed is True

    def test_concentration_intent_설정과_해제(self):
        before = UserConditions()
        result, _ = merge(
            before,
            [{"op": "Update", "field": "concentration_intent", "value": "SEEK"}],
        )
        assert result.conditions.concentration_intent == "SEEK"
        assert result.changed is True

        result2, _ = merge(
            result.conditions,
            [{"op": "Remove", "field": "concentration_intent"}],
        )
        assert result2.conditions.concentration_intent is None

    def test_예시4_변경_없는_재추천(self):
        """완료 기준 2번: 조건 변경 없는 재추천에서 기존 조건이 유지된다.

        MODIFY의 REJECT_ALL이 이 케이스에 해당한다.
        """
        before = UserConditions(place_types=["restaurant"], max_travel_time=15)
        result, _ = merge(before, [])

        assert result.conditions.place_types == ["restaurant"]
        assert result.conditions.max_travel_time == 15
        assert result.changed is False
        assert result.change_logs == []

    def test_예시5_허용되지_않은_연산(self):
        before = UserConditions(place_types=["restaurant"])
        result, ignored = merge(
            before, [{"op": "Add", "field": "place_types", "value": ["shopping"]}]
        )

        assert result.conditions.place_types == ["restaurant"]
        assert result.changed is False
        assert len(ignored) == 1
        assert ignored[0].reason == "unsupported_operation"


# ---------------------------------------------------------------- 연산 동작

class TestOperationBehavior:
    def test_Add는_기존_값에_누적한다(self):
        before = UserConditions(place_tags=["카페"])
        result, _ = merge(
            before, [{"op": "Add", "field": "place_tags", "value": ["박물관"]}]
        )
        assert result.conditions.place_tags == ["카페", "박물관"]

    def test_Add는_순서를_유지한다(self):
        """place_tags 언급 순서가 선호 순위이므로 순서가 의미를 갖는다.

        (int-01-recommend.md 7절 규칙 5)
        """
        before = UserConditions(place_tags=["A"])
        result, _ = merge(
            before, [{"op": "Add", "field": "place_tags", "value": ["B", "C"]}]
        )
        assert result.conditions.place_tags == ["A", "B", "C"]

    def test_Add는_중복을_건너뛴다(self):
        before = UserConditions(place_tags=["카페"])
        result, _ = merge(
            before, [{"op": "Add", "field": "place_tags", "value": ["카페", "박물관"]}]
        )
        assert result.conditions.place_tags == ["카페", "박물관"]

    def test_Update는_복수_필드_전체를_교체한다(self):
        before = UserConditions(place_types=["restaurant", "shopping"])
        result, _ = merge(
            before, [{"op": "Update", "field": "place_types", "value": ["attraction"]}]
        )
        assert result.conditions.place_types == ["attraction"]

    def test_Remove는_단일_필드를_None으로_되돌린다(self):
        before = UserConditions(budget="free")
        result, _ = merge(before, [{"op": "Remove", "field": "budget"}])
        assert result.conditions.budget is None

    def test_Remove는_value_없으면_리스트를_비운다(self):
        before = UserConditions(place_tags=["카페", "박물관"])
        result, _ = merge(before, [{"op": "Remove", "field": "place_tags"}])
        assert result.conditions.place_tags == []

    def test_Remove는_지정한_원소만_제거한다(self):
        before = UserConditions(place_tags=["카페", "박물관", "미술관"])
        result, _ = merge(
            before, [{"op": "Remove", "field": "place_tags", "value": ["카페"]}]
        )
        assert result.conditions.place_tags == ["박물관", "미술관"]

    def test_존재하지_않는_원소_제거는_오류가_아니다(self):
        before = UserConditions(place_tags=["카페"])
        result, _ = merge(
            before, [{"op": "Remove", "field": "place_tags", "value": ["없는태그"]}]
        )
        assert result.conditions.place_tags == ["카페"]
        assert result.changed is False

    def test_Keep은_아무것도_바꾸지_않는다(self):
        before = UserConditions(budget="free")
        result, _ = merge(before, [{"op": "Keep", "field": "budget"}])

        assert result.conditions.budget == "free"
        assert result.changed is False
        assert len(result.change_logs) == 1  # 기록에는 남는다


# ---------------------------------------------------------------- 순서

class TestOrdering:
    def test_같은_필드에_여러_연산이_오면_마지막이_이긴다(self):
        before = UserConditions(budget=None)
        result, _ = merge(
            before,
            [
                {"op": "Update", "field": "budget", "value": "free"},
                {"op": "Update", "field": "budget", "value": "10000"},
            ],
        )
        assert result.conditions.budget == "10000"
        assert len(result.change_logs) == 2

    def test_Add_후_Remove가_순서대로_적용된다(self):
        before = UserConditions(place_tags=["카페"])
        result, _ = merge(
            before,
            [
                {"op": "Add", "field": "place_tags", "value": ["박물관"]},
                {"op": "Remove", "field": "place_tags", "value": ["카페"]},
            ],
        )
        assert result.conditions.place_tags == ["박물관"]


# ---------------------------------------------------------------- 변경 판정

class TestChangeDetection:
    """계약 2.7절: 전후 스냅샷을 전체 비교하여 판정한다."""

    def test_같은_값으로_Update하면_변경이_아니다(self):
        before = UserConditions(max_travel_time=15)
        result, _ = merge(
            before, [{"op": "Update", "field": "max_travel_time", "value": 15}]
        )
        assert result.changed is False
        assert len(result.change_logs) == 1  # 기록은 남는다

    def test_왕복_연산은_변경이_아니다(self):
        """개별 연산은 각각 변경이지만 결과는 원래대로 돌아온다.

        연산별 판정이 아니라 전후 비교여야 잡을 수 있다.
        """
        before = UserConditions(place_tags=["카페"])
        result, _ = merge(
            before,
            [
                {"op": "Add", "field": "place_tags", "value": ["박물관"]},
                {"op": "Remove", "field": "place_tags", "value": ["박물관"]},
            ],
        )
        assert result.conditions.place_tags == ["카페"]
        assert result.changed is False

    def test_전부_무효면_변경이_아니다(self):
        before = UserConditions(budget="free")
        result, ignored = merge(
            before,
            [
                {"op": "Update", "field": "price", "value": 1},
                {"op": "Add", "field": "place_types", "value": ["shopping"]},
            ],
        )
        assert result.changed is False
        assert len(ignored) == 2


# ---------------------------------------------------------------- reset

class TestReset:
    """계약 5.5절 초기화 범위."""

    def test_soft는_조건을_전부_비운다(self):
        before = UserConditions(
            place_types=["restaurant"], budget="free", companion="parent"
        )
        result, _ = merge(before, [], reset="soft")

        assert result.conditions == UserConditions()
        assert result.reset_applied == "soft"
        assert result.changed is True

    def test_full도_조건을_비운다(self):
        before = UserConditions(budget="free")
        result, _ = merge(before, [], reset="full")
        assert result.conditions == UserConditions()

    def test_history는_조건을_유지한다(self):
        """history는 추천 이력만 비우고 조건은 그대로 둔다."""
        before = UserConditions(place_types=["restaurant"], budget="free")
        result, _ = merge(before, [], reset="history")

        assert result.conditions.place_types == ["restaurant"]
        assert result.conditions.budget == "free"
        assert result.reset_applied == "history"
        assert result.changed is False

    def test_reset이_operations보다_먼저_적용된다(self):
        """순서가 반대면 방금 적용한 조건이 지워진다. (계약 2.4절)"""
        before = UserConditions(
            place_types=["restaurant"], budget="free", companion="parent"
        )
        result, _ = merge(
            before,
            [{"op": "Update", "field": "place_types", "value": ["shopping"]}],
            reset="soft",
        )

        assert result.conditions.place_types == ["shopping"]
        assert result.conditions.budget is None
        assert result.conditions.companion is None

    def test_reset_기록이_seq_0으로_남는다(self):
        before = UserConditions(budget="free")
        result, _ = merge(
            before,
            [{"op": "Update", "field": "environment", "value": "indoor"}],
            reset="soft",
        )

        assert [(log.seq, log.op) for log in result.change_logs] == [
            (0, "Reset"),
            (1, "Update"),
        ]
        assert result.change_logs[0].reset_scope == "soft"
        assert result.change_logs[0].field is None

    def test_reset이_None이면_아무_일도_없다(self):
        before = UserConditions(budget="free")
        result, _ = merge(before, [], reset=None)

        assert result.conditions.budget == "free"
        assert result.reset_applied is None


# ---------------------------------------------------------------- 기록

class TestChangeLog:
    def test_전후_값이_분리되어_기록된다(self):
        """리스트를 복사하지 않으면 before_value가 after와 같아진다."""
        before = UserConditions(place_tags=["카페"])
        result, _ = merge(
            before, [{"op": "Add", "field": "place_tags", "value": ["박물관"]}]
        )
        log = result.change_logs[0]

        assert log.before_value == ["카페"]
        assert log.after_value == ["카페", "박물관"]

    def test_seq가_0부터_순차_증가한다(self):
        before = UserConditions()
        result, _ = merge(
            before,
            [
                {"op": "Update", "field": "budget", "value": "free"},
                {"op": "Update", "field": "companion", "value": "parent"},
                {"op": "Add", "field": "place_tags", "value": ["카페"]},
            ],
        )
        assert [log.seq for log in result.change_logs] == [0, 1, 2]

    def test_무효한_연산은_기록되지_않는다(self):
        before = UserConditions()
        result, _ = merge(
            before,
            [
                {"op": "Update", "field": "budget", "value": "free"},
                {"op": "Update", "field": "price", "value": 1},
            ],
        )
        assert len(result.change_logs) == 1
        assert result.change_logs[0].field == "budget"

    def test_기록에_session과_run이_담긴다(self):
        result, _ = merge(
            UserConditions(), [{"op": "Update", "field": "budget", "value": "free"}]
        )
        log = result.change_logs[0]

        assert log.session_id == SESSION_ID
        assert log.run_id == RUN_ID
        assert log.applied_at.tzinfo is not None


# ---------------------------------------------------------------- 불변성

class TestImmutability:
    def test_원본_조건이_변형되지_않는다(self):
        """병합이 도중에 실패해도 State가 절반만 바뀌지 않도록 보장한다."""
        before = UserConditions(place_tags=["카페"], budget="free")
        merge(
            before,
            [
                {"op": "Add", "field": "place_tags", "value": ["박물관"]},
                {"op": "Remove", "field": "budget"},
            ],
        )

        assert before.place_tags == ["카페"]
        assert before.budget == "free"

    def test_반환된_조건은_원본과_다른_객체다(self):
        before = UserConditions()
        result, _ = merge(before, [])
        assert result.conditions is not before