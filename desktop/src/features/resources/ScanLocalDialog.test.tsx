import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { lpmAction, selectDirectory } from "@/api/client";
import { createTranslator } from "@/app/i18n";
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
    const onScan = vi.fn();
    const onClose = vi.fn();
    vi.mocked(lpmAction).mockImplementation(async (action) => {
      if (action === "plugin_projects_list") return { projects: [project] } as never;
      throw new Error(`unexpected ${action}`);
    });
    render(<ScanLocalDialog t={t} onClose={onClose} onScan={onScan} />);

    expect(await screen.findByText("D:/code/demo")).toBeVisible();
    await user.click(screen.getByRole("checkbox", { name: "Scan global plugin locations" }));
    await user.click(screen.getByRole("button", { name: "Scan local" }));

    expect(onScan).toHaveBeenCalledWith({
      scan_global: false,
      project_ids: ["project-demo"],
    });
    expect(onClose).not.toHaveBeenCalled();
    expect(lpmAction).toHaveBeenCalledTimes(1);
  });

  it("keeps project mappings unchanged when directory selection is cancelled", async () => {
    const user = userEvent.setup();
    vi.mocked(selectDirectory).mockResolvedValue(null);
    vi.mocked(lpmAction).mockResolvedValue({ projects: [project] } as never);
    render(<ScanLocalDialog t={t} onClose={vi.fn()} onScan={vi.fn()} />);

    await screen.findByText("D:/code/demo");
    await user.click(screen.getByRole("button", { name: "Add project" }));

    expect(lpmAction).not.toHaveBeenCalledWith("plugin_projects_add", expect.anything());
    expect(screen.getByText("D:/code/demo")).toBeVisible();
  });
});
