"""`fetch_place_rows`의 페이지 넘김.

2026-08-28 강남구 스냅샷 한 번이 areaBasedList2 일일 한도 1,000회를 통째로 태웠다.
종료 조건이 `page_no * numOfRows >= totalCount` 하나뿐이었는데, TourAPI는 마지막
쪽을 지나면 numOfRows를 0으로 주므로 `page_no * 0`은 totalCount에 영원히 닿지
못한다. 빈 응답이라 100ms에 돌아오고, 멈춘 것은 429뿐이었다.

`fetch_place_rows`는 그때까지 테스트에서 늘 monkeypatch로 대체돼 있어서
(test_dev_routes.py 7곳) 페이지 넘김이 한 번도 실행된 적이 없었다. 1,000건을
넘는 구가 강남구가 처음이라 2쪽을 부를 일 자체도 없었다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import pytest

from app.domain.models import TourPlacePage, TourPlaceRecord
from app.errors import ProviderUnavailableError
from app.services import place_snapshot
from app.services.place_snapshot import KST, LIST_PAGE_SIZE, fetch_place_rows

FETCHED_AT = datetime(2026, 8, 28, 22, 23, 0, tzinfo=KST)


def _record(index: int) -> TourPlaceRecord:
    return TourPlaceRecord(
        content_id=str(index),
        content_type_id="12",
        title=f"장소 {index}",
        address="서울특별시 강남구",
        latitude=37.5,
        longitude=127.0,
        area_code="11",
        district_code="680",
        lcls_systm1="VE",
        lcls_systm2="VE01",
        lcls_systm3="VE010100",
        source_modified_at=None,
    )


@dataclass
class _FakeProvider:
    """페이지 응답을 정해둔 순서대로 돌려주는 Provider.

    정해둔 응답이 떨어진 뒤에도 계속 불리면 그것이 곧 무한 루프이므로, 조용히
    빈 페이지를 주는 대신 실패시킨다 — 테스트가 영원히 끝나지 않는 것보다
    "몇 번째에서 넘쳤다"가 드러나는 편이 낫다.
    """

    pages: list[TourPlacePage]
    requested_page_numbers: list[int] = field(default_factory=list)

    async def list_places_by_area(
        self,
        area_code: str,
        district_code: str,
        page_no: int,
        num_of_rows: int = 100,
    ) -> TourPlacePage:
        self.requested_page_numbers.append(page_no)
        if len(self.requested_page_numbers) > len(self.pages):
            raise AssertionError(
                f"정해둔 {len(self.pages)}쪽보다 많이 불렸다 "
                f"(pageNo={self.requested_page_numbers})"
            )
        return self.pages[len(self.requested_page_numbers) - 1]


def _install(monkeypatch: pytest.MonkeyPatch, pages: list[TourPlacePage]) -> _FakeProvider:
    provider = _FakeProvider(pages)
    monkeypatch.setattr(
        place_snapshot, "RealPlaceProvider", lambda **_kwargs: provider
    )
    return provider


async def _fetch() -> dict[str, dict[str, str]]:
    # client와 api_key는 가짜 Provider가 무시하므로 자리만 채운다.
    return await fetch_place_rows(None, "key", "11", "680", FETCHED_AT)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_single_page_calls_the_list_api_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """한 쪽에 다 들어오면 호출은 1회로 끝난다. 지금까지의 모든 구가 이 경우다."""
    pages = [
        TourPlacePage(
            page_no=1,
            num_of_rows=409,
            total_count=409,
            places=tuple(_record(i) for i in range(409)),
        )
    ]
    provider = _install(monkeypatch, pages)

    rows = await _fetch()

    assert provider.requested_page_numbers == [1]
    assert len(rows) == 409


@pytest.mark.asyncio
async def test_last_page_reporting_its_own_row_count_still_terminates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """마지막 쪽의 numOfRows가 요청값이 아니라 실제 건수로 와도 멈춘다.

    옛 조건 `page_no * numOfRows >= totalCount`는 여기서 `2 * 49 = 98 >= 1049`가
    거짓이라 3쪽을 부르고, 3쪽은 빈 응답이라 다시 거짓이 되어 끝나지 않았다.
    """
    total = LIST_PAGE_SIZE + 49
    pages = [
        TourPlacePage(
            page_no=1,
            num_of_rows=LIST_PAGE_SIZE,
            total_count=total,
            places=tuple(_record(i) for i in range(LIST_PAGE_SIZE)),
        ),
        TourPlacePage(
            page_no=2,
            num_of_rows=49,
            total_count=total,
            places=tuple(_record(LIST_PAGE_SIZE + i) for i in range(49)),
        ),
    ]
    provider = _install(monkeypatch, pages)

    rows = await _fetch()

    assert provider.requested_page_numbers == [1, 2]
    assert len(rows) == total


@pytest.mark.asyncio
async def test_empty_page_stops_the_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    """받은 게 없으면 멈춘다. 이것이 무한 루프를 실제로 막는 조건이다.

    마지막 쪽을 지나면 TourAPI는 items를 빈 문자열로 주고 numOfRows도 0으로 준다.
    """
    total = LIST_PAGE_SIZE + 49
    pages = [
        TourPlacePage(
            page_no=1,
            num_of_rows=LIST_PAGE_SIZE,
            total_count=total,
            places=tuple(_record(i) for i in range(LIST_PAGE_SIZE)),
        ),
        # 2쪽이 49건이 아니라 빈 응답으로 온다 — 받은 건수가 total_count에 못
        # 미치므로 누적 판정만으로는 멈추지 못하는 자리다.
        TourPlacePage(page_no=2, num_of_rows=0, total_count=total, places=()),
    ]
    provider = _install(monkeypatch, pages)

    rows = await _fetch()

    assert provider.requested_page_numbers == [1, 2]
    assert len(rows) == LIST_PAGE_SIZE


@pytest.mark.asyncio
async def test_pages_that_never_fill_total_count_raise_instead_of_looping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """빈 쪽도 안 오고 누적도 안 차면 쪽수 상한에서 예외로 끊는다.

    조용히 멈추지 않는 이유는, 여기 닿았다는 것이 곧 "목록을 다 못 받았다"라서다.
    그대로 스냅샷으로 저장하면 못 받은 장소가 다음 대조에서 삭제로 잡힌다.
    """
    total = LIST_PAGE_SIZE * 2
    # 쪽마다 1건씩만 주면서 total_count는 2,000이라고 우기는 응답. 상한이 없으면
    # 2,000쪽을 부른다.
    pages = [
        TourPlacePage(
            page_no=page_no,
            num_of_rows=1,
            total_count=total,
            places=(_record(page_no),),
        )
        for page_no in range(1, 4)
    ]
    provider = _install(monkeypatch, pages)

    with pytest.raises(ProviderUnavailableError) as excinfo:
        await _fetch()

    # total_count 2,000이면 상한은 2쪽이다.
    assert provider.requested_page_numbers == [1, 2]
    assert excinfo.value.details == {
        "upstream_detail": "areaBasedList2 returned 2 of 2000 places in 2 pages"
    }


@pytest.mark.asyncio
async def test_total_count_growing_mid_fetch_extends_the_page_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """조회 도중 total_count가 늘면 쪽수 상한도 함께 는다.

    상한을 첫 쪽 값으로 고정하면, 그 사이 늘어난 장소를 담은 뒷쪽을 못 받고
    예외로 끝난다.
    """
    # 1쪽 시점의 total_count 1,500이면 상한은 2쪽이다. 2쪽에서 2,500으로 늘어나
    # 상한이 3쪽으로 함께 늘어야 3쪽을 받을 수 있다.
    pages = [
        TourPlacePage(
            page_no=1,
            num_of_rows=LIST_PAGE_SIZE,
            total_count=LIST_PAGE_SIZE + 500,
            places=tuple(_record(i) for i in range(LIST_PAGE_SIZE)),
        ),
        TourPlacePage(
            page_no=2,
            num_of_rows=LIST_PAGE_SIZE,
            total_count=LIST_PAGE_SIZE * 2 + 500,
            places=tuple(
                _record(LIST_PAGE_SIZE + i) for i in range(LIST_PAGE_SIZE)
            ),
        ),
        TourPlacePage(
            page_no=3,
            num_of_rows=500,
            total_count=LIST_PAGE_SIZE * 2 + 500,
            places=tuple(
                _record(LIST_PAGE_SIZE * 2 + i) for i in range(500)
            ),
        ),
    ]
    provider = _install(monkeypatch, pages)

    rows = await _fetch()

    assert provider.requested_page_numbers == [1, 2, 3]
    assert len(rows) == LIST_PAGE_SIZE * 2 + 500


@pytest.mark.asyncio
async def test_empty_first_page_with_a_nonzero_total_count_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """한 건도 못 받았는데 totalCount가 0이 아니면 빈 스냅샷을 만들지 않는다.

    빈 스냅샷을 저장하면 다음 대조에서 그 구가 전량 삭제로 잡힌다.
    """
    pages = [TourPlacePage(page_no=1, num_of_rows=0, total_count=409, places=())]
    provider = _install(monkeypatch, pages)

    with pytest.raises(ProviderUnavailableError) as excinfo:
        await _fetch()

    assert provider.requested_page_numbers == [1]
    assert excinfo.value.details == {
        "upstream_detail": "areaBasedList2 returned no places for totalCount 409"
    }


@pytest.mark.asyncio
async def test_no_places_at_all_calls_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """장소가 0건인 구도 첫 쪽 1회로 끝난다."""
    pages = [TourPlacePage(page_no=1, num_of_rows=0, total_count=0, places=())]
    provider = _install(monkeypatch, pages)

    rows = await _fetch()

    assert provider.requested_page_numbers == [1]
    assert rows == {}
