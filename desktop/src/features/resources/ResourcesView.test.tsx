import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
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
  selectDirectory: vi.fn(),
}));

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

function renderView(
  resources = [resource()],
  options: {
    inventory?: AssetInventory | null;
    remoteRefreshBusy?: boolean;
    localScanBusy?: boolean;
    language?: "en" | "zh";
  } = {},
) {
  const data = options.inventory === undefined ? inventory(resources) : options.inventory;
  const onChanged = vi.fn(async () => undefined);
  const onOpenSettings = vi.fn();
  const onSelect = vi.fn();
  const onRefreshRemote = vi.fn(async () => undefined);
  const onScanLocal = vi.fn(async () => undefined);
  render(
    <TaskCenterProvider>
      <ResourcesView
        inventory={data}
        selectedKey={data?.resources[0]?.resource_key}
         t={createTranslator(options.language ?? "en")}
         onSelect={onSelect}
         remoteRefreshBusy={options.remoteRefreshBusy ?? false}
         localScanBusy={options.localScanBusy ?? false}
         remoteCheckedAt="2026-07-17T01:00:00Z"
         localScannedAt="2026-07-17T02:00:00Z"
         onRefreshRemote={onRefreshRemote}
         onScanLocal={onScanLocal}
        onChanged={onChanged}
        onError={vi.fn()}
        onOpenSettings={onOpenSettings}
      />
    </TaskCenterProvider>,
  );
  return { onChanged, onOpenSettings, onRefreshRemote, onScanLocal, onSelect };
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
  it("uses the shared full-width pill contract for Chinese and English asset labels", async () => {
    const user = userEvent.setup();
    const longStatus = resource({
      resource_key: "prompt:demo",
      kind: "prompt",
      status: "uncomparable",
      local_instances: [{
        ...resource().local_instances[0],
        status: "uncomparable",
      }],
    });

    renderView([longStatus], { language: "zh" });

    expect(screen.getAllByText("提示词").length).toBeGreaterThan(0);
    expect(screen.getAllByText("无法安全比较").length).toBeGreaterThan(0);
    expect(screen.getByText("在线", { selector: ".asset-source-state" })).toHaveClass("asset-pill");
    expect(screen.getByText("已扫描", { selector: ".asset-source-state" })).toHaveClass("asset-pill");
    for (const label of document.querySelectorAll(".kind, .asset-status, .asset-source-state")) {
      expect(label).toHaveClass("asset-pill");
    }

    await user.click(screen.getByRole("checkbox", { name: "demo" }));
    await user.click(screen.getByRole("button", { name: "上传到仓库" }));
    expect(within(screen.getByRole("dialog")).getByText("提示词")).toHaveClass("asset-pill");

    cleanup();
    renderView([longStatus], {
      language: "en",
      inventory: {
        ...inventory([longStatus]),
        repo_url: "",
        remote_available: false,
        scanned_local: false,
      },
    });

    expect(screen.getAllByText("prompt").length).toBeGreaterThan(0);
    expect(screen.getAllByText("cannot compare safely").length).toBeGreaterThan(0);
    expect(screen.getByText("Not configured", { selector: ".asset-source-state" })).toHaveClass("asset-pill");
    expect(screen.getByText("Not scanned", { selector: ".asset-source-state" })).toHaveClass("asset-pill");
    for (const label of document.querySelectorAll(".kind, .asset-status, .asset-source-state")) {
      expect(label).toHaveClass("asset-pill");
    }
  });

  it("renders structured resource messages in Chinese and English", () => {
    const localOnly = resource({
      status: "local-only",
      remote_status: "missing",
      remote: {
        exists: false,
        status: "missing",
        writable: true,
        read_only: false,
        commit: "",
        path: null,
        description: "",
      },
      diff_summary: ["Local content is not present in the remote repository."],
      diff_summary_refs: [{
        code: "asset.diff.local_only",
        fallback: "Local content is not present in the remote repository.",
      }],
      blockers: ["The target platform is not enabled."],
      blocker_refs: [{
        code: "asset.batch.platform_disabled",
        fallback: "The target platform is not enabled.",
      }],
    });

    renderView([localOnly], { language: "zh" });
    expect(screen.getByText("本地内容不存在于远端仓库中。")).toBeVisible();
    expect(screen.getByText("目标平台未启用。")).toBeVisible();

    cleanup();
    renderView([localOnly], { language: "en" });
    expect(screen.getByText("Local content is not present in the remote repository."))
      .toBeVisible();
    expect(screen.getByText("The target platform is not enabled.")).toBeVisible();
  });

  it("renders a structured batch-plan reason in Chinese", async () => {
    const user = userEvent.setup();
    const plan = batchPlan("upload", "blocked");
    plan.items[0].reason_ref = {
      code: "asset.batch.select_source_instance",
      fallback: plan.items[0].reason,
    };
    vi.mocked(lpmAction).mockResolvedValue(plan);
    renderView([resource()], { language: "zh" });

    await user.click(screen.getByRole("checkbox", { name: "demo" }));
    await user.click(screen.getByRole("button", { name: "上传到仓库" }));

    expect(await screen.findByText("存在多个本地版本，请选择来源实例。")).toBeVisible();
  });

  it("shows compact remote and local source status and refreshes only on demand", async () => {
    const user = userEvent.setup();
    const { onRefreshRemote } = renderView();

    expect(screen.getByText("Online")).toBeVisible();
    expect(screen.getByText("https://example.test/resources.git")).toBeVisible();
    expect(screen.getAllByText("1234567890ab").length).toBeGreaterThan(0);
    expect(screen.getByText("1 tools · 1 instances")).toBeVisible();
    expect(screen.getByRole("heading", { name: "Collect and import" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Collect from GitHub" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Import local folder" })).toBeEnabled();
    expect(screen.queryByRole("button", { name: "Add resource" })).not.toBeInTheDocument();
    expect(screen.queryByText("Plugin reference")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Refresh remote" }));
    expect(onRefreshRemote).toHaveBeenCalledTimes(1);
  });

  it("keeps collection visible but disabled when the repository is unconfigured", async () => {
    const user = userEvent.setup();
    const unconfigured = { ...inventory([resource()]), repo_url: "", remote_available: false };
    const { onOpenSettings } = renderView(undefined, { inventory: unconfigured });

    expect(screen.getByText("Not configured", { selector: ".asset-source-state" })).toBeVisible();
    expect(screen.getByText("Configure a resource repository before collecting or importing.")).toBeVisible();
    expect(screen.getByRole("button", { name: "Collect from GitHub" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Import local folder" })).toBeDisabled();
    const configureButtons = screen.getAllByRole("button", { name: "Configure repository" });
    await user.click(configureButtons[configureButtons.length - 1]);
    expect(onOpenSettings).toHaveBeenCalledTimes(1);
  });

  it("blocks collection while loading, refreshing, offline, or legacy-write-blocked", () => {
    renderView(undefined, { inventory: null });
    expect(screen.getByText("Loading repository status.")).toBeVisible();
    expect(screen.getByRole("button", { name: "Collect from GitHub" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Import local folder" })).toBeDisabled();

    cleanup();
    renderView(
      [resource({ kind: "plugin", resource_key: "plugin:demo" })],
      { remoteRefreshBusy: true },
    );
    expect(screen.getByRole("button", { name: "Refresh remote" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Scan local" })).toBeEnabled();
    expect(screen.getByText("Wait for the resource inventory refresh to finish.")).toBeVisible();
    expect(screen.getByRole("button", { name: "Collect from GitHub" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Import local folder" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Delete plugin" })).toBeDisabled();

    cleanup();
    renderView(
      [resource({ kind: "plugin", resource_key: "plugin:demo" })],
      { localScanBusy: true },
    );
    expect(screen.getByRole("button", { name: "Refresh remote" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Scan local" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Collect from GitHub" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Delete plugin" })).toBeDisabled();

    cleanup();
    renderView(undefined, { inventory: { ...inventory([resource()]), remote_available: false } });
    expect(screen.getByText("The remote repository is unavailable. Refresh it before collecting or importing.")).toBeVisible();
    expect(screen.getByRole("button", { name: "Collect from GitHub" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Import local folder" })).toBeDisabled();

    cleanup();
    renderView(undefined, {
      inventory: {
        ...inventory([resource()]),
        legacy_write_blocker: "Resolve the legacy workspace first.",
      },
    });
    expect(screen.getAllByText("Resolve the legacy workspace first.").length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "Collect from GitHub" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Import local folder" })).toBeDisabled();
  });

  it("shows one logical resource row with remote description and complete local detail", () => {
    renderView();

    const table = screen.getByRole("table");
    expect(within(table).getAllByRole("columnheader").map((header) => header.textContent)).toEqual([
      "",
      "Asset inventory",
      "Description",
      "Status",
    ]);
    const row = within(table).getByRole("row", { name: /skill:demo/ });
    expect(within(row).getByText("content differs")).toBeVisible();
    expect(within(row).queryByText("1 instance")).not.toBeInTheDocument();
    expect(within(row).queryByText("present")).not.toBeInTheDocument();
    expect(within(row).getByTitle("Remote demo description")).toBeVisible();
    expect(screen.getAllByText("demo").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Remote demo description").length).toBeGreaterThan(0);
    expect(screen.getAllByText("content differs").length).toBeGreaterThan(0);
    expect(screen.getByText("Local and remote content differ.")).toBeVisible();
    expect(screen.getAllByText("cursor").length).toBeGreaterThan(0);
    expect(screen.getByText("managed")).toBeVisible();
  });

  it("orders detail diagnosis before source metadata, local instances, and destructive actions", () => {
    renderView([resource({
      kind: "plugin",
      resource_key: "plugin:demo",
      plugin_track: "reference",
      plugin_platform: "claude-code",
      plugin_source_kind: "marketplace",
      plugin_source_id: "demo",
      warnings: ["Review this plugin warning."],
    })]);

    const warning = screen.getByText("Review this plugin warning.");
    const differences = screen.getByRole("heading", { name: "Difference summary" });
    const source = screen.getByRole("heading", { name: "Source and sync" });
    const instances = screen.getByRole("heading", { name: "Local instances" });
    const danger = screen.getByRole("heading", { name: "Danger zone" });

    expect(warning.compareDocumentPosition(differences) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(differences.compareDocumentPosition(source) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(source.compareDocumentPosition(instances) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(instances.compareDocumentPosition(danger) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getByRole("button", { name: "Delete plugin" })).toBeVisible();
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
    await user.click(screen.getByRole("checkbox", { name: "Select visible" }));
    expect(screen.getByText("2 selected")).toBeVisible();

    expect(screen.getByText("1 selected outside the current filters")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Upload to repository" }));
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
    await user.click(screen.getByRole("button", { name: "Install to tools" }));
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
    await user.click(screen.getByRole("button", { name: "Upload to repository" }));
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
    await user.click(screen.getByRole("button", { name: "Upload to repository" }));
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

  it("collects a GitHub resource, clears filters and batch selection, and targets the new row", async () => {
    const user = userEvent.setup();
    vi.mocked(lpmAction).mockResolvedValue({ entry: { kind: "skill", name: "new-resource" } });
    const { onChanged, onSelect } = renderView();

    await user.click(screen.getByRole("checkbox", { name: "demo" }));
    await user.selectOptions(screen.getByLabelText("Resource type"), "prompt");
    await user.click(screen.getByRole("button", { name: "Collect from GitHub" }));
    const dialog = screen.getByRole("dialog", { name: "Collect from GitHub" });
    expect(dialog).toBeVisible();
    expect(screen.queryByRole("tab")).not.toBeInTheDocument();
    await user.type(screen.getByLabelText("GitHub URL"), "https://github.com/example/new-resource");
    await user.click(within(dialog).getByRole("button", { name: "Collect from GitHub" }));

    await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(1));
    expect(onSelect).toHaveBeenCalledWith("skill:new-resource");
    expect(screen.queryByRole("dialog", { name: "Collect from GitHub" })).not.toBeInTheDocument();
    expect(screen.getByLabelText("Resource type")).toHaveValue("all");
    expect(screen.queryByRole("toolbar", { name: "Selected resource actions" })).not.toBeInTheDocument();
    expect(lpmAction).toHaveBeenCalledWith("collect", expect.objectContaining({
      github_url: "https://github.com/example/new-resource",
      push: true,
    }));
  });

  it("opens local import as a separate dialog without collection mode tabs", async () => {
    const user = userEvent.setup();
    renderView();

    await user.click(screen.getByRole("button", { name: "Import local folder" }));
    expect(screen.getByRole("dialog", { name: "Import local folder" })).toBeVisible();
    expect(screen.getByLabelText("Local path")).toBeVisible();
    expect(screen.queryByLabelText("GitHub URL")).not.toBeInTheDocument();
    expect(screen.queryByRole("tab")).not.toBeInTheDocument();
    expect(screen.queryByText("Plugin reference")).not.toBeInTheDocument();
  });

  it("searches name, key, and description and exposes advanced filters with an active count", async () => {
    const user = userEvent.setup();
    const prompt = resource({
      resource_key: "prompt:release-notes",
      kind: "prompt",
      name: "release-notes",
      description: "Summarize a deployment",
      local_status: "missing",
      local_instances: [],
      status: "remote-only",
    });
    renderView([resource(), prompt]);

    await user.type(screen.getByRole("textbox", { name: "Search resources" }), "deployment");
    expect(screen.getByRole("row", { name: /release-notes/ })).toBeVisible();
    expect(screen.queryByRole("row", { name: /skill:demo/ })).not.toBeInTheDocument();

    await user.clear(screen.getByRole("textbox", { name: "Search resources" }));
    await user.click(screen.getByRole("button", { name: "More filters" }));
    await user.selectOptions(screen.getByLabelText("Local state"), "missing");
    await user.selectOptions(screen.getByLabelText("Resource status"), "remote-only");
    await user.type(screen.getByRole("textbox", { name: "Search resources" }), "release");
    expect(screen.getByRole("button", { name: /More filters/ })).toHaveTextContent("3");
    expect(screen.getByRole("row", { name: /release-notes/ })).toBeVisible();
    expect(screen.queryByRole("row", { name: /skill:demo/ })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Clear filters" }));
    expect(screen.getByRole("textbox", { name: "Search resources" })).toHaveValue("");
    expect(screen.getByLabelText("Resource status")).toHaveValue("all");
    expect(screen.getByLabelText("Local state")).toHaveValue("all");
    expect(screen.getByRole("row", { name: /skill:demo/ })).toBeVisible();
  });
});
