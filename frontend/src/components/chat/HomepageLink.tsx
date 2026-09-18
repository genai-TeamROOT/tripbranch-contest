/*
 * 역할: 장소의 홈페이지 주소를 눌러서 여는 버튼 한 개로 보여준다.
 * 입력: TourAPI homepage 원문(주소 하나 또는 설명이 섞인 문자열).
 * 출력: 도메인만 적힌 알약 모양 링크.
 * 호출 시점: 답변 카드와 상세 모달의 "홈페이지" 행.
 *
 * 원문을 그대로 두면 "http://www.royalpalace.go.kr/..." 같은 주소가 두세 줄로
 * 접혀 그 행만 유난히 커지고, 파란 밑줄 글자가 본문과 뒤엉킨다. 사람이 이 행에서
 * 하는 일은 "눌러서 연다" 하나뿐이라, 읽을 것은 도메인 하나면 충분하다.
 *
 * 주소를 지우지는 않는다 — 전체 주소는 title로 남아 마우스를 올리면 보이고,
 * 낭독기도 링크 이름으로 읽는다.
 */

import { ExternalLink } from "lucide-react";

const URL_IN_TEXT = /(https?:\/\/[^\s]+|www\.[^\s]+)/;

function toHref(url: string): string {
  // www.만 있으면 상대경로로 오인돼 우리 사이트 안의 없는 페이지로 이동한다.
  return url.startsWith("www.") ? `https://${url}` : url;
}

/** 화면에 적을 이름. 도메인만 남기고 www.과 끝의 /는 뗀다. */
function toDisplayName(url: string): string {
  const withoutScheme = url.replace(/^https?:\/\//, "").replace(/^www\./, "");
  const host = withoutScheme.split("/")[0];
  return host || withoutScheme;
}

export function HomepageLink({ value, isEn = false }: { value: string; isEn?: boolean }) {
  const match = value.match(URL_IN_TEXT);
  /* 주소가 없는 값(“홈페이지 없음” 같은 안내문)은 링크로 만들 수 없다.
     그대로 글자로 보여준다 — 누를 수 없는 것을 버튼처럼 그리지 않는다. */
  if (!match) return <span className="text-ink">{value}</span>;

  const url = match[0].replace(/[),.]+$/, "");
  return (
    <a
      href={toHref(url)}
      target="_blank"
      rel="noreferrer"
      title={url}
      className="inline-flex max-w-full items-center gap-1.5 rounded-full border border-border bg-white px-2.5 py-1 text-xs font-medium text-brand transition-colors hover:border-brand hover:bg-sky-light"
    >
      <ExternalLink size={12} className="shrink-0" aria-hidden />
      <span className="truncate">{toDisplayName(url)}</span>
      <span className="sr-only">{isEn ? "(opens in a new tab)" : "(새 탭에서 열림)"}</span>
    </a>
  );
}
