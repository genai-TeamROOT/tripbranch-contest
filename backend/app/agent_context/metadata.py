"""A–C Context의 Provider 메타데이터를 응답 단위로 집계한다."""

from __future__ import annotations

from datetime import datetime

from app.agent_context.schemas import (
    ProviderMetadata,
    RecommendationContext,
    ResponseMetadata,
)


def collect_provider_metadata(
    context: RecommendationContext | None,
) -> list[ProviderMetadata]:
    """Context 순서를 유지하며 중복 없는 Provider 메타데이터를 반환한다."""

    if context is None:
        return []

    collected: list[ProviderMetadata] = []
    seen: set[tuple[str, str, datetime]] = set()
    context_values = (
        context.location,
        # 발화 위치를 따로 지오코딩하면 호출이 하나 더 는다. 여기 넣지 않으면 그
        # 호출만 응답 메타데이터에서 빠진다 — 기준점과 같은 질의를 재사용한 경우는
        # 아래 seen 집합이 중복으로 걸러낸다.
        context.user_location,
        context.weather,
        context.places,
        context.holidays,
    )

    for context_value in context_values:
        if context_value is None:
            continue
        for metadata in context_value.provider_metadata:
            key = (metadata.source, metadata.status, metadata.retrieved_at)
            if key in seen:
                continue
            seen.add(key)
            collected.append(metadata)

    return collected


def build_response_metadata(
    context: RecommendationContext | None,
    *,
    rule_versions: dict[str, str] | None = None,
) -> ResponseMetadata:
    """Context 집계 결과와 규칙 버전으로 최상위 응답 메타데이터를 만든다."""

    return ResponseMetadata(
        rule_versions=dict(rule_versions) if rule_versions is not None else {},
        provider_metadata=collect_provider_metadata(context),
    )
