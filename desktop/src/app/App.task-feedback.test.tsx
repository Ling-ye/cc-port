import { StrictMode } from "react";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "@/app/App";
import { createTranslator, LANGUAGE_STORAGE_KEY } from "@/app/i18n";
import { TaskCenterProvider } from "@/app/TaskCenterContext";
import type { ScanScope } from "@/features/resources/ScanLocalDialog";
import type { AssetInventory, AssetResourceRow } from "@/types/lpm";

const clientMocks = vi.hoisted(() => ({
  lpmAction: vi.fn(),
}));

vi.mock("@/api/client", () => ({
  lpmAction: clientMocks.lpmAction,
  openPath: vi.fn(),
  selectDirectory: vi.fn(),
}));

vi.mock("@/features/resources/ResourcesView", () => ({
  ResourcesView: ({
    inventory,
    remoteRefreshBusy,
    localScanBusy,
    remoteCheckedAt,
    localScannedAt,
    onRefreshRemote,
    onScanLocal,
    onChanged,
  }: {
    inventory: AssetInventory | null;
    remoteRefreshBusy: boolean;
    localScanBusy: boolean;
    remoteCheckedAt: string | null;
    localScannedAt: string | null;
    onRefreshRemote: () => Promise<void> | void;
    onScanLocal: (scope: ScanScope) => Promise<void> | void;
    onChanged: () => Promise<void> | void;
  }) => (
    <section aria-label="Resources test view">
      <span data-testid="remote-commit">{inventory?.remote_commit || ""}</span>
      <span data-testid="remote-available">{String(inventory?.remote_available ?? "")}</span>
      <span data-testid="remote-warning">{inventory?.remote_warning || ""}</span>
      <span data-testid="remote-checked-at">{remoteCheckedAt || ""}</span>
      <span data-testid="local-scanned-at">{localScannedAt || ""}</span>
      <span data-testid="inventory-generated-at">{inventory?.generated_at || ""}</span>
      {inventory?.resources.map((resource) => (
        <span key={resource.resource_key} data-testid={`resource-${resource.resource_key}`}>
          {resource.resource_key}
        </span>
      ))}
      <button type="button" disabled={remoteRefreshBusy} onClick={() => void onRefreshRemote()}>
        Refresh remote
      </button>
      <button
        type="button"
        disabled={localScanBusy}
        onClick={() => void onScanLocal({
          scan_global: false,
          project_ids: ["project-a"],
        })}
      >
        Scan local
      </button>
      <button type="button" disabled={remoteRefreshBusy || localScanBusy} onClick={() => void onChanged()}>
        Resource changed
      </button>
    </section>
  ),
}));

vi.mock("@/features/settings/SettingsView", () => ({
  SettingsView: ({
    refreshVersion,
    onChanged,
  }: {
    refreshVersion: number;
    onChanged: () => Promise<void> | void;
  }) => (
    <section aria-label="Settings test view">
      <span data-testid="settings-refresh-version">{refreshVersion}</span>
      <button type="button" onClick={() => void onChanged()}>Settings changed</button>
    </section>
  ),
}));

vi.mock("@/features/guide/GuideView", () => ({
  GuideView: () => <section aria-label="Guide test view" />,
}));

function makeInventory(overrides: Partial<AssetInventory> = {}): AssetInventory {
  return {
    branch: "main",
    remote_commit: "abc123",
    repo_url: "https://github.com/example/resources",
    remote_available: true,
    remote_warning: "",
    scanned_local: false,
    generated_at: "2026-07-23T08:00:00.000Z",
    legacy_write_blocker: "",
    resources: [],
    ...overrides,
  };
}

