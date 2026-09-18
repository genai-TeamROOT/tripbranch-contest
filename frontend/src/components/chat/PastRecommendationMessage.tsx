/*
 * 역할: 지난 대화에서 화면에 나갔던 장소들을 한 줄의 카드로 그린다.
 * 입력: GET/POST /api/sessions/{id}[/resume]가 돌려준 PastRecommendation 목록.
 * 출력: "그때 추천받은 곳" 캡션 + 카드 목록. 카드를 누르면 상세 모달이 열린다.
 * 호출 시점: ChatMessageList가 past_recommendation_result 메시지를 그릴 때.
 *
 * **PlaceCard를 재사용하지 않는다.** 그 컴포넌트는 remaining_minutes로 "지금
 * 영업 중 / N분 후 마감"을 그리는데, 여기 값은 사흘 전 스냅샷이라 그대로 그리면
 * 현재 상태를 잘못 말하게 된다. 점수·사진·카테고리도 저장 자체가 없어(실측
 * 459건 중 이름 100%, 거리·실내외 87%, 이유 13%) 빈 칸으로 채우면 "그때 그
 * 카드"인 척하는 화면이 된다.
 *
 * 대신 상세는 누를 때 실시간으로 조회한다 — 모달이 place_id만으로 스스로
 * 불러오므로, 대화를 열자마자 장소 수만큼 요청이 나가는 일이 없다.
 *
 * 담기(♥)는 두지 않는다. 보관함은 세션에 묶여 있고(SCHEDULE-12), 지난 대화를
 * 펼쳐 보는 화면에서 담기는 것이 어느 세션의 보관함인지가 분명하지 않다.
 */

import { motion } from "framer-motion";
import { ChevronRight, MapPin } from "lucide-react";
import { useState } from "react";
import type { Language, PastRecommendation } from "../../types";
import { PlaceCardRow } from "./PlaceCardRow";
import { RecommendationDetailPreviewModal } from "./RecommendationDetailPreviewModal";

interface PastRecommendationMessageProps {
  places: PastRecommendation[];
  language?: Language;
}

const ENVIRONMENT_LABEL: Record<string, { ko: string; en: string }> = {
  indoor: { ko: "실내", en: "Indoor" },
  outdoor: { ko: "실외", en: "Outdoor" },
  mixed: { ko: "실내·실외", en: "Indoor & outdoor" },
};

export function PastRecommendationMessage({
  places,
  language = "ko",
}: PastRecommendationMessageProps) {
  const text =
    language === "en"
      ? { caption: "Places recommended back then", preview: "Preview" }
      : { caption: "그때 추천받은 곳", preview: "장소 미리보기" };
  const [selected, setSelected] = useState<PastRecommendation | null>(null);

  if (places.length === 0) return null;

  return (
    <article className="mr-auto flex w-full flex-col gap-3">
      <PlaceCardRow caption={text.caption}>
        {places.map((place) => {
          /* 저장된 값만 줄로 세운다. 없는 값은 줄 자체가 없다. */
          const facts = [
            place.distance_km != null ? `${place.distance_km.toFixed(1)}km` : null,
            place.environment_type
              ? ENVIRONMENT_LABEL[place.environment_type]?.[language]
              : null,
          ].filter(Boolean);

          return (
            <motion.li
              key={`${place.run_id}-${place.place_id}`}
              className="w-40 shrink-0"
              variants={{ hidden: { opacity: 0, y: 8 }, visible: { opacity: 1, y: 0 } }}
            >
              <div
                role="button"
                tabIndex={0}
                aria-label={
                  language === "en"
                    ? `View details for ${place.name}`
                    : `${place.name} 장소 정보 미리 보기`
                }
                className="w-full cursor-pointer text-left"
                onClick={() => setSelected(place)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    setSelected(place);
                  }
                }}
              >
                {/* 사진은 저장하지 않는다. 자리표시 칩에 아무 말도 넣지 않는 이유는
                    여기 넣을 만한 사실이 없기 때문이다 — 카테고리도 기록이 없다. */}
                <span className="flex h-28 w-full items-center justify-center rounded-2xl bg-chip text-muted">
                  <MapPin size={18} />
                </span>
                <div className="pt-2">
                  <p className="truncate text-sm font-bold text-ink">{place.name}</p>
                  {facts.length > 0 && (
                    <p className="mt-1 text-[11px] text-muted">{facts.join(" · ")}</p>
                  )}
                  {place.reason && (
                    <p className="mt-1 line-clamp-2 text-[11px] leading-relaxed text-muted">
                      {place.reason}
                    </p>
                  )}
                  <p className="mt-1.5 flex items-center gap-0.5 text-[11px] font-semibold text-brand">
                    {text.preview} <ChevronRight size={11} />
                  </p>
                </div>
              </div>
            </motion.li>
          );
        })}
      </PlaceCardRow>

      {selected && (
        <RecommendationDetailPreviewModal
          placeId={selected.place_id}
          placeName={selected.name}
          onClose={() => setSelected(null)}
        />
      )}
    </article>
  );
}
