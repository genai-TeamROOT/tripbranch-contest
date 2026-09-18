"""Colab에서 받은 지역구 임베딩 압축 파일을 검증하고 Supabase에 적재한다.

실제 검증·적재 규칙은 같은 폴더의 import_place_embeddings.py를 호출해 한 곳에서
관리한다. 기본 실행은 항상 dry-run을 먼저 수행하며, 사용자가 지역구 확인 문구를
입력해야 실제 DB를 변경한다.
"""

from __future__ import annotations

import argparse
import gzip
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
IMPORTER = BACKEND_DIR / "scripts" / "import_place_embeddings.py"
DEFAULT_DISTRIBUTED_ROOT = BACKEND_DIR.parent.parent / "TripBranch_RAG_Distributed_Embedding"
FILE_RE = re.compile(
    r"^place_embeddings_(?P<district>.+?)_jhgan_(?:structural|evidence)_120t_v\d+\.jsonl(?:\.gz)?$"
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Colab에서 다운로드한 지역구 임베딩 .jsonl.gz를 dry-run으로 검증한 "
            "뒤 팀 Supabase에 적재합니다."
        )
    )
    parser.add_argument(
        "archive",
        type=Path,
        help=(
            "place_embeddings_<구>_jhgan_<structural|evidence>_120t_"
            "v<버전>.jsonl.gz 경로"
        ),
    )
    parser.add_argument(
        "--dry-run-only",
        action="store_true",
        help="검증만 수행하고 실제 DB는 변경하지 않음",
    )
    parser.add_argument(
        "--replace-existing",
        action="store_true",
        help="새 적재 성공 후 담당 구·동일 모델의 과거 임베딩 버전을 정리",
    )
    parser.add_argument(
        "--scope-content-ids",
        type=Path,
        help=(
            "--replace-existing 범위 place_catalog.csv. 생략하면 형제 분산 패키지의 "
            "지역구 input 폴더에서 자동 탐색"
        ),
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="dry-run 통과 후 실제 적재 확인 입력을 생략(CI 전용)",
    )
    return parser


def district_from_filename(path: Path) -> str:
    match = FILE_RE.match(path.name)
    if not match:
        raise ValueError(
            "파일명이 예상 형식과 다릅니다. Colab에서 생성한 "
            "place_embeddings_<구>_jhgan_<structural|evidence>_120t_"
            "v<버전>.jsonl.gz를 "
            "그대로 사용하세요."
        )
    return match.group("district")


def resolve_scope_catalog(district: str, explicit: Path | None) -> Path:
    path = (
        explicit.expanduser().resolve()
        if explicit is not None
        else (
            DEFAULT_DISTRIBUTED_ROOT
            / "districts"
            / district
            / "input"
            / "place_catalog.csv"
        ).resolve()
    )
    if not path.is_file():
        raise FileNotFoundError(
            f"교체 범위 파일을 찾지 못했습니다: {path}\n"
            "기존 버전을 교체하려면 --scope-content-ids로 담당 구 "
            "place_catalog.csv를 지정하세요."
        )
    return path


def unpack(archive: Path, temp_dir: Path) -> Path:
    if archive.name.endswith(".jsonl.gz"):
        output = temp_dir / archive.name.removesuffix(".gz")
        print(f"압축 해제 중: {archive.name}")
        with gzip.open(archive, "rb") as source, output.open("wb") as target:
            shutil.copyfileobj(source, target, length=8 * 1024 * 1024)
        return output
    if archive.suffix == ".jsonl":
        return archive
    raise ValueError(".jsonl.gz 또는 .jsonl 파일만 사용할 수 있습니다.")


def python_runner() -> list[str]:
    """프로젝트 실행기를 uv → backend/.venv → 현재 Python 순으로 선택한다."""
    uv = shutil.which("uv")
    if uv:
        return [uv, "run", "python", "-u"]

    venv_python = BACKEND_DIR / ".venv" / "bin" / "python"
    if venv_python.is_file():
        return [str(venv_python), "-u"]

    return [sys.executable, "-u"]


def run_importer(
    jsonl: Path,
    *,
    dry_run: bool,
    replace_existing: bool,
    scope_catalog: Path | None,
) -> None:
    command = [
        *python_runner(),
        "scripts/import_place_embeddings.py",
        "--jsonl",
        str(jsonl),
    ]
    if dry_run:
        command.append("--dry-run")
    if replace_existing:
        if scope_catalog is None:
            raise ValueError("--replace-existing에는 place_catalog.csv가 필요합니다.")
        command.extend(
            ["--replace-existing", "--scope-content-ids", str(scope_catalog)]
        )

    label = "DRY-RUN 검증" if dry_run else "실제 DB 적재"
    print(f"\n=== {label} 시작 ===")
    child_env = os.environ.copy()
    existing_pythonpath = child_env.get("PYTHONPATH", "")
    child_env["PYTHONPATH"] = os.pathsep.join(
        part for part in (str(BACKEND_DIR), existing_pythonpath) if part
    )
    subprocess.run(command, cwd=BACKEND_DIR, env=child_env, check=True)
    print(f"=== {label} 완료 ===")


def main() -> int:
    args = build_parser().parse_args()
    archive = args.archive.expanduser().resolve()
    if not archive.is_file():
        raise FileNotFoundError(f"다운로드 파일을 찾지 못했습니다: {archive}")
    if not IMPORTER.is_file():
        raise FileNotFoundError(f"최신 적재 스크립트를 찾지 못했습니다: {IMPORTER}")

    env_path = BACKEND_DIR / ".env"
    if not env_path.is_file():
        raise FileNotFoundError(
            f"팀 Supabase 접속 정보가 있는 파일을 찾지 못했습니다: {env_path}"
        )

    district = district_from_filename(archive)
    scope_catalog = (
        resolve_scope_catalog(district, args.scope_content_ids)
        if args.replace_existing
        else None
    )

    print("TripBranch 지역구 임베딩 적재")
    print(f"  지역구: {district}")
    print(f"  입력 파일: {archive}")
    print(f"  환경설정: {env_path} (비밀값은 출력하지 않음)")
    print(f"  기존 버전 교체: {args.replace_existing}")
    print("  적재 방식: PostgREST 200행 배치 / 요청 제한 120초")
    print(
        "  주의: 전체 대량 적재 전 place_embeddings HNSW 인덱스를 한 번 제거하고, "
        "모든 구 적재 후 한 번 재생성하세요."
    )

    with tempfile.TemporaryDirectory(prefix=f"tripbranch_{district}_") as temp:
        jsonl = unpack(archive, Path(temp))
        print(f"검증 대상: {jsonl} ({jsonl.stat().st_size / 1024**3:.2f} GB)")

        run_importer(
            jsonl,
            dry_run=True,
            replace_existing=args.replace_existing,
            scope_catalog=scope_catalog,
        )

        if args.dry_run_only:
            print("\n검증만 완료했습니다. DB는 변경하지 않았습니다.")
            return 0

        if not args.yes:
            expected = f"IMPORT {district}"
            answer = input(
                "\n실제 팀 Supabase DB에 적재하려면 "
                f"'{expected}'를 정확히 입력하세요: "
            ).strip()
            if answer != expected:
                print("확인 문구가 일치하지 않아 실제 적재를 취소했습니다.")
                return 0

        run_importer(
            jsonl,
            dry_run=False,
            replace_existing=args.replace_existing,
            scope_catalog=scope_catalog,
        )

    print(f"\n✅ {district} 적재가 완료되었습니다.")
    print("다운로드한 .jsonl.gz는 작업 기록으로 보관하세요.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
