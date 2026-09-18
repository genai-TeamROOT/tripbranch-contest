/*
 * 역할: 채팅 메시지 배열을 시간순으로 렌더링한다.
 * 입력: ChatMessage 배열, 디버그 노출 여부, 추천 관련 콜백.
 * 출력: 사용자/assistant/디버그/추천 결과 메시지 UI.
 * 호출 시점: ChatPage가 대화 본문을 그릴 때 호출된다.
 * TODO: 메시지 타입이 늘어나면 타입별 렌더러를 별도 파일로 분리한다.
 *
 * isDeveloperView는 /dev-chat에서만 true다(DeveloperChatPage). Intent 배지뿐
 * 아니라 추천 결과의 지연시간(elapsed_ms/server_elapsed_ms) 노출도 이 플래그로
 * 통일한다 — 실사용자 화면(ChatPage/HomePage)에는 내부 지연시간 숫자를 보여줄
 * 이유가 없다(개발자용 정보가 실서비스 화면에 새던 문제를 정리함).
 */

import { CircleAlert, Sparkles } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import type { AgentProgressEvent, ChatMessage, Language, TravelOriginToggle } from "../../types";
import { AgentProgressMessage } from "./AgentProgressMessage";
import { ClarificationMessage } from "./ClarificationMessage";
import { CompareResultCards } from "./CompareResultCards";
import { LocationRefreshMessage } from "./LocationRefreshMessage";
import { ConditionDebugMessage } from "./ConditionDebugMessage";
import { FeedbackButtons } from "./FeedbackButtons";
import { PlaceInfoCard } from "./PlaceInfoCard";
import { PhotoSimilarResultMessage } from "./PhotoSimilarResultMessage";
import { PastRecommendationMessage } from "./PastRecommendationMessage";
import { RecommendationActionsMessage } from "./RecommendationActionsMessage";
import { RecommendationResultMessage } from "./RecommendationResultMessage";
import { PreferenceTagSummaryTable } from "./PreferenceTagSummaryTable";
import { ScheduleActionsMessage } from "./ScheduleActionsMessage";
import { ScheduleResultMessage } from "./ScheduleResultMessage";
import { SessionStatusMessage } from "./SessionStatusMessage";
import { SuggestedFollowUps } from "./SuggestedFollowUps";
import { findTurnText } from "../../utils/turnText";

function StreamingDots({ language }: { language: Language }) {
  const loadingLabel = language === "en" ? "Generating a response" : "답변 생성 중";
  return (
    <span className="flex h-6 items-center gap-1.5" aria-label={loadingLabel} role="status">
      {[0, 1, 2].map((index) => (
        <span
          key={index}
          aria-hidden="true"
          className="h-2 w-2 animate-bounce rounded-full bg-brand/60"
          style={{ animationDelay: `${index * 150}ms`, animationDuration: "900ms" }}
        />
      ))}
      <span className="sr-only">{loadingLabel}</span>
    </span>
  );
}

// 줄 안의 인라인 마크다운. 지금은 **강조**만 처리한다 — 백엔드 답변 생성
// 프롬프트가 실제로 쓰는 문법이 이것뿐이라, 더 넓은 문법(링크·이탤릭 등)은
// 필요해지면 그때 추가한다.
function renderInline(text: string): ReactNode {
  const segments = text.split(/(\*\*[^*]+\*\*)/g).filter((segment) => segment !== "");
  return segments.map((segment, index) =>
    segment.startsWith("**") && segment.endsWith("**") ? (
      <strong key={index}>{segment.slice(2, -2)}</strong>
    ) : (
      <span key={index}>{segment}</span>
    ),
  );
}

// "- "/"* "/"• " 셋 다 불릿으로 본다 — 같은 답변 안에서도 모델이 섞어 쓴다.
const BULLET_PREFIXES = ["- ", "* ", "• "];

