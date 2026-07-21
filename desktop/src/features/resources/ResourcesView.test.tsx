import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { lpmAction } from "@/api/client";
import { createTranslator } from "@/app/i18n";
import { TaskCenterProvider } from "@/app/TaskCenterContext";
import { ResourcesView } from "@/features/resources/ResourcesView";
import type {
  AssetBatchPlan,
  AssetBatchResult,
  AssetInventory,
  AssetResourceRow,
} from "@/types/lpm";

vi.mock("@/api/client", () => ({
  lpmAction: vi.fn(),
  openPath: vi.fn(),
}));

const t = createTranslator("en");

function resource(overrides: Partial<AssetResourceRow> = {}): AssetResourceRow {
  return {
    resource_key: "skill:demo",
    kind: "skill",
    name: "demo",
    description: "Remote demo description",
    description_source: "remote",
    local_status: "single",
    remote_status: "present",
    status: "content-different",
    remote: {
      exists: true,
      status: "present",
      writable: true,
      read_only: false,
      commit: "1234567890abcdef",
      path: "resources/skills/demo",
      description: "Remote demo description",
    },
    local_instances: [{
      id: "expected-cursor-demo",
      platform: "cursor",
      install_name: "demo",
      path: "C:/Users/test/.cursor/skills/demo",
      ownership: "managed",
      fingerprint: "local",
      description: "Local description",
      status: "content-different",
      warnings: [],
      blockers: [],
    }],
    metadata_differences: ["description"],
    diff_summary: ["Local and remote content differ."],
    warnings: [],
    blockers: [],
    available_actions: ["download", "upload"],
    ...overrides,
  };
}

function inventory(resources: AssetResourceRow[]): AssetInventory {
  return {
    branch: "main",
    remote_commit: "1234567890abcdef",
    repo_url: "https://example.test/resources.git",
    remote_available: true,
    remote_warning: "",
    scanned_local: true,
    generated_at: "2026-07-17T00:00:00Z",
    legacy_write_blocker: "",
    resources,
  };
}

function renderView(resources = [resource()]) {
  const data = inventory(resources);
  const onChanged = vi.fn(async () => undefined);
  const onOpenSettings = vi.fn();
  render(
    <TaskCenterProvider>
      <ResourcesView
        inventory={data}
        selectedKey={data.resources[0]?.resource_key}
        t={t}
        onSelect={vi.fn()}
        onInventory={vi.fn()}
        onChanged={onChanged}
        onError={vi.fn()}
        onOpenSettings={onOpenSettings}
      />
    </TaskCenterProvider>,
  );
  return { onChanged, onOpenSettings };
}

