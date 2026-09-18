"""프롬프트 슬롯 레지스트리 — ``meta.yaml``을 실제로 읽어 버전을 해석한다.

각 인텐트 폴더의 ``meta.yaml``이 슬롯·소유자·현재 관리 버전의 기준이다. 이 모듈은 그
선언을 읽어 (1) 슬롯별 버전 조회와 (2) 한 턴이 실제로 사용한 슬롯들의 버전 문자열
조립을 제공한다.

**왜 필요한가**: 예전에는 실행 기록(B의 LLMOps Trace)에 손으로 적은 고정 문자열
하나(``agent-interpret-prompts-1.0.16``)만 남았다. 담당자가 자기 인텐트의 프롬프트를
고쳐도 그 값이 그대로여서, 나중에 "이 응답은 어느 프롬프트에서 나왔나"에 답할 수
없었다. 이제 인텐트별 슬롯 버전이 기록에 실린다.

의존성: ``meta.yaml`` 파싱에 PyYAML을 쓴다. 프롬프트 본문 로딩(``loader.py``)은 여전히
Markdown만 읽으므로 이 모듈을 import하지 않는 경로는 YAML을 요구하지 않는다.
"""

from __future__ import annotations

import re
from functools import cache
from pathlib import Path
from typing import Final

import yaml

from app.prompts.loader import PROMPT_ROOT, active_variant
from app.schemas import Intent

# 한 턴에 실제로 사용되는 슬롯 목록. 분류(router.classify)는 항상 돌고, 그 뒤 인텐트별
# 추출/편성 슬롯이 하나 더 돈다. 새 인텐트가 생기면 여기에 한 줄을 추가한다.
#
# 답변 생성 슬롯(*.answer, *.summary)은 넣지 않는다 — 기록 시점(step="llm_interpret")에는
# 아직 돌지 않았고, 회귀 판정에 쓰는 지표(intent·조건 추출 정확도)가 전부 해석 단계
# 산출물이라 없이도 추적이 성립한다.
INTENT_SLOTS: dict[Intent, tuple[str, ...]] = {
    Intent.RECOMMEND: ("router.classify", "recommend.extract"),
    Intent.MODIFY: ("router.classify", "modify.extract"),
    Intent.INFO: ("router.classify", "info.extract"),
    Intent.COMPARE: ("router.classify", "compare.extract"),
    Intent.GENERAL: ("router.classify", "general.extract"),
    Intent.SCHEDULE: ("router.classify", "schedule.plan"),
    Intent.OUT_OF_SCOPE: ("router.classify", "out_of_scope.classify"),
}

# LLM 호출 하나(`providers/gemini.py`의 operation)가 어느 슬롯을 쓰는지. 관측에서
# generation 하나에 프롬프트 버전을 붙이는 데 쓴다.
#
# **INTENT_SLOTS와 목적이 다르다.** 저쪽은 "이 턴이 해석 단계에서 어느 슬롯을
# 지났나"(B의 Trace용)라 답변 생성 슬롯을 일부러 뺐다. 이쪽은 **호출 하나가 실제로
# 무엇을 썼나**라서 생성 슬롯(*.answer, *.summary)까지 전부 넣는다 — 그 호출들이
# 토큰과 비용을 가장 많이 쓰는데 버전을 못 달면 비교가 안 된다.
#
# 새 operation을 추가하면서 여기 빠뜨리면 그 호출만 조용히 버전 없이 기록된다.
# tests/test_prompt_operation_slots.py가 gemini.py의 operation 목록과 대조한다.
OPERATION_SLOTS: dict[str, str] = {
    "classify_intent": "router.classify",
    "extract_recommend_conditions": "recommend.extract",
    # TP-266: 조건이 빈손이라 다시 뽑는 호출. 프롬프트는 같으므로 슬롯도 같다
    # (generate_/stream_recommendation_summary가 같은 선례다). 별도 operation으로
    # 두는 것은 지연·비용·빈도를 따로 보기 위해서다.
    "extract_recommend_conditions_retry": "recommend.extract",
    "extract_modify_conditions": "modify.extract",
    "extract_info_query": "info.extract",
    "extract_compare_request": "compare.extract",
    "extract_general_request": "general.extract",
    "generate_recommendation_summary": "recommend.summary",
    "stream_recommendation_summary": "recommend.summary",
    "generate_place_reason": "recommend.place_reason",
    "generate_compare_summary": "compare.summary",
    "generate_general_answer": "general.answer",
    "stream_general_answer": "general.answer",
    "stream_info_answer": "info.answer",
    "filter_review_evidence": "info.review_evidence_filter",
    "stream_review_answer": "info.review_answer",
    "generate_schedule_plan": "schedule.plan",
    "generate_schedule_fill": "schedule.fill",
    "generate_follow_up_suggestions": "follow_up.suggest",
    "extract_closure_rules": "closure.extract",
    "judge_travel_modes": "mode_judge.select",
}

