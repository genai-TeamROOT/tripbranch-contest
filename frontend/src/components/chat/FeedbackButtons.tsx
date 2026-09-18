/*
 * 역할: 추천/일정 결과 카드에 붙는 좋아요·싫어요 피드백 버튼.
 * 입력: 피드백을 연결할 session_id/run_id, 찾을 수 있으면 이 턴의 질문·답변
 *   원문·intent.
 * 출력: POST /api/feedback 호출, 선택 상태 표시.
 * 호출 시점: RecommendationResultMessage/ScheduleResultMessage가 결과 카드 하단에 렌더링한다.
 * 참고: roadmap #14. 값은 한 번 기록하면 그대로 두는 append-only 저장이라(B-01 경계
 * 원칙과 별개로 rating 자체는 고정 vocabulary), 버튼을 다시 눌러 바꾸면 새 레코드가
 * 하나 더 쌓인다 — 최신 레코드가 사실상 최종 선택으로 취급된다.
 *
 * 싫어요는 개선 가능한 표준 사유 하나를 선택해 기록한다. 자유 입력은 모든 사유에
 * 선택적으로 덧붙일 수 있어, 집계 가능한 값과 정성 의견을 함께 남길 수 있다.
 */

import { ThumbsDown, ThumbsUp } from "lucide-react";
import { useState } from "react";
import { sendFeedback } from "../../api/feedback";
import type { FeedbackReasonCode, RecordFeedbackRequest } from "../../types";

interface FeedbackButtonsProps {
  sessionId: string;
  runId: string;
  intent?: string;
  userInput?: string;
  assistantMessage?: string;
}

type Rating = RecordFeedbackRequest["rating"];

const COMMENT_MAX_LENGTH = 500;

const DISLIKE_REASONS: ReadonlyArray<{ code: FeedbackReasonCode; label: string }> = [
  { code: "intent_mismatch", label: "요청한 기능과 다른 답변이에요" },
  { code: "clarification_unhelpful", label: "되묻기나 선택지가 상황에 맞지 않아요" },
  { code: "context_not_preserved", label: "앞에서 말한 조건·맥락이 반영되지 않았어요" },
  { code: "location_misunderstood", label: "현재 위치나 장소를 잘못 이해했어요" },
  { code: "conditions_not_applied", label: "요청한 조건이 추천에 반영되지 않았어요" },
  { code: "recommendation_not_suitable", label: "추천 장소가 제 취향이나 목적에 맞지 않아요" },
  { code: "other", label: "기타" },
];