function makeLocalOnlyResource(): AssetResourceRow {
  return {
    resource_key: "skill:local-only",
    kind: "skill",
    name: "local-only",
    description: "Local-only test resource",
    description_source: "local",
    local_status: "single",
    remote_status: "missing",
    status: "local-only",
    remote: {
      exists: false,
      status: "missing",
      writable: true,
      read_only: false,
      commit: "",
      description: "",
    },
    local_instances: [{
      id: "cursor:skill:local-only",
      platform: "cursor",
      install_name: "local-only",
      path: "C:/Users/test/.cursor/skills/local-only",
      ownership: "managed",
      fingerprint: "local-only-fingerprint",
      description: "Local-only test resource",
      status: "local-only",
      warnings: [],
      blockers: [],
    }],
    metadata_differences: [],
    diff_summary: [],
    warnings: [],
    blockers: [],
    available_actions: ["upload"],
  };
}

function renderApp(strict = false) {
  const content = (
    <TaskCenterProvider>
      <App />
    </TaskCenterProvider>
  );
  return render(strict ? <StrictMode>{content}</StrictMode> : content);
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, reject, resolve };
}

async function waitForStartup() {
  await waitFor(() => expect(clientMocks.lpmAction).toHaveBeenCalledTimes(1));
  await waitFor(() => expect(screen.getByTestId("remote-commit")).toHaveTextContent("abc123"));
}

beforeEach(() => {
  window.localStorage.setItem(LANGUAGE_STORAGE_KEY, "en");
  clientMocks.lpmAction.mockReset();
  clientMocks.lpmAction.mockImplementation(async (action: string) => {
    if (action === "asset_inventory") return makeInventory();
    throw new Error(`Unexpected action: ${action}`);
  });
});

afterEach(() => {
  cleanup();
  window.localStorage.clear();
});

