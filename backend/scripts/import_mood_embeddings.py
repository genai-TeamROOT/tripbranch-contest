"""장소 사진의 분위기 임베딩을 Supabase에 적재한다.

입력은 코랩에서 만들어 내려받은 파일들이다. 구마다 한 벌씩 있다.

  {구}_image_vectors.npy   사진 벡터 (사진 수 × 768)
  {구}_manifest.json       벡터 순서대로의 사진 목록
  mood_anchors.json        분위기 축 정의와 벡터 (구와 무관하게 하나)

**한 번에 한 구씩 넣는다.** TourAPI detailImage2가 하루 1,000회라 구 하나도 여러 날에
걸쳐 받게 되고, 임베딩도 코랩 세션 단위로 나뉜다. 장소 벡터는 그 장소 사진들의
평균이라 구 사이에 의존이 없어, 나눠 넣어도 결과가 달라지지 않는다.

manifest에는 원본 주소가 없어(파일 경로만 있다) 수집 때 만든 images.json에서
(content_id, 파일명)으로 이어 붙인다. 원본 주소를 남겨야 사진 파일을 보관하지 않고도
나중에 다시 받을 수 있다.

종로(jongno)만 파일 이름이 다르다. 배치 방식을 만들기 전에 넣은 첫 구라
manifest.json·jongno_image_vectors.npy이고, images.json도 두 곳(최상위 시드 29곳과
jongno/)에 나뉘어 있다. 이름을 바꾸면 이미 적재된 것과 대조할 근거가 사라지므로
그대로 두고 예외로 처리한다.

사진 벡터에서 장소 평균을 여기서 계산한다. 코랩에서 미리 계산해 오지 않는 이유는,
장소 평균이 사진 몇 장으로 만들었느냐에 따라 달라지는 파생값이라 원본과 함께
한곳에서 만드는 편이 어긋날 여지가 적기 때문이다.

**인덱스가 걸린 상태에서 실행하지 않는다.** 202608250002_restore_place_embeddings_hnsw_index에
남은 대로, HNSW가 걸린 채 대량 upsert하면 인덱스 갱신 비용 때문에 매 요청이
statement_timeout(57014)에 걸린다. 순서는 202608260002(테이블) → 이 스크립트 →
202608260003(인덱스)이다.

numpy 없이 .npy를 읽는다. 이 스크립트는 백엔드 런타임이 아니라 일회성 적재 도구라
의존성을 늘리지 않는 편이 낫고, .npy는 헤더 한 줄 뒤에 값이 그대로 붙어 있는
단순한 형식이다.
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import hashlib
import json
import math
import struct
from array import array
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import httpx

from app.config import Settings

_UPSERT_CHUNK_SIZE = 200
_DEFAULT_INPUT_DIR = Path.home() / "Dev" / "image_embedding"

# 종로는 배치 방식 이전에 넣어 파일 이름이 다르다. 위 모듈 문서 참고.
_LEGACY_BATCH = "jongno"


@dataclass
class MoodEmbeddingImportResult:
    batch: str
    photo_count: int
    place_count: int
    single_photo_places: int
    axis_count: int
    enabled_axes: list[str]
    anchors_version: str
    model_name: str
    imported_photos: int
    imported_places: int
    imported_axes: int
    dry_run: bool


def read_npy(path: Path) -> list[list[float]]:
    """numpy 없이 .npy를 읽는다. float32 2차원 배열만 다룬다."""
    raw = path.read_bytes()
    if raw[:6] != b"\x93NUMPY":
        raise ValueError(f"{path.name}: npy 파일이 아닙니다.")
    major = raw[6]
    if major == 1:
        header_length = struct.unpack("<H", raw[8:10])[0]
        start = 10
    else:
        header_length = struct.unpack("<I", raw[8:12])[0]
        start = 12
    header = ast.literal_eval(raw[start : start + header_length].decode("latin1").strip())
    if header["descr"] != "<f4":
        raise ValueError(f"{path.name}: float32가 아닙니다({header['descr']}).")
    if header["fortran_order"]:
        raise ValueError(f"{path.name}: fortran_order는 지원하지 않습니다.")
    if len(header["shape"]) != 2:
        raise ValueError(f"{path.name}: 2차원 배열이 아닙니다({header['shape']}).")

    values = array("f")
    values.frombytes(raw[start + header_length :])
    rows, columns = header["shape"]
    if len(values) != rows * columns:
        raise ValueError(f"{path.name}: 값 개수가 shape와 맞지 않습니다.")
    return [values[i * columns : (i + 1) * columns].tolist() for i in range(rows)]


def normalize(vector: Sequence[float]) -> list[float]:
    length = math.sqrt(sum(x * x for x in vector))
    if length == 0:
        raise ValueError("길이가 0인 벡터는 정규화할 수 없습니다.")
    return [x / length for x in vector]


def images_json_paths(batch: str) -> tuple[str, ...]:
    """그 구의 수집 목록(images.json) 경로들. 뒤에 오는 것이 우선한다."""
    if batch == _LEGACY_BATCH:
        # 종로는 시드 29곳과 함께 임베딩했다. 겹치는 11곳은 손으로 걸러낸
        # 최상위(images.json) 쪽을 쓴다 — 정답표가 그 사진들을 보고 매겨졌다.
        return ("jongno/images.json", "images.json")
    return (f"{batch}/images.json",)


def load_origin_urls(input_dir: Path, batch: str) -> dict[tuple[str, str], str]:
    """(content_id, 파일명) → 관광공사 원본 주소.

    manifest는 파일 경로만 담고 있어 여기서 이어 붙인다.
    """
    urls: dict[tuple[str, str], str] = {}
    for relative in images_json_paths(batch):
        path = input_dir / relative
        if not path.exists():
            continue
        for place in json.loads(path.read_text(encoding="utf-8")):
            for image in place["images"]:
                url = image.get("origin_url")
                if url:
                    # http와 https가 섞여 있다. 같은 서버라 https로 통일한다.
                    if "://" in url:
                        url = "https://" + url.split("://", 1)[1]
                    urls[(place["content_id"], image["filename"])] = url
    return urls


def batch_files(batch: str) -> tuple[str, str]:
    """(manifest 파일명, 벡터 파일명). 종로만 이름이 다르다."""
    if batch == _LEGACY_BATCH:
        return "manifest.json", "jongno_image_vectors.npy"
    return f"{batch}_manifest.json", f"{batch}_image_vectors.npy"


def build_payloads(
    input_dir: Path,
    batch: str,
) -> tuple[
    list[dict[str, object]],
    list[dict[str, object]],
    list[dict[str, object]],
    MoodEmbeddingImportResult,
]:
    manifest_name, vectors_name = batch_files(batch)
    for name in (manifest_name, vectors_name, "mood_anchors.json"):
        if not (input_dir / name).exists():
            raise ValueError(
                f"{input_dir / name} 가 없습니다. "
                "코랩 산출물을 내려받았는지 확인하세요."
            )

    manifest = json.loads((input_dir / manifest_name).read_text(encoding="utf-8"))
    origin_urls = load_origin_urls(input_dir, batch)
    anchors = json.loads((input_dir / "mood_anchors.json").read_text(encoding="utf-8"))
    vectors = read_npy(input_dir / vectors_name)

    photo_rows = [
        (place, image)
        for place in manifest
        for image in place["images"]
    ]
    if len(photo_rows) != len(vectors):
        raise ValueError(
            f"manifest의 사진 {len(photo_rows)}장과 벡터 {len(vectors)}장이 맞지 않습니다."
        )

    dimension = anchors["dim"]
    model_name = anchors["model_id"]
    if len(vectors[0]) != dimension:
        raise ValueError(
            f"벡터 차원 {len(vectors[0])}이 mood_anchors의 {dimension}과 다릅니다."
        )

    enabled = list(anchors["enabled"])
    # 켠 축의 문구를 이어 붙여 해시한다. 파이썬 내장 hash()는 실행마다 값이 달라져
    # (문자열 해시 무작위화) 판본 구실을 못하므로 sha256을 쓴다.
    signature = "|".join(
        f"{name}:{anchors['axes'][name]['positive_text']}"
        f"/{anchors['axes'][name]['negative_text']}"
        for name in sorted(enabled)
    )
    digest = hashlib.sha256(signature.encode("utf-8")).hexdigest()[:10]
    anchors_version = f"{model_name}#{digest}"

    photo_payloads: list[dict[str, object]] = []
    by_place: dict[str, list[list[float]]] = {}
    missing_urls: list[str] = []
    for (place, image), vector in zip(photo_rows, vectors, strict=True):
        content_id = place["content_id"]
        filename = image["filename"]
        origin_url = origin_urls.get((content_id, filename))
        if origin_url is None:
            missing_urls.append(f"{content_id}/{filename}")
            continue
        photo_payloads.append(
            {
                "content_id": content_id,
                # 파일명이 곧 detailImage2가 준 순서다(001.jpg → 1).
                "photo_order": int(Path(filename).stem),
                "origin_url": origin_url,
                "image_name": image.get("image_name") or None,
                "embedding": vector,
                "model_name": model_name,
            }
        )
        by_place.setdefault(content_id, []).append(vector)

    if missing_urls:
        raise ValueError(
            f"원본 주소를 찾지 못한 사진 {len(missing_urls)}장: "
            + ", ".join(missing_urls[:10])
        )

    place_payloads: list[dict[str, object]] = []
    for content_id, group in by_place.items():
        mean = [sum(column) / len(group) for column in zip(*group, strict=True)]
        place_vector = normalize(mean)
        # 축 점수는 정규화된 두 벡터의 내적이다. 여기서 미리 계산해 두면 발화 경로가
        # 조회 때 벡터 연산을 하지 않는다.
        scores = {
            name: sum(
                a * b
                for a, b in zip(
                    place_vector, anchors["axes"][name]["vector"], strict=True
                )
            )
            for name in enabled
        }
        place_payloads.append(
            {
                "content_id": content_id,
                "embedding": place_vector,
                "axis_scores": scores,
                "photo_count": len(group),
                "model_name": model_name,
                "anchors_version": anchors_version,
            }
        )

    # 축 방향 벡터도 함께 올린다(TP-206). 사진 검색이 올린 사진의 축 점수를
    # 실행 시점에 계산하려면 이 벡터가 DB에 있어야 한다 — 장소 쪽은 위에서
    # axis_scores로 미리 계산해 두지만, 질의는 미리 계산할 수 없다.
    #
    # **여기서 채우는 이유가 있다.** anchors_version을 만드는 곳이 여기라, 같은
    # 파일로 표까지 채우면 축 정의와 판본이 절대 어긋나지 않는다. 마이그레이션에
    # 벡터를 박아 두면 축 문구를 고칠 때 두 곳을 따로 고쳐야 한다.
    axis_payloads = [
        {
            "name": name,
            "embedding": anchors["axes"][name]["vector"],
            "enabled": name in enabled,
            "positive_text": anchors["axes"][name]["positive_text"],
            "negative_text": anchors["axes"][name]["negative_text"],
            "positive_label": anchors["axes"][name].get("positive_label"),
            "negative_label": anchors["axes"][name].get("negative_label"),
            "anchors_version": anchors_version,
        }
        # 꺼진 축도 올린다. 나중에 켤 때 벡터를 다시 구할 필요가 없고, enabled를
        # 보면 지금 무엇이 쓰이는지 표만 봐도 알 수 있다.
        for name in sorted(anchors["axes"])
    ]

    summary = MoodEmbeddingImportResult(
        batch=batch,
        photo_count=len(photo_payloads),
        place_count=len(place_payloads),
        single_photo_places=sum(1 for p in place_payloads if p["photo_count"] == 1),
        axis_count=len(anchors["axes"]),
        enabled_axes=enabled,
        anchors_version=anchors_version,
        model_name=model_name,
        imported_photos=0,
        imported_places=0,
        imported_axes=0,
        dry_run=True,
    )
    return photo_payloads, place_payloads, axis_payloads, summary


async def _validate_places_exist(
    client: httpx.AsyncClient,
    place_payloads: Sequence[dict[str, object]],
) -> None:
    """places에 없는 장소를 미리 걸러낸다.

    외래키가 있어 넣다가 실패해도 막히기는 하지만, 200개 묶음 중 하나가 걸리면
    그 묶음 전체가 롤백돼 어느 장소가 문제인지 알기 어렵다.
    """
    wanted = {str(p["content_id"]) for p in place_payloads}
    found: set[str] = set()
    ids = sorted(wanted)
    for start in range(0, len(ids), 200):
        chunk = ids[start : start + 200]
        response = await client.get(
            "/rest/v1/places",
            params={
                "select": "content_id",
                "content_id": "in.(" + ",".join(chunk) + ")",
                "limit": "1000",
            },
        )
        response.raise_for_status()
        found.update(str(row["content_id"]) for row in response.json())

    missing = sorted(wanted - found)
    if missing:
        raise ValueError(
            f"places에 없는 content_id {len(missing)}건: " + ", ".join(missing[:10])
        )


async def _upsert(
    client: httpx.AsyncClient,
    table: str,
    on_conflict: str,
    payloads: Sequence[dict[str, object]],
) -> int:
    for start in range(0, len(payloads), _UPSERT_CHUNK_SIZE):
        response = await client.post(
            f"/rest/v1/{table}",
            params={"on_conflict": on_conflict},
            headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
            json=list(payloads[start : start + _UPSERT_CHUNK_SIZE]),
        )
        response.raise_for_status()
    return len(payloads)


async def run(
    args: argparse.Namespace,
    settings: Settings,
) -> MoodEmbeddingImportResult:
    if not settings.supabase_url:
        raise ValueError("SUPABASE_URL이 필요합니다.")
    if not settings.supabase_secret_key:
        raise ValueError("SUPABASE_SECRET_KEY가 필요합니다.")

    photo_payloads, place_payloads, axis_payloads, summary = build_payloads(
        args.input_dir, args.batch
    )
    summary.dry_run = args.dry_run

    base_url = settings.supabase_url
    if not base_url.startswith("http"):
        base_url = f"https://{base_url}.supabase.co"
    headers = {
        "apikey": settings.supabase_secret_key,
        "Authorization": f"Bearer {settings.supabase_secret_key}",
    }
    async with httpx.AsyncClient(
        base_url=base_url.rstrip("/"),
        headers=headers,
        timeout=60.0,
    ) as client:
        await _validate_places_exist(client, place_payloads)
        if not args.dry_run:
            summary.imported_photos = await _upsert(
                client,
                "place_image_embeddings",
                "content_id,photo_order",
                photo_payloads,
            )
            summary.imported_places = await _upsert(
                client, "place_mood_vectors", "content_id", place_payloads
            )
            summary.imported_axes = await _upsert(
                client, "place_mood_axes", "name", axis_payloads
            )
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="장소 분위기 임베딩 적재")
    parser.add_argument(
        "--batch",
        required=True,
        help="구 이름. 예: jung. {구}_manifest.json과 {구}_image_vectors.npy를 읽는다",
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=_DEFAULT_INPUT_DIR,
        help="코랩 산출물과 수집 목록이 있는 폴더",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="계산과 검증만 하고 적재하지 않는다",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = asyncio.run(run(args, Settings()))
    print(json.dumps(result.__dict__, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