export function FeedbackButtons({
  sessionId,
  runId,
  intent,
  userInput,
  assistantMessage,
}: FeedbackButtonsProps) {
  const [selected, setSelected] = useState<Rating | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isReasonOpen, setIsReasonOpen] = useState(false);
  const [selectedReason, setSelectedReason] = useState<FeedbackReasonCode | null>(null);
  const [comment, setComment] = useState("");

  // 카드가 재구성 흐름(dead APPEND_RECOMMENDATIONS 등)에서 run_id 없이 만들어졌다면
  // 어느 턴의 피드백인지 연결할 수 없으니 버튼 자체를 숨긴다.
  if (!sessionId || !runId) return null;

  async function submit(rating: Rating, reasonCode?: FeedbackReasonCode, commentText?: string) {
    if (isSubmitting) return;
    setIsSubmitting(true);
    setError(null);
    try {
      const trimmed = commentText?.trim();
      await sendFeedback({
        session_id: sessionId,
        run_id: runId,
        rating,
        ...(intent ? { intent } : {}),
        ...(userInput ? { user_input: userInput } : {}),
        ...(assistantMessage ? { assistant_message: assistantMessage } : {}),
        ...(reasonCode ? { reason_code: reasonCode } : {}),
        ...(trimmed ? { comment: trimmed } : {}),
      });
      setSelected(rating);
      setIsReasonOpen(false);
      setSelectedReason(null);
      setComment("");
    } catch {
      setError("피드백 전송에 실패했어요. 다시 시도해주세요.");
    } finally {
      setIsSubmitting(false);
    }
  }

  function handleLikeClick() {
    setIsReasonOpen(false);
    setSelectedReason(null);
    setComment("");
    void submit("like");
  }

  function handleDislikeClick() {
    // 이미 좋아요를 눌렀어도 싫어요로 번복할 수 있어야 한다 — 같은 rating을
    // 다시 누르면 사유 선택 패널만 닫는다.
    if (isReasonOpen) {
      setIsReasonOpen(false);
      setSelectedReason(null);
      setComment("");
      return;
    }
    setComment("");
    setSelectedReason(null);
    setIsReasonOpen(true);
  }

  function handleReasonSelect(reasonCode: FeedbackReasonCode) {
    setSelectedReason(reasonCode);
  }

  return (
    <div className="flex flex-col items-start gap-1.5">
      <div className="flex items-center gap-2">
        <button
          type="button"
          disabled={isSubmitting}
          aria-pressed={selected === "like"}
          aria-label="좋아요"
          title="좋아요"
          onClick={handleLikeClick}
          className={`flex h-7 w-7 items-center justify-center transition-colors disabled:opacity-50 ${
            selected === "like" ? "text-brand" : "text-muted hover:text-brand"
          }`}
        >
          <ThumbsUp size={16} />
        </button>
        <button
          type="button"
          disabled={isSubmitting}
          aria-pressed={selected === "dislike"}
          aria-expanded={isReasonOpen}
          aria-label="별로예요"
          title="별로예요"
          onClick={handleDislikeClick}
          className={`flex h-7 w-7 items-center justify-center transition-colors disabled:opacity-50 ${
            selected === "dislike" || isReasonOpen ? "text-rust" : "text-muted hover:text-rust"
          }`}
        >
          <ThumbsDown size={16} />
        </button>
      </div>

      {isReasonOpen && (
        <div className="flex w-72 flex-col gap-2 rounded-2xl bg-white p-3 shadow-card">
          <p className="text-sm font-bold text-ink">어떤 점이 아쉬웠나요?</p>
          <div className="grid gap-1">
            {DISLIKE_REASONS.map((reason) => (
              <button
                key={reason.code}
                type="button"
                disabled={isSubmitting}
                onClick={() => handleReasonSelect(reason.code)}
                className={`rounded-lg px-2 py-1.5 text-left text-sm transition-colors disabled:opacity-50 ${
                  selectedReason === reason.code
                    ? "bg-brand text-white"
                    : "bg-chip text-ink hover:bg-sky-light"
                }`}
              >
                {reason.label}
              </button>
            ))}
          </div>

          {selectedReason && (
            <>
              <textarea
                autoFocus
                value={comment}
                onChange={(event) => setComment(event.target.value.slice(0, COMMENT_MAX_LENGTH))}
                disabled={isSubmitting}
                placeholder="추가 의견이 있다면 알려주세요. (선택)"
                rows={2}
                className="w-full resize-none rounded-lg border border-border bg-transparent p-1.5 text-sm text-ink placeholder:text-muted focus:outline-none disabled:opacity-50"
              />
              <div className="flex justify-end gap-1.5">
                <button
                  type="button"
                  disabled={isSubmitting}
                  onClick={handleDislikeClick}
                  className="rounded-lg px-2 py-1 text-sm text-muted hover:text-ink disabled:opacity-50"
                >
                  취소
                </button>
                <button
                  type="button"
                  disabled={isSubmitting}
                  onClick={() => void submit("dislike", selectedReason, comment)}
                  className="rounded-full bg-brand px-3 py-1 text-sm font-semibold text-white transition-colors hover:bg-brand-deep disabled:opacity-50"
                >
                  제출
                </button>
              </div>
            </>
          )}
        </div>
      )}

      {error && <span className="text-xs text-rust">{error}</span>}
    </div>
  );
}
