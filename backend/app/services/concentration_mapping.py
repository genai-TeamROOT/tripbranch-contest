"""집중률 API 장소명과 `places`를 붙이는 매칭 로직.

역할: 집중률 API가 쓰는 장소명을 모아 우리 DB의 장소와 짝지어 매핑 후보를 만든다.
입력: 지역·시군구 코드, 수동 지정 파일, 거절 목록.
출력: 매칭된 행(MappingRow), 못 붙인 장소, 쓰이지 않은 집중률 이름.
호출 시점: `scripts/build_concentration_mappings.py` CLI와 개발자 Ops 패널.

집중률 API의 장소명과 TourAPI 장소명이 달라(예: `서울 운현궁` vs `운현궁`) 매핑
테이블에 이름이 있어도 조회가 실패하는 문제를 줄이는 것이 목적이다.

매칭은 보수적으로 한다 — 정확 일치와 규칙 기반 정규화 일치만 자동으로 붙이고,
편집거리 같은 유사도 매칭은 쓰지 않는다. 이름이 크게 다른 장소를 잘못 붙이면 엉뚱한
곳의 혼잡도를 답하게 되므로, 애매한 항목은 사람이 판단하도록 남긴다(D-043).

CLI에 있던 것을 여기로 옮겼다. `app`이 `scripts`를 임포트한 적이 없고 의존 방향이
반대(scripts → app)라, 라우트가 스크립트를 부르면 방향이 뒤집힌다. 스크립트는 이
모듈을 재수출해 기존 호출부와 테스트를 그대로 둔다.
"""

from __future__ import annotations

import csv
import json
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from app.config import Settings
from app.observability.api_usage import create_external_client
from app.providers.concentration import _CONCENTRATION_URL

_KST = ZoneInfo("Asia/Seoul")
DATA_DIR = Path(__file__).resolve().parent.parent.parent.parent / "supabase" / "data"
_PAGE_SIZE = 100
# 응답 한 페이지에 장소별 날짜 예보가 여러 행 들어와, 100행에 장소는 4곳 정도만 담긴다.
# 전체 장소명을 모으려면 totalCount까지 끝까지 넘겨야 한다.
_MAX_PAGES = 200

_PLACE_NAME_KEYS = ("tAtsNm", "tatsNm", "touristAttractionName")

# 규칙으로 못 붙이는 짝을 사람이 확인해 적어두는 파일. 표기가 크게 달라(예:
# "낙산묘각사" ↔ "묘각사(서울)") 자동 유사도 매칭으로는 위험한 항목을 담는다.
DEFAULT_OVERRIDES = DATA_DIR / "concentration_manual_overrides.csv"

# 사람이 "이건 붙이지 마라"고 판정한 짝. overrides가 강제로 붙이는 목록이면 이건
# 강제로 떼는 목록이다. 화면에서 애매한 후보의 체크를 풀면 여기 쌓인다.
#
# 파일에 남기지 않으면 다음 생성 때 같은 후보가 다시 올라와 매번 같은 판정을
# 되풀이해야 한다. DB가 아니라 파일로 두는 이유는 overrides와 짝이 맞아야 하고,
# 매핑 생성 자체가 CSV를 만드는 흐름이라 판정도 같은 자리에 있는 편이 읽힌다.
DEFAULT_REJECTIONS = DATA_DIR / "concentration_rejections.csv"

# 표기 차이 정규화 규칙. 실측(2026-08-03)에서 실패한 30건의 패턴을 반영한다.
_BRACKET_PATTERN = re.compile(r"\s*\[[^\]]*\]")
_PAREN_PATTERN = re.compile(r"\s*\([^)]*\)")
_SEOUL_PREFIX = "서울 "
_WHITESPACE_PATTERN = re.compile(r"\s")

# 모양이 같아 사람 눈에는 구분되지 않지만 코드포인트가 다른 문자. 한쪽으로 모아
# 비교한다. 2026-08-26 중구 실측: 집중률 API `초전섬유ㆍ퀼트박물관`(아래아 U+318D)과
# places `초전섬유·퀼트박물관`(가운뎃점 U+00B7)이 이것 때문에 안 붙었다.
_EQUIVALENT_CHARACTERS = {
    "ㆍ": "·",  # U+318D HANGUL LETTER ARAEA → U+00B7 MIDDLE DOT
    ".": "·",  # 나열 구분에 마침표를 쓰기도 한다. 집중률 API `국립4.19민주묘지`와
    #           places `국립4·19민주묘지`가 이것 때문에 안 붙었다(2026-08-26 강북구).
}

