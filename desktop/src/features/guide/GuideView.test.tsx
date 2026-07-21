import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { openExternalUrl } from "@/api/client";
import { createTranslator } from "@/app/i18n";
import { navItems } from "@/app/navigation";
import { GuideView } from "@/features/guide/GuideView";

vi.mock("@/api/client", () => ({
  openExternalUrl: vi.fn(),
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("GuideView merged project information", () => {
  it("keeps only the Guide navigation entry", () => {
    expect(navItems.some((item) => item.id === "guide")).toBe(true);
    expect(navItems.some((item) => String(item.id) === "about")).toBe(false);
    expect(navItems.some((item) => String(item.id) === "health")).toBe(false);
  });

  it("shows resources, desktop functions, and project information in one page", async () => {
    const user = userEvent.setup();
    render(<GuideView t={createTranslator("en")} />);

    expect(screen.getByText("Managed resource types")).toBeVisible();
    expect(screen.getByText("Desktop app functions")).toBeVisible();
    expect(screen.getByText("Project information")).toBeVisible();
    expect(screen.queryByText("Health")).not.toBeInTheDocument();
    expect(screen.queryByText("Overview")).not.toBeInTheDocument();
    expect(screen.queryByText("Add Resource")).not.toBeInTheDocument();
    expect(screen.getByText(/Collects GitHub references, imports local folders/)).toBeVisible();
    expect(screen.getByText("Open source under the MIT License.")).toBeVisible();

    await user.click(screen.getByRole("button", { name: /github.com\/Ling-ye\/LingyePluginMarketplace/ }));
    expect(openExternalUrl).toHaveBeenCalledWith(
      "https://github.com/Ling-ye/LingyePluginMarketplace",
    );
  });
});
