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
    if (action === "asset_inventory") {
      return {
        branch: "main",
        remote_commit: "abc123",
        repo_url: "",
        remote_available: true,
        remote_warning: "",
        scanned_local: false,
        generated_at: "",
        legacy_write_blocker: "",
        rows: [],
      };
    }
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

    await screen.findByText("资源类型");
    await user.click(screen.getByTitle("刷新"));

    expect(await screen.findByRole("status")).toHaveTextContent("刷新");
    expect(document.querySelector(".banner.success")).toBeNull();

    await user.click(screen.getByRole("button", { name: "任务中心" }));
    expect(screen.getByRole("complementary", { name: "任务中心" })).toHaveTextContent("刷新");
    await waitFor(() => expect(screen.getByText("已完成")).toBeVisible());
  });
});