// "#"/"##"/"###"... 몇 개든 제목으로 본다 — 모델이 h1~h3를 섞어 쓴다. 1~2단계는
// 크게, 3단계부터는 한 크기로 묶는다(더 잘게 나눠봐야 챗 버블 안에서 구분이 안 감).
const HEADING_PATTERN = /^(#{1,6})\s+(.*)$/;

function MarkdownText({ text }: { text: string }) {
  const lines = text.split("\n");
  const elements: ReactNode[] = [];

  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index];
    // 불릿 기호만 있고 내용은 다음 줄에 오는 경우(모델이 가끔 이렇게 끊어 보냄) —
    // 빈 불릿을 그리지 않고 건너뛴다.
    if (!line || line.trim() === "•" || line.trim() === "*") continue;
    const headingMatch = HEADING_PATTERN.exec(line);
    if (headingMatch) {
      const level = headingMatch[1].length;
      const content = renderInline(headingMatch[2]);
      elements.push(
        level === 1 ? (
          <h2 key={`heading-${index}`} className="text-xl font-bold text-ink">
            {content}
          </h2>
        ) : (
          <h3 key={`heading-${index}`} className="mt-3 text-lg font-bold text-ink">
            {content}
          </h3>
        ),
      );
      continue;
    }
    const bulletPrefix = BULLET_PREFIXES.find((prefix) => line.startsWith(prefix));
    if (bulletPrefix) {
      elements.push(
        <p key={`item-${index}`} className="flex gap-2 text-sm">
          <span aria-hidden="true">•</span>
          <span>{renderInline(line.slice(bulletPrefix.length))}</span>
        </p>,
      );
      continue;
    }
    elements.push(
      <p key={`paragraph-${index}`} className="leading-relaxed text-ink">
        {renderInline(line)}
      </p>,
    );
  }

  return <div className="space-y-1.5">{elements}</div>;
}

function StreamingText({ text, streaming }: { text: string; streaming: boolean }) {
  // Gemini는 단어·문장 단위 청크를 보내기도 한다. 화면에서는 청크 크기와 무관하게
  // 한 글자씩 이어 보여, 첫 텍스트가 도착한 뒤에도 생성 중이라는 감각을 유지한다.
  const [visibleText, setVisibleText] = useState(() => (streaming ? "" : text));

  useEffect(() => {
    if (!text.startsWith(visibleText)) {
      setVisibleText(streaming ? "" : text);
      return;
    }
    if (visibleText.length >= text.length) return;

    const timer = window.setInterval(() => {
      setVisibleText((current) =>
        current.length < text.length ? text.slice(0, current.length + 1) : current,
      );
    }, 18);
    return () => window.clearInterval(timer);
  }, [streaming, text, visibleText]);

  return <MarkdownText text={visibleText} />;
}

/*
 * 대화가 언제 오간 것인지 알리는 가운데 정렬 한 줄. 메신저에서 늘 보던 모양이라
 * 읽지 않아도 뜻이 통한다 — "지난 대화예요"라는 배너를 대신한다.
 *
 * 오늘·어제는 날짜 대신 그렇게 부른다. 그편이 "9월 3일"보다 빨리 읽힌다.
 */
function TimeSeparator({
  at,
  partial,
  language,
}: {
  at: string;
  partial?: boolean;
  language: Language;
}) {
  const when = new Date(at);
  if (Number.isNaN(when.getTime())) return null;

  const locale = language === "en" ? "en-US" : "ko-KR";
  const time = when.toLocaleTimeString(locale, { hour: "numeric", minute: "2-digit" });
  const startOfDay = (value: Date) =>
    new Date(value.getFullYear(), value.getMonth(), value.getDate()).getTime();
  const daysAgo = Math.round((startOfDay(new Date()) - startOfDay(when)) / 86_400_000);

  let day: string;
  if (daysAgo === 0) day = language === "en" ? "Today" : "오늘";
  else if (daysAgo === 1) day = language === "en" ? "Yesterday" : "어제";
  else day = when.toLocaleDateString(locale, { month: "long", day: "numeric" });

  /* 옛 대화는 남은 말풍선으로만 되돌아온다. 앞부분이 없다는 사실을 여기서 밝힌다. */
  const note = partial ? (language === "en" ? " · earlier part not kept" : " · 앞부분은 남아 있지 않아요") : "";

  return (
    <p className="py-1 text-center text-xs text-muted">
      {day} {time}
      {note}
    </p>
  );
}

