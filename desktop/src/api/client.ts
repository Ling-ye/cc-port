import { invoke } from "@tauri-apps/api/core";
import type { LpmResponse } from "@/types/lpm";

export async function lpmAction<T>(
  action: string,
  payload: Record<string, unknown> = {},
): Promise<T> {
  const response = await invoke<LpmResponse<T>>("lpm_action", {
    request: { action, payload },
  });

  if (!response.ok) {
    const message = response.error?.message || "LPM action failed";
    throw new Error(message);
  }

  return response.data as T;
}
