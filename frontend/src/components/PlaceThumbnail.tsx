/*
 * 역할: 장소 사진 한 장을 카드 썸네일 자리에 그리고, 사진이 없거나 못 불러오면
 *       같은 모양의 자리표시를 보여준다.
 * 입력: 이미지 주소(등록되지 않은 장소는 null/undefined로 온다)와 그것이 실패했을
 *       때 대신 부를 주소.
 * 출력: <img> 또는 "사진 없음" 자리표시.
 * 호출 시점: 추천 카드(PlaceCard), 행사 카드(PlaceInfoCard의 RealtimeEventCard),
 *            사진 유사 검색 결과(PhotoSimilarResultMessage)가 썸네일을 그릴 때.
 *
 * 세 화면이 같은 블록을 복사해 쓰면서 자리표시가 서로 달랐다(카테고리 영문 슬러그 /
 * "행사" / 빈 칩). 사진이 없다는 사실은 어느 화면에서든 같은 뜻이므로 한 모양으로 묶는다.
 */

import { useState } from "react";
import { ImageOff } from "lucide-react";

interface PlaceThumbnailProps {
  /** 없으면 자리표시를 그린다. */
  src?: string | null;
  /**
   * src가 실패했을 때 한 번 더 시도할 주소. 추천 카드만 넘긴다.
   *
   * 작은 썸네일(firstimage2)만 관광공사 서버에서 사라진 장소가 있다 — 아현시장이
   * 그렇고, 그 장소의 원본(firstimage)은 살아 있다. 서버가 미리 확인해 고르지 않는
   * 이유는 비용이다: 추천 한 번에 카드가 5장이라 확인 요청이 5~10건 붙고 그만큼
   * 응답이 늦어진다. 정상 카드는 여기서도 요청이 한 건뿐이고, 실패한 카드에서만
   * 두 번째가 나간다.
   */
  fallbackSrc?: string | null;
  /**
   * 크기·모양 클래스. 안 넘기면 추천 카드용 기본값이다.
   *
   * **밖에서 정할 수 있게 둔 이유**는 일정 화면이 같은 사진을 다른 크기로 쓰기
   * 때문이다(정류장 96px 정사각, 지금 있는 곳은 전체 폭). 여기서 못 바꾸면 그
   * 화면이 <img>와 자리표시를 다시 짜게 되고, 그러면 이 컴포넌트를 만든 이유가
   * 사라진다 — 화면마다 "사진 없음" 모양이 갈렸던 것이 그 시작이었다.
   */
  className?: string;
}

const DEFAULT_SHAPE = "h-28 w-full rounded-2xl transition-transform duration-300 group-hover:scale-105";

export function PlaceThumbnail({ src, fallbackSrc, className }: PlaceThumbnailProps) {
  const shape = className ?? DEFAULT_SHAPE;
  /*
   * 실패한 주소들을 그대로 담는다. boolean으로 두면 같은 카드가 다른 사진으로 다시
   * 그려질 때(재랭킹 등) 실패 표시가 남아 멀쩡한 사진까지 가린다 — 목록은
   * key={place_id}로 그려지므로 src만 바뀌고 이 컴포넌트는 그대로 살아 있다.
   *
   * 이전에는 onError에서 event.currentTarget.style.visibility를 직접 바꿨는데,
   * React가 모르는 변경이라 같은 <img> DOM이 재사용될 때 숨김이 그대로 남았다.
   */
  const [failed, setFailed] = useState<readonly string[]>([]);

  // 아직 실패하지 않은 첫 주소를 쓴다. 서버가 fallbackSrc에 src와 같은 주소를 넣지
  // 않지만, 여기서도 걸러 같은 주소를 두 번 부르지 않게 한다.
  const current = [src, fallbackSrc].find(
    (candidate): candidate is string => Boolean(candidate) && !failed.includes(candidate as string),
  );

  if (!current) {
    return (
      // 사진이 없는 것과 원본이 사라진 것을 구분해 보여주지 않는다 — 사용자가 할 수
      // 있는 일이 같고, 목록에서 자리표시가 연달아 나올 때 설명이 길면 장소 이름보다
      // 눈에 띈다.
      <span
        data-testid="place-thumbnail-placeholder"
        className={`flex items-center justify-center bg-chip text-gray-400 ${shape}`}
      >
        <ImageOff size={26} strokeWidth={1.5} aria-hidden />
      </span>
    );
  }

  return (
    <img
      // 주소가 바뀌면 <img>를 새로 만든다. 같은 DOM에 src만 갈아끼우면 브라우저가
      // 이전 요청을 이어받는 경우가 있어 두 번째 시도가 조용히 실패한다.
      key={current}
      src={current}
      alt=""
      loading="lazy"
      className={`object-cover ${shape}`}
      onError={() => setFailed((previous) => [...previous, current])}
    />
  );
}
