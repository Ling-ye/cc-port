import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { lpmAction, openExternalUrl } from "@/api/client";
import { createTranslator } from "@/app/i18n";
import { TaskCenterProvider } from "@/app/TaskCenterContext";
import { ResourcesView } from "@/features/resources/ResourcesView";
import type {
  AssetActionPlan,
  AssetActionResult,
  AssetInventory,
  AssetPlatformRow,
} from "@/types/lpm";

vi.mock("@/api/client", () => ({
  copyText: vi.fn(),
  lpmAction: vi.fn(),
  openExternalUrl: vi.fn(),
  openPath: vi.fn(),
}));

const t = createTranslator("en");

function row(overrides: Partial<AssetPlatformRow> = {}): AssetPlatformRow {
  return {
    resource_key: "skill:demo",
    kind: "skill",
    name: "demo",
    platform: "cursor",
    local_instance_id: "expected-cursor-demo",
    local_locator: "expected",
    install_name: "demo",
    configured: true,
    enabled: true,
    detected: true,
    supported: true,
    remote_exists: true,
    local_exists: true,
    remote_writable: true,
    read_only_reference: false,
    remote_path: "resources/skills/demo",
    local_path: "C:/Users/test/.cursor/skills/demo",
    target_path: "C:/Users/test/.cursor/skills/demo",
    ownership: "managed",
    status: "content-different",
    remote_commit: "1234567890abcdef",
    reference_commit: "",
    remote_content_fingerprint: "remote",
    remote_asset_fingerprint: "remote-asset",
    local_fingerprint: "local",
    metadata_differences: ["description"],
    diff_summary: ["Local and remote content differ."],
    blockers: [],
    warnings: [],
    available_actions: [
      "download",
      "upload",
      "copy-to-local",
      "copy-to-remote",
      "set-platform-install-name",
    ],
    entry: {
      name: "demo",
      kind: "skill",
      source: "local",
      repo: "",
      path: "resources/skills/demo",
      subdir: "",
      ref: "main",
      install_dir: "",
      description: "Demo",
      tags: [],
      category: "",
    },
    ...overrides,
  };
}

function inventory(rows: AssetPlatformRow[]): AssetInventory {
  return {
    branch: "main",
    remote_commit: "1234567890abcdef",
    repo_url: "https://example.test/resources.git",
    remote_available: true,
    remote_warning: "",
    scanned_local: true,
    generated_at: "2026-07-17T00:00:00Z",
    legacy_write_blocker: "",
    rows,
  };
}

