import { en, type I18nKey } from "@/app/i18n/catalog";
import type { TFunction, TranslationValues } from "@/app/i18n/core";
import type { UiMessageParam, UiMessageRef } from "@/types/lpm";

export type { UiMessageRef } from "@/types/lpm";

type MessageI18nKey = Extract<I18nKey, `message.${string}`>;

const legacyErrorCodes: Record<string, string> = {
  OAuthConfigurationError: "api.github.oauth_not_configured",
  GithubApiError: "api.github.request_failed",
  OAuthSessionError: "api.github.session_failed",
  GithubAuthError: "api.github.authentication_failed",
  GithubOwnerScopeRequired: "api.github.owner_scope_required",
  GithubDeleteScopeRequired: "api.github.delete_scope_required",
};

export function translateMessage(
  ref: UiMessageRef | null | undefined,
  t: TFunction,
  legacyFallback = "",
): string {
  if (!ref) return legacyFallback;
  const key = `message.${ref.code}` as MessageI18nKey;
  if (Object.prototype.hasOwnProperty.call(en, key)) {
    return t(key, normalizeValues(ref.params));
  }
  return ref.fallback || legacyFallback || t("common.unknownError");
}

export function translateMessageList(
  refs: UiMessageRef[] | null | undefined,
  legacyValues: string[],
  t: TFunction,
): string[] {
  if (!refs?.length) return legacyValues;
  const length = Math.max(refs.length, legacyValues.length);
  return Array.from({ length }, (_, index) => {
    const ref = refs[index];
    return ref ? translateMessage(ref, t, legacyValues[index] || "") : legacyValues[index];
  });
}

export function displayError(error: unknown, t: TFunction): string {
  const messageRef = readMessageRef(error);
  if (messageRef) return translateMessage(messageRef, t);
  if (isRecord(error)) {
    const code = typeof error.code === "string" ? error.code : "";
    const detail = typeof error.detail === "string" ? error.detail : "";
    const legacyMessageCode = legacyErrorCodes[code];
    if (legacyMessageCode) {
      const fallback = typeof error.message === "string" ? error.message : detail;
      return translateMessage(
        {
          code: legacyMessageCode,
          fallback,
          params: legacyMessageCode === "api.github.authentication_failed"
            ? { detail: fallback }
            : undefined,
        },
        t,
      );
    }
    if (code.startsWith("bridge.")) {
      return translateMessage({ code, fallback: detail, params: { detail } }, t);
    }
    if (typeof error.message === "string") return error.message;
    if (detail) return detail;
  }
  if (error instanceof Error) return error.message || t("common.unknownError");
  return typeof error === "string" && error ? error : t("common.unknownError");
}

function readMessageRef(error: unknown): UiMessageRef | null {
  if (!isRecord(error)) return null;
  if (isUiMessageRef(error.messageRef)) return error.messageRef;
  if (isUiMessageRef(error.message_ref)) return error.message_ref;
  return null;
}

function isUiMessageRef(value: unknown): value is UiMessageRef {
  return isRecord(value)
    && typeof value.code === "string"
    && typeof value.fallback === "string";
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function normalizeValues(
  values: Record<string, UiMessageParam> | undefined,
): TranslationValues | undefined {
  if (!values) return undefined;
  return Object.fromEntries(
    Object.entries(values).map(([key, value]) => [key, value === null ? "" : String(value)]),
  );
}
