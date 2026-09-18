"""TourAPI `homepage` 필드의 실제 형태를 실측한다.

상세 카드는 같은 `homepage` 값을 두 자리에 보여준다 — "관련 정보" 박스의
`answer_fields.homepage`와 하단 "공식 홈페이지 보기" 링크다. 어느 쪽을 남길지
정하려면 원문이 실제로 어떤 모양인지 알아야 한다. 두 가지를 확인한다.

1. **한 장소에 링크가 2개 이상인가.** 여러 개라면 값 안의 URL을 각각 링크로
   렌더하는 박스 쪽이 단일 `href`를 쓰는 하단 링크보다 정확하다.
2. **원문이 HTML(`<a href=...>`)로 오는가.** `info_field_rules.clean_text()`가
   태그를 벗기므로, HTML이면 href가 사라지고 링크 텍스트만 남는다. 그러면 하단
   링크의 `href`가 URL이 아니게 되어 클릭해도 열리지 않는다(잠재 버그).
   `docs/package-a/info-question-types-handoff.md`가 "HTML 태그 제거"라고 적어둔
   것이 이 경우를 가리킨다.

`places` 테이블에는 `homepage` 컬럼이 없다(동기화 대상이 아니다). 그래서 저장소에서
content_id만 읽고 detailCommon2를 장소마다 호출한다 — TourAPI 일일·초당 한도를
쓰므로 `--delay`로 간격을 둔다.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import re
from pathlib import Path

import httpx

from app.agent_context.info_field_rules import clean_text
from app.config import Settings
from app.providers.real_place import RealPlaceProvider
from app.repositories.supabase_places import SupabasePlaceRepository

# href="..." / href='...' 안의 값.
_HREF_PATTERN = re.compile(r"""href\s*=\s*["']([^"']+)["']""", re.IGNORECASE)
# 태그 밖에 그냥 적힌 URL.
_BARE_URL_PATTERN = re.compile(r"""https?://[^\s<>"'()]+""", re.IGNORECASE)
_TAG_PATTERN = re.compile(r"<[^>]+>")

_OUTPUT = Path("test_results/place_homepage_analysis.csv")


def _urls_in(raw: str) -> list[str]:
    """원문에서 URL을 전부 뽑는다. href 우선, 태그를 지운 뒤 평문 URL도 더한다."""
    urls = _HREF_PATTERN.findall(raw)
    without_tags = _TAG_PATTERN.sub(" ", raw)
    urls.extend(_BARE_URL_PATTERN.findall(without_tags))
    seen: list[str] = []
    for url in urls:
        normalized = url.strip().rstrip(".,)")
        if normalized and normalized not in seen:
            seen.append(normalized)
    return seen


async def _collect(
    settings: Settings, limit: int | None, delay: float
) -> list[dict[str, object]]:
    if not settings.tour_api_service_key:
        raise ValueError("TOUR_API_SERVICE_KEY가 필요합니다.")
    if not settings.supabase_url or not settings.supabase_secret_key:
        raise ValueError("SUPABASE_URL / SUPABASE_SECRET_KEY가 필요합니다.")

    async with httpx.AsyncClient() as client:
        provider = RealPlaceProvider(
            api_key=settings.tour_api_service_key,
            client=client,
            timeout_seconds=settings.external_api_timeout_seconds,
        )
        repository = SupabasePlaceRepository(
            supabase_url=settings.supabase_url,
            secret_key=settings.supabase_secret_key,
            client=client,
            timeout_seconds=settings.external_api_timeout_seconds,
        )

        states = await repository.get_region_place_states(
            settings.place_sync_area_code, settings.place_sync_district_code
        )
        content_ids = sorted(
            content_id for content_id, state in states.items() if state.is_active
        )
        if limit is not None:
            content_ids = content_ids[:limit]
        print(f"대상 장소 {len(content_ids)}건 (활성)")

        rows: list[dict[str, object]] = []
        for index, content_id in enumerate(content_ids, start=1):
            try:
                result = await provider.get_common_details(content_id)
                raw = result.data.homepage
                error = None
            except Exception as exc:  # noqa: BLE001 - 실 API 분석 스크립트
                raw, error = None, f"{type(exc).__name__}: {exc}"

            raw_text = (raw or "").strip()
            urls = _urls_in(raw_text) if raw_text else []
            cleaned = clean_text(raw_text) if raw_text else None
            # clean_text가 지나간 뒤에도 URL이 남는가 = 하단 링크가 살아있는가.
            cleaned_has_url = bool(cleaned and _BARE_URL_PATTERN.search(cleaned))

            rows.append(
                {
                    "content_id": content_id,
                    "has_homepage": bool(raw_text),
                    "is_html": "<" in raw_text and ">" in raw_text,
                    "href_count": len(_HREF_PATTERN.findall(raw_text)),
                    "url_count": len(urls),
                    "cleaned_has_url": cleaned_has_url,
                    "raw": raw_text,
                    "cleaned": cleaned or "",
                    "error": error or "",
                }
            )
            if index % 50 == 0:
                print(f"  {index}/{len(content_ids)}", flush=True)
            await asyncio.sleep(delay)
    return rows


def _report(rows: list[dict[str, object]]) -> None:
    total = len(rows)
    errors = [r for r in rows if r["error"]]
    with_home = [r for r in rows if r["has_homepage"]]
    html_rows = [r for r in with_home if r["is_html"]]
    multi = [r for r in with_home if int(r["url_count"]) >= 2]
    lost_url = [r for r in with_home if not r["cleaned_has_url"]]

    print(f"\n{'=' * 70}")
    print(f"조회 {total}건 (오류 {len(errors)}건)")
    print(f"homepage 있음        {len(with_home)}건")
    if not with_home:
        return
    pct = lambda n: f"{n / len(with_home) * 100:.1f}%"  # noqa: E731
    print(f"  HTML 형태          {len(html_rows)}건 ({pct(len(html_rows))})")
    print(f"  링크 2개 이상      {len(multi)}건 ({pct(len(multi))})")
    print("  URL 개수 분포      ", end="")
    counts: dict[int, int] = {}
    for row in with_home:
        counts[int(row["url_count"])] = counts.get(int(row["url_count"]), 0) + 1
    print(", ".join(f"{k}개={v}건" for k, v in sorted(counts.items())))
    print(
        f"  clean_text 후 URL 소실  {len(lost_url)}건 ({pct(len(lost_url))})"
        "  ← 하단 '공식 홈페이지 보기' 링크가 깨지는 케이스"
    )

    if multi:
        print("\n--- 링크 2개 이상 샘플 (최대 5건) ---")
        for row in multi[:5]:
            print(f"  [{row['content_id']}] url_count={row['url_count']}")
            print(f"    raw    : {str(row['raw'])[:110]}")
            print(f"    cleaned: {str(row['cleaned'])[:110]}")

    if lost_url:
        print("\n--- clean_text 후 URL이 사라진 샘플 (최대 5건) ---")
        for row in lost_url[:5]:
            print(f"  [{row['content_id']}]")
            print(f"    raw    : {str(row['raw'])[:110]}")
            print(f"    cleaned: {str(row['cleaned'])[:110]}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=None, help="조회할 장소 수(기본: 전체)")
    parser.add_argument("--delay", type=float, default=0.15, help="호출 간 대기(초)")
    args = parser.parse_args()

    settings = Settings()
    rows = asyncio.run(_collect(settings, args.limit, args.delay))

    _OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with _OUTPUT.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    _report(rows)
    print(f"\n원자료: {_OUTPUT}")


if __name__ == "__main__":
    main()
