"""응답 피드백(좋아요/싫어요) 기록 시나리오.

roadmap.md 14번. test_trace.py와 같은 구조를 따르되, rating이 자유 문자열이
아니라 검증된 값이라는 점(TestInvalidRating)만 다르다.
"""

from datetime import timedelta

import pytest
from pydantic import ValidationError

from app.state import feedback as feedback_module
from app.state import service as svc
from app.state import trace as trace_module
from app.state.schema import FeedbackRecord, now_kst
from app.state.store import InMemoryStateStore


@pytest.fixture
def store() -> InMemoryStateStore:
    return InMemoryStateStore()


def record_feedback(store, **kwargs) -> svc.RecordFeedbackResponse:
    """피드백 기록 호출. 테스트 편의용 헬퍼."""
    kwargs.setdefault("session_id", "sess_test")
    kwargs.setdefault("run_id", "run_test")
    kwargs.setdefault("rating", "like")
    return svc.record_feedback(svc.RecordFeedbackRequest(**kwargs), store=store)


class TestRecordFeedback:
    def test_기록_시각을_반환한다(self, store):
        response = record_feedback(store)
        assert response.recorded_at is not None

    def test_전달한_값이_그대로_저장된다(self, store):
        record_feedback(store, run_id="run_abc", rating="dislike")

        [saved] = feedback_module.get_feedback(store, "sess_test")
        assert saved.session_id == "sess_test"
        assert saved.run_id == "run_abc"
        assert saved.rating == "dislike"

    def test_발화_응답_intent도_함께_저장된다(self, store):
        record_feedback(
            store,
            rating="dislike",
            intent="INFO",
            user_input="경복궁 지금 사람 많아?",
            assistant_message="북촌한옥마을 기준으로는 보통이에요.",
        )

        [saved] = feedback_module.get_feedback(store, "sess_test")
        assert saved.intent == "INFO"
        assert saved.user_input == "경복궁 지금 사람 많아?"
        assert saved.assistant_message == "북촌한옥마을 기준으로는 보통이에요."

    def test_like도_저장된다(self, store):
        record_feedback(store, rating="like")

        [saved] = feedback_module.get_feedback(store, "sess_test")
        assert saved.rating == "like"


class TestFeedbackTurnText:
    """user_input/assistant_message는 선택 필드다 — 피드백을 남긴 턴에 한해서만
    질문·답변 원문을 함께 저장한다(대화 전체 로그와는 다르다)."""

    def test_원문을_함께_보내면_그대로_저장된다(self, store):
        record_feedback(
            store,
            rating="dislike",
            user_input="경복궁 근처 카페 추천해줘",
            assistant_message="이런 곳들을 찾아봤어요.",
        )

        [saved] = feedback_module.get_feedback(store, "sess_test")
        assert saved.user_input == "경복궁 근처 카페 추천해줘"
        assert saved.assistant_message == "이런 곳들을 찾아봤어요."

    def test_원문을_안_보내면_None으로_저장된다(self, store):
        record_feedback(store, rating="like")

        [saved] = feedback_module.get_feedback(store, "sess_test")
        assert saved.user_input is None
        assert saved.assistant_message is None


class TestFeedbackIntentComment:
    """intent(분류 결과 복사)/comment(싫어요 사유 자유 텍스트)도 선택 필드다."""

    def test_intent와_comment를_함께_보내면_그대로_저장된다(self, store):
        record_feedback(
            store,
            rating="dislike",
            intent="RECOMMEND",
            comment="추천 장소가 너무 멀어요",
        )

        [saved] = feedback_module.get_feedback(store, "sess_test")
        assert saved.intent == "RECOMMEND"
        assert saved.comment == "추천 장소가 너무 멀어요"

    def test_안_보내면_None으로_저장된다(self, store):
        record_feedback(store, rating="like")

        [saved] = feedback_module.get_feedback(store, "sess_test")
        assert saved.intent is None
        assert saved.comment is None

