/*
 * 역할: 추천 API 응답을 채팅 메시지 안에서 장소 카드 목록으로 렌더링한다.
 * 입력: 정상 추천 목록, 운영시간 미확인 목록, 추가 추천 요청 콜백.
 * 출력: 추천 결과 메시지와 PlaceCard 목록 — **줄은 언제나 하나다**("추천 장소").
 *   운영시간을 확인하지 못한 후보(원문이 없거나 지금 폐점)도 이 줄에 함께
 *   들어간다(2026-09-08, 아래 rankedRecommendations 주석). 그 줄 오른쪽에는
 *   추천 기준 보조설명이 붙는다(PlaceCardRow의 note, 2026-09-09).
 *
 * **동작 버튼과 취향 표는 여기 없다.** 각각 RecommendationActionsMessage와
 * PreferenceTagSummaryTable이 별도 메시지로 그린다 — 버튼은 다음 발화가 나가면
 * 걷어내야 하는데 카드와 한 메시지에 있으면 같이 지워지기 때문이다.
 * 호출 시점: ChatPage가 recommendation_result 메시지를 렌더링할 때 호출된다.
 * 담기/빼기는 useSavedPlaces()로 직접 읽고 쓴다 — 카드가 메시지 목록 깊숙이
 * 있어 prop으로 내리면 중간 컴포넌트 셋을 전부 거쳐야 한다.
 * TODO: 지도/동선 액션이 생기면 PlaceCard 주변 액션으로 확장한다.
 *
 * showElapsedTime이 false면(실사용자 화면) 지연시간(elapsedMs/serverElapsedMs)을
 * 아예 렌더링하지 않는다 — 개발자 확인용 숫자가 실서비스 화면에 새던 걸 정리함.
 * /dev-chat(ChatMessageList의 isDeveloperView)에서만 true로 넘어온다.
 */

import { useState } from "react";
import type { Language, RecommendationItem } from "../../types";
import { useSavedPlaces } from "../../hooks/useSavedPlaces";
import { useTasteEnabled } from "../../state/FeatureFlagsContext";
import { PlaceCard } from "../PlaceCard";
import { PlaceCardRow } from "./PlaceCardRow";
import { RecommendationDetailPreviewModal } from "./RecommendationDetailPreviewModal";
import { TourApiSourceNote } from "./SourceNotes";

interface RecommendationResultMessageProps {
  recommendations: RecommendationItem[];
  unverifiedRecommendations: RecommendationItem[];
  elapsedMs: number;
  serverElapsedMs: number;
  showElapsedTime?: boolean;
  language?: Language;
}

function formatDuration(milliseconds: number | undefined) {
  if (typeof milliseconds !== "number" || !Number.isFinite(milliseconds)) return "-";
  return milliseconds >= 1000
    ? `${(milliseconds / 1000).toFixed(1)}초`
    : `${Math.round(milliseconds)}ms`;
}

