/*
 * 역할: 화면 청크를 받아오지 못했을 때 흰 화면 대신 안내와 새로고침 버튼을 보여준다.
 * 입력: 자식 화면(Suspense로 감싼 Routes).
 * 출력: 정상이면 자식 그대로, 렌더 중 오류가 나면 안내 화면.
 * 호출 시점: App이 Suspense 바깥에서 한 번 감싼다.
 *
 * 왜 필요한가: 화면을 나눠 받아오기 시작하면(React.lazy) 전에 없던 실패가 생긴다 —
 * 앱을 켜둔 채로 새 배포가 나가면 옛 index.html이 가리키는 청크 파일이 이미 사라져
 * import()가 실패한다. 바운더리가 없으면 React가 트리를 통째로 버려 **앱 전체가
 * 흰 화면**이 된다. 한 덩어리로 받던 때는 없던 문제라, 청크를 나눈 커밋과 짝으로 둔다.
 *
 * 새로고침을 권하는 이유: 이 오류의 대부분은 "받아둔 index.html이 낡음"이고,
 * 새로고침하면 새 index.html과 새 청크 이름을 받아 스스로 해결된다.
 *
 * 오류 바운더리는 아직 클래스 컴포넌트로만 만들 수 있다(React 19 기준).
 */

import { Component, type ErrorInfo, type ReactNode } from "react";

interface Props {
  children: ReactNode;
}

interface State {
  hasError: boolean;
}

export class RouteErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false };

  static getDerivedStateFromError(): State {
    return { hasError: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // 사용자에게는 안내만 보여주고, 원인은 콘솔에 남긴다 — 청크 로드 실패인지
    // 화면 코드의 버그인지는 스택을 봐야 갈린다.
    console.error("화면을 그리는 중 오류가 났습니다.", error, info.componentStack);
  }

  render() {
    if (!this.state.hasError) return this.props.children;

    return (
      <div
        role="alert"
        className="flex min-h-screen flex-col items-center justify-center gap-4 px-6 text-center"
      >
        <p className="text-sm text-ink">화면을 불러오지 못했어요.</p>
        <p className="text-xs text-muted">
          앱이 업데이트됐을 수 있어요. 새로고침하면 대부분 해결됩니다.
        </p>
        <button
          type="button"
          onClick={() => window.location.reload()}
          className="rounded-full bg-brand px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-brand-deep"
        >
          새로고침
        </button>
      </div>
    );
  }
}
