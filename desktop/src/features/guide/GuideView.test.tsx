import { cleanup, render, screen, waitFor } from "@testing-library/react";
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
    const onError = vi.fn();
    render(<GuideView t={createTranslator("en")} onError={onError} />);

    expect(screen.getByText("Enjoying CC Port?")).toBeVisible();
    expect(screen.getByText("Ling-ye/cc-port")).toBeVisible();
    expect(screen.getByText(/star it on GitHub to support its continued improvement/)).toBeVisible();
    expect(screen.getByText("Managed resource types")).toBeVisible();
    expect(screen.getByText("Desktop app functions")).toBeVisible();
    expect(screen.getByText("Project information")).toBeVisible();
    expect(screen.queryByText("Health")).not.toBeInTheDocument();
    expect(screen.queryByText("Overview")).not.toBeInTheDocument();
    expect(screen.queryByText("Add Resource")).not.toBeInTheDocument();
    expect(screen.getByText(/Collects GitHub references, imports local folders/)).toBeVisible();
    expect(screen.getByText("Open source under the MIT License.")).toBeVisible();

    await user.click(screen.getByRole("button", { name: "Star CC Port on GitHub" }));
    await user.click(screen.getByRole("button", { name: /github.com\/Ling-ye\/cc-port/ }));
    expect(openExternalUrl).toHaveBeenNthCalledWith(1, "https://github.com/Ling-ye/cc-port");
    expect(openExternalUrl).toHaveBeenNthCalledWith(2, "https://github.com/Ling-ye/cc-port");
    expect(onError).not.toHaveBeenCalled();
  });

  it("renders the friendly Star invitation in Chinese", () => {
    render(<GuideView t={createTranslator("zh")} onError={vi.fn()} />);

    expect(screen.getByText("喜欢 CC Port？")).toBeVisible();
    expect(screen.getByText("如果 CC Port 对你有帮助，欢迎在 GitHub 点个 Star，支持项目持续完善。")).toBeVisible();
    expect(screen.getByRole("button", { name: "去 GitHub 加 Star" })).toBeVisible();
  });

  it("reports an external-link failure through the app error channel", async () => {
    const user = userEvent.setup();
    const onError = vi.fn();
    vi.mocked(openExternalUrl).mockRejectedValueOnce(new Error("Browser unavailable"));
    render(<GuideView t={createTranslator("en")} onError={onError} />);

    await user.click(screen.getByRole("button", { name: "Star CC Port on GitHub" }));

    await waitFor(() => expect(onError).toHaveBeenCalledWith("Browser unavailable"));
  });
});
