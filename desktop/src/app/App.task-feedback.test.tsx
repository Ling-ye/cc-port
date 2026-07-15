import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { TaskCenterProvider } from "@/app/TaskCenterContext";
import App from "@/app/App";

vi.mock("@/api/client", () => ({
  lpmAction: vi.fn(async (action: string) => {
    if (action === "summary") {
      return {
        counts: { total: 0, by_kind: {}, by_source: {} },
        installed: 0,
        updates: 0,
        registry_path: "~/LPM/registry.yaml",
        resource_repo_display_name: "Test repository",
      };
    }
    if (action === "resource_inventory") return { registry_path: "~/LPM/registry.yaml", items: [] };
    if (action === "platforms") return { platforms: [] };
    if (action === "doctor") return { checks: [{ id: "git", label: "Git", ok: true, detail: "Ready" }] };
    throw new Error(`Unexpected action: ${action}`);
  }),
  openPath: vi.fn(),
}));

afterEach(() => {
  cleanup();
  window.localStorage.clear();
});

describe("App task feedback", () => {
  it("shows tracked completion in Toast and task center without a success banner", async () => {
    const user = userEvent.setup();
    render(
      <TaskCenterProvider>
        <App />
      </TaskCenterProvider>,
    );

    await user.click(await screen.findByRole("button", { name: "健康检查" }));
    await user.click(screen.getByRole("button", { name: "运行检查" }));

    expect(await screen.findByRole("status")).toHaveTextContent("已完成 1 项检查");
    expect(document.querySelector(".banner.success")).toBeNull();

    await user.click(screen.getByRole("button", { name: "任务中心" }));
    expect(screen.getByRole("complementary", { name: "任务中心" })).toHaveTextContent("运行检查");
    await waitFor(() => expect(screen.getByText("已完成")).toBeVisible());
  });
});

