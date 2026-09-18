/*
 * 역할: 올린 사진과 분위기가 닮은 장소를 대화에 보여준다.
 * 입력: 올린 사진 축소본, 진행 상태, 검색 중심 이름, 장소 목록, 후보 수.
 * 출력: 사진(우측 말풍선) + 결과(좌측 말풍선). 카드를 누르면 상세가 열린다.
 * 호출 시점: ChatMessageList가 photo_similar_result 메시지를 만났을 때.
 *
 * **사진은 사용자 쪽, 결과는 응답 쪽에 둔다.** 대화의 나머지와 같은 규칙이다 —
 * 사용자 발화는 `ml-auto`, 응답은 `mr-auto`. 사진은 사용자가 올린 것이므로
 * 오른쪽이 맞다.
 *
 * **되돌린 대화에는 사진 대신 안내를 놓는다.** 사진은 서버에 남기지 않아 다른
 * 기기에서 열면 가져올 데가 없다. 빈 자리만 두면 사진이 사라진 것인지 원래
 * 없던 것인지 알 수 없어, 왜 안 보이는지를 그 자리에 적는다.
 *
 * **사진 아래에 발화 문구를 함께 둔다.** 사진만 있으면 그 턴에 무엇을 요청한
 * 것인지가 화면에 안 남아, 지난 대화를 되돌렸을 때 사진 한 장이 맥락 없이
 * 놓인다. 서버도 같은 문장을 대화 기록에 남긴다(routes/photo_similar.py의
 * PHOTO_SEARCH_USER_INPUT) — 고칠 때 두 곳을 함께 고친다.
 *
 * **이 문장은 해석되지 않는다.** 사진 검색은 인텐트를 타지 않으므로 분류되는
 * 일이 없다. 사용자가 친 말이 아니라 그 턴을 사람이 읽을 수 있게 옮긴 것이다.
 *
 * **결과는 가로로 늘어놓는다.** 세로 목록이면 카드 하나가 한 줄을 통째로 쓰면서
 * 오른쪽이 비고, 5곳이면 화면이 그만큼 길어진다. 가로로 두면 사진이 나란히
 * 놓여 분위기를 한눈에 견줄 수 있다 — 이 화면의 목적이 그 비교다.
 *
 * **유사도를 백분율로 보여주지 않는다.** 그 값은 순위를 위한 것이지 "얼마나
 * 닮았다"의 눈금이 아니다(D-094). 정말 닮았는지는 사용자가 상세를 열어 본다.
 */

import { useState } from "react";
import type { PhotoSimilarPlace } from "../../types";
import { RecommendationDetailPreviewModal } from "./RecommendationDetailPreviewModal";
import { TourApiSourceNote } from "./SourceNotes";
import { PlaceThumbnail } from "../PlaceThumbnail";

/** 이 미만이면 벡터가 사진 한 장에 좌우된다(D-087). 표시를 달리한다. */
const RELIABLE_PHOTO_COUNT = 2;

/** 사진 검색 턴의 사용자 발화. 서버의 PHOTO_SEARCH_USER_INPUT과 같은 문장이다. */
const PHOTO_SEARCH_USER_INPUT = "이 사진과 비슷한 장소 추천해줘";

interface PhotoSimilarResultMessageProps {
  imageUrl?: string | null;
  /* 지난 대화를 되돌려 그리는 중인지. imageUrl이 비었다는 것만으로는 갈라낼 수
     없다 — 실시간에도 브라우저가 못 여는 형식이면 축소본이 없는데, 그때
     "저장하지 않아서"라고 말하면 틀린 설명이 된다. */
  restored?: boolean;
  /* failed는 요청이 실패한 경우다. 사유는 바로 뒤 turn_error가 말하므로 여기서는
     올린 사진만 남기고 아래 영역을 그리지 않는다(TP-245).

     location_required는 보낼 위치가 없어 요청을 아예 하지 않은 경우다. 이쪽은
     반대로 문구를 여기에 그린다 — 실패가 아니라 아직 답하지 않은 물음이라,
     사용자가 무엇을 해야 하는지가 사진 바로 아래 붙어야 읽힌다. */
  status?: "loading" | "done" | "failed" | "location_required";
  centerName: string;
  places: PhotoSimilarPlace[];
  candidateCount: number;
  /* 위치 설정 화면으로 보낸다. 이동은 화면(ChatPage)이 맡는다 — 대화 카드들은
     전부 표시 전용이라 라우터를 직접 부르지 않는다. 안 넘기면 버튼을 그리지
     않는다(onRetryTurn과 같은 관례). */
  onSetLocation?: () => void;
}

