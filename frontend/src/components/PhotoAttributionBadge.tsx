import type { ImageAttribution } from "../types";

/**
 * Google Places 사진에 붙이는 출처 배지. 큰 사진의 오른쪽 아래에 얹는다.
 *
 * Google Maps Platform 정책은 사진을 보여줄 때 작성자를 밝히고, 사용자가 원본
 * 사진을 Google 지도에서 볼 수 있게 하라고 요구한다. 그래서 이건 장식이 아니라
 * **사진을 표시하기 위한 조건**이다.
 *
 * 목록 썸네일에는 붙이지 않고 상세의 큰 사진에만 붙인다. 정책이 갤러리·썸네일에서
 * 생략을 허용하는 근거가 "사용자가 전체 출처가 달린 큰 사진에 닿을 수 있을 것"이라,
 * 상세에 이 배지가 없으면 목록에서 생략한 것까지 함께 근거를 잃는다.
 *
 * 관광공사 사진에는 attribution이 없어 아무것도 그리지 않는다.
 */
export function PhotoAttributionBadge({
  attribution,
  className = "",
}: {
  attribution?: ImageAttribution | null;
  className?: string;
}) {
  if (!attribution?.author_name) {
    return null;
  }

  const { author_name: authorName, author_uri: authorUri } = attribution;
  const sourceUri = attribution.source_uri;
  const provider = attribution.provider || "Google Maps";

  // 사진을 눌러 넘기는 동작이 링크 클릭까지 같이 받지 않게 막는다.
  const stop = (event: React.MouseEvent | React.KeyboardEvent) => {
    event.stopPropagation();
  };

  const author = authorUri ? (
    <a
      href={authorUri}
      target="_blank"
      rel="noopener noreferrer"
      className="underline underline-offset-2 hover:text-white"
      onClick={stop}
      onKeyDown={stop}
    >
      {authorName}
    </a>
  ) : (
    <span>{authorName}</span>
  );

  const source = sourceUri ? (
    <a
      href={sourceUri}
      target="_blank"
      rel="noopener noreferrer"
      className="underline underline-offset-2 hover:text-white"
      onClick={stop}
      onKeyDown={stop}
    >
      {provider}
    </a>
  ) : (
    <span>{provider}</span>
  );

  return (
    <span
      data-testid="photo-attribution"
      title={`사진 ${authorName} · ${provider}`}
      className={`absolute bottom-2 right-2 max-w-[calc(100%-1rem)] truncate rounded-full bg-black/60 px-2 py-0.5 text-[11px] leading-tight text-white/90 ${className}`}
    >
      사진 {author} · {source}
    </span>
  );
}
