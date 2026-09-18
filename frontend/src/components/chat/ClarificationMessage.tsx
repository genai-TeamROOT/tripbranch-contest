/*
 * 역할: 인텐트가 모호할 때 되묻기 문구 + 버튼을 채팅 메시지로 보여준다.
 * 입력: 되묻기 문구, 버튼 목록(ClarificationOption[]), 선택 콜백.
 * 출력: 배경 없는 본문 텍스트 + 버튼 목록.
 * 호출 시점: ChatMessageList가 clarification 메시지를 렌더링할 때 호출된다.
 *
 * **말풍선을 쓰지 않는다**(2026-09-07). 전에는 회색 말풍선(`rounded-md
 * bg-gray-100`)에 담겨 있었는데, 같은 화면의 `assistant_text`는 DESIGN_SYSTEM
 * §6.3대로 배경 없이 본문처럼 흐른다 — 한 대화 안에서 어시스턴트가 두 가지
 * 모양으로 말하고 있었다. 되묻기도 어시스턴트의 말이므로 같은 모양을 쓴다.
 * 버튼 클릭은 텍스트 재전송이 아니라 결정적 override다
 * (docs/design/clarification-options.md 3절) — label은 화면 표시용, id는
 * clarification_choice로 그대로 전송된다.
 */

import type { ClarificationOption } from "../../types";

interface ClarificationMessageProps {
  text: string;
  options: ClarificationOption[];
  isLoading: boolean;
  onSelectOption: (optionId: string, label: string) => void;
}

export function ClarificationMessage({
  text,
  options,
  isLoading,
  onSelectOption,
}: ClarificationMessageProps) {
  return (
    <div className="flex w-full flex-col gap-3 text-sm text-ink">
      <p className="whitespace-pre-line leading-6">{text}</p>
      {/* 턴이 지나면 선택지를 비운 채로 온다(TripContext의 withoutPastTurnControls).
          지난 턴의 되묻기를 지금 누르면 그때 기준의 답이 지금 맥락으로 나가서다.
          문구는 그 턴의 답변이라 기록으로 남기고 누를 수 있는 것만 없앤다. */}
      {/*
       * 선택지 버튼은 `RecommendationActionsMessage`의 "다른 장소 보기"와 **같은
       * 모양이다**(2026-09-08). `docs/design/clarification-options.md`가 이
       * 컴포넌트를 만들 때부터 "onRequestMore/onRelaxRadius 버튼 스타일 재사용"으로
       * 정해 뒀는데, 2026-09-01에 채팅 카드들을 새 디자인 시스템으로 재작성하면서
       * 이 파일만 빠져 옛 모양(rounded-md, border-gray-300)이 남아 있었다.
       *
       * 말풍선을 걷어낸 뒤 이 어긋남이 눈에 띄게 됐다 — 바로 아래 붙는
       * `SuggestedFollowUps` 칩과 같은 배경 위에 나란히 놓이는데, 둘 다 "누르면 그
       * 말이 보내진다"는 같은 성격인데도 하나만 각져 있었다.
       */}
      {options.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {options.map((option) => (
            <button
              key={option.id}
              type="button"
              disabled={isLoading}
              onClick={() => onSelectOption(option.id, option.label)}
              className="w-fit rounded-full border border-border bg-white px-4 py-2.5 text-sm font-medium text-ink transition-colors hover:border-brand hover:text-brand disabled:opacity-50"
            >
              {option.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
