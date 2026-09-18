"""KOSTAT 행정구역경계 원본에서 한 시도의 구 경계를 뽑아 한 파일로 저장한다.

역할: `app.service_area`가 읽는 `resources/boundaries/seoul.geojson`을 만든다.
입력: KOSTAT 시군구 경계 GeoJSON(원본 17MB, 저장소에 넣지 않는다).
출력: 그 시도의 모든 구를 담은 FeatureCollection 한 장.

지원 구만 뽑지 않고 시도 전체를 담는 이유는, 지원 구가 늘 때 파일 작업 없이
`SUPPORTED_DISTRICTS` 한 줄만 고치면 되게 하기 위해서다. 서울 25개 구를 다 담아도
214KB, 파싱 2.5ms라 미리 담아 두는 비용이 사실상 없다.

원본을 저장소에 두지 않는 이유는 크기다. 대신 이 스크립트가 URL에서 받는다.
행정구역이 개편되면 `--base-year`를 바꿔 다시 돌리면 된다.

사용법:
    python -m scripts.extract_district_boundaries --extracted-at 2026-08-24
    python -m scripts.extract_district_boundaries --extracted-at 2026-08-24 --dry-run
    python -m scripts.extract_district_boundaries --extracted-at 2026-08-24 \
        --source path/to/원본.json
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

import httpx

from app.service_area import SUPPORTED_DISTRICTS

_SOURCE_URL_TEMPLATE = (
    "https://raw.githubusercontent.com/southkorea/southkorea-maps/master"
    "/kostat/{base_year}/json/skorea-municipalities-{base_year}-geo.json"
)
_DEFAULT_BASE_YEAR = "2018"
_BOUNDARY_DIR = Path(__file__).resolve().parents[1] / "resources" / "boundaries"

# KOSTAT 시군구 코드의 앞 두 자리가 시도다. 이름만으로 고르면 안 된다 — "중구"는
# 서울·부산·대구·인천 등 일곱 곳에 있다.
_REGIONS = {
    "11": ("서울특별시", "seoul.geojson"),
}

_EARTH_RADIUS_KM = 6371.0

# 지원 구의 공식 면적(km²). 추출한 폴리곤이 엉뚱한 구가 아닌지 확인하는 데 쓴다.
_OFFICIAL_AREA_KM2 = {
    "종로구": 23.91,
    "중구": 9.96,
    "용산구": 21.87,
    "성동구": 16.85,
}


def _download(base_year: str) -> dict[str, Any]:
    url = _SOURCE_URL_TEMPLATE.format(base_year=base_year)
    print(f"원본을 내려받습니다: {url}")
    response = httpx.get(url, timeout=300.0, follow_redirects=True)
    response.raise_for_status()
    return response.json()


def _load_source(source: Path | None, base_year: str) -> dict[str, Any]:
    if source is None:
        return _download(base_year)
    with source.open(encoding="utf-8") as fp:
        return json.load(fp)


def _rings(geometry: dict[str, Any]) -> list[list[list[float]]]:
    if geometry["type"] == "Polygon":
        polygons = [geometry["coordinates"]]
    elif geometry["type"] == "MultiPolygon":
        polygons = geometry["coordinates"]
    else:
        raise ValueError(f"지원하지 않는 geometry 형식입니다: {geometry['type']}")
    return [ring for polygon in polygons for ring in polygon]


def _area_km2(geometry: dict[str, Any]) -> float:
    """외곽선 면적의 합. 위도에 맞춘 평면 근사라 구 규모에서는 오차가 작다."""
    total = 0.0
    for ring in _rings(geometry):
        if len(ring) < 4:
            continue
        mean_latitude = math.radians(sum(point[1] for point in ring) / len(ring))
        projected = [
            (
                math.radians(point[0]) * _EARTH_RADIUS_KM * math.cos(mean_latitude),
                math.radians(point[1]) * _EARTH_RADIUS_KM,
            )
            for point in ring
        ]
        doubled = 0.0
        for index in range(len(projected) - 1):
            x1, y1 = projected[index]
            x2, y2 = projected[index + 1]
            doubled += x1 * y2 - x2 * y1
        total += abs(doubled / 2)
    return total


def _district_features(source: dict[str, Any], region_code: str) -> list[dict[str, Any]]:
    features = [
        feature
        for feature in source.get("features", [])
        if str(feature.get("properties", {}).get("code", "")).startswith(region_code)
    ]
    if not features:
        raise ValueError(f"원본에서 시도 코드 {region_code}의 구를 찾지 못했습니다.")
    return sorted(features, key=lambda f: str(f["properties"].get("code", "")))


def _build_collection(
    features: list[dict[str, Any]],
    region_name: str,
    base_year: str,
    extracted_at: str,
) -> dict[str, Any]:
    """출처를 파일 안에 함께 넣는다. 데이터와 출처가 떨어지면 근거를 못 찾는다."""
    return {
        "type": "FeatureCollection",
        "attribution": {
            "source": "KOSTAT 센서스용 행정구역경계 (통계청 SGIS)",
            "via": "https://github.com/southkorea/southkorea-maps",
            "license": "KOSTAT: Free to share or remix.",
            "base_year": base_year,
            "extracted_at": extracted_at,
            "note": (
                f"skorea-municipalities-{base_year}-geo.json에서 {region_name} "
                "시군구만 추출했다. 좌표 순서는 GeoJSON 표준인 [경도, 위도]다. "
                "properties.code는 KOSTAT 코드로, TourAPI lDongSignguCd와 다른 "
                "체계다."
            ),
        },
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "name": feature["properties"].get("name"),
                    "name_eng": feature["properties"].get("name_eng"),
                    "code": feature["properties"].get("code"),
                },
                "geometry": feature["geometry"],
            }
            for feature in features
        ],
    }


def extract(
    source: Path | None,
    base_year: str,
    extracted_at: str,
    out_dir: Path,
    region_code: str,
    dry_run: bool,
) -> None:
    if region_code not in _REGIONS:
        raise ValueError(f"모르는 시도 코드입니다: {region_code}")
    region_name, filename = _REGIONS[region_code]
    payload = _load_source(source, base_year)
    features = _district_features(payload, region_code)
    collection = _build_collection(features, region_name, base_year, extracted_at)

    supported_names = {district.name for district in SUPPORTED_DISTRICTS}
    found_names = {str(feature["properties"]["name"]) for feature in collection["features"]}
    missing = sorted(supported_names - found_names)
    if missing:
        raise ValueError(
            f"SUPPORTED_DISTRICTS에 있는 구가 추출 결과에 없습니다: {', '.join(missing)}"
        )

    print(f"{region_name} {len(features)}개 구를 담습니다. 지원 구 면적 대조:")
    for feature in collection["features"]:
        name = str(feature["properties"]["name"])
        if name not in supported_names:
            continue
        point_count = sum(len(ring) for ring in _rings(feature["geometry"]))
        area = _area_km2(feature["geometry"])
        official = _OFFICIAL_AREA_KM2.get(name)
        gap = f"{(area - official) / official * 100:+.2f}%" if official else "-"
        print(
            f"  {name} 좌표 {point_count}개, 면적 {area:.2f} km² "
            f"(공식 {official if official else '?'} km², 차이 {gap})"
        )

    target = out_dir / filename
    body = json.dumps(collection, ensure_ascii=False, separators=(",", ":")) + "\n"
    print(f"-> {target.name} ({len(body.encode()) / 1024:.1f} KB)")
    if not dry_run:
        target.write_text(body, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        type=Path,
        default=None,
        help="내려받아 둔 원본 GeoJSON 경로. 없으면 URL에서 받는다.",
    )
    parser.add_argument("--base-year", default=_DEFAULT_BASE_YEAR)
    parser.add_argument(
        "--extracted-at",
        required=True,
        help="추출일(YYYY-MM-DD). 파일의 attribution에 그대로 들어간다.",
    )
    parser.add_argument("--out-dir", type=Path, default=_BOUNDARY_DIR)
    parser.add_argument("--region-code", default="11", help="KOSTAT 시도 코드")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    extract(
        source=args.source,
        base_year=args.base_year,
        extracted_at=args.extracted_at,
        out_dir=args.out_dir,
        region_code=args.region_code,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
