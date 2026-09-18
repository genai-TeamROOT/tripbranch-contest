"""프롬프트 Markdown 자산을 읽고, 제한된 자리표시자만 치환한다.

Jinja2를 도입하지 않는다. 조건 분기·반복·대화 상태 직렬화는 Python 호출부가 맡고,
이 모듈은 ``{{name}}`` 형태의 단순 값 삽입만 제공한다.
"""

from __future__ import annotations

import json
import os
from functools import cache
from pathlib import Path
from typing import Final

PROMPT_ROOT: Final = Path(__file__).resolve().parent
PROMPT_VARIANT_ENV: Final = "TRIPBRANCH_PROMPT_VARIANT"
# 라이브러리 안에 있지만 모델에 넣는 지침이 아닌 Markdown. 동기화 대상이 아니다.
_NON_ASSET_NAMES: Final = frozenset({"HISTORY.md", "README.md", "OWNERS.md"})
_CURRENT_VARIANT: Final = "current"


def active_variant() -> str:
    """현재 서버가 사용할 프롬프트 기준선 ID를 반환한다.

    기준선을 바꾸는 것은 서버 시작 전 환경변수로만 허용한다. 요청 중간에 프롬프트가
    바뀌면 같은 세션의 재현성이 깨질 수 있기 때문이다.
    """

    return os.getenv(PROMPT_VARIANT_ENV, "").strip() or _CURRENT_VARIANT


def _normalize_relative_path(relative_path: str) -> str:
    path = Path(relative_path)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"프롬프트 라이브러리 밖 경로는 읽을 수 없습니다: {relative_path}")
    return path.as_posix()


def _safe_path(relative_path: str) -> Path:
    path = (PROMPT_ROOT / relative_path).resolve()
    if not path.is_relative_to(PROMPT_ROOT):
        raise ValueError(f"프롬프트 라이브러리 밖 경로는 읽을 수 없습니다: {relative_path}")
    return path


@cache
def _variant_overrides(variant: str) -> dict[str, str]:
    """선택한 과거 기준선이 덮어쓸 Markdown 경로 목록을 읽는다."""

    if variant == _CURRENT_VARIANT:
        return {}

    found: dict[str, object] | None = None
    known_variants: list[str] = []
    for manifest_path in sorted(PROMPT_ROOT.glob("*/archive/variants.json")):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            message = f"프롬프트 기준선 매니페스트가 올바른 JSON이 아닙니다: {manifest_path}"
            raise ValueError(message) from exc
        if not isinstance(manifest, dict):
            raise ValueError(f"프롬프트 기준선 매니페스트는 객체여야 합니다: {manifest_path}")
        known_variants.extend(key for key in manifest if isinstance(key, str))
        candidate = manifest.get(variant)
        if candidate is None:
            continue
        if found is not None:
            raise ValueError(f"프롬프트 기준선 ID가 중복됐습니다: {variant}")
        if not isinstance(candidate, dict):
            raise ValueError(f"프롬프트 기준선 설정은 객체여야 합니다: {variant}")
        found = candidate

    if found is None:
        choices = ", ".join(sorted(known_variants)) or "없음"
        raise ValueError(
            f"알 수 없는 프롬프트 기준선입니다: {variant}. 사용 가능: current, {choices}"
        )

    overrides = found.get("overrides")
    if not isinstance(overrides, dict) or not overrides:
        raise ValueError(f"프롬프트 기준선에는 비어 있지 않은 overrides가 필요합니다: {variant}")

    normalized: dict[str, str] = {}
    for source, replacement in overrides.items():
        if not isinstance(source, str) or not isinstance(replacement, str):
            raise ValueError(f"프롬프트 기준선 경로는 문자열이어야 합니다: {variant}")
        source_path = _normalize_relative_path(source)
        replacement_path = _normalize_relative_path(replacement)
        if not _safe_path(replacement_path).is_file():
            raise ValueError(
                f"프롬프트 기준선 파일을 찾을 수 없습니다: {variant} -> {replacement_path}"
            )
        normalized[source_path] = replacement_path
    return normalized


def asset_paths() -> list[str]:
    """활성 프롬프트 Markdown의 상대경로 목록. `archive/`와 문서 파일은 뺀다.

    **한 곳에서만 센다.** 동기화 스크립트와 스파이크가 각자 glob을 돌리면 한쪽만
    제외 규칙이 바뀌었을 때 "레포에는 있는데 올라가지 않는" 자산이 조용히 생긴다.
    """

    return sorted(
        path.relative_to(PROMPT_ROOT).as_posix()
        for path in PROMPT_ROOT.rglob("*.md")
        if "archive" not in path.relative_to(PROMPT_ROOT).parts
        and path.name not in _NON_ASSET_NAMES
    )


def load_text(relative_path: str) -> str:
    """프롬프트 원문을 UTF-8 문자열로 읽는다.

    **읽는 곳이 둘이다.** 기본은 지금까지처럼 레포의 Markdown이고,
    `LANGFUSE_PROMPTS_ENABLED=true`면 Langfuse를 먼저 본다. 못 가져오면 디스크
    원문으로 돌아가므로, 켜도 Langfuse가 죽으면 서비스는 그대로 돈다.

    **과거 기준선(`TRIPBRANCH_PROMPT_VARIANT`)은 디스크만 본다.** 그 기능은 "이
    커밋 시점의 원문으로 돌려보기"라서 원격 최신본을 섞으면 의미가 사라진다.
    기준선 비교는 레포가, 현행 운영은 Langfuse가 맡는다.

    치환은 여기서 하지 않는다 — `render_text()`가 한다. 원격이든 디스크든 같은
    치환 엔진을 타야 두 경로가 어긋나지 않는다.
    """

    normalized_path = _normalize_relative_path(relative_path)
    overrides = _variant_overrides(active_variant())
    selected_path = overrides.get(normalized_path, normalized_path)
    disk_text = _safe_path(selected_path).read_text(encoding="utf-8").strip()

    if normalized_path in overrides:
        return disk_text
    return _remote_text(normalized_path, fallback=disk_text)


def _remote_text(normalized_path: str, *, fallback: str) -> str:
    """Langfuse 원문. 꺼져 있거나 실패하면 `fallback`.

    import를 함수 안에 둔다 — 프롬프트 라이브러리가 관측 모듈에 부팅 의존을 걸지
    않게 하려는 것이다(`registry.py`가 PyYAML을 같은 이유로 다루는 방식).
    """

    from app.observability import langfuse_prompts

    if not langfuse_prompts.is_enabled():
        return fallback
    return langfuse_prompts.fetch_text(normalized_path, fallback=fallback)


def render_text(relative_path: str, /, **values: object) -> str:
    """제어문 없는 ``{{key}}`` 자리표시자를 값으로 치환한다."""

    rendered = load_text(relative_path)
    for key, value in values.items():
        rendered = rendered.replace(f"{{{{{key}}}}}", str(value))
    return rendered
