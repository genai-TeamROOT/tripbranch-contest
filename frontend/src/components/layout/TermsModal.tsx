/*
 * 역할: 회원가입 화면에서 이용약관·개인정보처리방침을 보여주는 모달.
 * 입력: 없음.
 * 출력: onClose.
 * 호출 시점: SignupPage의 약관 줄 오른쪽 "보기" 버튼.
 * 근거: Figma "Modal — 이용약관"(64:2). 모달 본체는 64:39, 본문 묶음은 64:47,
 *   하단 버튼은 64:61("확인했어요"). package_D/DESIGN_SYSTEM.md §6.15와 같은 골격이다.
 *
 * **두 문서의 본문 형식이 다르다(2026-09-15 결정).** 방침은 **용어만 나열한다** — 수집
 * 항목이나 이용 목적은 읽고 이해할 문장이 아니라 확인할 목록이라, 문장으로 이으면 오히려
 * 찾기 어려워진다. 약관은 한 문장씩 쓴다 — 계약 내용이라 용어로 끊으면 뜻이 서지 않는다.
 *
 * 어느 쪽이든 조 하나에 한 줄을 넘기지 않는다. 길어지면 아무도 읽지 않아서, 지키지도 않을
 * 문장을 적어 둔 것과 결과가 같아진다. 왜 그런지는 전부 이 주석에 두고 본문은 결론만
 * 남긴다(Clause.body가 string 하나인 이유).
 *
 * ──────────────────────────────────────────────────────────────────────────
 * **방침의 범위는 회원가입·인증으로 좁혔다(2026-09-15 D 결정).** 7개 조 구성은 D가
 * 지정했다 — 수집 항목 / 이용 목적 / 보유 기간 / 제3자 제공 / 처리 위탁 / 이용자의 권리 /
 * 보호책임자.
 *
 * **그래서 서비스가 실제로 처리하는 다음 것들이 방침에 없다.** 공개 전에 반드시 다시
 * 볼 자리라 지워지지 않게 여기 적어 둔다 — 아래는 전부 같은 날 코드와 DB로 확인한 사실이다.
 *
 *   - **대화 내용** — session_messages(1,029행). 방침의 수집 항목에 없다.
 *   - **위치 좌표** — 요청 처리에만 쓰고 저장하진 않는다(state/store.py::for_persistence()가
 *     저장 사본에서 gps_location을 지운다). 저장하지 않아도 *처리*는 하므로 기재 대상이다.
 *   - **음성과 사진** — Google로 보낸다. 저장은 하지 않는다(routes/transcribe.py는 텍스트만
 *     돌려주고, photo_similar.py에 사진을 쓰거나 올리는 코드가 없다).
 *   - **국외 이전** — providers/gemini.py가 genai.Client(api_key=...)만 쓴다. Vertex가 아니라
 *     AI Studio 경로라 리전을 지정할 수 없어 국외에서 처리될 수 있다.
 *   - **취향·즐겨찾기·저장한 일정** — user_preferences / user_favorites / saved_schedules.
 *     계정 단위로 서버에 쌓이는데 수집 항목에 없다.
 *   - **피드백에 담긴 대화 원문** — response_feedback에 user_input·assistant_message·comment가
 *     있고 실제로 채워져 있다(발화 10건, 주관식 4건). 세션 정리 대상에서 제외돼 있어
 *     (state/store.py) 지워지지 않는다.
 *   - **수탁자 5곳** — 방침에는 Supabase·Google만 적었다. 실제 호출 호스트는 그 밖에
 *     apis.data.go.kr(한국관광공사·기상청·한국천문연구원), openapi.seoul.go.kr(서울시 —
 *     혼잡도·주차장·화장실), dapi.kakao.com, maps/naverapihub.apigw.ntruss.com이 있다.
 *     좌표를 보내므로 개인위치정보 처리로 볼 여지가 있다.
 *   - **쿠키** — 쓰지 않는다. 프론트에 document.cookie·js-cookie가 없고 백엔드에 set_cookie도
 *     없다. Supabase도 storage 옵션을 주지 않아 기본값인 localStorage를 쓴다. 그래서 자동
 *     수집 장치 조를 두지 않은 것은 사실과 어긋나지 않는다.
 *   - **만 14세 미만** — 화면이 나이를 확인하지 않아 법정대리인 동의 절차가 없다.
 * ──────────────────────────────────────────────────────────────────────────
 *
 * **Figma 시안의 문장은 옮기지 않았다.** 시안은 "수집된 정보는 추천 정확도 개선 목적으로만
 * 사용되며, 관련 법령에 따라 안전하게 보관됩니다"라고 적는데 그게 사실이 아니었다.
 * **지키지 못하는 문장을 약관에 적는 것이 안 적는 것보다 나쁘다.** 이 원칙은 범위를 좁힌
 * 뒤에도 그대로다 — 적힌 문장은 전부 사실이어야 하고, 아직 하나가 그렇지 못하다(제3조의
 * "탈퇴 시까지"는 탈퇴 기능이 없다).
 *
 * 약관에서 확인한 사실 둘. 게스트 승계는 auth/AuthContext.tsx가 signUp이 아니라
 * updateUser를 써서 uid가 유지되는 것이고(제3조), 추천 정보의 출처가 외부 API라
 * 정확성을 보장하지 못하는 것은 providers/ 전체가 근거다(제2조).
 *
 * **두 문서를 한 모달에 함께 둔 것은 의도한 선택이다.** 지침은 방침을 다른 고지사항과
 * 구분해 공개하라고 권하지만(p.78) 화면을 쪼개지 않기로 했다(2026-09-15 결정). 대신 두
 * 문서의 제목을 조보다 크게 둬서 경계가 보이게 했다. 방침이 회원가입 모달 안에만 있어
 * **비로그인 이용자는 열 수 없다는 점은 남아 있는 한계다**(지침 p.14는 로그인 여부와
 * 무관한 접근을 요구한다).
 *
 * 남은 할 일과 배경은 package_D/[초안] 이용약관·개인정보처리방침.md에 있다.
 */

