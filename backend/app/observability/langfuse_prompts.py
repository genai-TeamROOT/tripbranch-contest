"""프롬프트 원문을 Langfuse에서 읽어 온다. 실패하면 레포의 Markdown으로 돌아간다.

역할: `app/prompts/loader.py`가 디스크를 읽기 전에 여기에 먼저 물어본다. `langfuse`
패키지를 직접 만지는 곳은 `langfuse_tracing`과 여기 둘뿐이다.
입력: `settings.langfuse_prompts_enabled`, 자산의 상대경로(`recommend/extract.md`).
출력: 프롬프트 원문 문자열. 자리표시자(`{{name}}`)는 **치환하지 않고 그대로** 준다.
호출 시점: 프롬프트를 조립할 때마다(대부분 캐시 적중, 실측 0.006ms).

**`compile()`을 쓰지 않는다.** Langfuse도 `{{name}}` 문법이라 쓸 수 있지만, 그러면
치환 엔진이 둘이 된다 — 폴백으로 디스크를 읽는 순간 `loader.render_text()`가 돌고,
정상 경로는 SDK가 돈다. 두 결과가 어긋나도 아무도 모른다. 그래서 여기서는 **원문만**
가져오고 치환은 항상 `loader`가 한다. (스파이크에서 두 결과가 바이트 단위로 같음을
확인했지만, 같다는 것과 앞으로도 같다는 것은 다르다.)

**이름 대응은 확장자만 뗀다.** `recommend/extract.md` → `recommend/extract`. Langfuse가
`/`를 폴더로 다루므로 레포 구조가 화면에 그대로 보인다. 다만 **API 경로에 넣을 때는
URL 인코딩이 필요하다** — SDK의 `api.prompts.delete()`가 그걸 안 해서 폴더형 이름에
404가 난다(2026-08-25 실측). 조회는 SDK가 알아서 처리한다.

실패는 전부 흡수한다. Langfuse가 죽어도, 키가 없어도, 그 이름의 프롬프트가 아직
없어도 디스크 원문으로 돌아간다 — 프롬프트를 못 읽으면 답변 자체가 안 나가므로
여기서 예외를 올리면 관측 설정 하나로 서비스가 멈춘다.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Final

from app.config import settings
from app.observability.langfuse_tracing import get_prompt_client

logger = logging.getLogger(__name__)

_MARKDOWN_SUFFIX: Final = ".md"

# 첫 조회에서 전체를 한꺼번에 데울 때 쓰는 동시성. 실측(2026-08-26): 43개를 순차로
# 읽으면 2.28초, 8스레드면 0.4초다. 부팅이 +1.5초 느려지던 것이 이 때문이었다
# (`gemini_prompts.py`가 모듈 수준에서 22개를 읽는다).
_WARM_WORKERS: Final = 8

# 한 프로세스에서 한 번만 데운다.
_warmed = False

# 폴백으로 돌아간 이름. 한 번씩만 알리고 이후에는 조용히 넘어간다 — 프롬프트는
# 요청마다 여러 번 읽히므로 매번 찍으면 로그가 그것만 남는다.
_warned: set[str] = set()


def prompt_name(relative_path: str) -> str:
    """자산 경로를 Langfuse 프롬프트 이름으로 바꾼다.

    `push`·`check`·조회가 **같은 함수를 쓴다.** 각자 문자열을 만들면 한쪽만 바뀌었을 때
    조용히 다른 프롬프트를 보게 된다.
    """

    return relative_path.removesuffix(_MARKDOWN_SUFFIX)


def is_enabled() -> bool:
    """프롬프트를 Langfuse에서 읽는가."""

    return settings.langfuse_prompts_enabled


def fetch_text(relative_path: str, *, fallback: str) -> str:
    """프롬프트 원문. 못 가져오면 `fallback`(디스크 원문)을 그대로 돌려준다.

    SDK가 캐시를 들고 있어 대부분은 왕복이 없다. 만료되면 **백그라운드로 갱신하고
    기존 값을 즉시** 돌려주므로 요청이 막히지 않는다(`_client/client.py::get_prompt`).
    """

    client = get_prompt_client()
    if client is None:
        return fallback

    _warm_cache(client)
    name = prompt_name(relative_path)
    try:
        prompt: Any = client.get_prompt(
            name,
            fallback=fallback,
            cache_ttl_seconds=settings.langfuse_prompt_cache_ttl_seconds,
        )
    except Exception:
        _warn_once(name, "조회 실패")
        return fallback

    # SDK는 fetch에 실패하면 fallback으로 만든 객체를 돌려주고 표시를 남긴다.
    # 표시가 있으면 **켰는데 디스크로 돌고 있다**는 뜻이라 알려야 한다 — 조용하면
    # "Langfuse에서 고쳤는데 왜 그대로지"로 며칠 간다.
    if getattr(prompt, "is_fallback", False):
        _warn_once(name, "Langfuse에 없거나 조회 실패")
        return fallback

    text = getattr(prompt, "prompt", None)
    if not isinstance(text, str):
        _warn_once(name, f"text 프롬프트가 아니다({type(text).__name__})")
        return fallback
    return text


def prompt_object(relative_path: str) -> Any | None:
    """generation에 링크할 Langfuse 프롬프트 객체. 없거나 꺼져 있으면 `None`.

    **`fetch_text()`와 목적이 다르다.** 저쪽은 원문 문자열을 가져와 조립에 쓰고,
    이쪽은 객체 자체를 관측에 넘겨 "이 호출이 이 프롬프트 버전을 썼다"를 남긴다.
    그래야 Langfuse가 버전별 지연·비용·Score를 자동으로 묶는다.

    **폴백 객체는 링크하지 않는다.** SDK가 fetch에 실패하면 우리가 준 fallback으로
    객체를 만들어 주는데, 그건 원격의 어느 버전도 아니라서 링크하면 통계가 거짓이 된다.
    여기서는 fallback을 아예 안 넘겨 실패를 실패로 둔다 — 링크가 없어도 답변은 나간다.
    """

    client = get_prompt_client()
    if client is None:
        return None
    _warm_cache(client)
    try:
        prompt = client.get_prompt(
            prompt_name(relative_path),
            cache_ttl_seconds=settings.langfuse_prompt_cache_ttl_seconds,
        )
    except Exception:
        return None
    return None if getattr(prompt, "is_fallback", False) else prompt


def _warm_cache(client: Any) -> None:
    """첫 조회 때 자산 전체를 병렬로 한 번 데운다.

    **하나씩 읽으면 부팅이 느려진다.** `gemini_prompts.py`가 모듈 수준에서 22개를
    읽으므로 그게 전부 import 시점에 순차 왕복이 된다 — 실측 +1.5초. 첫 요청이
    들어오기 전에 끝나야 하는 시간이라 그냥 두면 배포마다 눈에 띈다.

    실패는 무시한다. 여기서 못 데운 이름은 아래 개별 조회가 폴백으로 처리하므로,
    데우기가 실패했다고 해서 따로 알릴 것이 없다.
    """

    global _warmed
    if _warmed:
        return
    _warmed = True  # 실패해도 다시 시도하지 않는다 — 매 조회마다 43회를 또 돌면 안 된다.

    from app.prompts.loader import asset_paths

    ttl = settings.langfuse_prompt_cache_ttl_seconds

    def warm(path: str) -> None:
        try:
            client.get_prompt(prompt_name(path), cache_ttl_seconds=ttl)
        except Exception:
            pass

    try:
        with ThreadPoolExecutor(max_workers=_WARM_WORKERS) as pool:
            list(pool.map(warm, asset_paths()))
    except Exception:
        logger.warning("프롬프트 캐시 예열 실패(디스크 폴백으로 계속 동작한다)", exc_info=True)


def _warn_once(name: str, reason: str) -> None:
    if name in _warned:
        return
    _warned.add(name)
    logger.warning(
        "프롬프트를 디스크에서 읽는다 — %s: %s (레포 원문으로 계속 동작한다)", name, reason
    )


def reset_fallback_warnings() -> None:
    """폴백 경고와 예열 기록을 지운다. 테스트가 매번 같은 조건에서 시작하게 한다."""

    global _warmed
    _warned.clear()
    _warmed = False


__all__ = [
    "fetch_text",
    "is_enabled",
    "prompt_name",
    "prompt_object",
    "reset_fallback_warnings",
]
