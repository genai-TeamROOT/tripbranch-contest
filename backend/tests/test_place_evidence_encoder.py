"""ko-sroberta 인코더의 지연 로딩과 설치 안내 테스트 (torch 없이 돈다)."""

from __future__ import annotations

import builtins
import sys
import threading
import types
from typing import Any

import pytest

from app.providers.place_evidence_encoder import (
    MODEL_NAME,
    KoSrobertaEncoder,
    get_shared_encoder,
)


class _Vector:
    def tolist(self) -> list[float]:
        return [0.1] * 768


class _FakeModel:
    def __init__(self, name: str) -> None:
        self.name = name

    def encode(self, text: str, normalize_embeddings: bool = False) -> _Vector:
        assert normalize_embeddings is True, (
            "적재를 정규화 임베딩으로 했으므로 질의도 같은 조건이어야 유사도가 의미를 갖는다"
        )
        return _Vector()


@pytest.fixture
def loaded_names(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """sentence_transformers를 가짜 모듈로 바꿔 생성자 호출을 센다."""
    names: list[str] = []

    def ctor(name: str) -> _FakeModel:
        names.append(name)
        return _FakeModel(name)

    module = types.ModuleType("sentence_transformers")
    module.SentenceTransformer = ctor  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sentence_transformers", module)
    return names


def test_model_is_loaded_once_and_reused(loaded_names: list[str]) -> None:
    """요청마다 1.2GB 모델을 다시 올리면 서버가 버티지 못한다."""
    encoder = KoSrobertaEncoder()

    encoder.encode("조용한 곳")
    encoder.encode("혼자 쉬기 좋은 곳")

    assert loaded_names == [MODEL_NAME]


def test_model_is_not_loaded_until_used(loaded_names: list[str]) -> None:
    """생성만으로 모델을 올리면 취향 검색을 안 쓰는 배포에서도 1.2GB를 문다."""
    KoSrobertaEncoder()

    assert loaded_names == []


def test_warmup_loads_the_model_up_front(loaded_names: list[str]) -> None:
    """lifespan에서 미리 올려두지 않으면 첫 요청이 적재 시간을 뒤집어쓴다."""
    KoSrobertaEncoder().warmup()

    assert loaded_names == [MODEL_NAME]


def test_background_warmup_does_not_block_and_loads_once(
    loaded_names: list[str],
) -> None:
    """동기 예열은 부팅을 9.4초 늦추고 앱을 띄우는 테스트마다 그 비용을 문다."""
    encoder = KoSrobertaEncoder()

    thread = encoder.warmup_in_background()
    thread.join(timeout=5)

    assert not thread.is_alive()
    assert loaded_names == [MODEL_NAME]


def test_background_warmup_failure_is_swallowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """예열 실패가 스레드 밖으로 새면 기동이 깨진다."""
    monkeypatch.delitem(sys.modules, "sentence_transformers", raising=False)
    real_import = builtins.__import__

    def blocked(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "sentence_transformers":
            raise ImportError("없음")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked)

    thread = KoSrobertaEncoder().warmup_in_background()
    thread.join(timeout=5)

    assert not thread.is_alive()


def test_concurrent_loads_produce_one_model(loaded_names: list[str]) -> None:
    """두 벌이 올라가면 순간 RSS가 두 배가 된다(실측 537MB x 2)."""
    encoder = KoSrobertaEncoder()

    threads = [
        threading.Thread(target=lambda: encoder.encode("조용한 곳")) for _ in range(4)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert loaded_names == [MODEL_NAME]


def test_encode_returns_768_floats(loaded_names: list[str]) -> None:
    """RPC가 vector(768)을 요구한다 — 차원이 다르면 Postgres가 거부한다."""
    assert len(KoSrobertaEncoder().encode("조용한 곳")) == 768


def test_missing_dependency_explains_how_to_install(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """선택 의존성이라 미설치 환경에서 ImportError만 보면 원인을 알기 어렵다."""
    real_import = builtins.__import__

    def blocked(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "sentence_transformers":
            raise ImportError("No module named 'sentence_transformers'")
        return real_import(name, *args, **kwargs)

    monkeypatch.delitem(sys.modules, "sentence_transformers", raising=False)
    monkeypatch.setattr(builtins, "__import__", blocked)

    with pytest.raises(RuntimeError, match="embeddings"):
        KoSrobertaEncoder().encode("조용한 곳")


def test_shared_encoder_is_one_instance_per_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """요청마다 새로 만들면 모델도 매번 적재된다 — 요청당 9.4초·537MB가 붙는다."""
    import app.providers.place_evidence_encoder as module

    monkeypatch.setattr(module, "_shared_encoder", None)

    assert get_shared_encoder() is get_shared_encoder()


def test_warmup_and_request_path_share_the_instance(
    monkeypatch: pytest.MonkeyPatch, loaded_names: list[str]
) -> None:
    """예열한 인스턴스와 요청이 쓰는 인스턴스가 다르면 예열이 무의미하다."""
    import app.providers.place_evidence_encoder as module

    monkeypatch.setattr(module, "_shared_encoder", None)

    get_shared_encoder().warmup()
    get_shared_encoder().encode("조용한 곳")

    assert loaded_names == [MODEL_NAME]
