"""레포↔Langfuse 프롬프트 동기화 도구의 라벨·메타데이터 규칙.

**이 도구에는 테스트가 없었다.** 43개 자산을 외부 SaaS에 쓰는 경로인데 무엇이 어떤
라벨로 올라가는지를 아무것도 지키지 않고 있었다. 실제로 쓰는 `--push` 경로는
네트워크가 필요하지만, **무엇을 올릴지 정하는 판단은 순수 함수**라 여기서 잡는다.
"""

from __future__ import annotations

import pytest

from app.prompts.registry import (
    CONFIG_SEMVER_KEY,
    CONFIG_SLOT_KEY,
    SLOT_ENTRY_TEMPLATES,
    slot_versions,
)
from scripts.sync_langfuse_prompts import (
    _LABEL_PATTERN,
    PRODUCTION_LABEL,
    asset_config,
    push_labels,
)


def test_entry_template_gets_the_semver_as_a_label() -> None:
    """Langfuse의 버전은 정수 자동 증가라 semver를 담을 수 없다.

    `1, 2, 3, 4`는 우리가 몇 번 올렸는지를 셀 뿐이라 `2.4.0`과 대응하지 않는다.
    화면 목록에서 어느 버전이 그 semver인지 눌러보지 않고 알 수 있어야 한다.
    """
    labels = push_labels({CONFIG_SLOT_KEY: "recommend.extract", CONFIG_SEMVER_KEY: "2.4.0"})

    assert labels == [PRODUCTION_LABEL, "2.4.0"]


def test_fragment_gets_only_the_production_label() -> None:
    """조각은 슬롯 여러 개에 걸쳐 있어 semver가 하나로 정해지지 않는다.

    `asset_config()`가 조각에 빈 config를 주므로 여기서도 자연히 빠진다 — 두 곳이
    같은 사실을 각자 판단하지 않게 한다.
    """
    assert push_labels({}) == [PRODUCTION_LABEL]
    assert push_labels(asset_config("_shared/rules/budget.md")) == [PRODUCTION_LABEL]


@pytest.mark.parametrize("bad", ["2.5.0-RC1", "V2.5.0", "2.5.0@prod", "2.5.0 rc", ""])
def test_label_rule_violations_drop_the_label_instead_of_failing(bad: str) -> None:
    """라벨은 소문자·숫자·`_`·`-`·`.`만 받는다(실측).

    여기서 예외를 올리면 **라벨 하나 때문에 43개 푸시가 통째로 멈춘다.** 올리는 것이
    본문이고 라벨은 표시라, 표시가 안 되는 쪽이 덜 나쁘다.
    """
    assert push_labels({CONFIG_SEMVER_KEY: bad}) == [PRODUCTION_LABEL]


def test_every_real_slot_version_survives_the_label_rule() -> None:
    """지금 레포에 있는 semver가 전부 라벨로 나갈 수 있는지 본다.

    누군가 `meta.yaml`에 대문자가 섞인 버전을 적으면 그 슬롯만 조용히 라벨을 잃는다.
    빌드가 깨지지 않으므로 여기서 안 잡으면 화면에서 비어 있는 걸 보고서야 안다.
    """
    unusable = {
        slot: version
        for slot, version in slot_versions().items()
        if not _LABEL_PATTERN.fullmatch(version)
    }

    assert not unusable, f"라벨로 못 쓰는 슬롯 버전: {unusable}"


def test_only_entry_templates_carry_a_semver() -> None:
    """semver는 슬롯 단위, Langfuse 버전은 파일 단위다 — 진입 템플릿만 1:1이다."""
    templates = set(SLOT_ENTRY_TEMPLATES.values())

    for template in templates:
        config = asset_config(template)
        # 슬롯 버전이 없는 템플릿은 config도 비운다(부분만 채우지 않는다).
        if config:
            assert config[CONFIG_SEMVER_KEY]
            assert config[CONFIG_SLOT_KEY]

    assert asset_config("router/intent_definitions.md") == {}