/*
 * 실패한 턴을 알리는 가운데 정렬 한 줄(TP-245).
 *
 * TimeSeparator와 같은 모양과 색을 쓴다. 대화 흐름에 끼어드는 줄이라 붉은색은
 * 시선을 너무 끌었다 — 실패 사실만 조용히 알리고, 다음 행동은 "다시 시도"가 맡는다.
 *
 * 대신 아이콘을 하나 붙인다. 색까지 같으면 시각 구분선과 생김새가 완전히 같아져
 * 훑어볼 때 둘이 구분되지 않는다.
 */
function TurnErrorNotice({
  text,
  retryInput,
  onRetry,
  language,
}: {
  text: string;
  retryInput?: string;
  onRetry?: (input: string) => void;
  language: Language;
}) {
  return (
    <div className="flex flex-col items-center gap-1 py-1">
      {/* 이 저장소가 오류에 쓰는 role 그대로다(ErrorBanner·ChatComposer·LocationPage). */}
      <p
        role="alert"
        className="flex items-start justify-center gap-1 text-center text-xs text-muted"
      >
        {/* 문구가 이미 실패를 말하므로 낭독에서는 뺀다. 여러 줄로 접힐 때 첫 줄에
            맞도록 위로 붙인다. */}
        <CircleAlert size={12} aria-hidden="true" className="mt-0.5 shrink-0" />
        {text}
      </p>
      {retryInput && onRetry && (
        <button
          type="button"
          onClick={() => onRetry(retryInput)}
          className="rounded-full px-2 py-0.5 text-xs font-medium text-ink underline underline-offset-2 transition-colors hover:bg-chip"
        >
          {language === "en" ? "Try again" : "다시 시도"}
        </button>
      )}
    </div>
  );
}

/*
 * 추천 카드 앞에 뜨는 고정 캡션 한 줄(문구 통합, 2026-09-09). 추천 기준
 * 보조설명("거리·날씨·취향 등을 고려했어요")은 여기 없다 — "추천 장소" 가로
 * 줄 오른쪽에 붙는다(RecommendationResultMessage → PlaceCardRow의 note,
 * 2026-09-09 사용자 피드백). 카드·팁과 메시지를 갈라 둔 이유는
 * ChatMessageList.tsx의 "recommendation_caption" 분기 옆 주석과
 * agentMessages.ts의 buildRecommendationCaptionMessage 주석 참고.
 */
function RecommendationCaptionMessage({ language }: { language: Language }) {
  const summary =
    language === "en" ? "Trivi's ranked picks for you:" : "트리비가 추천하는 관광명소 순위예요:";
  return (
    <div className="flex w-full items-center gap-1.5 text-sm text-ink">
      <Sparkles size={16} className="shrink-0 text-brand" aria-hidden="true" />
      <p>{summary}</p>
    </div>
  );
}

interface ChatMessageListProps {
  messages: ChatMessage[];
  showDebug: boolean;
  isLoading: boolean;
  deviceLocation: string | null;
  isDeveloperView?: boolean;
  onRequestMore: () => void;
  onRelaxRadius: () => void;
  onSelectClarificationOption: (optionId: string, label: string) => void;
  onSelectFollowUpSuggestion: (suggestion: string) => void;
  /** 실패한 턴의 "다시 시도". 안 넘기면 버튼 자체를 그리지 않는다. */
  onRetryTurn?: (input: string) => void;
  /** 사진 검색이 위치를 몰라 멈췄을 때의 "위치 정하기". 안 넘기면 안 그린다. */
  onSetLocation?: () => void;
  onToggleTravelOrigin?: (toggle: TravelOriginToggle) => void;
  locationRefresh: {
    ageMinutes: number | null;
    onUsePrevious: () => void;
    onRefreshLocation: () => void;
  } | null;
  progress: AgentProgressEvent | null;
  language?: Language;
}

