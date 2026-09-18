/*
 * 역할: 사용자에게 API/검증 오류 메시지를 보여주는 공통 배너 컴포넌트.
 * 입력: 화면에 표시할 message 문자열과 선택적인 onRetry 핸들러.
 * 출력: 접근 가능한 alert UI와 선택적인 재시도 버튼.
 * 호출 시점: 페이지 컴포넌트가 요청 실패나 입력 오류를 표시할 때 호출된다.
 * TODO: 오류 코드별 안내가 필요해지면 props를 확장한다.
 */

interface ErrorBannerProps {
  message: string;
  onRetry?: () => void;
}

export function ErrorBanner({ message, onRetry }: ErrorBannerProps) {
  return (
    <div
      role="alert"
      className="flex flex-col gap-2 rounded-md border border-red-300 bg-red-50 p-3 text-sm text-red-800 sm:flex-row sm:items-center sm:justify-between"
    >
      <span>{message}</span>
      {onRetry && (
        <button
          type="button"
          onClick={onRetry}
          className="shrink-0 rounded-md border border-red-300 px-3 py-1 font-medium hover:bg-red-100"
        >
          다시 시도
        </button>
      )}
    </div>
  );
}
