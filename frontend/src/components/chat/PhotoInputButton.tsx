/*
 * 역할: 채팅 입력창 왼쪽의 "+" 버튼. 누르면 카메라/갤러리 메뉴가 열리고, 고른 사진을
 * 부모에게 넘긴다.
 * 입력: 사진 선택 콜백과 외부 요청 중 여부.
 * 출력: "+" 버튼과 메뉴. 사진을 고르면 onSelect로 File을 넘긴다.
 * 호출 시점: ChatComposer가 입력창을 렌더링할 때.
 *
 * "카메라"와 "갤러리"를 나눈 것은 input 하나의 capture 속성 차이다 — capture가 있으면
 * 카메라가 바로 열리고, 없으면 갤러리(파일 선택)가 열린다. 데스크톱 브라우저는
 * capture를 무시하고 둘 다 파일 선택으로 떨어지므로, 메뉴는 그대로 두되 동작이
 * 같아지는 것을 정상으로 본다.
 *
 * 앞 항목의 이름이 "사진"이었는데, 아이콘만 카메라라 무엇이 다른지 이름에서 읽히지
 * 않았다. "사진"이 더 포괄적으로 읽혀 오히려 갤러리 쪽으로 착각하기 쉽다 — 실제로
 * 2026-09-07에 이것 때문에 카메라 기능이 없는 줄 알고 카드를 새로 만들었다가
 * 닫았다(TP-249). 하는 일 그대로 "카메라"로 부른다(TP-250).
 */

import { Camera, ImageIcon, Plus } from "lucide-react";
import { useEffect, useRef, useState } from "react";

/** 서버 상한과 같다(app/routes/photo_similar.py). 올리기 전에 걸러 왕복을 아낀다. */
const MAX_IMAGE_BYTES = 10 * 1024 * 1024;

/** 서버가 받는 형식. 여기서 좁혀 두면 예상 못 한 파일이 업로드까지 가지 않는다. */
const ACCEPT = "image/jpeg,image/png,image/webp,image/heic,image/heif";

interface PhotoInputButtonProps {
  disabled?: boolean;
  onSelect: (file: File) => void | Promise<void>;
  onError?: (message: string) => void;
}

export function PhotoInputButton({ disabled = false, onSelect, onError }: PhotoInputButtonProps) {
  const [open, setOpen] = useState(false);
  const cameraRef = useRef<HTMLInputElement>(null);
  const galleryRef = useRef<HTMLInputElement>(null);
  const rootRef = useRef<HTMLDivElement>(null);

  // 메뉴 밖을 누르거나 Esc를 누르면 닫는다. 열어 둔 채로 다른 곳을 눌렀을 때
  // 메뉴가 남아 있으면 입력창을 가린다.
  useEffect(() => {
    if (!open) return;
    function onPointerDown(event: PointerEvent) {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") setOpen(false);
    }
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  function handleFile(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    // 같은 파일을 연속으로 고를 수 있게 값을 비운다. 안 비우면 change가 안 난다.
    event.target.value = "";
    setOpen(false);
    if (!file) return;
    if (file.size > MAX_IMAGE_BYTES) {
      onError?.("사진이 너무 커요. 10MB 이하로 올려 주세요.");
      return;
    }
    void onSelect(file);
  }

  return (
    <div ref={rootRef} className="relative flex items-center">
      <button
        type="button"
        disabled={disabled}
        aria-label="사진 추가"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
        className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full text-ink transition-colors hover:bg-chip disabled:opacity-50"
      >
        <Plus size={18} aria-hidden />
      </button>

      {open && (
        <div
          role="menu"
          className="absolute bottom-full left-0 z-30 mb-2 flex w-44 flex-col gap-0.5 rounded-2xl border border-white bg-white/60 p-1.5 shadow-card backdrop-blur-md"
        >
          <button
            type="button"
            role="menuitem"
            onClick={() => cameraRef.current?.click()}
            className="flex items-center gap-2.5 rounded-xl px-3 py-2.5 text-left text-sm font-medium text-ink transition-colors hover:bg-chip"
          >
            <Camera size={16} className="text-brand" aria-hidden /> 카메라
          </button>
          <button
            type="button"
            role="menuitem"
            onClick={() => galleryRef.current?.click()}
            className="flex items-center gap-2.5 rounded-xl px-3 py-2.5 text-left text-sm font-medium text-ink transition-colors hover:bg-chip"
          >
            <ImageIcon size={16} className="text-brand" aria-hidden /> 갤러리
          </button>
        </div>
      )}

      {/* capture가 있으면 휴대폰이 카메라를 바로 연다. 없으면 파일 선택이다. */}
      <input
        ref={cameraRef}
        type="file"
        accept={ACCEPT}
        capture="environment"
        onChange={handleFile}
        className="hidden"
        data-testid="photo-camera-input"
      />
      <input
        ref={galleryRef}
        type="file"
        accept={ACCEPT}
        onChange={handleFile}
        className="hidden"
        data-testid="photo-gallery-input"
      />
    </div>
  );
}
