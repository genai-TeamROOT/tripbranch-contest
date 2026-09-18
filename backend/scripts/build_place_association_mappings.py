"""연관 관광지 원본(collect_place_associations.py 출력)을 places.content_id에
이름+구 기준으로 매칭해 매핑 CSV를 만든다.

역할: TarRlteTarService1 응답의 tAtsCd/rlteTatsCd는 32자리 해시코드로 TourAPI
표준 content_id 체계와 다르다. place_concentration_mappings(D-043/D-057)가
같은 문제(집중률 API도 장소 고유 ID가 없어 이름으로만 매칭 가능)를 exact/
normalized/manual 매칭 + 구 단위 필터로 이미 풀어낸 전례가 있어 그 패턴을
그대로 재사용한다.

집중률 매칭과 다른 점: 집중률 API는 이름 문자열만 주지만, 이 API는 각 이름에
signguCd(5자리, "11"+구 3자리)가 함께 붙어 있다. 그래서 구가 다른 동명이인
장소를 이름만으로 잘못 붙이는 사고(EXP-01 교훈)를 애초에 구 필터로 막을 수 있다
— 정규화 매칭이라도 같은 구 안에서만 후보를 찾는다.

입력: --input-jsonl(collect_place_associations.py 출력, 생략하면
      supabase/data/place_associations_raw_*.jsonl 중 최신 파일 사용)
      --places-snapshot(없으면 Supabase places에서 활성 장소를 읽는다)
출력: supabase/data/place_association_mapping_<오늘>.csv
      + 매칭 실패 목록(구 커버리지 밖 vs 같은 구인데 이름 불일치로 구분)을
        표준 출력에 나열한다.
호출 시점: `python -m scripts.build_place_association_mappings`로 수동 실행한다.

매칭은 보수적으로 한다 — 정확 일치와 규칙 기반 정규화 일치만 자동으로 붙이고,
편집거리 같은 유사도 매칭은 쓰지 않는다(build_concentration_mappings.py와 같은
원칙). tAtsCd 하나는 원본에서 base 장소로도, 다른 행의 연관 장소로도 등장할 수
있어 코드 단위로 먼저 중복 제거한 뒤 매칭한다.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from app.config import Settings

_KST = ZoneInfo("Asia/Seoul")
_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "supabase" / "data"

_BRACKET_PATTERN = re.compile(r"\s*\[[^\]]*\]")
_PAREN_PATTERN = re.compile(r"\s*\([^)]*\)")
_SEOUL_PREFIX = "서울 "


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="연관 관광지 코드 ↔ content_id 매핑 CSV 생성")
    parser.add_argument(
        "--input-jsonl",
        type=Path,
        help="collect_place_associations.py 출력 JSONL. 생략하면 최신 파일 자동 탐색.",
    )
    parser.add_argument(
        "--places-snapshot",
        type=Path,
        help="content_id,title,district_code 컬럼을 가진 CSV. 생략하면 Supabase에서 읽는다.",
    )
    parser.add_argument(
        "--output-dir", type=Path, default=_DATA_DIR, help="CSV 저장 디렉터리"
    )
    return parser


def _normalize_key(name: str) -> str:
    """비교용 키. 공백을 지우고 소문자로 맞춘다(build_concentration_mappings.py와 동일)."""
    return name.replace(" ", "").casefold()


_MIN_SUBSTRING_KEY_LENGTH = 2


def _stripped_key(name: str) -> str:
    """괄호/대괄호 부기를 뗀 뒤 정규화한 키. 포함 관계(부분 문자열) 비교 전용이다.

    '창덕궁'과 '창덕궁과 후원 [유네스코 세계유산]'처럼 한쪽이 다른 쪽의 부분
    문자열인 경우를 잡는다 — _variants()는 완전히 같은 문자열이 될 때만
    붙이므로 이 경우를 못 잡는다.
    """
    stripped = _PAREN_PATTERN.sub("", _BRACKET_PATTERN.sub("", name)).strip()
    return _normalize_key(stripped or name)


def _variants(name: str) -> list[str]:
    """이름에서 파생되는 비교 후보. 원본을 먼저 두고 정규화본을 뒤에 붙인다."""
    candidates = [name]
    stripped = _PAREN_PATTERN.sub("", _BRACKET_PATTERN.sub("", name)).strip()
    if stripped and stripped != name:
        candidates.append(stripped)
    for base in list(candidates):
        if base.startswith(_SEOUL_PREFIX):
            candidates.append(base[len(_SEOUL_PREFIX) :].strip())
    seen: set[str] = set()
    unique: list[str] = []
    for candidate in candidates:
        if candidate and candidate not in seen:
            seen.add(candidate)
            unique.append(candidate)
    return unique


@dataclass(frozen=True)
class AssociationNode:
    """연관 관광지 원본의 장소 하나(기준 관광지 쪽이든 연관 관광지 쪽이든 동일하게 다룬다)."""

    code: str
    name: str
    signgu_cd: str  # 5자리, 예: "11110"

    @property
    def district_code(self) -> str:
        """places.district_code(3자리)와 대응되는 값. signguCd는 항상 '11'+3자리다."""
        return self.signgu_cd[-3:]


@dataclass(frozen=True)
class PlaceRow:
    content_id: str
    title: str
    district_code: str


@dataclass(frozen=True)
class MatchedAssociation:
    code: str
    name: str
    district_code: str
    content_id: str
    place_title: str
    match_method: str


def find_latest_input_jsonl(data_dir: Path) -> Path | None:
    candidates = sorted(data_dir.glob("place_associations_raw_*.jsonl"))
    return candidates[-1] if candidates else None


def parse_association_nodes(path: Path) -> dict[str, AssociationNode]:
    """JSONL을 읽어 코드 단위로 중복 제거한 노드 딕셔너리를 만든다.

    한 행에 기준 관광지(tAtsCd)와 연관 관광지(rlteTatsCd) 두 장소가 같이 들어있고,
    같은 코드가 다른 행에서는 반대 역할로도 등장할 수 있어 코드를 키로 병합한다.
    """
    nodes: dict[str, AssociationNode] = {}
    with path.open(encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if not line:
                continue
            item = json.loads(line)
            base_code = str(item.get("tAtsCd") or "")
            if base_code and base_code not in nodes:
                nodes[base_code] = AssociationNode(
                    code=base_code,
                    name=str(item.get("tAtsNm") or ""),
                    signgu_cd=str(item.get("signguCd") or ""),
                )
            rlte_code = str(item.get("rlteTatsCd") or "")
            if rlte_code and rlte_code not in nodes:
                nodes[rlte_code] = AssociationNode(
                    code=rlte_code,
                    name=str(item.get("rlteTatsNm") or ""),
                    signgu_cd=str(item.get("rlteSignguCd") or ""),
                )
    return nodes


def load_places_from_snapshot(path: Path) -> list[PlaceRow]:
    with path.open(encoding="utf-8-sig") as fp:
        return [
            PlaceRow(
                content_id=row["content_id"].strip(),
                title=row["title"].strip(),
                district_code=row["district_code"].strip(),
            )
            for row in csv.DictReader(fp)
            if row.get("content_id") and row.get("title") and row.get("district_code")
        ]


_PLACES_PAGE_SIZE = 1000


async def load_places_from_supabase(
    settings: Settings, client: httpx.AsyncClient | None = None
) -> list[PlaceRow]:
    """활성 장소를 전부 읽는다.

    PostgREST(Supabase REST)는 쿼리에 준 limit과 무관하게 프로젝트 기본
    max-rows(1000)로 응답을 자른다(D-081에서 trace_records/response_feedback
    조회로 이미 겪은 문제). 여기서도 같은 상한에 걸리면 1000건을 넘는 구(예:
    종로구+중구만 합쳐도 1,736건)의 뒷부분이 조용히 빠진 채 매칭 대상에서
    누락된다 — limit/offset으로 페이지를 끝까지 넘겨 받는다.

    client를 주면(테스트용 MockTransport 등) 그걸 그대로 쓰고, 안 주면 이
    함수가 새로 만들고 닫는다.
    """

    async def _load(active_client: httpx.AsyncClient) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        offset = 0
        while True:
            response = await active_client.get(
                settings.supabase_url.rstrip("/") + "/rest/v1/places",
                params={
                    "select": "content_id,title,district_code",
                    "is_active": "eq.true",
                    "limit": str(_PLACES_PAGE_SIZE),
                    "offset": str(offset),
                },
                headers={"apikey": settings.supabase_secret_key},
                timeout=settings.external_api_timeout_seconds,
            )
            response.raise_for_status()
            page = response.json()
            rows.extend(page)
            if len(page) < _PLACES_PAGE_SIZE:
                break
            offset += _PLACES_PAGE_SIZE
        return rows

    if client is not None:
        rows = await _load(client)
    else:
        async with httpx.AsyncClient() as owned_client:
            rows = await _load(owned_client)

    return [
        PlaceRow(
            content_id=str(row["content_id"]),
            title=str(row["title"]),
            district_code=str(row["district_code"]),
        )
        for row in rows
        if row.get("district_code")
    ]


def match_associations(
    nodes: Iterable[AssociationNode], places: Sequence[PlaceRow]
) -> tuple[list[MatchedAssociation], list[AssociationNode], list[AssociationNode]]:
    """구 필터 안에서 정확 일치 → 정규화 일치 순으로 붙인다.

    반환값 세 번째(out_of_coverage)는 우리 places에 그 구 자체가 아직 없는
    경우다 — 이름이 안 맞은 게 아니라 애초에 그 구를 places로 동기화하지
    않아서 비교 대상이 없는 것이므로, 매칭 실패(unmatched)와 구분해야 한다.
    """
    places_by_district: dict[str, list[PlaceRow]] = {}
    for place in places:
        places_by_district.setdefault(place.district_code, []).append(place)

    matched: list[MatchedAssociation] = []
    unmatched: list[AssociationNode] = []
    out_of_coverage: list[AssociationNode] = []

    for node in nodes:
        district_places = places_by_district.get(node.district_code)
        if not district_places:
            out_of_coverage.append(node)
            continue

        by_exact = {_normalize_key(p.title): p for p in district_places}
        exact = by_exact.get(_normalize_key(node.name))
        if exact is not None:
            matched.append(
                MatchedAssociation(
                    code=node.code,
                    name=node.name,
                    district_code=node.district_code,
                    content_id=exact.content_id,
                    place_title=exact.title,
                    match_method="exact",
                )
            )
            continue

        by_variant: dict[str, PlaceRow] = {}
        for place in district_places:
            for variant in _variants(place.title):
                by_variant.setdefault(_normalize_key(variant), place)

        found: PlaceRow | None = None
        for variant in _variants(node.name):
            found = by_variant.get(_normalize_key(variant))
            if found is not None:
                break
        if found is not None:
            matched.append(
                MatchedAssociation(
                    code=node.code,
                    name=node.name,
                    district_code=node.district_code,
                    content_id=found.content_id,
                    place_title=found.title,
                    match_method="normalized",
                )
            )
            continue

        # 3단계: 부분 문자열(포함) 매칭. 유일한 후보일 때만 자동으로 붙인다 —
        # 짧은 이름이 여러 장소에 걸리면(예: "종묘" ↔ "종묘"/"종묘광장공원")
        # 자동으로 고르지 않고 unmatched로 남겨 사람이 확인하게 한다
        # (build_concentration_mappings.py와 같은 보수적 원칙).
        node_key = _stripped_key(node.name)
        candidates: list[PlaceRow] = []
        if len(node_key) >= _MIN_SUBSTRING_KEY_LENGTH:
            for place in district_places:
                place_key = _stripped_key(place.title)
                if node_key in place_key or place_key in node_key:
                    candidates.append(place)

        if len(candidates) == 1:
            only = candidates[0]
            matched.append(
                MatchedAssociation(
                    code=node.code,
                    name=node.name,
                    district_code=node.district_code,
                    content_id=only.content_id,
                    place_title=only.title,
                    match_method="substring",
                )
            )
        else:
            unmatched.append(node)

    return matched, unmatched, out_of_coverage


def write_mapping_csv(rows: Sequence[MatchedAssociation], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as fp:
        writer = csv.writer(fp)
        writer.writerow(
            [
                "tats_cd",
                "association_name",
                "district_code",
                "content_id",
                "place_title",
                "match_method",
            ]
        )
        for row in rows:
            writer.writerow(
                [
                    row.code,
                    row.name,
                    row.district_code,
                    row.content_id,
                    row.place_title,
                    row.match_method,
                ]
            )


async def run(args: argparse.Namespace, settings: Settings) -> int:
    input_path = args.input_jsonl or find_latest_input_jsonl(_DATA_DIR)
    if input_path is None or not input_path.exists():
        raise ValueError(
            "입력 JSONL을 찾을 수 없습니다. --input-jsonl로 지정하거나 먼저 "
            "collect_place_associations.py를 실행하세요."
        )
    nodes = parse_association_nodes(input_path)
    print(f"원본 노드 {len(nodes)}건 파싱: {input_path.name}")

    if args.places_snapshot is not None:
        places = load_places_from_snapshot(args.places_snapshot)
        print(f"places 스냅샷 {len(places)}건: {args.places_snapshot.name}")
    else:
        if not settings.supabase_url or not settings.supabase_secret_key:
            raise ValueError("SUPABASE_URL / SUPABASE_SECRET_KEY가 필요합니다.")
        places = await load_places_from_supabase(settings)
        print(f"Supabase 활성 장소 {len(places)}건")

    matched, unmatched, out_of_coverage = match_associations(nodes.values(), places)

    now = datetime.now(_KST)
    output = args.output_dir / f"place_association_mapping_{now:%Y%m%d}.csv"
    write_mapping_csv(matched, output)

    method_counts: dict[str, int] = {}
    for row in matched:
        method_counts[row.match_method] = method_counts.get(row.match_method, 0) + 1
    total = len(nodes)
    print(
        f"\n매칭 {len(matched)}/{total}건 "
        f"(정확 {method_counts.get('exact', 0)} / 정규화 {method_counts.get('normalized', 0)} / "
        f"부분일치 {method_counts.get('substring', 0)})"
    )
    for row in matched:
        if row.match_method != "exact":
            print(
                f"  {row.match_method}: '{row.name}' → "
                f"'{row.place_title}' ({row.district_code})"
            )

    print(f"\n구 커버리지 밖(우리 places에 해당 구 자체가 없음) {len(out_of_coverage)}건")
    by_district: dict[str, int] = {}
    for node in out_of_coverage:
        by_district[node.district_code] = by_district.get(node.district_code, 0) + 1
    for district_code, count in sorted(by_district.items()):
        print(f"  구 {district_code}: {count}건")

    print(f"\n같은 구인데 이름이 안 맞은 {len(unmatched)}건(사람 확인 필요):")
    for node in unmatched:
        print(f"  [{node.district_code}] {node.name} ({node.code})")

    print(f"\nCSV 저장: {output}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return asyncio.run(run(args, Settings()))


if __name__ == "__main__":
    raise SystemExit(main())