# 괄호 기호 자체. 안 내용을 지우는 _PAREN_PATTERN과 달리 기호만 공백으로 바꾼다.
_BRACKET_CHARACTERS = "()[]"

# 낱말이 한 글자도 없는 토큰을 걸러낼 때 쓴다.
_WORD_CHARACTER_PATTERN = re.compile(r"\w")


@dataclass(frozen=True)
class PlaceRow:
    content_id: str
    title: str


@dataclass(frozen=True)
class ManualOverride:
    """규칙으로 못 붙이는 짝을 사람이 지정한 값.

    primary가 비어 있으면 매칭은 규칙에 맡기고 별칭만 덧붙인다 — 집중률 API에
    "청와대 앞길"과 "청와대"가 모두 있는 것처럼, 정확 일치를 살리면서 다른 표기도
    함께 인정해야 하는 경우가 있다.
    """

    primary: str | None
    aliases: tuple[str, ...]


def load_manual_overrides(path: Path) -> dict[str, ManualOverride]:
    """places 장소명 → 수동 지정값."""
    if not path.exists():
        return {}
    overrides: dict[str, ManualOverride] = {}
    with path.open(encoding="utf-8-sig") as fp:
        for row in csv.DictReader(fp):
            place_title = (row.get("place_title") or "").strip()
            if not place_title:
                continue
            primary = (row.get("concentration_title") or "").strip() or None
            aliases = tuple(
                alias.strip()
                for alias in (row.get("concentration_aliases") or "").split("|")
                if alias.strip()
            )
            if primary is None and not aliases:
                continue
            overrides[place_title] = ManualOverride(primary=primary, aliases=aliases)
    return overrides


@dataclass(frozen=True)
class Rejection:
    """붙이지 않기로 한 (장소, 집중률 이름) 짝."""

    place_title: str
    concentration_title: str
    note: str = ""


def rejection_key(place_title: str, concentration_title: str) -> tuple[str, str]:
    """거절 판정을 찾는 키. 장소와 집중률 이름을 함께 본다.

    장소만으로 거절하면 그 장소는 영영 어떤 이름에도 못 붙는다. 실제로 막고 싶은
    것은 "이 장소를 이 이름에 붙이는 것"이라, 나중에 집중률 API에 맞는 이름이
    생기면 그건 다시 후보로 올라와야 한다.
    """
    return (place_title.strip(), concentration_title.strip())


def load_rejections(path: Path) -> dict[tuple[str, str], Rejection]:
    """거절 목록. 파일이 없으면 빈 사전이다."""
    if not path.exists():
        return {}
    rejections: dict[tuple[str, str], Rejection] = {}
    with path.open(encoding="utf-8-sig") as fp:
        for row in csv.DictReader(fp):
            place_title = (row.get("place_title") or "").strip()
            concentration_title = (row.get("concentration_title") or "").strip()
            if not place_title or not concentration_title:
                continue
            rejections[rejection_key(place_title, concentration_title)] = Rejection(
                place_title=place_title,
                concentration_title=concentration_title,
                note=(row.get("note") or "").strip(),
            )
    return rejections


def append_rejections(rejections: Sequence[Rejection], path: Path) -> list[Rejection]:
    """새로 거절한 짝을 파일 끝에 덧붙인다. 실제로 추가된 것만 돌려준다.

    이미 있는 짝은 다시 쓰지 않는다 — 같은 판정을 두 번 눌러도 파일이 늘지 않아야
    한다. 파일이 없으면 머리글과 함께 만든다.
    """
    existing = load_rejections(path)
    fresh = [
        rejection
        for rejection in rejections
        if rejection_key(rejection.place_title, rejection.concentration_title)
        not in existing
    ]
    if not fresh:
        return []
    path.parent.mkdir(parents=True, exist_ok=True)
    is_new = not path.exists()
    with path.open("a", newline="", encoding="utf-8-sig") as fp:
        writer = csv.writer(fp)
        if is_new:
            writer.writerow(["place_title", "concentration_title", "note"])
        for rejection in fresh:
            writer.writerow(
                [rejection.place_title, rejection.concentration_title, rejection.note]
            )
    return fresh


