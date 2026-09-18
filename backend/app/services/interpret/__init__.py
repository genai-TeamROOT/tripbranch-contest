"""interpret 서비스 패키지 진입점.

역할: 패키지 분할(orchestrator/state_transform/session_orchestrator) 이전의 import 경로
(`from app.services.interpret import interpret_user_input`)가 그대로 동작하도록 재노출한다.
"""

from __future__ import annotations

from app.services.interpret.orchestrator import build_interpretation, interpret_user_input

__all__ = ["build_interpretation", "interpret_user_input"]