import { useEffect, useId } from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";

interface TermsModalProps {
  onClose: () => void;
}

interface Clause {
  title: string;
  /** 한 줄. 약관은 한 문장, 방침은 용어구다. 두 문장이 되면 조를 쪼개라는 신호다. */
  body?: string;
  /** 항목이 둘 이상이면 문장으로 잇지 않고 목록으로 끊는다. */
  bullets?: string[];
}

const TERMS: Clause[] = [
  {
    title: "제1조 (서비스의 내용)",
    body: "TripBranch는 이용자가 말한 상황에 맞는 장소를 추천합니다.",
  },
  {
    title: "제2조 (정보의 정확성)",
    body: "영업시간·혼잡도·이동 시간은 외부에서 받아 온 값이라 실제와 다를 수 있습니다.",
  },
  {
    title: "제3조 (계정)",
    body: "로그인 없이 이용할 수 있고, 가입하면 그동안의 대화와 일정이 계정으로 이어집니다.",
  },
  {
    title: "제4조 (서비스의 변경·중단)",
    body: "기능이 예고 없이 바뀌거나 멈출 수 있고, 저장한 기록도 사라질 수 있습니다.",
  },
  {
    /*
     * **"고의·중대한 과실인 경우는 그렇지 않다"는 단서를 뺀 것은 D의 결정이다
     * (2026-09-15).** 다만 그 단서가 없어도 고의·중과실 책임까지 면제되지는 않는다 —
     * 약관규제법 제7조 제1호가 그런 조항을 무효로 본다. 즉 문장을 줄여서 얻는 것은
     * 없고 잃는 것만 있을 수 있다는 점을 적어 둔다. 되돌릴 때 이 주석을 보면 된다.
     *
     * 약관의 변경 조항도 같은 날 뺐다. 변경을 알리는 근거는 제4조가 겸한다.
     */
    title: "제5조 (책임의 제한)",
    body: "무료 서비스이며, 안내한 장소를 방문했다가 생긴 손해는 책임지지 않습니다.",
  },
];

const PRIVACY: Clause[] = [
  {
    title: "제1조 (수집하는 항목)",
    bullets: ["이메일", "닉네임", "비밀번호 — 되돌릴 수 없는 형태로 저장"],
  },
  {
    title: "제2조 (수집·이용 목적)",
    bullets: ["회원 식별 및 가입", "로그인·인증", "서비스 제공"],
  },
  {
    /*
     * **여기부터는 알면서 지키지 못하는 문장이다(2026-09-15, D 결정).** 실제로는
     * "회원 탈퇴 시까지"가 전부가 아니다 — 보내주신 의견(FeedbackButtons.tsx의
     * "추가 의견")은 탈퇴해도 지워지지 않는다(B와 협의, accounts/deletion.py가
     * response_feedback을 건드리지 않는다). 원래 그 예외를 "— 의견은 예외"로
     * 적어 뒀는데, 문구를 짧게 두는 쪽을 택하며 그 사실을 빼기로 했다.
     *
     * 이 파일의 다른 모든 조는 "지키지 못하는 문장을 적는 것이 안 적는 것보다
     * 나쁘다"를 지킨다(파일 서두). 이 조만 그 규칙의 의도적인 예외다 — 사실을
     * 몰라서가 아니라 알고도 뺐다는 것을 다음에 이 파일을 읽는 사람이 알 수
     * 있게 여기 적어 둔다.
     */
    title: "제3조 (보유 및 이용 기간)",
    body: "회원 탈퇴 시까지",
  },
  {
    title: "제4조 (제3자 제공)",
    body: "제공하지 않음",
  },
  {
    title: "제5조 (처리 위탁)",
    bullets: [
      "Supabase — 계정 인증, 데이터 보관",
      "Google — 대화 해석, 음성 전사, 사진 분석, 문장 번역",
    ],
  },
  {
    title: "제6조 (이용자의 권리)",
    body: "열람·수정·삭제 요청 — 아래 문의처",
  },
  {
    /* 운영 주체가 정해져야 채울 수 있는 유일한 자리다. 비워 두는 대신 왜 비었는지 적는다. */
    title: "제7조 (개인정보 보호책임자와 문의)",
    body: "운영 주체 확정 후 기재",
  },
];

