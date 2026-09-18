/*
 * 역할: 한 턴이 끝난 뒤 사용자가 이어서 물을 만한 질문을 버튼으로 보여준다.
 * 입력: 제안 문구 목록, 로딩 여부, 선택 콜백.
 * 출력: assistant 쪽에 붙는 버튼 묶음.
 * 호출 시점: ChatMessageList가 follow_up_suggestions 메시지를 렌더링할 때 호출된다.
 *
 * ClarificationMessage와 생김새는 비슷하지만 동작이 다르다 — 저쪽은 버튼 id를
 * clarification_choice로 보내 Intent를 못 박고, 이쪽은 **문구를 그대로 사용자
 * 발화로 보낸다.** 그래서 여기에는 id가 없고 문자열만 있다.
 */

interface SuggestedFollowUpsProps {
  suggestions: string[];
  isLoading: boolean;
  onSelect: (suggestion: string) => void;
  language: "ko" | "en";
}

export function SuggestedFollowUps({
  suggestions,
  isLoading,
  onSelect,
  language,
}: SuggestedFollowUpsProps) {
  if (suggestions.length === 0) return null;

  const label = language === "en" ? "Suggested next questions" : "이어서 물어볼 만한 질문";

  return (
    <div className="mr-auto flex flex-col gap-1.5 px-1">
      <p className="text-[11px] font-semibold text-muted">{label}</p>
      {/* 한 줄에 하나씩 세로로 쌓는다. 문구가 한 문장이라 가로로 흘리면 길이에 따라
          두 개가 붙었다 떨어졌다 해서 줄 수가 제안 개수와 어긋난다. */}
      <div className="flex flex-col items-start gap-1.5" role="group" aria-label={label}>
        {suggestions.map((suggestion) => (
          <button
            key={suggestion}
            type="button"
            disabled={isLoading}
            onClick={() => onSelect(suggestion)}
            /* 문구가 한 문장이라 가운데 정렬하면 줄바꿈될 때 읽기 나쁘다.
               왼쪽으로 붙이고 버튼 폭은 글자만큼만 차지하게 둔다. */
            className="rounded-full border border-border bg-white px-3.5 py-2 text-left text-xs font-medium text-ink transition-colors hover:border-brand hover:text-brand disabled:opacity-50"
          >
            {suggestion}
          </button>
        ))}
      </div>
    </div>
  );
}
