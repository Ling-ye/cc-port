import { describe, expect, it } from "vitest";
import { navItems } from "@/app/navigation";

describe("removed desktop entry points", () => {
  it("keeps only resources, settings, and guide in desktop navigation", () => {
    const ids = navItems.map((item) => String(item.id));
    expect(ids).toEqual(["resources", "settings", "guide"]);
    expect(ids).not.toContain("operations");
  });
});