/* 나열 항목은 불릿을 글자로 찍지 않고 marker로 둔다 — 줄이 넘칠 때 둘째 줄이
   불릿 아래로 파고들지 않고 들여쓰기를 유지한다. */
function BulletList({ items }: { items: string[] }) {
  return (
    <ul className="mt-1.5 flex list-outside list-disc flex-col gap-1 pl-4 marker:text-muted">
      {items.map((item) => (
        <li key={item} className="text-sm leading-relaxed text-muted">
          {item}
        </li>
      ))}
    </ul>
  );
}

function ClauseGroup({
  heading,
  clauses,
  className,
}: {
  heading: string;
  clauses: Clause[];
  className?: string;
}) {
  return (
    <section className={className}>
      {/* 한 모달에 두 문서가 들어 있다. 문서 제목을 항목 제목보다 확실히 크게 둬야
          "7. 약관이 바뀔 때" 다음에 "1. 무엇에 쓰나"가 오는 자리에서 경계가 보인다. */}
      <h3 className="border-b border-border pb-1.5 text-sm font-bold text-ink">{heading}</h3>
      <div className="mt-3 flex flex-col gap-4">
        {clauses.map((clause) => (
          <article key={clause.title}>
            <h4 className="text-sm font-bold text-ink">{clause.title}</h4>
            {clause.body ? (
              <p className="mt-1.5 text-sm leading-relaxed text-muted">{clause.body}</p>
            ) : null}
            {clause.bullets ? <BulletList items={clause.bullets} /> : null}
          </article>
        ))}
      </div>
    </section>
  );
}

export function TermsModal({ onClose }: TermsModalProps) {
  const titleId = useId();

  /* 형제 모달(AddFavorite·AddKeyword)에는 없는 처리다. 저 둘은 입력칸 하나짜리라
     닫을 곳이 바로 보이지만, 이건 본문이 길어 스크롤하다 보면 X 버튼이 화면 밖으로
     밀린다 — 키보드로도 빠져나갈 길을 둔다. */
  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [onClose]);

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-end justify-center p-4 md:items-center">
      <button
        type="button"
        aria-label="닫기"
        onClick={onClose}
        className="absolute inset-0 bg-ink-strong/40"
      />
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className="relative flex max-h-[80dvh] w-full max-w-md flex-col rounded-3xl bg-white p-5 pb-6 shadow-card"
      >
        {/* Head — Figma 64:40. 제목 왼쪽, 닫기 아이콘 오른쪽 끝. */}
        <div className="flex shrink-0 items-center justify-between gap-2">
          <h2 id={titleId} className="text-base font-bold text-ink">
            이용약관 및 개인정보처리방침
          </h2>
          <button
            type="button"
            aria-label="닫기"
            onClick={onClose}
            className="flex h-8 w-8 shrink-0 items-center justify-center text-muted transition-colors hover:text-ink"
          >
            <X size={18} />
          </button>
        </div>

        {/* 본문 — Figma 64:47. 길어지면 이 안에서만 스크롤한다.
            overscroll-contain이 "이 안에서만"을 실제로 지킨다 — 없으면 끝에 닿았을 때
            스크롤이 뒤 화면으로 넘어간다(RecommendationDetailPreviewModal과 같은 이유). */}
        <div className="mt-3 min-h-0 flex-1 overflow-y-auto overscroll-contain">
          <ClauseGroup heading="이용약관" clauses={TERMS} />
          <ClauseGroup heading="개인정보처리방침" clauses={PRIVACY} className="mt-7" />
        </div>

        {/* Figma 64:61은 "확인했어요"다. 동의 체크박스를 대신 켜지는 않는다 —
            읽었다는 것과 동의한다는 것은 다르고, 동의는 사용자가 직접 눌러야 한다. */}
        <button
          type="button"
          onClick={onClose}
          className="mt-5 flex h-12 w-full shrink-0 items-center justify-center rounded-full bg-brand text-sm font-bold text-white transition-colors hover:bg-brand-deep"
        >
          확인했어요
        </button>
      </div>
    </div>,
    document.body,
  );
}
