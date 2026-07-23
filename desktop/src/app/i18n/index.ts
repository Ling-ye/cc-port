export type { I18nKey } from "@/app/i18n/catalog";
export {
  createTranslator,
  DEFAULT_LANGUAGE,
  interpolate,
  LANGUAGE_STORAGE_KEY,
  nextLanguage,
  readStoredLanguage,
  storeLanguage,
  type Language,
  type TFunction,
  type TranslationValues,
} from "@/app/i18n/core";
export { resourceKindLabel } from "@/app/i18n/domainLabels";
export {
  displayError,
  translateMessage,
  translateMessageList,
  type UiMessageRef,
} from "@/app/i18n/messages";
