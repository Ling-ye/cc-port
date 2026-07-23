import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

describe("desktop window layout contract", () => {
  it("opens at the documented default size and enforces the minimum workspace", () => {
    const configPath = resolve(process.cwd(), "src-tauri", "tauri.conf.json");
    const config = JSON.parse(readFileSync(configPath, "utf8"));

    expect(config.app.windows[0]).toMatchObject({
      width: 1360,
      height: 820,
      minWidth: 1280,
      minHeight: 720,
    });
  });
});
