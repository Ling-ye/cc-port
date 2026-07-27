import { en, translations, type I18nKey } from "@/app/i18n/catalog";

export type Language = keyof typeof translations;
export type TranslationValues = Record<string, string | number>;
export type TFunction = (key: I18nKey, values?: TranslationValues) => string;

export const DEFAULT_LANGUAGE: Language = "zh";
export const LANGUAGE_STORAGE_KEY = "cc-port.language";

export function readStoredLanguage(): Language {
  if (typeof window === "undefined") return DEFAULT_LANGUAGE;

  try {
    const value = window.localStorage.getItem(LANGUAGE_STORAGE_KEY);
    return value === "en" || value === "zh" ? value : DEFAULT_LANGUAGE;
  } catch {
    return DEFAULT_LANGUAGE;
  }
}

export function storeLanguage(language: Language) {
  try {
    window.localStorage.setItem(LANGUAGE_STORAGE_KEY, language);
  } catch {
    // Ignore storage failures; the in-memory language state still works.
  }
}

export function nextLanguage(language: Language): Language {
  return language === "zh" ? "en" : "zh";
}

export function createTranslator(language: Language): TFunction {
  const dictionary = translations[language];
  return (key, values) => interpolate(dictionary[key] || en[key], values);
}

export function interpolate(text: string, values?: TranslationValues): string {
  if (!values) return text;
  return text.replace(/\{(\w+)\}/g, (match, key: string) => String(values[key] ?? match));
}
