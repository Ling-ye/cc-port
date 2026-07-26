import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, describe, expect, it } from "vitest";

const globalStyles = readFileSync(resolve(process.cwd(), "src/styles/global.css"), "utf8");

function cssRule(selector) {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = globalStyles.match(new RegExp(`${escaped}\\s*\\{[^}]*\\}`));
  expect(match, `Missing ${selector} rule`).not.toBeNull();
  return match?.[0] ?? "";
}

describe("asset pill layout contract", () => {
  afterEach(() => {
    document.head.innerHTML = "";
    document.body.innerHTML = "";
  });

  it("keeps asset labels horizontal, content-sized, and non-shrinking", () => {
    const style = document.createElement("style");
    style.textContent = [
      cssRule(".asset-pill"),
      cssRule(".asset-resource-table"),
      cssRule(".asset-status-column"),
    ].join("\n");
    document.head.append(style);
    document.body.innerHTML = `
      <span class="asset-pill kind kind-prompt">提示词</span>
      <span class="asset-pill asset-status status-uncomparable">无法安全比较</span>
      <span class="asset-pill asset-source-state state-online">在线</span>
      <table class="asset-resource-table">
        <colgroup>
          <col class="asset-select-column">
          <col class="asset-name-column">
          <col class="asset-description-column">
          <col class="asset-status-column">
        </colgroup>
      </table>
    `;

    for (const label of document.querySelectorAll(".asset-pill")) {
      const computed = getComputedStyle(label);
      expect(computed.display).toBe("inline-flex");
      expect(computed.flexGrow).toBe("0");
      expect(computed.flexShrink).toBe("0");
      expect(computed.inlineSize).toBe("max-content");
      expect(computed.maxWidth).toBe("none");
      expect(computed.whiteSpace).toBe("nowrap");
      expect(computed.writingMode).toBe("horizontal-tb");
    }

    expect(getComputedStyle(document.querySelector(".asset-resource-table")).tableLayout).toBe("auto");
    expect(getComputedStyle(document.querySelector(".asset-status-column")).width).toBe("1%");
  });
});