export function RecommendationResultMessage({
  recommendations,
  unverifiedRecommendations,
  elapsedMs,
  serverElapsedMs,
  showElapsedTime = false,
  language = "ko",
}: RecommendationResultMessageProps) {
  /* 취향이 꺼진 서버에서는 순위에 취향 축이 아예 없다(taste_evidence_enabled).
     그때 "취향을 고려했다"고 쓰면 사실과 다르다. */
  const tasteEnabled = useTasteEnabled();
  const text =
    language === "en"
      ? {
          noResults: "We couldn’t find a place that matches those conditions.",
          recommendations: "Recommended places",
          recommendationsNote: tasteEnabled
            ? "Ranked by distance, weather, your preferences, and more"
            : "Ranked by distance, weather, and more",
        }
      : {
          noResults: "조건에 맞는 장소를 찾지 못했어요.",
          recommendations: "추천 장소",
          recommendationsNote: tasteEnabled
            ? "거리·날씨·취향 등을 고려했어요"
            : "거리·날씨 등을 고려했어요",
        };
  const [selectedRecommendation, setSelectedRecommendation] = useState<RecommendationItem | null>(
    null,
  );
  const { savedPlaceIds, toggleSaved } = useSavedPlaces();
  /*
   * **줄은 하나다**(2026-09-08). 전에는 세 줄이었다 — "추천 장소",
   * "현재 운영시간이 아닌 장소"(운영시간 원문은 있지만 지금 닫힌 후보),
   * "운영시간을 확인할 수 없는 장소"(원문조차 없는 후보). 뒤 둘을 차례로
   * 이 줄에 합쳤다.
   *
   * **캡션이 하던 말을 카드가 이미 한다.** 운영시간 자리에 "확인 불가" 또는
   * "19:00~23:00 (현재 운영시간 아님)"이 찍히고(PlaceCard의 hoursRemainingLabel),
   * 그 아래 경고 줄에 "방문 전에 운영 여부를 확인해주세요." 또는 "지금은
   * 운영시간이 아니에요. 방문 전에 다시 확인해주세요."가 붙는다
   * (domain/scoring.py의 _UNVERIFIED_WARNING·_CLOSED_NOW_WARNING). 캡션은 그
   * 말을 한 번 더 하면서 줄을 갈랐다.
   *
   * **줄 분리는 mintee가 4cab841a에서 넣은 것이고 이 변경이 그걸 덮는다**
   * (사용자 결정, 2026-09-08). 다만 그 커밋의 핵심 의도인 "폐점 후보의 실제
   * 운영시간을 보존해 00:00~00:00 표기를 제거"는 그대로 산다 — 그건 줄 분리가
   * 아니라 카드가 operating_hours_display를 읽는 방식이다.
   *
   * **순위 번호가 이어 붙는다**(사용자 결정). 검증된 후보가 5개면 나머지는 6·7위로
   * 보인다. 백엔드는 원래 검증·미확인을 한 목록에서 함께 줄 세워 rank를 매기지만
   * (domain/scoring.py의 `rank=index + 1`) 그 값을 응답에 싣지 않으므로,
   * 화면의 번호는 배열 순서로 다시 붙인 것이다 — 실제로 3위였던 미확인 후보가
   * 6위로 보일 수 있다. 검증된 후보가 하나도 없으면 미확인 후보가 1위 자리에 온다.
   *
   * 순서는 백엔드가 준 그대로다. 각 목록 안은 점수 내림차순이므로 합치면
   * "검증된 것들(점수순) → 확인 못 한 것들(점수순)"이 된다.
   */
  const rankedRecommendations = [...recommendations, ...unverifiedRecommendations];
  const hasNoResults = rankedRecommendations.length === 0;

  return (
    <article className="mr-auto flex w-full flex-col gap-3">
      {showElapsedTime && (
        <div className="flex flex-wrap items-baseline justify-end gap-2">
          <p className="text-xs text-muted">
            {formatDuration(elapsedMs)} 소요 (서버 {formatDuration(serverElapsedMs)})
          </p>
        </div>
      )}

      {hasNoResults ? (
        /* 버튼은 여기 없다 — RecommendationActionsMessage가 뒤이어 그린다.
           안내 문구는 그때 받은 답이라 기록으로 남긴다. */
        <div className="flex flex-col gap-3 text-sm">
          <p className="text-ink">{text.noResults}</p>
        </div>
      ) : (
        <>
          {rankedRecommendations.length > 0 && (
            <PlaceCardRow caption={text.recommendations} note={text.recommendationsNote}>
              {rankedRecommendations.map((item, index) => (
                <PlaceCard
                  key={item.place_id}
                  item={item}
                  rank={index + 1}
                  language={language}
                  isSaved={savedPlaceIds.has(item.place_id)}
                  onToggleSave={(selectedItem) => void toggleSaved(selectedItem)}
                  onOpenDetail={(selectedItem) => setSelectedRecommendation(selectedItem)}
                />
              ))}
            </PlaceCardRow>
          )}
          {/* 추천 목록의 장소·사진·운영시간은 관광공사 장소 데이터를 저장해 둔
              것에서 나온다. 카드마다 붙이면 가로 스크롤이 표기로 뒤덮이므로
              목록 아래 한 줄로만 밝힌다. */}
          {rankedRecommendations.length > 0 && <TourApiSourceNote isEn={language === "en"} />}
        </>
      )}

      {selectedRecommendation && (
        <RecommendationDetailPreviewModal
          item={selectedRecommendation}
          onClose={() => setSelectedRecommendation(null)}
        />
      )}
    </article>
  );
}
