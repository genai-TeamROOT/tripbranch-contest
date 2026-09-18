import { useState } from "react";

import type { Language, PreferenceTagSummaryEntry } from "../../types";

interface PreferenceTagSummaryTableProps {
  /* RecommendationItem을 그대로 넘겨도 된다 — 이 모양을 만족한다. */
  items: PreferenceTagSummaryEntry[];
  language: Language;
}

export function PreferenceTagSummaryTable({ items, language }: PreferenceTagSummaryTableProps) {
  const [isSourceOpen, setIsSourceOpen] = useState(false);
  const taggedItems = items.filter((item) => (item.preference_tags?.length ?? 0) > 0);
  if (taggedItems.length === 0) return null;

  return (
    <section>
      <div className="overflow-hidden rounded-2xl border border-border/70 bg-white shadow-resting">
        <div className="flex items-start justify-between gap-2 px-4 pb-2 pt-3.5">
          <h3 className="text-base font-semibold tracking-tight text-ink">
            {language === "en" ? "Visitor preference tags by place" : "장소별 방문자 취향 태그"}
          </h3>
          <span className="relative inline-flex shrink-0">
            <button
              type="button"
              aria-label={language === "en" ? "Show tag sources" : "태그 출처 보기"}
              aria-expanded={isSourceOpen}
              onMouseEnter={() => setIsSourceOpen(true)}
              onMouseLeave={() => setIsSourceOpen(false)}
              onFocus={() => setIsSourceOpen(true)}
              onBlur={() => setIsSourceOpen(false)}
              onClick={() => setIsSourceOpen((open) => !open)}
              className="flex h-4 w-4 items-center justify-center rounded-full text-[11px] font-bold leading-none text-muted hover:text-label focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand"
            >
              ⓘ
            </button>
            {isSourceOpen && (
              <p
                role="tooltip"
                className="absolute right-0 top-full z-10 mt-1 w-max max-w-[min(280px,calc(100vw-32px))] break-keep rounded-lg border border-border bg-surface px-2.5 py-2 text-right text-[11px] leading-4 text-muted shadow-card"
              >
                <span className="block">
                  {language === "en"
                    ? "Source: Naver Blog posts and Google Maps reviews"
                    : "출처: 네이버 블로그 후기 · 구글 지도 리뷰"}
                </span>
                <span className="block">
                  {language === "en" ? "About 30 reviews per place" : "장소별 약 30건"}
                </span>
              </p>
            )}
          </span>
        </div>
        <table
          className="w-full table-fixed text-left text-sm"
          aria-label={
            language === "en" ? "Visitor preference tags by place" : "장소별 방문자 취향 태그"
          }
        >
          <thead className="sr-only">
            <tr>
              <th className="w-1/3">{language === "en" ? "Place" : "장소"}</th>
              <th>{language === "en" ? "Tags" : "취향 태그"}</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border/60">
            {taggedItems.map((item) => (
              <tr key={item.place_id}>
                <th scope="row" className="w-1/3 px-4 py-3 align-top text-[13px] font-medium text-ink">
                  {item.name}
                </th>
                <td className="px-4 py-2.5">
                  {/*
                    **좁은 화면에서는 태그가 다음 줄로 내려간다(2026-09-16).**

                    전에는 flex-nowrap이었다. 표가 table-fixed에 장소 칸이 w-1/3이라
                    태그 칸 너비가 화면에 따라 고정되는데, 칩은 whitespace-nowrap이라
                    줄바꿈도 축소도 하지 않는다. 그래서 넘친 만큼을 바깥 카드의
                    overflow-hidden(둥근 모서리용)이 **말없이 잘라냈다.** 스크롤도
                    안 되니 둘째 태그를 볼 방법이 아예 없었다.

                    실측(태그 문구 실제 값 기준):
                      기기 390px → 태그 칸 205px, 칩 247~253px, 20~31px 잘림
                      기기 360px → 185px 칸에 40~51px 잘림
                      기기 320px → 159px 칸에 66~77px 잘림
                    390px에서 이미 모든 행의 둘째 태그가 잘렸다.

                    줄바꿈을 허용하면 280px까지 넘침이 0이다. 대가는 높이다 —
                    행 44px → 74px(3행 기준 표 133px → 222px). 장소 칸을 좁혀
                    한 줄에 맞추는 안도 재봤지만, w-1/5까지 줄여야 들어가고
                    그러면 장소명이 서너 줄로 깨져서 접었다.
                  */}
                  <div className="flex flex-wrap gap-1.5">
                    {item.preference_tags?.slice(0, 2).map((tag) => (
                      <span
                        key={tag.code}
                        data-query-match={tag.is_query_match ? "true" : undefined}
                        /*
                          질문에 걸린 태그만 브랜드 색으로 **채운다**(2026-09-16).
                          나머지는 바탕도 테두리도 없이 브랜드 색 글자만 남는다.
                          색은 한 가지고 채움 여부만 다르므로 "이 줄에서 무엇이
                          답인가"가 한눈에 읽힌다.

                          전에는 걸린 것이 옅은 브랜드 배경, 나머지가 하늘색 배경이라
                          색이 두 갈래로 갈렸다.
                        */
                        className={`flex items-center gap-1 whitespace-nowrap rounded-full px-2.5 py-1 text-[11px] font-semibold ${
                          tag.is_query_match ? "bg-brand text-white" : "bg-white text-brand"
                        }`}
                      >
                        {tag.label}
                        {/*
                          개수는 칩의 색과 굵기를 그대로 물려받는다. 전에는 회색
                          가는 글씨였는데, 채운 칩 위에서 대비가 떨어져 읽히지 않았다.
                          따로 감싸 두는 것은 개수만 집어낼 수 있게 하기 위해서다.
                        */}
                        <span>({tag.mention_count})</span>
                      </span>
                    ))}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
