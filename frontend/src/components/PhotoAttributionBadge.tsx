import type { ImageAttribution } from "../types";

/**
 * Google Places 사진에 붙이는 출처 배지.
 *
 * Google Maps Platform 정책은 사진을 보여줄 때 작성자를 밝히고, 사용자가 원본
 * 사진을 Google 지도에서 볼 수 있게 하라고 요구한다. 그래서 이 배지는 장식이
 * 아니라 **사진을 표시하기 위한 조건**이다. 사진을 그리는 자리에서 이걸 빼면
 * 정책 위반이 된다.
 *
 * 관광공사 사진에는 붙지 않는다 — attribution이 없으면 아무것도 그리지 않는다.
 *
 * 사진 위에 얹는 이유는 카드가 좁아서다. 아래에 한 줄을 더 쓰면 장소명·배지가
 * 밀려 카드 높이가 장소마다 들쭉날쭉해진다. 대신 글자가 사진에 묻히지 않도록
 * 어두운 그라데이션을 깐다.
 */
export function PhotoAttributionBadge({
  attribution,
  className = "",
  compact = false,
}: {
  attribution?: ImageAttribution | null;
  className?: string;
  /**
   * 일정 카드처럼 썸네일이 96px밖에 안 되는 자리에서 쓴다. 글자를 줄이고 한 줄로
   * 자르되 작성자 이름은 남긴다 — 정책이 요구하는 건 작성자 크레딧이라, 자리가
   * 좁다고 이름을 빼면 표기를 안 한 것과 같아진다. 잘린 이름은 title로 볼 수 있다.
   */
  compact?: boolean;
}) {
  if (!attribution?.author_name) {
    return null;
  }

  const { author_name: authorName, author_uri: authorUri } = attribution;
  const sourceUri = attribution.source_uri;
  const provider = attribution.provider || "Google Maps";

  // 카드를 누르면 상세가 열리므로, 링크 클릭이 그 동작까지 같이 부르지 않게 막는다.
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
    <div
      data-testid="photo-attribution"
      title={`사진 ${authorName} · ${provider}`}
      className={`pointer-events-none absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/70 to-transparent px-1.5 pb-1 text-white/90 ${
        compact ? "pt-2 text-[9px] leading-none" : "pt-3 text-[10px] leading-tight"
      } ${className}`}
    >
      <span className="pointer-events-auto block truncate">
        {compact ? null : "사진 "}
        {author} · {source}
      </span>
    </div>
  );
}