function renderView(rows: AssetPlatformRow[]) {
  const data = inventory(rows);
  const onChanged = vi.fn(async () => undefined);
  render(
    <TaskCenterProvider>
      <ResourcesView
        inventory={data}
        selected={rows[0]}
        t={t}
        onSelect={vi.fn()}
        onInventory={vi.fn()}
        onChanged={onChanged}
        onError={vi.fn()}
      />
    </TaskCenterProvider>,
  );
  return { onChanged };
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("ResourcesView asset synchronization", () => {
  it("shows per-platform differences and requires explicit replacement before apply", async () => {
    const user = userEvent.setup();
    const plan: AssetActionPlan = {
      operation_id: "plan-1",
      action: "download",
      resource_key: "skill:demo",
      target_resource_key: "skill:demo",
      kind: "skill",
      name: "demo",
      platform: "cursor",
      local_instance_id: "expected-cursor-demo",
      local_locator: "expected",
      remote_commit: "1234567890abcdef",
      remote_target_exists: true,
      remote_target_fingerprint: "remote-asset",
      local_source_fingerprint: "local",
      target_path: "C:/Users/test/.cursor/skills/demo",
      target_exists: true,
      target_fingerprint: "local",
      target_managed: true,
      overwrite_unmanaged: false,
      new_name: "",
      new_install_name: "",
      warnings: ["A newer unrelated remote commit will be replayed safely."],
      blockers: [],
      blocked: false,
      created_at: "2026-07-17T00:00:00Z",
      schema_version: 1,
    };
    const result: AssetActionResult = {
      operation_id: "plan-1",
      action: "download",
      status: "succeeded",
      resource_key: "skill:demo",
      target_resource_key: "skill:demo",
      platform: "cursor",
      message: "Downloaded skill:demo.",
      remote_commit: "1234567890abcdef",
      local_path: "C:/Users/test/.cursor/skills/demo",
      replayed_on_latest: false,
      push_retry_count: 0,
      warnings: [],
      operation_status: "succeeded",
    };
    vi.mocked(lpmAction).mockImplementation(async (action) => {
      if (action === "asset_action_plan") return plan;
      if (action === "asset_action_apply") return result;
      throw new Error(`Unexpected action: ${action}`);
    });
    const { onChanged } = renderView([row()]);

    expect(screen.getByText("cursor / demo")).toBeVisible();
    expect(screen.getAllByText("content differs").length).toBeGreaterThan(0);
    expect(screen.getByText("Local and remote content differ.")).toBeVisible();
    expect(screen.getByText(/description/)).toBeVisible();

    await user.click(screen.getByRole("button", { name: "Download / update local" }));
    await user.click(screen.getByRole("button", { name: "Create safety plan" }));
    expect(await screen.findByText("Explicitly confirm the replacement before planning.")).toBeVisible();
    expect(lpmAction).not.toHaveBeenCalled();

    await user.click(screen.getByRole("checkbox"));
    await user.click(screen.getByRole("button", { name: "Create safety plan" }));
    expect(await screen.findByText("A newer unrelated remote commit will be replayed safely.")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Apply" }));

    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    expect(vi.mocked(lpmAction).mock.calls[0]).toEqual([
      "asset_action_plan",
      expect.objectContaining({
        action: "download",
        kind: "skill",
        name: "demo",
        platform: "cursor",
        local_instance_id: "expected-cursor-demo",
      }),
    ]);
    expect(vi.mocked(lpmAction).mock.calls[1]).toEqual([
      "asset_action_apply",
      { operation_id: "plan-1" },
    ]);
  });

  it("keeps read-only references immutable while allowing an explicit remote copy", () => {
    renderView([
      row({
        remote_writable: false,
        read_only_reference: true,
        status: "read-only-reference",
        available_actions: ["copy-to-remote"],
        warnings: ["This asset is a read-only reference."],
      }),
    ]);

    expect(screen.getAllByText("read-only reference").length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "Save remote copy" })).toBeEnabled();
    expect(screen.queryByRole("button", { name: "Upload / update remote" })).not.toBeInTheDocument();
    expect(screen.getByText("This asset is a read-only reference.")).toBeVisible();
  });

  it("filters target conflicts and validates platform install names before planning", async () => {
    const user = userEvent.setup();
    renderView([
      row({
        status: "target-conflict",
        available_actions: ["set-platform-install-name"],
        blockers: ["Another asset resolves to the same target path."],
      }),
      row({
        resource_key: "skill:other",
        name: "other",
        local_instance_id: "expected-cursor-other",
        install_name: "other",
        status: "same",
        available_actions: ["download"],
      }),
    ]);

    await user.click(screen.getByRole("button", { name: "target conflict" }));
    expect(screen.getByText("cursor / demo")).toBeVisible();
    expect(screen.queryByText("cursor / other")).not.toBeInTheDocument();
    expect(screen.getByText("Another asset resolves to the same target path.")).toBeVisible();

    await user.click(screen.getByRole("button", { name: "Change install name" }));
    const input = screen.getByLabelText("Platform install name");
    await user.clear(input);
    await user.type(input, "Bad Name");

    expect(screen.getByText(/safe lowercase path segment/)).toBeVisible();
    expect(screen.getByRole("button", { name: "Create safety plan" })).toBeDisabled();
  });

  it("requests delete_repo only when deleting an owned remote repository", async () => {
    const user = userEvent.setup();
    vi.mocked(lpmAction).mockImplementation(async (action) => {
      if (action === "github_auth_status") {
        return {
          state: "connected",
          source: "config",
          login: "Lingye",
          scopes: ["repo"],
          token_preview: "masked",
          config_token_preview: "masked",
          can_reveal: true,
          can_clear: true,
          env_override: false,
          oauth_configured: true,
          error: "",
        };
      }
      if (action === "github_auth_start") {
        return {
          session_id: "delete_session_1234",
          user_code: "DELETE-ME",
          verification_uri: "https://github.com/login/device",
          expires_in: 900,
          interval: 5,
          purpose: "remote_delete",
          scopes: ["repo", "delete_repo"],
        };
      }
      throw new Error(`Unexpected action: ${action}`);
    });
    const owned = row({
      entry: {
        ...row().entry!,
        source: "owned",
        repo: "Lingye/demo",
      },
    });
    renderView([owned]);

    await user.click(screen.getByRole("button", { name: "Delete resource" }));
    await user.type(screen.getByLabelText("Type demo to confirm."), "demo");
    await user.click(screen.getByRole("button", { name: "Confirm" }));

    expect(await screen.findByText("DELETE-ME")).toBeVisible();
    expect(lpmAction).toHaveBeenCalledWith("github_auth_start", { purpose: "remote_delete" });
    expect(openExternalUrl).toHaveBeenCalledWith("https://github.com/login/device");
    expect(vi.mocked(lpmAction).mock.calls.some(([action]) => action === "resource_delete")).toBe(false);
  });
});