class TestComment:
    """싫어요 표준 사유와 선택적 자유 의견."""

    def test_reason_code를_전달하면_그대로_저장된다(self, store):
        record_feedback(
            store,
            rating="dislike",
            reason_code="context_not_preserved",
        )

        [saved] = feedback_module.get_feedback(store, "sess_test")
        assert saved.reason_code == "context_not_preserved"

    def test_comment을_전달하면_그대로_저장된다(self, store):
        record_feedback(store, rating="dislike", comment="장소가 너무 멀어요")

        [saved] = feedback_module.get_feedback(store, "sess_test")
        assert saved.comment == "장소가 너무 멀어요"

    def test_comment을_생략하면_None이다(self, store):
        record_feedback(store, rating="dislike")

        [saved] = feedback_module.get_feedback(store, "sess_test")
        assert saved.comment is None

    def test_다른_표준_사유에도_comment를_함께_남길_수_있다(self, store):
        record_feedback(
            store,
            rating="dislike",
            reason_code="location_misunderstood",
            comment="기기 위치가 아닌 검색 중심으로 안내됐어요",
        )

        [saved] = feedback_module.get_feedback(store, "sess_test")
        assert saved.reason_code == "location_misunderstood"
        assert saved.comment == "기기 위치가 아닌 검색 중심으로 안내됐어요"

    def test_500자를_넘으면_거부된다(self):
        with pytest.raises(ValidationError):
            svc.RecordFeedbackRequest(
                session_id="sess_test",
                run_id="run_test",
                rating="dislike",
                comment="x" * 501,
            )


class TestInvalidRating:
    """RecordTraceRequest.step과 달리 rating은 B가 검증하는 예외적인 필드다."""

    def test_잘못된_rating은_거부된다(self):
        with pytest.raises(ValidationError):
            svc.RecordFeedbackRequest(
                session_id="sess_test", run_id="run_test", rating="neutral"
            )

    def test_정해지지_않은_reason_code는_거부된다(self):
        with pytest.raises(ValidationError):
            svc.RecordFeedbackRequest(
                session_id="sess_test",
                run_id="run_test",
                rating="dislike",
                reason_code="operating_hours_wrong",
            )

    def test_좋아요에는_reason_code나_comment를_남길_수_없다(self):
        with pytest.raises(ValidationError):
            svc.RecordFeedbackRequest(
                session_id="sess_test",
                run_id="run_test",
                rating="like",
                reason_code="other",
            )


class TestAppendOnly:
    def test_같은_세션에서_여러_건이_순서대로_쌓인다(self, store):
        record_feedback(store, run_id="run_1", rating="like")
        record_feedback(store, run_id="run_2", rating="dislike")

        saved = feedback_module.get_feedback(store, "sess_test")
        assert [f.rating for f in saved] == ["like", "dislike"]

    def test_기존_기록을_지우는_메서드가_없다(self, store):
        assert not hasattr(store, "delete_feedback")


class TestSessionIsolation:
    def test_다른_세션의_기록은_섞이지_않는다(self, store):
        record_feedback(store, session_id="sess_a", rating="like")
        record_feedback(store, session_id="sess_b", rating="dislike")

        assert [f.rating for f in feedback_module.get_feedback(store, "sess_a")] == [
            "like"
        ]
        assert [f.rating for f in feedback_module.get_feedback(store, "sess_b")] == [
            "dislike"
        ]

    def test_기록이_없는_세션은_빈_목록을_반환한다(self, store):
        assert feedback_module.get_feedback(store, "sess_never_used") == []


class TestListDislikes:
    """list_dislikes()는 세션 범위가 아니라 테이블 전체를 대상으로 한다."""

    def test_다른_세션에_흩어진_dislike도_모두_모은다(self, store):
        record_feedback(store, session_id="sess_a", rating="dislike")
        record_feedback(store, session_id="sess_b", rating="dislike")
        record_feedback(store, session_id="sess_c", rating="like")

        dislikes = feedback_module.list_dislikes(store)

        assert len(dislikes) == 2
        assert {d.session_id for d in dislikes} == {"sess_a", "sess_b"}

    def test_최근순으로_정렬된다(self, store):
        """시각차를 명시적으로 줘서 실행 속도에 좌우되지 않게 한다."""
        now = now_kst()
        store.append_feedback(
            [
                FeedbackRecord(
                    session_id="sess_older",
                    run_id="run_1",
                    rating="dislike",
                    recorded_at=now - timedelta(minutes=10),
                ),
                FeedbackRecord(
                    session_id="sess_newer",
                    run_id="run_2",
                    rating="dislike",
                    recorded_at=now,
                ),
            ]
        )

        dislikes = feedback_module.list_dislikes(store)

        assert [d.session_id for d in dislikes] == ["sess_newer", "sess_older"]

    def test_limit을_넘는_건_잘린다(self, store):
        for i in range(5):
            record_feedback(store, session_id=f"sess_{i}", run_id=f"run_{i}", rating="dislike")

        dislikes = feedback_module.list_dislikes(store, limit=3)

        assert len(dislikes) == 3

    def test_dislike가_없으면_빈_목록이다(self, store):
        record_feedback(store, rating="like")
        assert feedback_module.list_dislikes(store) == []


