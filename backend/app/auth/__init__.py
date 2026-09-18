"""게스트/정식 신원 검증 (D-062 Phase 2).

요청의 `Authorization: Bearer <supabase access token>`을 검증해 `Principal`을
만든다. 저장(Phase 3)과 필수화(Phase 4)는 여기서 하지 않는다.
"""

from app.auth.principal import Principal

__all__ = ["Principal"]