function batchPlan(direction: "upload" | "download", disposition: "create" | "update" | "blocked" = "update"): AssetBatchPlan {
  return {
    direction,
    resource_keys: ["skill:demo"],
    target_platforms: direction === "download" ? ["cursor"] : [],
    remote_commit: "1234567890abcdef",
    plan_hash: "plan-hash",
    items: [{
      id: `skill:demo|${direction === "download" ? "cursor" : ""}`,
      resource_key: "skill:demo",
      platform: direction === "download" ? "cursor" : "",
      local_instance_id: "expected-cursor-demo",
      action: direction,
      disposition,
      target_resource_key: "skill:demo",
      reason: disposition === "blocked" ? "Choose a source instance." : "Content differs.",
      warnings: [],
      blockers: disposition === "blocked" ? ["Choose a source instance."] : [],
      plan: null,
    }],
    executable_count: disposition === "blocked" ? 0 : 1,
    blocked_count: disposition === "blocked" ? 1 : 0,
    skipped_count: 0,
    status: disposition === "blocked" ? "blocked" : "ready",
  };
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("ResourcesView unified inventory", () => {
  it("shows one logical resource row with remote description and complete local detail", () => {
    renderView();

    expect(screen.getAllByText("demo").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Remote demo description").length).toBeGreaterThan(0);
    expect(screen.getAllByText("content differs").length).toBeGreaterThan(0);
    expect(screen.getByText("Local and remote content differ.")).toBeVisible();
    expect(screen.getAllByText("cursor").length).toBeGreaterThan(0);
    expect(screen.getByText("managed")).toBeVisible();
  });

  it("keeps selection across filters and plans mixed selections without hiding skipped items", async () => {
    const user = userEvent.setup();
    const prompt = resource({
      resource_key: "prompt:other",
      kind: "prompt",
      name: "other",
      status: "remote-only",
      local_status: "missing",
      local_instances: [],
      description: "Remote prompt",
    });
    vi.mocked(lpmAction).mockResolvedValue(batchPlan("upload", "blocked"));
    renderView([resource(), prompt]);

    await user.click(screen.getByRole("checkbox", { name: "demo" }));
    await user.selectOptions(screen.getByLabelText("Resource type"), "prompt");
    await user.click(screen.getByRole("button", { name: "Select visible" }));
    expect(screen.getByText("2 selected")).toBeVisible();

    await user.click(screen.getByRole("button", { name: "Upload selected" }));
    expect(await screen.findByText("Choose a source instance.")).toBeVisible();
    expect(lpmAction).toHaveBeenCalledWith("asset_batch_plan", expect.objectContaining({
      direction: "upload",
      resource_keys: expect.arrayContaining(["skill:demo", "prompt:other"]),
    }));
  });

  it("selects enabled target tools, reviews a download plan, and applies the same plan hash", async () => {
    const user = userEvent.setup();
    const plan = batchPlan("download");
    const result: AssetBatchResult = {
      status: "succeeded",
      plan_hash: "plan-hash",
      results: [],
      stale_plan: null,
    };
    vi.mocked(lpmAction).mockImplementation(async (action) => {
      if (action === "config_get") {
        return {
          config: {
            platforms: [
              { name: "cursor", enabled: true, skills_dir: "", mcp_json: "", rules_dir: "", plugins_dir: "" },
              { name: "codex", enabled: false, skills_dir: "", mcp_json: "", rules_dir: "", plugins_dir: "" },
            ],
          },
        };
      }
      if (action === "asset_batch_plan") return plan;
      if (action === "asset_batch_apply") return result;
      throw new Error(`Unexpected action: ${action}`);
    });
    const { onChanged } = renderView();

    await user.click(screen.getByRole("checkbox", { name: "demo" }));
    await user.click(screen.getByRole("button", { name: "Download selected" }));
    const cursor = await screen.findByRole("checkbox", { name: /cursor/i });
    const codex = screen.getByRole("checkbox", { name: /codex/i });
    expect(codex).toBeDisabled();
    await user.click(cursor);
    await user.click(screen.getByRole("button", { name: "Create safety plan" }));
    expect(await screen.findByText("Content differs.")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Apply batch" }));

    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    expect(lpmAction).toHaveBeenCalledWith("asset_batch_apply", expect.objectContaining({
      direction: "download",
      target_platforms: ["cursor"],
      plan_hash: "plan-hash",
    }));
  });

  it("rejects a stale apply and presents the rebuilt plan for review", async () => {
    const user = userEvent.setup();
    const initial = batchPlan("upload");
    const refreshed = { ...batchPlan("upload", "blocked"), plan_hash: "new-plan-hash" };
    vi.mocked(lpmAction).mockImplementation(async (action) => {
      if (action === "asset_batch_plan") return initial;
      if (action === "asset_batch_apply") {
        return {
          status: "stale-plan",
          plan_hash: "new-plan-hash",
          results: [],
          stale_plan: refreshed,
        } satisfies AssetBatchResult;
      }
      throw new Error(`Unexpected action: ${action}`);
    });
    renderView();

    await user.click(screen.getByRole("checkbox", { name: "demo" }));
    await user.click(screen.getByRole("button", { name: "Upload selected" }));
    expect(await screen.findByText("Content differs.")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Apply batch" }));

    expect(await screen.findByText(/Assets changed after review/)).toBeVisible();
    expect(screen.getByText("Choose a source instance.")).toBeVisible();
  });

  it("plans every different local instance as a separately renamed upload", async () => {
    const user = userEvent.setup();
    const variants = resource({
      local_status: "variants",
      local_instances: [
        resource().local_instances[0],
        {
          ...resource().local_instances[0],
          id: "expected-codex-demo",
          platform: "codex",
          path: "C:/Users/test/.codex/skills/demo",
          fingerprint: "different-local",
        },
      ],
    });
    vi.mocked(lpmAction).mockResolvedValue(batchPlan("upload"));
    renderView([variants]);

    await user.click(screen.getByRole("checkbox", { name: "demo" }));
    await user.click(screen.getByRole("button", { name: "Upload selected" }));
    await screen.findByText("Content differs.");
    await user.click(screen.getByRole("checkbox", { name: "Rename and upload every local variant" }));
    expect(screen.getAllByLabelText("New asset name")).toHaveLength(2);
    await user.click(screen.getByRole("button", { name: "Create safety plan" }));

    await waitFor(() => expect(lpmAction).toHaveBeenLastCalledWith(
      "asset_batch_plan",
      expect.objectContaining({
        direction: "upload",
        choices: expect.arrayContaining([
          expect.objectContaining({
            local_instance_id: "expected-cursor-demo",
            new_name: "demo-cursor-1",
            resolution: "rename",
          }),
          expect.objectContaining({
            local_instance_id: "expected-codex-demo",
            new_name: "demo-codex-2",
            resolution: "rename",
          }),
        ]),
      }),
    ));
  });
});
