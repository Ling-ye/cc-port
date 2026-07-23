import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { lpmAction, selectDirectory } from "@/api/client";
import { createTranslator } from "@/app/i18n";
import { TaskCenterProvider } from "@/app/TaskCenterContext";
import { ScanLocalDialog } from "@/features/resources/ScanLocalDialog";

vi.mock("@/api/client", () => ({
  lpmAction: vi.fn(),
  selectDirectory: vi.fn(),
}));

const t = createTranslator("en");
const project = {
  id: "project-demo",
  path: "D:/code/demo",
  repo: "github.com/acme/demo",
  subdir: "",
  portable: true,
  exists: true,
};

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("ScanLocalDialog", () => {
  it("scans only the selected global and saved project roots", async () => {
    const user = userEvent.setup();
    const onScanned = vi.fn();
    vi.mocked(lpmAction).mockImplementation(async (action) => {
      if (action === "plugin_projects_list") return { projects: [project] } as never;
      if (action === "asset_inventory") return { resources: [] } as never;
      throw new Error(`unexpected ${action}`);
    });
    render(
      <TaskCenterProvider>
        <ScanLocalDialog t={t} onClose={vi.fn()} onScanned={onScanned} />
      </TaskCenterProvider>,
    );

    expect(await screen.findByText("D:/code/demo")).toBeVisible();
    await user.click(screen.getByRole("checkbox", { name: "Scan global plugin locations" }));
    await user.click(screen.getByRole("button", { name: "Scan local" }));

    await waitFor(() => expect(onScanned).toHaveBeenCalled());
    expect(lpmAction).toHaveBeenCalledWith("asset_inventory", {
      scan_local: true,
      scan_global: false,
      project_ids: ["project-demo"],
      refresh_remote: false,
    });
    expect(onScanned).toHaveBeenCalledWith(
      expect.objectContaining({ resources: [] }),
      { scan_global: false, project_ids: ["project-demo"] },
    );
  });

  it("keeps project mappings unchanged when directory selection is cancelled", async () => {
    const user = userEvent.setup();
    vi.mocked(selectDirectory).mockResolvedValue(null);
    vi.mocked(lpmAction).mockResolvedValue({ projects: [project] } as never);
    render(
      <TaskCenterProvider>
        <ScanLocalDialog t={t} onClose={vi.fn()} onScanned={vi.fn()} />
      </TaskCenterProvider>,
    );

    await screen.findByText("D:/code/demo");
    await user.click(screen.getByRole("button", { name: "Add project" }));

    expect(lpmAction).not.toHaveBeenCalledWith("plugin_projects_add", expect.anything());
    expect(screen.getByText("D:/code/demo")).toBeVisible();
  });
});