export function PhotoSimilarResultMessage({
  imageUrl,
  restored = false,
  status = "done",
  centerName,
  places,
  candidateCount,
  onSetLocation,
}: PhotoSimilarResultMessageProps) {
  const [selected, setSelected] = useState<PhotoSimilarPlace | null>(null);

  return (
    <>
      {imageUrl ? (
        <img src={imageUrl} alt="올린 사진" className="ml-auto max-h-48 rounded-md object-cover" />
      ) : (
        restored && (
          <div className="ml-auto max-w-[80%] rounded-md border border-dashed border-border px-4 py-3 text-right">
            <p className="text-sm text-muted">[사용자 입력 사진]</p>
            <p className="mt-1 text-xs text-muted">
              올리신 사진은 따로 저장하지 않아서 다시 보여드릴 수 없어요.
            </p>
          </div>
        )
      )}

      {/* 사용자 말풍선과 같은 모양이다(ChatMessageList의 user_text). */}
      <p className="ml-auto max-w-[80%] rounded-2xl rounded-br-md bg-brand px-4 py-2.5 text-sm text-white">
        {PHOTO_SEARCH_USER_INPUT}
      </p>

      {status === "location_required" ? (
        <div className="mr-auto max-w-full text-sm text-ink">
          <p className="mb-3">
            어디 근처에서 찾을지 몰라서 아직 못 찾았어요. 위치를 정하고 사진을 다시 올려
            주세요.
          </p>
          {onSetLocation && (
            <button
              type="button"
              onClick={onSetLocation}
              className="rounded-full border border-border px-4 py-2 text-sm font-medium text-ink transition-colors hover:bg-mist"
            >
              위치 정하기
            </button>
          )}
        </div>
      ) : status === "failed" ? null : (
        <div className="mr-auto max-w-full text-sm text-ink">
          {status === "loading" ? (
            <p className="flex items-center gap-2 text-muted">
              <Spinner />
              분위기가 닮은 곳을 찾고 있어요…
            </p>
          ) : places.length === 0 ? (
            /*
             * 두 상황을 구분한다. 문구가 하나면 "왜 안 나왔는지"를 사용자도
             * 개발자도 알 수 없다.
             *
             *   후보 0곳    지금 갈 수 있는 곳 자체가 없었다(영업시간·반경).
             *   후보 있음   후보는 있는데 사진 벡터가 없다. 적재가 안 된 구다.
             */
            <p>
              {candidateCount === 0 ? (
                <>
                  <span className="font-medium">{centerName}</span> 주변에서 지금 갈 수 있는 곳을
                  찾지 못했어요. 다른 지역으로 찾아볼까요?
                </>
              ) : (
                <>
                  <span className="font-medium">{centerName}</span> 주변 {candidateCount}곳을 봤는데
                  사진과 비교할 수 있는 곳이 없었어요. 아직 사진을 모으지 못한 지역이에요.
                </>
              )}
            </p>
          ) : (
            <>
              <p className="mb-3">
                <span className="font-medium">{centerName}</span> 주변에서 분위기가 닮은 곳이에요.
                눌러서 사진을 확인해 보세요.
              </p>
              {/* 좁은 화면에서는 가로 스크롤로 흘린다. 줄바꿈하면 다시 세로로 길어진다. */}
              <ul className="scrollbar-none -mx-1 flex gap-3 overflow-x-auto px-1 pb-1">
                {places.map((place, index) => (
                  <li key={place.content_id} className="w-28 shrink-0">
                    <button
                      type="button"
                      onClick={() => setSelected(place)}
                      className="group text-left"
                    >
                      {/*
                       * 비교에 실제로 쓴 사진이다(place_image_embeddings의 첫 장).
                       * places.first_image_url이 아니다 — 절반 이상이 다른 주소라
                       * 대표 이미지를 쓰면 비교하지 않은 사진을 보여주게 된다.
                       */}
                      <PlaceThumbnail src={place.image_url} />
                      <span className="mt-2 flex items-baseline gap-1">
                        <span className="text-[11px] tabular-nums text-brand">{index + 1}</span>
                        <span className="line-clamp-2 text-xs font-bold text-ink">
                          {place.title}
                        </span>
                      </span>
                      {place.photo_count < RELIABLE_PHOTO_COUNT && (
                        /* 사진 한 장으로 만든 벡터라 덜 믿을 만하다는 것을 숨기지 않는다. */
                        <span className="text-[11px] leading-tight text-muted">사진 1장 비교</span>
                      )}
                    </button>
                  </li>
                ))}
              </ul>
              {/* 비교 대상 사진과 장소 이름이 모두 관광공사 장소 데이터다. */}
              <TourApiSourceNote />
            </>
          )}
        </div>
      )}

      {selected && (
        <RecommendationDetailPreviewModal
          placeId={selected.content_id}
          placeName={selected.title}
          onClose={() => setSelected(null)}
        />
      )}
    </>
  );
}

function Spinner() {
  return (
    <svg
      viewBox="0 0 24 24"
      className="size-4 animate-spin text-muted"
      fill="none"
      aria-hidden="true"
    >
      <circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="3" opacity="0.25" />
      <path d="M21 12a9 9 0 0 0-9-9" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
    </svg>
  );
}