def load_names_file(path: Path) -> list[str]:
    with path.open(encoding="utf-8-sig") as fp:
        return [
            row["concentration_title"].strip()
            for row in csv.DictReader(fp)
            if row.get("concentration_title")
        ]


def write_names_file(names: Sequence[str], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as fp:
        writer = csv.writer(fp)
        writer.writerow(["concentration_title"])
        for name in names:
            writer.writerow([name])


def _normalize_key(name: str) -> str:
    """비교용 키. 같은 문자의 다른 표기를 모으고, 공백을 지우고 소문자로 맞춘다."""
    unified = name
    for variant, canonical in _EQUIVALENT_CHARACTERS.items():
        unified = unified.replace(variant, canonical)
    return unified.replace(" ", "").casefold()


def _variants(name: str) -> list[str]:
    """이름에서 파생되는 비교 후보. 원본을 먼저 두고 정규화본을 뒤에 붙인다."""
    candidates = [name]
    stripped = _PAREN_PATTERN.sub("", _BRACKET_PATTERN.sub("", name)).strip()
    if stripped and stripped != name:
        candidates.append(stripped)
    # 괄호 안이 부기가 아니라 이름의 일부인 경우가 있다 — 집중률 API
    # `서울시립미술관(서소문본관)`과 places `서울시립미술관 서소문본관`은 괄호를
    # 공백처럼 쓴 같은 이름이다. 안 내용을 지우면 `서울시립미술관`이 되어 어긋난다.
    unwrapped = name
    for character in _BRACKET_CHARACTERS:
        unwrapped = unwrapped.replace(character, " ")
    unwrapped = " ".join(unwrapped.split())
    if unwrapped and unwrapped != name:
        candidates.append(unwrapped)
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


def derive_search_key(canonical: str, names: Sequence[str]) -> tuple[str, str]:
    """집중률 조회에 실제로 쓸 검색어와 그 사유를 고른다.

    tAtsNm은 부분 일치 검색인데, 공백이 든 값을 넘기면 무엇을 넣든 0건이 돌아온다
    (2026-08-04 실측: `운현궁`→30건, `서울 운현궁`→0건). 그래서 공백이 있는 이름은
    공백 없는 토큰으로 바꿔야 조회가 된다.

    토큰은 "가장 긴 것"을 고른다. 유일하기만 하면 `한옥` 같은 짧은 토큰도 지금은
    통과하지만, 집중률 API에 장소가 추가되면 다른 곳까지 끌어올 수 있어서다. 검색어가
    여러 장소를 끌어오면 enrichment_service가 이름이 안 맞을 때 첫 예보로 폴백하므로
    엉뚱한 장소의 혼잡도를 조용히 답하게 된다.

    유일성은 집중률 API 목록 안에서만 따진다 — tAtsNm이 그 데이터셋만 검색하기
    때문이다. `places`에 같은 낱말을 쓰는 장소가 많아도(예: "한옥" 25건) 무관하다.
    다만 이 계산은 수집한 목록이 완전하다는 전제에 기대므로, 매핑을 만들 때마다
    목록을 다시 받아 새로 계산해야 한다.
    """
    if not _WHITESPACE_PATTERN.search(canonical):
        return canonical, "as_is"

    # 괄호·대괄호 안은 부기라 떼면 공백이 사라지는 경우가 많다("종묘 [유네스코
    # 세계유산]" → "종묘"). 남겨두면 "세계유산]" 같은 부기 토큰이 뽑혀 다른 장소까지
    # 끌어온다.
    base = _PAREN_PATTERN.sub("", _BRACKET_PATTERN.sub("", canonical)).strip() or canonical
    if _WHITESPACE_PATTERN.search(base):
        tokens = base.split()
        # 긴 토큰 우선, 길이가 같으면 뒤쪽을 쓴다. 한국어 장소명은 뒤가 핵심어라
        # ("아름다운 차박물관" → "차박물관") 그쪽이 검색어로 자연스럽다. set을 쓰면
        # 실행마다 순서가 흔들리므로 색인으로 순서를 고정한다.
        candidates = [
            tokens[index]
            for index in sorted(
                range(len(tokens)), key=lambda i: (-len(tokens[i]), -i)
            )
        ]
    else:
        candidates = [base]

    for candidate in candidates:
        if [name for name in names if candidate in name] == [canonical]:
            return candidate, "token"
    # 유일한 후보가 없다 — "종묘"가 "종묘광장공원"에도 걸리는 식이다. 그래도 여러
    # 장소가 섞여 올 뿐 0건은 아니므로, 조회가 아예 안 되는 원본보다 낫다. 걸리는
    # 장소가 가장 적은 후보를 골라 섞임을 줄이고, 정식 명칭을 별칭에 남겨 응답에서
    # 올바른 장소를 골라낼 수 있게 한다.
    hits = [(sum(c in name for name in names), c) for c in candidates]
    reachable = [(count, c) for count, c in hits if count]
    if reachable:
        return min(reachable, key=lambda item: item[0])[1], "token_ambiguous"
    return canonical, "no_unique_token"


def _candidate_tokens(canonical: str) -> list[str]:
    """검색어 후보를 순서대로 만든다. derive_search_key와 같은 기준을 쓴다.

    긴 토큰 우선, 길이가 같으면 뒤쪽을 먼저 둔다. 부기를 뗀 형태와 원본 양쪽에서
    토큰을 모으되 중복은 앞선 것을 남긴다.
    """
    base = _PAREN_PATTERN.sub("", _BRACKET_PATTERN.sub("", canonical)).strip() or canonical
    tokens: list[str] = []
    for source in (base, canonical):
        parts = source.split()
        tokens.extend(
            parts[index]
            for index in sorted(range(len(parts)), key=lambda i: (-len(parts[i]), -i))
        )
    ordered: list[str] = []
    for token in tokens:
        if not token or _WHITESPACE_PATTERN.search(token) or token in ordered:
            continue
        # 원본에서 자른 토큰에는 부기 조각이 섞인다("종묘 [유네스코 세계유산]" →
        # "[유네스코", "세계유산]"). 괄호가 한쪽만 붙은 값은 장소명이 아니라 잘린
        # 부기이므로 검색어로 쓰지 않는다.
        if any(char in token for char in _BRACKET_CHARACTERS):
            continue
        # 낱말이 한 글자도 없는 토큰은 장소명이 아니다. 부기 안의 구분 기호가
        # 그대로 토큰이 된다 — `황학동 벼룩시장 (도깨비시장 / 만물시장)`에서
        # 검색어 "/"가 나왔다(2026-08-26 중구 실측).
        if not _WORD_CHARACTER_PATTERN.search(token):
            continue
        ordered.append(token)
    return ordered


def derive_search_keys(
    canonical: str, names: Sequence[str], primary: str | None
) -> list[str]:
    """조회에 순서대로 시도할 검색어 목록을 만든다(D-057).

    1순위는 기존 검색어다. 지금 값들이 전부 정상 조회되는 것이 확인됐으므로
    휴리스틱으로 재계산해 회귀를 만들지 않는다 — 토큰 추가는 능력 추가로만 둔다.

    나머지는 `_candidate_tokens()` 순서를 따르되, 집중률 목록의 어떤 이름에도
    걸리지 않는 토큰은 뺀다. 호출해도 0건이라 시도할 값어치가 없다.

    목적은 정식 명칭과 어긋나는 발화를 받아내는 것이다 — "닭한마리 골목 혼잡해?"는
    '서울 동대문 닭한마리 골목'과 문자열이 다르지만 '닭한마리'로는 찾아진다.
    """
    keys: list[str] = []
    first = (primary or canonical).strip()
    if first and not _WHITESPACE_PATTERN.search(first):
        keys.append(first)
    for token in _candidate_tokens(canonical):
        if token in keys:
            continue
        if any(token in name for name in names):
            keys.append(token)
    return keys


def apply_search_keys(
    rows: Sequence[MappingRow], names: Sequence[str]
) -> tuple[list[MappingRow], list[MappingRow]]:
    """조회에 쓸 검색어를 채운다. 정식 명칭은 그대로 둔다.

    두 값의 역할이 다르다 - 검색어는 tAtsNm에 넣어 조회하는 값이고, 정식 명칭은
    응답에서 올바른 장소를 골라낼 때 대조하는 값이다. 정식 명칭 그대로 조회되면
    검색어를 비워 둬 호출자가 정식 명칭을 쓰게 한다.

    돌려주는 두 번째 목록은 검색어가 다른 집중률 장소까지 끌어오는 건이다. 조회는
    되지만 응답 대조가 반드시 필요하다.
    """
    applied: list[MappingRow] = []
    unresolved: list[MappingRow] = []
    for row in rows:
        key, reason = derive_search_key(row.concentration_title, names)
        search_key = None if key == row.concentration_title else key
        resolved = MappingRow(
            row.content_id,
            row.place_title,
            row.concentration_title,
            row.match_method,
            row.aliases,
            search_key=search_key,
            search_keys=tuple(
                derive_search_keys(row.concentration_title, names, search_key)
            ),
        )
        applied.append(resolved)
        if reason in ("no_unique_token", "token_ambiguous"):
            unresolved.append(resolved)
    return applied, unresolved


async def fetch_concentration_place_names(
    settings: Settings, area_code: str, district_code: str
) -> list[str]:
    """집중률 API가 다루는 장소명을 페이지 끝까지 모은다.

    계측 클라이언트로 부른다. 이 호출은 챗봇이 "지금 붐벼?"에 답할 때 쓰는 것과
    **같은 오퍼레이션**이고 일일 한도도 같다. 계측을 빼면 구 하나에 8~9회를 쓰고도
    호출량 패널에는 0으로 보여, 그날 혼잡도 한도가 왜 줄었는지 설명할 수 없다.
    """
    names: dict[str, None] = {}
    async with create_external_client() as client:
        for page_no in range(1, _MAX_PAGES + 1):
            response = await client.get(
                _CONCENTRATION_URL,
                params={
                    "serviceKey": settings.tour_api_service_key,
                    "pageNo": str(page_no),
                    "numOfRows": str(_PAGE_SIZE),
                    "MobileOS": "ETC",
                    "MobileApp": "TripBranch",
                    "areaCd": area_code,
                    "signguCd": district_code,
                    "_type": "json",
                },
                timeout=settings.external_api_timeout_seconds,
            )
            response.raise_for_status()
            body = response.json().get("response", {}).get("body", {})
            items = body.get("items") or {}
            rows = items.get("item", []) if isinstance(items, dict) else []
            if isinstance(rows, dict):
                rows = [rows]
            for row in rows:
                for key in _PLACE_NAME_KEYS:
                    value = row.get(key)
                    if value:
                        names[str(value).strip()] = None
                        break
            total_count = int(body.get("totalCount") or 0)
            if not rows or page_no * _PAGE_SIZE >= total_count:
                break
    return list(names)



def places_district_code(area_code: str, district_code: str) -> str:
    """집중률 API의 signguCd(11140)에서 `places.district_code`(140)를 뗀다.

    집중률 API는 법정동 코드 시군구 5자리(시도 2 + 시군구 3)를 쓰고, places는
    TourAPI의 lDongSignguCd, 즉 뒤 3자리만 담는다. 같은 값의 다른 표기다
    (app/service_area.py의 ServiceDistrict 주석 참고).
    """
    if not district_code.startswith(area_code):
        raise ValueError(
            f"--district-code({district_code})가 --area-code({area_code})로 시작하지 "
            "않습니다. 두 값이 같은 지역을 가리키는지 확인하세요."
        )
    return district_code[len(area_code) :]


# PostgREST가 한 응답에 돌려주는 최대 행 수. 이보다 큰 limit을 보내도 서버가 자른다.
_PLACES_PAGE_SIZE = 1000


def _parse_total_count(content_range: str) -> int | None:
    """PostgREST의 Content-Range(`0-999/4355`)에서 전체 건수를 읽는다."""
    _, _, total = content_range.partition("/")
    return int(total) if total.isdigit() else None


async def load_places_from_supabase(
    settings: Settings, *, district_code: str
) -> list[PlaceRow]:
    """한 구의 활성 장소를 전부 읽는다. 한 건이라도 빠지면 예외로 끊는다.

    PostgREST는 limit을 아무리 크게 줘도 한 응답을 1000행에서 자른다. 2026-08-26
    중구 실행에서 활성 891건 중 387건만 읽고도 오류 없이 끝나 매칭률이 40%로
    나왔다 — limit=2000을 보내고 1000행을 받은 것을 아무도 확인하지 않아서였다.
    그래서 Range로 끝까지 넘기고 마지막에 Content-Range의 전체 건수와 대조한다.

    구로 거르는 이유는 따로 있다. 예전에는 전체 구를 읽어 이름만으로 붙였는데,
    이름이 같은 다른 구 장소가 붙을 수 있었다.

    페이지를 넘기려면 정렬이 고정돼야 한다. order가 없으면 같은 행이 두 페이지에
    나오거나 아예 빠질 수 있다.
    """
    url = settings.supabase_url.rstrip("/") + "/rest/v1/places"
    rows: list[PlaceRow] = []
    total: int | None = None
    offset = 0
    async with httpx.AsyncClient() as client:
        while True:
            response = await client.get(
                url,
                params={
                    "select": "content_id,title",
                    "is_active": "eq.true",
                    "district_code": f"eq.{district_code}",
                    "order": "content_id",
                },
                headers={
                    "apikey": settings.supabase_secret_key,
                    "Range-Unit": "items",
                    "Range": f"{offset}-{offset + _PLACES_PAGE_SIZE - 1}",
                    # 전체 건수를 받아야 다 읽었는지 대조할 수 있다.
                    "Prefer": "count=exact",
                },
                timeout=settings.external_api_timeout_seconds,
            )
            response.raise_for_status()
            page = response.json()
            if total is None:
                total = _parse_total_count(response.headers.get("content-range", ""))
            rows.extend(
                PlaceRow(content_id=str(row["content_id"]), title=str(row["title"]))
                for row in page
            )
            if not page:
                break
            offset += len(page)
            if total is not None and offset >= total:
                break

    if total is None:
        raise ValueError(
            "Supabase 응답에 Content-Range가 없어 전체 건수를 확인할 수 없습니다. "
            "몇 건이 빠졌는지 알 수 없으므로 매칭을 진행하지 않습니다."
        )
    if len(rows) != total:
        raise ValueError(
            f"places를 {total}건 중 {len(rows)}건만 읽었습니다. 일부만 가지고 매칭하면 "
            "매칭률이 실제보다 낮게 나오므로 끊습니다."
        )
    return rows


@dataclass(frozen=True)
class MappingRow:
    content_id: str
    place_title: str
    concentration_title: str
    match_method: str
    aliases: tuple[str, ...] = ()
    # tAtsNm에 넣을 검색어. 정식 명칭 그대로 조회되면 비워 둔다.
    search_key: str | None = None
    # 순서대로 시도할 검색어 목록. 1순위는 search_key(없으면 정식 명칭)다(D-057).
    search_keys: tuple[str, ...] = ()


def match_places(
    places: Iterable[PlaceRow],
    concentration_names: Sequence[str],
    overrides: dict[str, ManualOverride] | None = None,
    rejections: dict[tuple[str, str], Rejection] | None = None,
) -> tuple[list[MappingRow], list[PlaceRow], list[str]]:
    """수동 지정 → 정확 일치 → 정규화 일치 순으로 붙인다. 못 붙인 쪽은 그대로 돌려준다.

    `rejections`에 있는 (장소, 집중률 이름) 짝은 붙이지 않고 못 붙인 쪽으로 보낸다.
    사람이 한 번 "이건 아니다"라고 판정한 것을 다음 생성 때 다시 후보로 올리면,
    같은 판정을 매번 되풀이하게 된다. 수동 지정(overrides)은 그대로 이긴다 —
    손으로 붙이라고 적어둔 것을 거절 목록이 뒤집으면 어느 쪽이 이기는지 알 수 없다.
    """
    overrides = overrides or {}
    rejections = rejections or {}
    available = set(concentration_names)
    by_exact = {_normalize_key(name): name for name in concentration_names}
    by_variant: dict[str, str] = {}
    for name in concentration_names:
        for variant in _variants(name):
            by_variant.setdefault(_normalize_key(variant), name)

    matched: list[MappingRow] = []
    unmatched: list[PlaceRow] = []
    used: set[str] = set()
    # TourAPI에 같은 이름이 중복 등록된 장소가 있다(익선동 한옥거리 등). 같은 집중률
    # 장소에 여러 매핑을 만들지 않도록 제목당 한 건만 남긴다.
    seen_titles: set[str] = set()
    deduped: list[PlaceRow] = []
    duplicates: list[PlaceRow] = []
    for place in sorted(places, key=lambda item: item.content_id):
        if place.title in seen_titles:
            duplicates.append(place)
            continue
        seen_titles.add(place.title)
        deduped.append(place)

    for place in deduped:
        override = overrides.get(place.title)
        aliases = _override_aliases(override)

        if override is not None and override.primary is not None:
            if override.primary not in available:
                # 집중률 API에서 사라진 이름을 계속 붙이면 조회가 실패한다.
                unmatched.append(place)
                continue
            matched.append(
                MappingRow(place.content_id, place.title, override.primary, "manual", aliases)
            )
            used.add(override.primary)
            continue

        exact = by_exact.get(_normalize_key(place.title))
        if exact is not None and rejection_key(place.title, exact) in rejections:
            exact = None
        if exact is not None:
            method = "exact_with_alias" if aliases else "exact"
            matched.append(MappingRow(place.content_id, place.title, exact, method, aliases))
            used.add(exact)
            used.update(aliases)
            continue

        found: str | None = None
        for variant in _variants(place.title):
            candidate = by_variant.get(_normalize_key(variant))
            if candidate is None:
                continue
            if rejection_key(place.title, candidate) in rejections:
                # 이 이름은 거절됐다. 다른 변형이 다른 이름을 찾을 수 있으니 계속 본다.
                continue
            found = candidate
            break
        if found is not None:
            matched.append(
                MappingRow(place.content_id, place.title, found, "normalized", aliases)
            )
            used.add(found)
            used.update(aliases)
        else:
            unmatched.append(place)

    leftover = [name for name in concentration_names if name not in used]
    return matched, unmatched + duplicates, leftover


def _override_aliases(override: ManualOverride | None) -> tuple[str, ...]:
    """사람이 지정한 별칭을 그대로 싣는다.

    "이 장소를 가리키는 다른 이름"이라는 뜻이라 집중률 API에 있을 필요가 없다.
    사용자는 "창덕궁"이라고 하지만 저장소 제목은 "창덕궁과 후원 [유네스코 세계유산]"
    이고 집중률 목록에도 그 이름뿐이다. 조회는 concentration_search_keys가 맡으므로
    별칭을 집중률 목록으로 거를 이유가 없다.
    """
    if override is None:
        return ()
    return override.aliases


def write_mapping_csv(rows: Sequence[MappingRow], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as fp:
        writer = csv.writer(fp)
        writer.writerow(
            [
                "content_id",
                "place_title",
                "concentration_title",
                "concentration_search_key",
                "concentration_search_keys",
                "concentration_aliases",
                "match_status",
                "match_method",
                "confidence_score",
                "verified_at",
            ]
        )
        for row in rows:
            writer.writerow(
                [
                    row.content_id,
                    row.place_title,
                    row.concentration_title,
                    row.search_key or "",
                    json.dumps(list(row.search_keys), ensure_ascii=False),
                    json.dumps(list(row.aliases), ensure_ascii=False),
                    "matched",
                    row.match_method,
                    "1.0000",
                    "",
                ]
            )




# 한 번에 올리는 행 수. scripts/import_concentration_mappings.py와 같은 값이다.
UPSERT_CHUNK_SIZE = 100


def mapping_payload(row: MappingRow) -> dict[str, object]:
    """`place_concentration_mappings`에 올릴 한 행.

    CSV 경로(`scripts/import_concentration_mappings.py`의 `load_mapping_payloads`)와
    같은 모양을 만든다. 두 경로가 같은 테이블에 쓰므로 열이 갈라지면 어느 쪽으로
    적재했느냐에 따라 조회 결과가 달라진다. 같은 행으로 두 결과를 비교하는 테스트를
    둬서 묶어 놓는다.

    검색어가 없으면 정식 명칭을 쓴다. `tAtsNm`은 공백이 든 값을 넘기면 0건이
    돌아오므로(2026-08-04 실측), 공백이 있는 이름은 검색어를 따로 뽑아야 한다(D-057).
    """
    search_keys = list(row.search_keys) or [row.concentration_title]
    return {
        "content_id": row.content_id,
        "primary_concentration_name": row.concentration_title,
        "concentration_search_keys": search_keys,
        "concentration_aliases": list(row.aliases),
        "match_method": row.match_method,
        "confidence_score": 1.0,
        "verified_at": None,
    }


async def upsert_mappings(
    settings: Settings, rows: Sequence[MappingRow]
) -> int:
    """승인된 매핑을 `place_concentration_mappings`에 올린다. 올린 수를 돌려준다.

    content_id 충돌은 병합한다 — 같은 장소의 매핑을 다시 만들면 새 값으로 덮는 것이
    맞다. 장소가 실제로 존재하는지는 확인하지 않는다: 행은 방금 `places`에서 읽은
    것이라 그 사이 사라졌을 가능성이 낮고, 확인하려면 조회가 청크마다 한 번씩 더
    붙는다. CSV 경로는 손으로 만든 파일도 받으므로 거기서는 확인한다.
    """
    if not rows:
        return 0
    url = settings.supabase_url.strip()
    key = settings.supabase_secret_key.strip()
    if not url or not key:
        raise ValueError("SUPABASE_URL / SUPABASE_SECRET_KEY가 필요합니다.")

    payloads = [mapping_payload(row) for row in rows]
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    async with httpx.AsyncClient(
        base_url=url.rstrip("/"),
        headers=headers,
        timeout=max(settings.external_api_timeout_seconds, 30.0),
    ) as client:
        for start in range(0, len(payloads), UPSERT_CHUNK_SIZE):
            response = await client.post(
                "/rest/v1/place_concentration_mappings",
                params={"on_conflict": "content_id"},
                headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
                json=payloads[start : start + UPSERT_CHUNK_SIZE],
            )
            response.raise_for_status()
    return len(payloads)


async def count_mappings_by_district(
    settings: Settings, district_codes: Sequence[str]
) -> dict[str, int]:
    """구별로 매핑이 몇 건 있는지. `place_concentration_mappings`에는 구 열이 없어
    `places`를 거쳐 센다.

    구가 없는 테이블이라 조인이 필요한데, PostgREST의 임베디드 조회는 개수만 셀 때도
    행을 다 받아온다. 구마다 count 조회를 한 번씩 보내는 편이 응답이 작다.
    """
    url = settings.supabase_url.strip()
    key = settings.supabase_secret_key.strip()
    if not url or not key:
        return {}

    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    counts: dict[str, int] = {}
    async with httpx.AsyncClient(
        base_url=url.rstrip("/"),
        headers=headers,
        timeout=max(settings.external_api_timeout_seconds, 30.0),
    ) as client:
        for district_code in district_codes:
            response = await client.get(
                "/rest/v1/places",
                params={
                    "select": "content_id,place_concentration_mappings!inner(content_id)",
                    "district_code": f"eq.{district_code}",
                    "is_active": "eq.true",
                },
                headers={"Prefer": "count=exact", "Range": "0-0"},
            )
            response.raise_for_status()
            counts[district_code] = _parse_total_count(
                response.headers.get("content-range", "")
            ) or 0
    return counts


async def count_places_created_after(
    settings: Settings, district_code: str, since: str
) -> int:
    """그 구에 `since` 이후 새로 들어온 활성 장소 수.

    "CSV가 오래됐다"는 갱신이 필요하다는 뜻이 아니다 — 그 구에 새 장소가 들어오지
    않았으면 매핑을 다시 만들어도 결과가 같다. 실제로 봐야 할 신호는 마지막 CSV
    이후에 생긴 장소 수다.

    행을 받지 않고 Content-Range의 전체 건수만 읽는다. 세는 데 장소를 다 받아올
    이유가 없다.
    """
    url = settings.supabase_url.strip()
    key = settings.supabase_secret_key.strip()
    if not url or not key:
        return 0

    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    async with httpx.AsyncClient(
        base_url=url.rstrip("/"),
        headers=headers,
        timeout=max(settings.external_api_timeout_seconds, 30.0),
    ) as client:
        response = await client.get(
            "/rest/v1/places",
            params={
                "select": "content_id",
                "district_code": f"eq.{district_code}",
                "is_active": "eq.true",
                "created_at": f"gte.{since}",
            },
            headers={"Prefer": "count=exact", "Range": "0-0"},
        )
        response.raise_for_status()
    return _parse_total_count(response.headers.get("content-range", "")) or 0