class TestGetDislikeFeedbackWithTrace:
    """svc.get_dislike_feedback()가 run_id로 trace 정보를 조인하는지 확인한다."""

    def test_같은_run_id의_trace에서_버전_정보를_채운다(self, store):
        record_feedback(store, session_id="sess_a", run_id="run_1", rating="dislike")
        trace_module.record(
            store,
            "sess_a",
            "run_1",
            "llm_interpret",
            prompt_version="intent_v1.2",
        )
        trace_module.record(
            store,
            "sess_a",
            "run_1",
            "scoring",
            scoring_version="score_v0.3",
        )

        response = svc.get_dislike_feedback(store=store)

        assert len(response.items) == 1
        item = response.items[0]
        assert item.session_id == "sess_a"
        assert item.run_id == "run_1"
        assert item.prompt_version == "intent_v1.2"
        assert item.scoring_version == "score_v0.3"

    def test_싫어요_조회에_사유와_의견도_포함된다(self, store):
        record_feedback(
            store,
            session_id="sess_a",
            run_id="run_1",
            rating="dislike",
            reason_code="clarification_unhelpful",
            comment="이미 위치를 말했어요",
        )

        response = svc.get_dislike_feedback(store=store)

        assert response.items[0].reason_code == "clarification_unhelpful"
        assert response.items[0].comment == "이미 위치를 말했어요"

    def test_싫어요_조회에_발화와_응답_문맥도_포함된다(self, store):
        record_feedback(
            store,
            session_id="sess_a",
            run_id="run_1",
            rating="dislike",
            intent="RECOMMEND",
            user_input="비 피할 실내 장소 추천해줘",
            assistant_message="조건에 맞는 곳을 찾지 못했어요.",
        )

        response = svc.get_dislike_feedback(store=store)

        assert response.items[0].intent == "RECOMMEND"
        assert response.items[0].user_input == "비 피할 실내 장소 추천해줘"
        assert response.items[0].assistant_message == "조건에 맞는 곳을 찾지 못했어요."

    def test_trace_기록이_없으면_버전_정보는_None이다(self, store):
        record_feedback(store, session_id="sess_a", run_id="run_1", rating="dislike")

        response = svc.get_dislike_feedback(store=store)

        assert response.items[0].prompt_version is None
        assert response.items[0].scoring_version is None

    def test_다른_run_id의_trace는_섞이지_않는다(self, store):
        """같은 세션 안에 여러 run이 있어도 feedback의 run_id와 일치하는
        trace만 골라 써야 한다 — 세션 단위로 뭉뚱그리면 다른 턴의 버전
        정보가 잘못 채워질 수 있다."""
        record_feedback(store, session_id="sess_a", run_id="run_2", rating="dislike")
        trace_module.record(
            store, "sess_a", "run_1", "llm_interpret", prompt_version="v1"
        )
        trace_module.record(
            store, "sess_a", "run_2", "llm_interpret", prompt_version="v2"
        )

        response = svc.get_dislike_feedback(store=store)

        assert response.items[0].prompt_version == "v2"

    def test_like는_포함되지_않는다(self, store):
        record_feedback(store, rating="like")
        response = svc.get_dislike_feedback(store=store)
        assert response.items == []

    def test_원문도_함께_노출된다(self, store):
        record_feedback(
            store,
            session_id="sess_a",
            run_id="run_1",
            rating="dislike",
            user_input="비 피할 곳 있어?",
            assistant_message="근처 실내 장소를 찾아봤어요.",
        )

        response = svc.get_dislike_feedback(store=store)

        assert response.items[0].user_input == "비 피할 곳 있어?"
        assert response.items[0].assistant_message == "근처 실내 장소를 찾아봤어요."

    def test_intent와_comment도_함께_노출된다(self, store):
        record_feedback(
            store,
            session_id="sess_a",
            run_id="run_1",
            rating="dislike",
            intent="RECOMMEND",
            comment="추천 장소가 너무 멀어요",
        )

        response = svc.get_dislike_feedback(store=store)

        assert response.items[0].intent == "RECOMMEND"
        assert response.items[0].comment == "추천 장소가 너무 멀어요"


