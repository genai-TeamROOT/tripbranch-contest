/*
 * 역할: 고른 장소를 출발지로 쓸지 검색 기준으로 쓸지 묻는 바텀시트 모달.
 * 입력: 고른 장소 이름.
 * 출력: onPick("origin" | "center") 또는 onClose.
 * 호출 시점: 위치 설정 화면에서 검색 결과·즐겨찾기·"현재 위치 사용"을 누를 때.
 *
 * 두 선택은 서로 다른 질문의 답이라 한 번에 정할 수 없다(D-067) — 출발지는
 * "어디서 출발해 얼마나 걸리는지"이고, 검색 기준은 "어디 주변을 찾을지"다.
 * 눌렀을 때 둘 중 무엇인지 화면이 임의로 정하면 반은 틀린다.
 *
 * "현재 위치 사용"은 이 모달을 거치지 않는다 — 그 버튼의 뜻은 "내 위치는 기기
 * 좌표다" 하나뿐이라 되물을 것이 없다.
 *
 * 모양은 FavoritesLimitModal과 같은 바텀시트다.
 */

import { useId } from "react";
import { createPortal } from "react-dom";
import { MapPin, Navigation, X } from "lucide-react";
import { useTripState } from "../../state/TripContext";

export type LocationPurpose = "origin" | "center";

interface LocationPurposeModalProps {
  placeName: string;
  onPick: (purpose: LocationPurpose) => void;
  onClose: () => void;
}

export function LocationPurposeModal({ placeName, onPick, onClose }: LocationPurposeModalProps) {
  const titleId = useId();
  const isEn = useTripState().language === "en";

  const options: Array<{
    purpose: LocationPurpose;
    icon: typeof MapPin;
    title: string;
    hint: string;
  }> = isEn
    ? [
        {
          purpose: "origin",
          icon: Navigation,
          title: `Start from ${placeName}`,
          hint: "Travel times will be measured from here",
        },
        {
          purpose: "center",
          icon: MapPin,
          title: `Find places around ${placeName}`,
          hint: "Show places near this location",
        },
      ]
    : [
        {
          purpose: "origin",
          icon: Navigation,
          title: `${placeName}에서 출발할게요`,
          hint: "이동 시간을 여기서부터 잽니다",
        },
        {
          purpose: "center",
          icon: MapPin,
          title: `${placeName} 주변에서 찾아주세요`,
          hint: "이 근처의 장소를 모아서 보여줍니다",
        },
      ];

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
        className="relative w-full max-w-md rounded-3xl bg-white p-5 pb-6 shadow-card"
      >
        <div className="mb-4 flex items-center justify-between">
          <h2 id={titleId} className="min-w-0 truncate text-base font-bold text-ink">
            {placeName}
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

        <div className="flex flex-col gap-2">
          {options.map(({ purpose, icon: Icon, title, hint }) => (
            <button
              key={purpose}
              type="button"
              onClick={() => onPick(purpose)}
              className="flex items-center gap-3 rounded-xl border border-border px-3.5 py-3 text-left transition-colors hover:bg-chip"
            >
              <Icon size={18} className="shrink-0 text-brand" />
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm font-bold text-ink">{title}</span>
                <span className="block truncate text-xs text-muted">{hint}</span>
              </span>
            </button>
          ))}
        </div>
      </div>
    </div>,
    document.body,
  );
}
