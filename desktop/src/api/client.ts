import { invoke } from "@tauri-apps/api/core";
import { writeText } from "@tauri-apps/plugin-clipboard-manager";
import { open } from "@tauri-apps/plugin-dialog";
import { openUrl } from "@tauri-apps/plugin-opener";
import type { CcPortResponse, UiMessageRef } from "@/types/cc-port";

export class CcPortApiError extends Error {
  code: string;
  messageRef?: UiMessageRef;

  constructor(code: string, message: string, messageRef?: UiMessageRef) {
    super(message);
    this.name = "CcPortApiError";
    this.code = code;
    this.messageRef = messageRef;
  }
}

export async function ccPortAction<T>(
  action: string,
  payload: Record<string, unknown> = {},
): Promise<T> {
  const response = await invoke<CcPortResponse<T>>("cc_port_action", {
    request: { action, payload },
  });

  if (!response.ok) {
    const message = response.error?.message || "CC Port action failed";
    throw new CcPortApiError(
      response.error?.code || "cc_port_error",
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

export async function copyText(value: string): Promise<void> {
  await writeText(value);
}

export async function selectDirectory(): Promise<string | null> {
  return open({ directory: true, multiple: false });
}