class TestFeedbackStats:
    """svc.get_feedback_stats()의 집계 결과를 확인한다. (TP-146)"""

    def test_비어있으면_전부_0이다(self, store):
        response = svc.get_feedback_stats(store=store)

        assert response.total == 0
        assert response.rating_counts == {"like": 0, "dislike": 0}
        assert set(response.reason_code_counts) == {
            "intent_mismatch",
            "clarification_unhelpful",
            "context_not_preserved",
            "location_misunderstood",
            "conditions_not_applied",
            "recommendation_not_suitable",
            "other",
            "unclassified",
        }
        assert all(count == 0 for count in response.reason_code_counts.values())
        assert response.top_intents == []
        assert response.other_intent_count == 0
        assert response.missing_intent_count == 0

    def test_rating별_건수를_센다(self, store):
        record_feedback(store, session_id="sess_a", rating="like")
        record_feedback(store, session_id="sess_b", rating="like")
        record_feedback(store, session_id="sess_c", rating="dislike")

        response = svc.get_feedback_stats(store=store)

        assert response.total == 3
        assert response.rating_counts == {"like": 2, "dislike": 1}

    def test_reason_code는_dislike만_센다(self, store):
        record_feedback(
            store, session_id="sess_a", rating="dislike", reason_code="other"
        )
        record_feedback(store, session_id="sess_b", rating="like")

        response = svc.get_feedback_stats(store=store)

        assert response.reason_code_counts["other"] == 1
        assert sum(response.reason_code_counts.values()) == 1

    def test_reason_code가_없는_dislike는_unclassified로_잡힌다(self, store):
        record_feedback(store, session_id="sess_a", rating="dislike")

        response = svc.get_feedback_stats(store=store)

        assert response.reason_code_counts["unclassified"] == 1

    def test_intent_상위_N개만_담고_나머지는_기타로_합친다(self, store):
        for i in range(3):
            record_feedback(
                store, session_id=f"sess_top_{i}", intent="RECOMMEND", rating="like"
            )
        for i in range(2):
            record_feedback(
                store, session_id=f"sess_mid_{i}", intent="INFO", rating="like"
            )
        record_feedback(store, session_id="sess_tail", intent="SMALLTALK", rating="like")

        response = svc.get_feedback_stats(store=store, top_intents=2)

        assert [item.intent for item in response.top_intents] == ["RECOMMEND", "INFO"]
        assert [item.count for item in response.top_intents] == [3, 2]
        assert response.other_intent_count == 1

    def test_intent가_없는_건은_missing으로_따로_센다(self, store):
        record_feedback(store, session_id="sess_a", rating="like")
        record_feedback(store, session_id="sess_b", intent="INFO", rating="like")

        response = svc.get_feedback_stats(store=store)

        assert response.missing_intent_count == 1
        assert response.other_intent_count == 0

    def test_since까지만_필터하면_그_전_기록은_빠진다(self, store):
        now = now_kst()
        store.append_feedback(
            [
                FeedbackRecord(
                    session_id="sess_old",
                    run_id="run_1",
                    rating="like",
                    recorded_at=now - timedelta(days=10),
                ),
                FeedbackRecord(
                    session_id="sess_new",
                    run_id="run_2",
                    rating="like",
                    recorded_at=now,
                ),
            ]
        )

        response = svc.get_feedback_stats(store=store, since=now - timedelta(days=1))

        assert response.total == 1

    def test_until은_그_시각_이전까지만_포함한다(self, store):
        now = now_kst()
        store.append_feedback(
            [
                FeedbackRecord(
                    session_id="sess_before",
                    run_id="run_1",
                    rating="like",
                    recorded_at=now - timedelta(days=1),
                ),
                FeedbackRecord(
                    session_id="sess_after",
                    run_id="run_2",
                    rating="like",
                    recorded_at=now + timedelta(days=1),
                ),
            ]
        )

        response = svc.get_feedback_stats(store=store, until=now)

        assert response.total == 1

    def test_since와_echo가_응답에도_그대로_담긴다(self, store):
        since = now_kst() - timedelta(days=7)
        until = now_kst()

        response = svc.get_feedback_stats(store=store, since=since, until=until)

        assert response.since == since
        assert response.until == until
