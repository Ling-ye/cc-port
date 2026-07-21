import { describe, expect, it } from "vitest";
import { navItems } from "@/app/navigation";

describe("removed desktop entry points", () => {
  it("keeps resources as the first entry and removes obsolete pages", () => {
    const ids = navItems.map((item) => String(item.id));
    expect(ids[0]).toBe("resources");
    expect(ids).not.toContain("dashboard");
    expect(ids).not.toContain("add");
    expect(ids).not.toContain("environment");
  });
});
