/*
 * 역할: 즐겨찾기가 한도까지 찼다는 것을 알리는 바텀시트 모달.
 * 입력: 담을 수 있는 최대 개수.
 * 출력: onClose.
 * 호출 시점: 위치 설정 화면에서 한도가 찬 뒤 검색 결과의 별을 누를 때.
 *
 * 한도는 화면 구석의 작은 문구로는 눈에 띄지 않아 누른 사람이 왜 안 담겼는지
 * 모른다. 손을 멈추게 하는 모달로 알린다.
 *
 * 최대 개수를 문구에 박지 않고 받아 쓰는 것은, 한도를 정한 곳(LocationPage의
 * MAX_FAVORITES)과 말하는 곳이 갈라져 서로 다른 숫자를 말하는 일을 막기 위해서다.
 *
 * 모양은 LocationPurposeModal과 같은 바텀시트다.
 */

import { useId } from "react";
import { createPortal } from "react-dom";
import { Star, X } from "lucide-react";
import { useTripState } from "../../state/TripContext";

interface FavoritesLimitModalProps {
  max: number;
  onClose: () => void;
}

export function FavoritesLimitModal({ max, onClose }: FavoritesLimitModalProps) {
  const titleId = useId();
  const bodyId = useId();
  const isEn = useTripState().language === "en";

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-end justify-center p-4 md:items-center">
      <button
        type="button"
        aria-label={isEn ? "Close" : "닫기"}
        onClick={onClose}
        className="absolute inset-0 bg-ink-strong/40"
      />
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={bodyId}
        className="relative w-full max-w-md rounded-3xl bg-white p-5 pb-6 shadow-card"
      >
        <div className="mb-3 flex items-center justify-between">
          <h2 id={titleId} className="flex items-center gap-2 text-base font-bold text-ink">
            <Star size={18} className="shrink-0 fill-gold text-gold" />
            {isEn ? "Favorites are full" : "즐겨찾기가 가득 찼어요"}
          </h2>
          <button
            type="button"
            aria-label={isEn ? "Close" : "닫기"}
            onClick={onClose}
            className="shrink-0 text-muted transition-colors hover:text-ink"
          >
            <X size={18} />
          </button>
        </div>
        <p id={bodyId} className="text-sm leading-relaxed text-muted">
          {isEn
            ? `You can save up to ${max} favorites. Remove one to add another.`
            : `즐겨찾기는 ${max}개까지 담을 수 있어요. 새로 담으려면 기존 즐겨찾기를 지워주세요.`}
        </p>
        <button
          type="button"
          autoFocus
          onClick={onClose}
          className="mt-5 flex h-12 w-full items-center justify-center rounded-full bg-brand text-sm font-bold text-white transition-colors hover:bg-brand-deep"
        >
          {isEn ? "OK" : "확인"}
        </button>
      </div>
    </div>,
    document.body,
  );
}