# 슬롯 하나가 조립을 시작하는 **진입 템플릿**. 조각(`_shared/rules/*`)은 이 템플릿의
# 자리표시자로 꽂히므로 여기 나오지 않는다.
#
# **관측에서 generation 하나에 프롬프트를 링크하는 데 쓴다.** 링크는 하나만 걸리므로
# "조각 23개 중 무엇이냐"가 아니라 "이 호출이 시작한 템플릿이 무엇이냐"로 답해야 한다.
# 그래야 Langfuse가 버전별 지연·비용·Score를 자동 집계한다.
#
# **기계적으로 못 만든다.** 진입점 후보는 15개인데 슬롯은 12종이다 — 차이는
# `schedule/*_context.md`(본문과 짝을 이루는 문맥)와 `_shared/rules/validation_retry.md`
# (재시도 부록)라 슬롯이 아니다. 그래서 손으로 선언하고 테스트로 잠근다.
SLOT_ENTRY_TEMPLATES: dict[str, str] = {
    "router.classify": "router/classify.md",
    "recommend.extract": "recommend/extract.md",
    "modify.extract": "modify/extract.md",
    "info.extract": "info/extract.md",
    "compare.extract": "compare/extract.md",
    "general.extract": "general/extract.md",
    "general.answer": "general/answer_instruction.md",
    "info.answer": "info/answer_instruction.md",
    "info.review_evidence_filter": "info/review_evidence_filter.md",
    "info.review_answer": "info/review_answer_instruction.md",
    "closure.extract": "closure/extract.md",
    "mode_judge.select": "mode_judge/select_instruction.md",
    "recommend.summary": "recommend/summary_instruction.md",
    "recommend.place_reason": "recommend/place_reason_instruction.md",
    "compare.summary": "compare/summary_instruction.md",
    "schedule.plan": "schedule/plan.md",
    "schedule.fill": "schedule/fill.md",
    "follow_up.suggest": "follow_up/suggest_instruction.md",
}

_FALLBACK_SLOTS: tuple[str, ...] = ("router.classify",)
_SEMVER_PATTERN = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


def metadata_paths() -> list[Path]:
    """라이브러리 안의 인텐트별 메타데이터 파일 목록을 반환한다."""

    return sorted(PROMPT_ROOT.glob("*/meta.yaml"))


@cache
def slot_versions() -> dict[str, str]:
    """모든 ``meta.yaml``을 읽어 ``{슬롯 ID: 버전}`` 표를 만든다.

    파일 내용은 프로세스 수명 동안 바뀌지 않는다고 보고 캐시한다(프롬프트 자산도
    같은 전제로 배포 단위로만 바뀐다).
    """

    versions: dict[str, str] = {}
    for path in metadata_paths():
        document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for slot_id, slot in (document.get("slots") or {}).items():
            version = slot.get("version")
            if not isinstance(version, str) or _SEMVER_PATTERN.fullmatch(version) is None:
                raise ValueError(
                    "프롬프트 슬롯 버전은 MAJOR.MINOR.PATCH 형식이어야 합니다: "
                    f"{path.parent.name}/{slot_id}"
                )
            if slot_id in versions:
                raise ValueError(f"프롬프트 슬롯 ID가 중복됐습니다: {slot_id}")
            versions[slot_id] = version
    return versions


def slots_for(intent: Intent | None) -> tuple[str, ...]:
    """이번 턴이 사용한 슬롯 ID들을 돌려준다. 모르는 인텐트면 분류 슬롯만 남긴다."""

    if intent is None:
        return _FALLBACK_SLOTS
    return INTENT_SLOTS.get(intent, _FALLBACK_SLOTS)


