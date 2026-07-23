import { invoke } from "@tauri-apps/api/core";
import { writeText } from "@tauri-apps/plugin-clipboard-manager";
import { getCurrent, onOpenUrl } from "@tauri-apps/plugin-deep-link";
import { open } from "@tauri-apps/plugin-dialog";
import { openUrl } from "@tauri-apps/plugin-opener";
import type { LpmResponse, UiMessageRef } from "@/types/lpm";

export class LpmApiError extends Error {
  code: string;
  messageRef?: UiMessageRef;

  constructor(code: string, message: string, messageRef?: UiMessageRef) {
    super(message);
    this.name = "LpmApiError";
    this.code = code;
    this.messageRef = messageRef;
  }
}

export async function lpmAction<T>(
  action: string,
  payload: Record<string, unknown> = {},
): Promise<T> {
  const response = await invoke<LpmResponse<T>>("lpm_action", {
    request: { action, payload },
  });

  if (!response.ok) {
    const message = response.error?.message || "LPM action failed";
    throw new LpmApiError(
      response.error?.code || "lpm_error",
      message,
      response.error?.message_ref || undefined,
    );
  }

  return response.data as T;
}

export async function openPath(path: string): Promise<void> {
  await invoke("open_path", { path });
}

export async function openExternalUrl(url: string): Promise<void> {
  await openUrl(url);
}

export async function listenForOAuthDeepLinks(
  handler: (urls: string[]) => void,
): Promise<() => void> {
  const unlisten = await onOpenUrl(handler);
  const current = await getCurrent();
  if (current?.length) handler(current);
  return unlisten;
}

export async function copyText(value: string): Promise<void> {
  await writeText(value);
}

export async function selectDirectory(): Promise<string | null> {
  return open({ directory: true, multiple: false });
}
