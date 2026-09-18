/*
 * 역할: ChatPage 하단에서 후속 사용자 입력을 받는다.
 * 입력: 텍스트 입력, 요청 중 여부, 제출 콜백, 상황별 placeholder.
 * 출력: 채팅 입력 form. 입력창은 여러 줄을 받으며(Shift+Enter) 내용만큼 위로 자란다.
 * 호출 시점: ChatPage가 대화 하단 입력창을 렌더링할 때 호출된다.
 * TODO: 실제 다회 대화 의미 분석이 생기면 입력 종류와 컨텍스트 전달을 확장한다.
 */

import { Send, Square } from "lucide-react";
import { useLayoutEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { useElementHeightVar } from "../../hooks/useElementHeightVar";
import type { Language } from "../../types";
import { PhotoInputButton } from "./PhotoInputButton";
import { VoiceInputButton } from "./VoiceInputButton";

const DEFAULT_PLACEHOLDER = "추가 조건을 입력해 주세요";

interface ChatComposerProps {
  disabled: boolean;
  onSubmit: (text: string) => Promise<void>;
  /* 되묻기처럼 특정 형태의 답변이 필요할 때 예시 문장을 안내한다. */
  placeholder?: string;
  language?: Language;
  /*
   * "+" 버튼으로 고른 사진. 안 넘기면 버튼 자체를 그리지 않는다 — 붙일 곳이
   * 준비되지 않은 화면에서 눌러도 아무 일이 없는 버튼을 보이지 않게 한다.
   */
  onPhotoSelect?: (file: File) => Promise<void> | void;
  /*
   * 둘 다 넘기면 입력값을 부모가 들고 있는다(제어 컴포넌트) — HomePage의 상황
   * 예시 칩처럼 컴포저 밖에서 입력창을 채워야 할 때만 쓴다. 안 넘기면(ChatPage의
   * 기본 사용법) 내부 상태로 그대로 동작한다.
   */
  value?: string;
  onChange?: (value: string) => void;
  /*
   * 전송 버튼의 접근성 이름. HomePage는 "추천 시작하기"처럼 화면의 주된 동작을
   * 그대로 쓴다(버튼 자체는 아이콘만 보이지만, 접근성 이름은 화면마다 의미가
   * 다르다) — 안 넘기면(ChatPage 기본값) 그냥 "보내기"/"Send".
   */
  sendLabel?: string;
  /*
   * 있으면(=응답을 기다리는 중) 전송 버튼 자리가 "중단" 버튼으로 바뀐다
   * (DESIGN_SYSTEM.md §7.2). HomePage의 최초 발화처럼 중단할 대상이 없는
   * 화면은 이 prop을 안 넘기면 기존처럼 비활성 아이콘만 보인다.
   */
  onCancel?: () => void;
}

export function ChatComposer({
  disabled,
  onSubmit,
  placeholder = DEFAULT_PLACEHOLDER,
  language = "ko",
  onPhotoSelect,
  value,
  onChange,
  sendLabel,
  onCancel,
}: ChatComposerProps) {
  const [internalText, setInternalText] = useState("");
  const text = value ?? internalText;
  const setText = onChange ?? setInternalText;
  // 음성과 사진이 같은 자리에 오류를 띄운다. 하나만 두면 뒤에 난 오류가 앞의 것을
  // 덮어쓰는데, 둘을 동시에 쓰는 흐름이 아니라 그 편이 자연스럽다.
  const [voiceError, setVoiceError] = useState<string | null>(null);

  /*
   * 입력창 높이를 내용에 맞춘다. **먼저 auto로 되돌리는 것이 핵심이다** —
   * scrollHeight는 지금 높이보다 작아지지 않아서, 지우는 중에는 줄어들지 않는다.
   *
   * 최댓값은 여기서 계산하지 않고 CSS(max-h-40)에 맡긴다. 두 곳에 같은 숫자를
   * 적어두면 한쪽만 고쳐진다. max-height는 scrollHeight를 깎지 않으므로 이 계산은
   * 그대로 맞고, 넘치는 만큼은 overflow-y-auto가 안에서 스크롤한다.
   */
  /*
   * 컴포저는 스크롤 영역 **위에 겹쳐** 선다 — 그래야 예전처럼 내용이 유리 뒤로
   * 지나간다(2026-09-09). 겹치는 만큼 스크롤 영역이 아래를 비워야 하므로 높이를
   * 재서 알려준다. 화면마다 `pb-[var(--tb-composer-h)]` 로 받아 쓴다.
   */
  const dockRef = useRef<HTMLDivElement>(null);
  useElementHeightVar(dockRef, "--tb-composer-h");

  const inputRef = useRef<HTMLTextAreaElement>(null);
  useLayoutEffect(() => {
    const element = inputRef.current;
    if (!element) return;

    const fit = () => {
      element.style.height = "auto";
      element.style.height = `${element.scrollHeight}px`;
    };
    fit();

    /*
     * **한 번만 재면 모자란다.** 처음 잰 뒤에 글이 다시 접히면 그 높이는 낡은
     * 값이 되고, 넘친 만큼이 안쪽 스크롤로 남는다 — 빈 입력창인데 스크롤이
     * 생기는 것이 그 모습이다.
     *
     * 다시 접히는 경우가 둘이다.
     * - **웹폰트가 늦게 올 때.** 대체 글꼴로 잰 높이는 Pretendard 로 바뀌면
     *   맞지 않는다. 자리표시가 긴 화면(홈)에서는 좁을수록 여러 줄이라 차이가
     *   그만큼 커진다(320px 에서 4줄, 2026-09-07 실측).
     * - **폭이 바뀔 때.** 회전하거나 창을 줄이면 줄 수가 달라지는데, 이 효과는
     *   text 가 바뀔 때만 돌아서 예전 높이가 그대로 남아 있었다.
     */
    let alive = true;
    void document.fonts?.ready.then(() => {
      if (alive) fit();
    });
    window.addEventListener("resize", fit);
    return () => {
      alive = false;
      window.removeEventListener("resize", fit);
    };
  }, [text]);

  async function submit() {
    const nextText = text.trim();
    if (!nextText || disabled) return;
    setText("");
    await onSubmit(nextText);
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await submit();
  }

  /*
   * textarea는 Enter가 줄바꿈이라 전송을 직접 처리한다. Enter=전송,
   * Shift+Enter=줄바꿈.
   *
   * **조합 중 Enter는 전송이 아니다.** 한글·일본어 입력기는 조합을 확정할 때
   * Enter를 쓴다 — "안녕"의 마지막 글자를 확정하려고 누른 Enter를 전송으로 받으면
   * 쓰던 도중에 글이 나가버린다.
   */
  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key !== "Enter" || event.shiftKey) return;
    if (event.nativeEvent.isComposing) return;
    event.preventDefault();
    void submit();
  }

  const resolvedSendLabel = sendLabel ?? (language === "en" ? "Send" : "보내기");

  /*
   * **더 이상 sticky 가 아니다**(2026-09-09). 전에는 `sticky bottom-0` 으로 스크롤
   * 영역 안에 두고 바닥에 붙였는데, iOS 사파리에서 소프트 키보드가 뜬 동안 그
   * sticky 가 통째로 죽었다 — 처음 붙은 자리에서 내용과 같이 흘러갔다(실측:
   * "창바닥 − 컴포저바닥"이 scrollTop 과 한 행도 빠짐없이 일치, 표본 전부).
   *
   * 지금은 쓰는 쪽이 스크롤 영역의 **형제**로 두고, 여기서 absolute 로 그 위에
   * 겹친다. 스크롤과 무관하게 자리가 고정이라 붙일 대상이 없고, 내용은 예전처럼
   * 유리 뒤로 지나간다. 키보드가 뜨면 `.tb-keyboard-lift` 가 가린 높이만큼
   * 올린다(index.css) — 위쪽 본문은 건드리지 않는다.
   */
  return (
    <div
      ref={dockRef}
      className="tb-keyboard-lift tb-composer-dock absolute inset-x-0 bottom-0 z-20 px-4 pt-6 md:mx-auto md:w-full md:max-w-2xl"
    >
      {voiceError && (
        <p role="alert" className="mb-2 text-sm text-rust">
          {voiceError}
        </p>
      )}
      <form
        onSubmit={handleSubmit}
        /* items-end — 여러 줄이 되면 버튼이 첫 줄 옆이 아니라 아래에 붙어야 한다.
           rounded-full 은 높이의 절반이 반지름이라 늘어날수록 통이 부푼다.
           한 줄일 때(52px)의 반지름 26px 과 거의 같은 값으로 고정한다. */
        className="flex items-end gap-1 rounded-3xl border border-white bg-white/60 p-1.5 shadow-card backdrop-blur-md"
      >
        {onPhotoSelect && (
          <PhotoInputButton
            disabled={disabled}
            onSelect={async (file) => {
              setVoiceError(null);
              await onPhotoSelect(file);
            }}
            onError={setVoiceError}
          />
        )}
        {/* min-h-10 은 버튼(h-10)과 같은 높이다 — 한 줄일 때 지금과 같은 모양이 된다.
            jsdom 처럼 scrollHeight 가 0인 환경에서도 이 값이 바닥을 잡아준다.

            placeholder-shown:pt-[10px]/pb-[6px] — 예시 문구만 실제 입력 글자보다
            작다(text-sm vs text-base). line-height(leading-6, 24px)를 그대로
            공유하면 작은 글자가 그 줄 안에서 위로 쏠려 보인다(2026-09-07, 실제
            렌더로 측정: 24px 줄 높이를 유지한 채 py-2만 쓰면 중심선보다 위에
            걸림). 값 자체를 계산이 아니라 헤드리스 브라우저로 여러 후보를 대 보고
            중심선에 맞는 것으로 골랐다 — 총합(16px)은 py-2·py-2와 같게 유지해
            입력칸 높이(min-h-10)는 그대로다. */}
        <textarea
          ref={inputRef}
          rows={1}
          value={text}
          onChange={(event) => setText(event.target.value)}
          onKeyDown={handleKeyDown}
          disabled={disabled}
          placeholder={placeholder}
          className="min-h-10 max-h-40 min-w-0 flex-1 resize-none overflow-y-auto bg-transparent px-1.5 py-2 text-base leading-6 text-ink placeholder:text-sm placeholder:text-muted placeholder-shown:pb-[6px] placeholder-shown:pt-[10px] focus:outline-none disabled:opacity-50"
        />
        <VoiceInputButton
          disabled={disabled}
          onTranscript={() => {
            setVoiceError(null);
          }}
          onAutoSubmit={async (transcript) => {
            setVoiceError(null);
            setText("");
            await onSubmit(transcript);
          }}
          onManualStop={(transcript) => {
            setVoiceError(null);
            setText(transcript);
          }}
          onError={setVoiceError}
        />
        {disabled && onCancel ? (
          <button
            type="button"
            onClick={onCancel}
            aria-label={language === "en" ? "Stop" : "중단"}
            className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-rust text-white transition-colors hover:bg-rust/90"
          >
            <Square size={14} className="fill-current" aria-hidden />
          </button>
        ) : (
          <button
            type="submit"
            aria-label={resolvedSendLabel}
            disabled={disabled || !text.trim()}
            className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-brand text-white transition-colors hover:enabled:bg-brand-deep disabled:bg-brand/30"
          >
            <Send size={16} aria-hidden />
          </button>
        )}
      </form>
    </div>
  );
}