export function ChatMessageList({
  messages,
  showDebug,
  isLoading,
  deviceLocation,
  isDeveloperView = false,
  onRequestMore,
  onRelaxRadius,
  onSelectClarificationOption,
  onSelectFollowUpSuggestion,
  onRetryTurn,
  onSetLocation,
  onToggleTravelOrigin,
  locationRefresh,
  progress,
  language = "ko",
}: ChatMessageListProps) {
  return (
    /*
     * 메시지 사이 간격은 24px이다(2026-09-07, 16px에서 넓혔다).
     *
     * 말풍선을 쓰는 쪽은 사용자 발화뿐이고 어시스턴트의 말은 배경 없이 흐르므로
     * (ClarificationMessage 주석), 블록을 갈라 주는 것이 배경이 아니라 이 간격
     * 하나다. 16px일 때는 질문과 답이 한 덩어리로 붙어 읽혔다.
     *
     * 값은 계산이 아니라 16·24px을 실제로 렌더해 보고 골랐다. 한 턴 안의
     * 답변·피드백·후속 질문 사이도 같이 넓어지는데, 24px에서는 아직 한 묶음으로
     * 읽혀 흩어지지 않았다.
     */
    <div className="flex flex-1 flex-col gap-6">
      {messages
        .filter((message) => showDebug || message.type !== "condition_debug")
        .map((message, index, renderedMessages) => {
          if (message.type === "time_separator") {
            return (
              <TimeSeparator
                key={message.id}
                at={message.at}
                partial={message.partial}
                language={language}
              />
            );
          }

          if (message.type === "turn_error") {
            return (
              <TurnErrorNotice
                key={message.id}
                text={message.text}
                retryInput={message.retryInput}
                onRetry={onRetryTurn}
                language={language}
              />
            );
          }

          if (message.type === "user_text") {
            return (
              /*
               * **묻고 나서 한 번 쉰다.** 목록의 공통 간격(24px) 위에 16px을 더해
               * 사용자 발화와 그 답변 사이만 40px로 벌린다(2026-09-07).
               *
               * 나머지 사이를 다 같이 넓히는 것으로는 이 자리가 해결되지 않았다 —
               * 한 턴은 "질문 하나 + 답변·피드백·후속 질문"이라, 간격이 균일하면
               * 어디서 턴이 갈리는지가 사라져 대화가 한 줄기로 흐른다. 여기가
               * 턴의 경계이므로 여기만 더 받는다.
               *
               * 값은 32·40px을 실제로 렌더해 보고 골랐다.
               */
              <div key={message.id} className="flex justify-end pb-4">
                <p className="max-w-[80%] rounded-2xl rounded-br-md bg-brand px-4 py-2.5 text-sm text-white">
                  {message.text}
                </p>
              </div>
            );
          }

          if (message.type === "assistant_text" || message.type === "interpretation_summary") {
            return (
              // 배경·패딩 없이 본문처럼 넓게 흐른다(DESIGN_SYSTEM.md §6.3) —
              // 카드가 뒤따라 붙는 구조라 말풍선을 쓰지 않는다.
              <div key={message.id} className="flex w-full flex-col gap-2 text-sm text-ink">
                {isDeveloperView && message.type === "assistant_text" && message.intent && (
                  <div className="flex flex-wrap gap-2 text-xs">
                    <span className="rounded-full bg-ink px-2 py-0.5 font-semibold text-white">
                      Intent: {message.intent}
                    </span>
                    {message.status && (
                      <span className="rounded-full border border-border px-2 py-0.5 text-muted">
                        {message.status}
                      </span>
                    )}
                  </div>
                )}
                {message.type === "assistant_text" && message.streaming && message.text === "…" ? (
                  <StreamingDots language={language} />
                ) : (
                  <StreamingText
                    text={message.text}
                    streaming={message.type === "assistant_text" && Boolean(message.streaming)}
                  />
                )}
                {message.type === "assistant_text" && message.footnote && (
                  <p className="text-xs text-muted">{message.footnote}</p>
                )}
              </div>
            );
          }

          if (message.type === "condition_debug") {
            return (
              <ConditionDebugMessage
                key={message.id}
                userInput={message.userInput}
                conditions={message.conditions}
                mergedConditions={message.mergedConditions}
                deviceLocation={deviceLocation}
                intent={message.intent ?? null}
                status={message.status}
              />
            );
          }

          if (message.type === "session_status") {
            return (
              <SessionStatusMessage
                key={message.id}
                status={message.status}
                error={message.error}
              />
            );
          }

          if (message.type === "schedule_result") {
            return (
              <ScheduleResultMessage
                key={message.id}
                schedule={message.schedule}
                elapsedMs={message.elapsed_ms}
                showElapsedTime={isDeveloperView}
                runId={message.run_id}
                sessionId={message.session_id}
              />
            );
          }

          /* 일정 재편성 버튼. 추천과 같은 이유로 새 발화가 나가면 걷어낸다. */
          if (message.type === "schedule_actions") {
            return (
              <ScheduleActionsMessage
                key={message.id}
                hasNoSchedule={message.has_no_schedule}
                isLoading={isLoading}
                onRequestMore={onRequestMore}
                onRelaxRadius={onRelaxRadius}
              />
            );
          }

          if (message.type === "place_info_result") {
            return <PlaceInfoCard key={message.id} card={message.card} />;
          }

          if (message.type === "compare_result") {
            return (
              <CompareResultCards
                key={message.id}
                comparison={message.comparison}
                deviceLocation={deviceLocation}
              />
            );
          }

          if (message.type === "feedback") {
            // "feedback" 메시지 자체에는 텍스트가 없다 — 바로 앞의 결과 카드를
            // 지나 그 턴의 user_text/assistant_text까지 거슬러 올라가 찾는다
            // (findTurnText는 카드/피드백 등 텍스트가 없는 메시지를 건너뛰고
            // 계속 탐색하므로 이 메시지의 index를 그대로 넘겨도 된다).
            const { userInput, assistantMessage, intent } = findTurnText(renderedMessages, index);
            return (
              <div key={message.id} className="mr-auto flex w-full px-1">
                <FeedbackButtons
                  sessionId={message.sessionId}
                  runId={message.runId}
                  userInput={userInput}
                  assistantMessage={assistantMessage}
                  intent={intent}
                />
              </div>
            );
          }

          if (message.type === "clarification") {
            return (
              <ClarificationMessage
                key={message.id}
                text={message.text}
                options={message.options}
                isLoading={isLoading}
                onSelectOption={onSelectClarificationOption}
              />
            );
          }

          if (message.type === "follow_up_suggestions") {
            return (
              <SuggestedFollowUps
                key={message.id}
                suggestions={message.suggestions}
                isLoading={isLoading}
                onSelect={onSelectFollowUpSuggestion}
                language={language}
              />
            );
          }

          if (message.type === "photo_similar_result") {
            return (
              <PhotoSimilarResultMessage
                key={message.id}
                imageUrl={message.imageUrl}
                restored={message.restored}
                status={message.status}
                centerName={message.centerName}
                places={message.places}
                candidateCount={message.candidateCount}
                onSetLocation={onSetLocation}
              />
            );
          }

          if (message.type === "recommendation_caption") {
            return <RecommendationCaptionMessage key={message.id} language={language} />;
          }

          if (message.type === "past_recommendation_result") {
            return (
              <PastRecommendationMessage
                key={message.id}
                places={message.places}
                language={language}
              />
            );
          }

          /* 추천 결과에서 갈라 나온 버튼. 새 발화가 나가면 이 메시지만 걷어내지므로
             (TripContext의 isPastTurnControl) 지난 턴의 버튼은 화면에 남지 않는다. */
          if (message.type === "recommendation_actions") {
            return (
              <RecommendationActionsMessage
                key={message.id}
                hasNoResults={message.has_no_results}
                travelOriginToggle={message.travel_origin_toggle}
                isLoading={isLoading}
                onRequestMore={onRequestMore}
                onRelaxRadius={onRelaxRadius}
                onToggleTravelOrigin={onToggleTravelOrigin}
                language={language}
              />
            );
          }

          if (message.type === "preference_tag_summary") {
            return (
              <PreferenceTagSummaryTable
                key={message.id}
                items={message.items}
                language={language}
              />
            );
          }

          return (
            <RecommendationResultMessage
              key={message.id}
              recommendations={message.recommendations}
              unverifiedRecommendations={message.unverified_recommendations}
              elapsedMs={message.elapsed_ms}
              serverElapsedMs={message.server_elapsed_ms}
              showElapsedTime={isDeveloperView}
              language={language}
            />
          );
        })}
      {locationRefresh && (
        <LocationRefreshMessage
          ageMinutes={locationRefresh.ageMinutes}
          isLoading={isLoading}
          onUsePrevious={locationRefresh.onUsePrevious}
          onRefreshLocation={locationRefresh.onRefreshLocation}
        />
      )}
      {isLoading && (
        <AgentProgressMessage
          schedulePlanning={progress?.stage === "scheduling"}
          progress={progress}
          language={language}
        />
      )}
    </div>
  );
}