describe("App inventory orchestration", () => {
  it("performs one silent remote-only inventory refresh on startup", async () => {
    renderApp(true);

    await waitForStartup();

    expect(clientMocks.lpmAction).toHaveBeenCalledWith("asset_inventory", {
      scan_local: false,
      refresh_remote: true,
    });
    expect(clientMocks.lpmAction).not.toHaveBeenCalledWith("summary");
    expect(screen.queryByRole("status")).toBeNull();
    expect(screen.getByTestId("remote-checked-at")).not.toHaveTextContent(/^$/);
  });

  it("uses the active page name in the topbar", async () => {
    const user = userEvent.setup();
    renderApp();
    await waitForStartup();

    expect(screen.getByRole("heading", { level: 1, name: "Resources" })).toBeVisible();

    await user.click(screen.getByRole("button", { name: "Settings" }));
    expect(screen.getByRole("heading", { level: 1, name: "Settings" })).toBeVisible();

    await user.click(screen.getByRole("button", { name: "Guide" }));
    expect(screen.getByRole("heading", { level: 1, name: "Guide" })).toBeVisible();
    expect(screen.queryByTitle("Refresh")).toBeNull();
  });

  it("rerenders the current desktop view when the language changes", async () => {
    const user = userEvent.setup();
    renderApp();
    await waitForStartup();

    expect(screen.getByRole("heading", { level: 1, name: "Resources" })).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Switch to Chinese" }));

    expect(screen.getByRole("heading", { level: 1, name: "资源" })).toBeVisible();
    expect(screen.getByRole("button", { name: "切换到英文" })).toBeVisible();
  });

  it("requests a settings refresh on every settings navigation click", async () => {
    const user = userEvent.setup();
    renderApp();
    await waitForStartup();

    const settingsButton = screen.getByRole("button", { name: "Settings" });
    await user.click(settingsButton);
    expect(screen.getByTestId("settings-refresh-version")).toHaveTextContent("1");

    await user.click(settingsButton);
    expect(screen.getByTestId("settings-refresh-version")).toHaveTextContent("2");
  });

  it("replays the exact session scan scope for remote and post-change refreshes", async () => {
    const user = userEvent.setup();
    clientMocks.lpmAction
      .mockResolvedValueOnce(makeInventory())
      .mockResolvedValue(makeInventory({
        scanned_local: true,
        resources: [makeLocalOnlyResource()],
      }));
    renderApp();
    await waitForStartup();

    await user.click(screen.getByRole("button", { name: "Scan local" }));
    await waitFor(() => expect(screen.getByTestId("local-scanned-at")).not.toHaveTextContent(/^$/));
    expect(screen.getByTestId("resource-skill:local-only")).toBeVisible();

    await user.click(screen.getByRole("button", { name: "Refresh remote" }));
    await waitFor(() => expect(clientMocks.lpmAction).toHaveBeenCalledTimes(3));
    expect(clientMocks.lpmAction).toHaveBeenLastCalledWith("asset_inventory", {
      scan_local: true,
      scan_global: false,
      project_ids: ["project-a"],
      refresh_remote: true,
    });
    expect(screen.getByTestId("resource-skill:local-only")).toBeVisible();

    await waitFor(() => expect(screen.getByRole("button", { name: "Resource changed" })).toBeEnabled());
    await user.click(screen.getByRole("button", { name: "Resource changed" }));
    await waitFor(() => expect(clientMocks.lpmAction).toHaveBeenCalledTimes(4));
    expect(clientMocks.lpmAction).toHaveBeenLastCalledWith("asset_inventory", {
      scan_local: true,
      scan_global: false,
      project_ids: ["project-a"],
      refresh_remote: true,
    });
  });

  it("runs remote refresh and local scan concurrently and publishes both completions", async () => {
    const user = userEvent.setup();
    const remoteResult = deferred<AssetInventory>();
    const localResult = deferred<AssetInventory>();
    clientMocks.lpmAction
      .mockResolvedValueOnce(makeInventory({ generated_at: "startup" }))
      .mockImplementationOnce(() => remoteResult.promise)
      .mockImplementationOnce(() => localResult.promise);
    renderApp();
    await waitForStartup();

    const refreshButton = screen.getByRole("button", { name: "Refresh remote" });
    const scanButton = screen.getByRole("button", { name: "Scan local" });
    await user.click(refreshButton);
    await waitFor(() => expect(refreshButton).toBeDisabled());
    expect(scanButton).toBeEnabled();

    await user.click(scanButton);
    await waitFor(() => expect(clientMocks.lpmAction).toHaveBeenCalledTimes(3));
    expect(refreshButton).toBeDisabled();
    expect(scanButton).toBeDisabled();
    expect(clientMocks.lpmAction).toHaveBeenNthCalledWith(2, "asset_inventory", {
      scan_local: false,
      refresh_remote: true,
    });
    expect(clientMocks.lpmAction).toHaveBeenNthCalledWith(3, "asset_inventory", {
      scan_local: true,
      scan_global: false,
      project_ids: ["project-a"],
      refresh_remote: false,
    });

    remoteResult.resolve(makeInventory({
      generated_at: "remote-complete",
      remote_commit: "def456",
    }));
    await waitFor(() => expect(screen.getByTestId("inventory-generated-at"))
      .toHaveTextContent("remote-complete"));
    expect(scanButton).toBeDisabled();

    localResult.resolve(makeInventory({
      generated_at: "local-complete",
      remote_commit: "def456",
      scanned_local: true,
      resources: [makeLocalOnlyResource()],
    }));
    await waitFor(() => expect(screen.getByTestId("inventory-generated-at"))
      .toHaveTextContent("local-complete"));
    expect(refreshButton).toBeEnabled();
    expect(scanButton).toBeEnabled();
  });

  it("reconciles a remote result when a newer local scan completes first", async () => {
    const user = userEvent.setup();
    const remoteResult = deferred<AssetInventory>();
    const localResult = deferred<AssetInventory>();
    clientMocks.lpmAction
      .mockResolvedValueOnce(makeInventory({ generated_at: "startup" }))
      .mockResolvedValueOnce(makeInventory({
        generated_at: "initial-local",
        scanned_local: true,
        resources: [makeLocalOnlyResource()],
      }))
      .mockImplementationOnce(() => remoteResult.promise)
      .mockImplementationOnce(() => localResult.promise)
      .mockResolvedValueOnce(makeInventory({
        generated_at: "reconciled",
        remote_commit: "def456",
        scanned_local: true,
        resources: [makeLocalOnlyResource()],
      }));
    renderApp();
    await waitForStartup();

    await user.click(screen.getByRole("button", { name: "Scan local" }));
    await waitFor(() => expect(screen.getByTestId("inventory-generated-at"))
      .toHaveTextContent("initial-local"));

    await user.click(screen.getByRole("button", { name: "Refresh remote" }));
    await user.click(screen.getByRole("button", { name: "Scan local" }));
    await waitFor(() => expect(clientMocks.lpmAction).toHaveBeenCalledTimes(4));

    localResult.resolve(makeInventory({
      generated_at: "new-local",
      scanned_local: true,
      resources: [makeLocalOnlyResource()],
    }));
    await waitFor(() => expect(screen.getByTestId("inventory-generated-at"))
      .toHaveTextContent("new-local"));

    remoteResult.resolve(makeInventory({
      generated_at: "stale-remote",
      remote_commit: "def456",
      scanned_local: true,
      resources: [makeLocalOnlyResource()],
    }));
    await waitFor(() => expect(clientMocks.lpmAction).toHaveBeenCalledTimes(5));
    expect(clientMocks.lpmAction).toHaveBeenLastCalledWith("asset_inventory", {
      scan_local: true,
      scan_global: false,
      project_ids: ["project-a"],
      refresh_remote: false,
    });
    await waitFor(() => expect(screen.getByTestId("inventory-generated-at"))
      .toHaveTextContent("reconciled"));
  });

  it("joins a retried local scan to the active scan without clearing busy or publishing twice", async () => {
    const user = userEvent.setup();
    const activeScan = deferred<AssetInventory>();
    clientMocks.lpmAction
      .mockResolvedValueOnce(makeInventory({ generated_at: "startup" }))
      .mockRejectedValueOnce(new Error("scan failed"))
      .mockImplementationOnce(() => activeScan.promise);
    renderApp();
    await waitForStartup();

    const scanButton = screen.getByRole("button", { name: "Scan local" });
    await user.click(scanButton);
    await waitFor(() => expect(scanButton).toBeEnabled());

    await user.click(scanButton);
    await waitFor(() => expect(clientMocks.lpmAction).toHaveBeenCalledTimes(3));
    expect(scanButton).toBeDisabled();

    const generatedAt = screen.getByTestId("inventory-generated-at");
    const publishedValues: string[] = [];
    const observer = new MutationObserver(() => {
      const value = generatedAt.textContent || "";
      if (publishedValues[publishedValues.length - 1] !== value) publishedValues.push(value);
    });
    observer.observe(generatedAt, { childList: true, characterData: true, subtree: true });

    await user.click(screen.getByRole("button", { name: "Task center" }));
    await user.click(await screen.findByRole("button", { name: "Retry" }));
    await waitFor(() => expect(screen.queryByRole("button", { name: "Retry" })).toBeNull());

    expect(clientMocks.lpmAction).toHaveBeenCalledTimes(3);
    expect(scanButton).toBeDisabled();

    activeScan.resolve(makeInventory({
      generated_at: "shared-local-result",
      scanned_local: true,
      resources: [makeLocalOnlyResource()],
    }));
    await waitFor(() => expect(generatedAt).toHaveTextContent("shared-local-result"));
    await waitFor(() => expect(scanButton).toBeEnabled());
    observer.disconnect();

    expect(clientMocks.lpmAction).toHaveBeenCalledTimes(3);
    expect(publishedValues.filter((value) => value === "shared-local-result")).toHaveLength(1);
  });

  it("coalesces pending refreshes and schedules another when changes happen during the follow-up", async () => {
    const user = userEvent.setup();
    const activeRefresh = deferred<AssetInventory>();
    const firstFollowUp = deferred<AssetInventory>();
    const secondFollowUp = deferred<AssetInventory>();
    clientMocks.lpmAction
      .mockResolvedValueOnce(makeInventory({ generated_at: "startup" }))
      .mockImplementationOnce(() => activeRefresh.promise)
      .mockImplementationOnce(() => firstFollowUp.promise)
      .mockImplementationOnce(() => secondFollowUp.promise);
    renderApp();
    await waitForStartup();

    await user.click(screen.getByRole("button", { name: "Refresh remote" }));
    await waitFor(() => expect(clientMocks.lpmAction).toHaveBeenCalledTimes(2));
    await user.click(screen.getByRole("button", { name: "Settings" }));

    const settingsChanged = screen.getByRole("button", { name: "Settings changed" });
    await user.click(settingsChanged);
    await user.click(settingsChanged);
    await user.click(settingsChanged);
    expect(clientMocks.lpmAction).toHaveBeenCalledTimes(2);

    activeRefresh.resolve(makeInventory({
      generated_at: "active-refresh-result",
      remote_commit: "def456",
    }));
    await waitFor(() => expect(clientMocks.lpmAction).toHaveBeenCalledTimes(3));
    expect(clientMocks.lpmAction).toHaveBeenNthCalledWith(3, "asset_inventory", {
      scan_local: false,
      refresh_remote: true,
    });

    await user.click(settingsChanged);
    await user.click(settingsChanged);
    expect(clientMocks.lpmAction).toHaveBeenCalledTimes(3);

    firstFollowUp.resolve(makeInventory({
      generated_at: "first-follow-up-result",
      remote_commit: "ghi789",
    }));
    await waitFor(() => expect(clientMocks.lpmAction).toHaveBeenCalledTimes(4));
    expect(clientMocks.lpmAction).toHaveBeenNthCalledWith(4, "asset_inventory", {
      scan_local: false,
      refresh_remote: true,
    });

    secondFollowUp.resolve(makeInventory({
      generated_at: "second-follow-up-result",
      remote_commit: "jkl012",
    }));
    await user.click(screen.getByRole("button", { name: "Resources" }));
    await waitFor(() => expect(screen.getByTestId("inventory-generated-at"))
      .toHaveTextContent("second-follow-up-result"));
    expect(clientMocks.lpmAction).toHaveBeenCalledTimes(4);
  });

  it("preserves a cached remote state when a local-only scan completes", async () => {
    const user = userEvent.setup();
    clientMocks.lpmAction.mockResolvedValueOnce(makeInventory({
      remote_available: false,
      remote_warning: "Remote unavailable; showing cache",
    }));
    renderApp();
    await waitForStartup();

    expect(screen.getByTestId("remote-available")).toHaveTextContent("false");
    await user.click(screen.getByRole("button", { name: "Scan local" }));

    await waitFor(() => expect(screen.getByTestId("local-scanned-at")).not.toHaveTextContent(/^$/));
    expect(screen.getByTestId("remote-available")).toHaveTextContent("false");
    expect(screen.getByTestId("remote-warning")).toHaveTextContent("Remote unavailable; showing cache");
  });

  it("reports unchanged, updated, and cached remote refresh results", async () => {
    const t = createTranslator("en");
    const user = userEvent.setup();
    clientMocks.lpmAction
      .mockResolvedValueOnce(makeInventory())
      .mockResolvedValueOnce(makeInventory())
      .mockResolvedValueOnce(makeInventory({ remote_commit: "def456" }))
      .mockResolvedValueOnce(makeInventory({
        remote_available: false,
        remote_warning: "Remote unavailable",
        remote_commit: "def456",
      }));
    renderApp();
    await waitForStartup();

    const refreshButton = screen.getByRole("button", { name: "Refresh remote" });
    await user.click(refreshButton);
    expect(await screen.findByText(t("assets.remoteUpToDate"))).toBeVisible();

    await waitFor(() => expect(refreshButton).toBeEnabled());
    await user.click(refreshButton);
    expect(await screen.findByText(t("assets.remoteUpdated"))).toBeVisible();

    await waitFor(() => expect(refreshButton).toBeEnabled());
    await user.click(refreshButton);
    expect(await screen.findByText(t("assets.remoteCacheFallback"))).toBeVisible();
    expect(clientMocks.lpmAction).toHaveBeenCalledTimes(4);
  });
});
