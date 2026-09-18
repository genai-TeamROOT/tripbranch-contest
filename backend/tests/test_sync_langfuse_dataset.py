"""골드셋 CSV ↔ Langfuse Dataset 동기화 규칙.

**대조 판단은 순수 함수라 네트워크 없이 잡을 수 있다.** 실제 왕복은 실행으로만
확인되지만, "무엇을 올릴지 / 무엇이 어긋났는지"를 정하는 곳은 여기서 지킨다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from scripts.evaluate_agent_quality import EvaluationCase
from scripts.sync_langfuse_dataset import (
    DATASET_NAMES,
    STATUS_DIFFERENT,
    STATUS_EXTRA,
    STATUS_MISSING,
    STATUS_SAME,
    ItemPayload,
    compare_split,
    item_id,
    to_payload,
)


def _case(case_id: str = "DEV-001") -> EvaluationCase:
    return EvaluationCase(
        case_id=case_id,
        title="기본 위치·카페 추천",
        turns=("경복궁 근처 카페 추천해줘",),
        expected_turn_intents=("RECOMMEND",),
        expected_final_conditions={"search_center": "경복궁", "place_tags": ["카페"]},
        device_location="37.5760,126.9769",
        note="단일 턴 기본 추천",
    )


def test_item_id_is_namespaced_by_dataset() -> None:
    """항목 id는 **데이터셋별이 아니라 프로젝트 전역**이다(2026-08-26 실측).

    맨 `DEV-001`을 쓰면 이 프로젝트의 다른 데이터셋과 부딪히고, 한 번 부딪히면
    그쪽 항목을 지운 뒤에도 id가 풀리지 않는다 — 실제로 그렇게 한 건이 막혔다.
    """
    assert item_id("DEV-001", "dev") == "agent-quality-dev-DEV-001"
    assert item_id("FINAL-001", "final") == "agent-quality-final-FINAL-001"
    # 두 split이 같은 번호를 써도 갈린다.
    assert item_id("001", "dev") != item_id("001", "final")


def test_payload_keeps_the_run_inputs_apart_from_the_human_notes() -> None:
    """`input`은 실행에 필요한 것만 담는다.

    나중에 이 Dataset으로 실험을 돌릴 때 `input`을 그대로 요청 본문에 쓸 수 있어야
    한다. `title`·`note`가 섞이면 그때 골라내는 일이 또 생긴다.
    """
    payload = to_payload(_case(), "dev")

    assert payload.input == {
        "turns": ["경복궁 근처 카페 추천해줘"],
        "device_location": "37.5760,126.9769",
    }
    assert payload.expected_output["turn_intents"] == ["RECOMMEND"]
    assert payload.metadata == {
        "title": "기본 위치·카페 추천",
        "note": "단일 턴 기본 추천",
        "split": "dev",
    }


def test_comparison_ignores_key_order() -> None:
    """왕복에서 키 순서가 보존된다는 보장이 없다 — 순서만 다른 것을 '다름'으로 읽으면
    매번 어긋난 것처럼 보여 `--check`가 쓸모없어진다."""
    a = ItemPayload(input={"x": 1, "y": 2}, expected_output={}, metadata={})
    b = ItemPayload(input={"y": 2, "x": 1}, expected_output={}, metadata={})

    assert a.normalized() == b.normalized()


# --- 대조 로직: 가짜 클라이언트로 왕복 없이 -----------------------------------


@dataclass
class _FakeMeta:
    total_pages: int = 1


@dataclass
class _FakeItem:
    id: str
    input: Any
    expected_output: Any
    metadata: Any


class _FakePage:
    def __init__(self, items: list[_FakeItem]) -> None:
        self.data = items
        self.meta = _FakeMeta()


class _FakeApi:
    def __init__(self, items: dict[str, list[_FakeItem]] | None) -> None:
        self._items = items
        self.datasets = self
        self.dataset_items = self

    def get(self, dataset_name: str) -> object:
        if self._items is None or dataset_name not in self._items:
            raise RuntimeError("데이터셋 없음")
        return object()

    def list(self, *, dataset_name: str, page: int = 1, limit: int = 50) -> _FakePage:
        return _FakePage((self._items or {}).get(dataset_name, []))


class _FakeClient:
    def __init__(self, items: dict[str, list[_FakeItem]] | None) -> None:
        self.api = _FakeApi(items)


def _remote_item(case_id: str, split: str, payload: ItemPayload) -> _FakeItem:
    return _FakeItem(
        id=item_id(case_id, split),
        input=payload.input,
        expected_output=payload.expected_output,
        metadata=payload.metadata,
    )


def test_missing_dataset_reports_every_case_as_missing() -> None:
    """데이터셋 자체가 없는 것과 항목만 없는 것을 같은 결과로 낸다 — 둘 다 올려야 한다."""
    rows = compare_split(_FakeClient(None), "dev")

    assert rows, "골드셋이 비어 있으면 이 테스트가 아무것도 안 잡는다"
    assert all(row.status == STATUS_MISSING for row in rows)


def test_changed_expectation_is_reported_as_different(monkeypatch) -> None:
    """골드셋을 고치고 안 올리면 Dataset이 옛 정답으로 남는다.

    그 상태로 잰 수치는 **무엇으로 잰 건지 말할 수 없다** — 그래서 어긋남이다.
    """
    import scripts.sync_langfuse_dataset as module

    case = _case()
    stale = to_payload(case, "dev")
    monkeypatch.setattr(module, "load_cases", lambda split: [case])

    same = _FakeClient({DATASET_NAMES["dev"]: [_remote_item("DEV-001", "dev", stale)]})
    assert [row.status for row in compare_split(same, "dev")] == [STATUS_SAME]

    drifted = ItemPayload(
        input=stale.input,
        expected_output={"turn_intents": ["MODIFY"], "final_conditions": {}},
        metadata=stale.metadata,
    )
    changed = _FakeClient({DATASET_NAMES["dev"]: [_remote_item("DEV-001", "dev", drifted)]})
    assert [row.status for row in compare_split(changed, "dev")] == [STATUS_DIFFERENT]


def test_remote_only_item_is_reported(monkeypatch) -> None:
    """케이스를 지웠는데 Dataset에 남아 있으면, 실행마다 '그때는 있던 케이스'가
    빠진 것으로 보인다 — 회차 비교가 조용히 어긋난다."""
    import scripts.sync_langfuse_dataset as module

    case = _case()
    monkeypatch.setattr(module, "load_cases", lambda split: [case])
    payload = to_payload(case, "dev")
    client = _FakeClient(
        {
            DATASET_NAMES["dev"]: [
                _remote_item("DEV-001", "dev", payload),
                _remote_item("DEV-999", "dev", payload),
            ]
        }
    )

    rows = compare_split(client, "dev")

    assert [row.status for row in rows] == [STATUS_SAME, STATUS_EXTRA]
    assert rows[1].case_id == item_id("DEV-999", "dev")
