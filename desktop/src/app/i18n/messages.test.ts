import { describe, expect, it } from "vitest";
import { LpmApiError } from "@/api/client";
import {
  createTranslator,
  displayError,
  translateMessage,
  translateMessageList,
} from "@/app/i18n";

const localOnly = {
  code: "asset.diff.local_only",
  fallback: "Local content is not present in the remote repository.",
};

describe("desktop semantic messages", () => {
  it("renders the same message reference in the active language", () => {
    expect(translateMessage(localOnly, createTranslator("en"))).toBe(
      "Local content is not present in the remote repository.",
    );
    expect(translateMessage(localOnly, createTranslator("zh"))).toBe(
      "本地内容不存在于远端仓库中。",
    );
  });

  it("interpolates structured parameters in both languages", () => {
    const ref = {
      code: "asset.warning.identical_content",
      params: { resource_keys: "skill:a, skill:b" },
      fallback: "Identical content also exists under: skill:a, skill:b",
    };
    expect(translateMessage(ref, createTranslator("en"))).toContain("skill:a, skill:b");
    expect(translateMessage(ref, createTranslator("zh"))).toBe(
      "以下资源键下也存在相同内容：skill:a, skill:b",
    );
  });

  it("falls back safely for an unknown message code", () => {
    expect(translateMessage(
      { code: "future.message", fallback: "Future fallback" },
      createTranslator("zh"),
    )).toBe("Future fallback");
  });

  it("keeps legacy values when references are absent", () => {
    expect(translateMessageList(undefined, ["external diagnostic"], createTranslator("zh")))
      .toEqual(["external diagnostic"]);
  });

  it("renders API and bridge failures through the same resolver", () => {
    const apiError = new LpmApiError(
      "invalid_payload",
      "legacy payload error",
      {
        code: "api.invalid_payload",
        fallback: "The desktop request payload must be a JSON object.",
      },
    );
    expect(displayError(apiError, createTranslator("zh"))).toBe(
      "桌面请求载荷必须是 JSON 对象。",
    );
    expect(displayError(
      { code: "bridge.open_path_failed", detail: "C:\\missing" },
      createTranslator("en"),
    )).toBe("Unable to open the local path: C:\\missing");
  });
});
