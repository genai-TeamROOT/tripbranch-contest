import { useState } from "react";

import type { Language } from "../types";

interface LanguageSelectorProps {
  language: Language;
  onChange: (language: Language) => void;
}

/** 한국어 Runtime을 유지하면서 화면·입출력 언어만 전환하는 작은 선택기. */
export function LanguageSelector({ language, onChange }: LanguageSelectorProps) {
  const [isOpen, setIsOpen] = useState(false);
  const selectLanguage = (nextLanguage: Language) => {
    onChange(nextLanguage);
    setIsOpen(false);
  };

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setIsOpen((open) => !open)}
        aria-haspopup="menu"
        aria-expanded={isOpen}
        aria-label="언어 선택"
        className="inline-flex items-center gap-1 rounded-md border border-gray-300 px-2 py-1 text-xs font-medium text-gray-700 hover:bg-gray-50 dark:border-gray-700 dark:text-gray-200 dark:hover:bg-gray-800"
      >
        언어
        <span aria-hidden="true" className="text-[10px]">▾</span>
      </button>
      {isOpen && (
        <div
          role="menu"
          aria-label="언어 목록"
          className="absolute right-0 z-30 mt-1 min-w-24 overflow-hidden rounded-md border border-gray-200 bg-white py-1 text-xs shadow-lg dark:border-gray-700 dark:bg-gray-900"
        >
          <button
            type="button"
            role="menuitemradio"
            aria-checked={language === "ko"}
            onClick={() => selectLanguage("ko")}
            className={`flex w-full items-center justify-between px-3 py-1.5 text-left hover:bg-gray-50 dark:hover:bg-gray-800 ${
              language === "ko" ? "font-semibold text-gray-900 dark:text-white" : "text-gray-600 dark:text-gray-300"
            }`}
          >
            한국어
            {language === "ko" && <span aria-hidden="true">✓</span>}
          </button>
          <button
            type="button"
            role="menuitemradio"
            aria-checked={language === "en"}
            onClick={() => selectLanguage("en")}
            className={`flex w-full items-center justify-between px-3 py-1.5 text-left hover:bg-gray-50 dark:hover:bg-gray-800 ${
              language === "en" ? "font-semibold text-gray-900 dark:text-white" : "text-gray-600 dark:text-gray-300"
            }`}
          >
            English
            {language === "en" && <span aria-hidden="true">✓</span>}
          </button>
        </div>
      )}
    </div>
  );
}
