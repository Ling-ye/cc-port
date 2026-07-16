import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { TaskCenterProvider } from "@/app/TaskCenterContext";
import { lpmAction } from "@/api/client";
import { RepositorySyncView } from "@/features/repository-sync/RepositorySyncView";

vi.mock("@/api/client", () => ({
  lpmAction: vi.fn(),
}));

const mockedAction = vi.mocked(lpmAction);
const t = (key: string, params?: Record<string, string | number>) => (
  params?.count !== undefined ? `${key}:${params.count}` : key
);

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("RepositorySyncView resource commit workflow", () => {
  it("shows resource-level blockers and disables commit", async () => {
    mockedAction.mockImplementation(async (action: string) => {
      if (action === "resource_sync_status") return dirtySyncPlan();
      if (action === "resource_commit_plan") {
        return {
          repo_path: "C:/resources",
          changed_paths: ["notes.txt"],
          managed_paths: [],
          resources: [{ name: "notes.txt", kind: "metadata", action: "added", paths: ["notes.txt"] }],
          blocked_paths: [{ path: "notes.txt", reason: "outside managed scope" }],
          secret_findings: [],
          suggested_message: "lpm: update resource metadata",
          blocked: true,
        };
      }
      throw new Error(`Unexpected action: ${action}`);
    });
    const user = userEvent.setup();

    render(
      <TaskCenterProvider>
        <RepositorySyncView t={t} />
      </TaskCenterProvider>,
    );
    await user.click(screen.getByRole("button", { name: "repoSync.refresh" }));

    expect(await screen.findByText("notes.txt: outside managed scope")).toBeVisible();
    expect(screen.getByRole("button", { name: "repoSync.commitPush" })).toBeDisabled();
  });

  it("commits an approved resource plan and refreshes synchronization state", async () => {
    let statusCalls = 0;
    mockedAction.mockImplementation(async (action: string, payload?: Record<string, unknown>) => {
      if (action === "resource_sync_status") {
        statusCalls += 1;
        return statusCalls === 1 ? dirtySyncPlan() : { ...dirtySyncPlan(), status: "clean" };
      }
      if (action === "resource_commit_plan") {
        return {
          repo_path: "C:/resources",
          changed_paths: ["skills/demo/SKILL.md"],
          managed_paths: ["skills/demo/SKILL.md"],
          resources: [{
            name: "demo",
            kind: "skill",
            action: "added",
            paths: ["skills/demo/SKILL.md"],
          }],
          blocked_paths: [],
          secret_findings: [],
          suggested_message: "lpm: added skill demo",
          blocked: false,
        };
      }
      if (action === "resource_commit_push") {
        expect(payload).toEqual({ message: "lpm: added skill demo" });
        return { dirty: false };
      }
      throw new Error(`Unexpected action: ${action}`);
    });
    const user = userEvent.setup();

    render(
      <TaskCenterProvider>
        <RepositorySyncView t={t} />
      </TaskCenterProvider>,
    );
    await user.click(screen.getByRole("button", { name: "repoSync.refresh" }));
    await user.click(await screen.findByRole("button", { name: "repoSync.commitPush" }));

    await waitFor(() => expect(mockedAction).toHaveBeenCalledWith(
      "resource_commit_push",
      { message: "lpm: added skill demo" },
    ));
    expect(await screen.findByText("repoSync.status.clean")).toBeVisible();
  });
});

function dirtySyncPlan() {
  return {
    operation_id: "",
    repo_path: "C:/resources",
    branch: "main",
    status: "dirty",
    local_commit: "local",
    remote_commit: "remote",
    merge_base: "base",
    ahead: 0,
    behind: 0,
    worktree_path: null,
    merge_commit: null,
    conflicts: [],
    detail: "Commit local resource changes before synchronization.",
    created_at: "",
    updated_at: "",
  };
}