def turn_prompt_version(intent: Intent | None) -> str:
    """실행 기록(Trace)에 남길 프롬프트 버전 문자열을 만든다.

    예: ``router.classify@2.0.0+info.extract@3.0.0``

    과거 기준선으로 실행 중이면(``TRIPBRANCH_PROMPT_VARIANT``) 그 ID를 뒤에 붙여
    "지금 이 기록은 옛 프롬프트로 낸 것"임을 남긴다.

    B는 이 값을 해석하지 않고 문자열로만 저장하므로(llmops-trace-contract-v1.md §7 Q2)
    형식을 바꿔도 B 쪽 스키마 변경이 필요 없다.
    """

    versions = slot_versions()
    rendered = "+".join(
        f"{slot}@{versions[slot]}" for slot in slots_for(intent) if slot in versions
    )
    variant = active_variant()
    if variant != "current":
        return f"{rendered}+variant:{variant}" if rendered else f"variant:{variant}"
    return rendered


def operation_prompt_version(operation: str) -> str | None:
    """LLM 호출 하나에 붙일 프롬프트 버전. 모르는 operation이면 `None`.

    예: `classify_intent` → `router.classify@2.1.0`

    **관측에서 `version` 자리에 넣는 값이다.** `turn_prompt_version()`이 턴 전체를
    한 문자열로 묶는 것과 달리 여기는 호출 하나만 가리킨다 — 한 턴이 슬롯을 여러 개
    쓰므로, 묶어버리면 "`recommend.extract` 2.3.0과 2.4.0 중 어느 쪽이 느린가"를
    가를 수 없다.

    과거 기준선으로 실행 중이면(`TRIPBRANCH_PROMPT_VARIANT`) 그 ID를 뒤에 붙인다 —
    옛 프롬프트로 낸 기록이 현재 버전 통계에 섞이면 비교가 오염된다.
    """

    slot = OPERATION_SLOTS.get(operation)
    if slot is None:
        return None
    version = live_slot_version(slot) or slot_versions().get(slot)
    if version is None:
        return None
    rendered = f"{slot}@{version}"
    variant = active_variant()
    return rendered if variant == "current" else f"{rendered}+variant:{variant}"


CONFIG_SEMVER_KEY: Final = "semver"
CONFIG_SLOT_KEY: Final = "slot"


def live_slot_version(slot: str) -> str | None:
    """**지금 돌고 있는** 프롬프트가 스스로 밝힌 슬롯 버전. 모르면 `None`.

    프롬프트를 Langfuse에서 읽으면 `meta.yaml`은 더 이상 "실제로 돈 것"의 근거가
    아니다 — UI에서 고치면 레포는 그대로인데 지침만 바뀐다. 그 상태로 관측에
    `recommend.extract@2.4.0`을 적으면 **기록이 거짓말을 하고 회귀 판정이 무의미해진다.**

    그래서 `--push`가 `meta.yaml`의 semver를 각 프롬프트 `config`에 함께 올리고,
    여기서는 그 값을 되읽는다. 올린 뒤 UI에서 본문만 고치면 semver는 그대로이므로
    **버전 문자열과 내용이 어긋난 상태 자체는 남는다** — 그건 `--check`가 잡는다.
    여기서 없애는 것은 "레포 값을 실제 값인 양 적는" 더 나쁜 쪽이다.

    프롬프트 관리가 꺼져 있으면 `None`이고 호출부가 `meta.yaml`로 돌아간다.
    """

    template = SLOT_ENTRY_TEMPLATES.get(slot)
    if template is None:
        return None
    # 함수 안에서 import 한다 — 프롬프트 라이브러리가 관측 모듈에 부팅 의존을 걸지
    # 않게 하려는 것이다(`loader._remote_text()`와 같은 이유).
    from app.observability import langfuse_prompts

    if not langfuse_prompts.is_enabled():
        return None
    prompt = langfuse_prompts.prompt_object(template)
    config = getattr(prompt, "config", None)
    if not isinstance(config, dict):
        return None
    semver = config.get(CONFIG_SEMVER_KEY)
    return semver if isinstance(semver, str) else None


def operation_entry_template(operation: str) -> str | None:
    """LLM 호출 하나가 시작한 진입 템플릿의 상대경로. 모르는 operation이면 `None`.

    예: `classify_intent` → `router/classify.md`

    `operation_prompt_version()`이 같은 호출에 **버전 문자열**을 붙인다면 이쪽은
    **어느 파일이냐**다. 둘 다 필요하다 — 버전 문자열은 관측을 꺼도 남는 자리
    (`version`)에 들어가고, 이 경로는 Langfuse 프롬프트 객체를 찾아 링크하는 데 쓴다.
    """

    slot = OPERATION_SLOTS.get(operation)
    if slot is None:
        return None
    return SLOT_ENTRY_TEMPLATES.get(slot)
