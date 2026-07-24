import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { lpmAction, openExternalUrl } from "@/api/client";
import { createTranslator } from "@/app/i18n";
import { TaskCenterProvider } from "@/app/TaskCenterContext";
import { SettingsView } from "@/features/settings/SettingsView";
import type {
  ConfigBindRepoResult,
  ConfigSettings,
  DoctorCheck,
  GitCredentialStatus,
} from "@/types/lpm";

vi.mock("@/api/client", () => ({
  lpmAction: vi.fn(),
  openExternalUrl: vi.fn(),
}));

const platformNames = ["codex", "claude-code", "cursor", "windsurf", "opencode"];

function settings(repoUrl = "", repoName = "LingyeAIResources"): ConfigSettings {
  return {
    path: "C:/Users/test/.config/lpm/config.toml",
    exists: true,
    config: {
      git: { executable: "" },
      install: { target: "~/.cursor/skills" },
      resources: {
        repo_name: repoName,
        repo_url: repoUrl,
        local_path: "",
        branch: "main",
        credential_mode: "native",
      },
      state: {
        lock_timeout_seconds: 10,
        retention_days: 90,
        keep_latest_operations: 20,
        max_backup_mb: 2048,
      },
      platforms: platformNames.map((name) => ({
        name,
        enabled: true,
        skills_dir: `~/.${name}/skills`,
        mcp_json: `~/.${name}/mcp.json`,
        rules_dir: `~/.${name}/rules`,
        plugins_dir: "",
      })),
    },
  };
}

function credentialStatus(
  overrides: Partial<GitCredentialStatus> = {},
): GitCredentialStatus {
  return {
    state: "ready",
    ready: true,
    git_available: true,
    git_path: "C:/Program Files/Git/cmd/git.exe",
    git_source: "PATH",
    git_version: "git version 2.50.0.windows.1",
    gcm_available: true,
    gcm_configured: true,
    gcm_version: "2.6.1",
    credential_helpers: ["manager"],
    detail: "Git and Git Credential Manager are ready.",
    install_url: "https://github.com/git-ecosystem/git-credential-manager/blob/main/docs/install.md",
    ...overrides,
  };
}

function bindingResult(next: ConfigSettings): ConfigBindRepoResult {
  return {
    settings: next,
    binding: {
      owner: "example",
      repo_name: next.config.resources.repo_name,
      repo_url: next.config.resources.repo_url,
      branch: "main",
      branches: ["main"],
      transport: "https",
      credential_mode: "native",
      read_verified: true,
      write_verified: true,
      remote_empty: false,
      local_path: `C:/Users/test/${next.config.resources.repo_name}`,
      replaced_repo_url: "",
    },
  };
}

function mockInitial(data = settings(), status = credentialStatus()) {
  vi.mocked(lpmAction).mockImplementation(async (action) => {
    if (action === "config_get") return data;
    if (action === "git_credential_status") return status;
    throw new Error(`Unexpected action: ${action}`);
  });
}

function renderView(refreshVersion = 0, language: "en" | "zh" = "en") {
  const onChanged = vi.fn(async () => undefined);
  const onError = vi.fn();
  const view = (version: number) => (
    <TaskCenterProvider>
      <SettingsView
        t={createTranslator(language)}
        refreshVersion={version}
        onError={onError}
        onChanged={onChanged}
      />
    </TaskCenterProvider>
  );
  const rendered = render(view(refreshVersion));
  return {
    onChanged,
    onError,
    rerender: (version: number) => rendered.rerender(view(version)),
  };
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.clearAllMocks();
});

describe("SettingsView native Git settings", () => {
  it("renders the complete disabled settings shell before initial reads finish", () => {
    vi.mocked(lpmAction).mockImplementation(() => new Promise(() => undefined));

    renderView();

    expect(screen.getByText("Connect resource repository")).toBeVisible();
    expect(screen.getByText("Target tools")).toBeVisible();
    expect(screen.getByText("Diagnostics")).toBeVisible();
    expect(within(screen.getByRole("status")).getByText(
      "Reading settings and Git credential status",
    )).toBeVisible();
    expect(screen.getByLabelText("Repository URL")).toBeDisabled();
    expect(screen.getByRole("button", { name: "Connect and verify repository" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Run diagnostics" })).toBeDisabled();

    const toolList = screen.getByRole("list", { name: "Target tools" });
    expect(within(toolList).getAllByRole("listitem")).toHaveLength(5);
    expect(within(toolList).queryAllByRole("checkbox")).toHaveLength(0);
  });

  it("loads config and Git credential status on mount and refresh", async () => {
    mockInitial();
    const { rerender } = renderView();

    await screen.findByText("Connect resource repository");
    expect(screen.queryByRole("button", { name: "Reload" })).not.toBeInTheDocument();
    expect(vi.mocked(lpmAction).mock.calls.filter(([action]) => action === "config_get")).toHaveLength(1);
    expect(vi.mocked(lpmAction).mock.calls.filter(([action]) => action === "git_credential_status")).toHaveLength(1);

    rerender(1);

    await waitFor(() => {
      expect(vi.mocked(lpmAction).mock.calls.filter(([action]) => action === "config_get")).toHaveLength(2);
      expect(vi.mocked(lpmAction).mock.calls.filter(([action]) => action === "git_credential_status")).toHaveLength(2);
    });
  });

  it("keeps the settings shell disabled after a load failure and retries both reads", async () => {
    let attempt = 0;
    vi.mocked(lpmAction).mockImplementation(async (action) => {
      if (action === "config_get") {
        attempt += 1;
        if (attempt === 1) throw new Error("Unable to read settings.");
        return settings();
      }
      if (action === "git_credential_status") return credentialStatus();
      throw new Error(`Unexpected action: ${action}`);
    });
    const { onError } = renderView();
    const user = userEvent.setup();

    const loadAlert = await screen.findByRole("alert");
    expect(within(loadAlert).getByText("Settings could not be loaded")).toBeVisible();
    expect(within(loadAlert).getByText("Unable to read settings.")).toBeVisible();
    expect(loadAlert.closest(".settings-view")).toHaveAttribute("aria-busy", "false");
    expect(screen.getByLabelText("Repository URL")).toBeDisabled();
    expect(screen.getByRole("button", { name: "Run diagnostics" })).toBeDisabled();
    expect(onError).toHaveBeenCalledWith("Unable to read settings.");

    await user.click(screen.getByRole("button", { name: "Retry" }));

    expect(await screen.findByRole("checkbox", { name: "Codex" })).toBeEnabled();
    expect(screen.getByLabelText("Repository URL")).toBeEnabled();
    expect(screen.queryAllByText("Settings could not be loaded")).toHaveLength(0);
    expect(vi.mocked(lpmAction).mock.calls.filter(([action]) => action === "config_get")).toHaveLength(2);
    expect(vi.mocked(lpmAction).mock.calls.filter(([action]) => action === "git_credential_status")).toHaveLength(2);
  });

  it("ignores an older settings response when a newer refresh finishes first", async () => {
    let resolveFirstConfig: (value: ConfigSettings) => void = () => undefined;
    const firstConfig = new Promise<ConfigSettings>((resolve) => {
      resolveFirstConfig = resolve;
    });
    let configCalls = 0;
    vi.mocked(lpmAction).mockImplementation(async (action) => {
      if (action === "config_get") {
        configCalls += 1;
        return configCalls === 1
          ? firstConfig
          : settings("https://github.com/example/new-settings.git", "new-settings");
      }
      if (action === "git_credential_status") return credentialStatus();
      throw new Error(`Unexpected action: ${action}`);
    });
    const { rerender } = renderView();
    await waitFor(() => expect(configCalls).toBe(1));

    rerender(1);

    expect(await screen.findByText("https://github.com/example/new-settings.git")).toBeVisible();
    await act(async () => {
      resolveFirstConfig(settings("https://github.com/example/stale-settings.git", "stale-settings"));
      await Promise.resolve();
    });
    expect(screen.queryByText("https://github.com/example/stale-settings.git")).not.toBeInTheDocument();
  });

  it("removes all GitHub OAuth and token controls", async () => {
    mockInitial();
    renderView();

    expect(await screen.findByText("Connect resource repository")).toBeVisible();
    for (const text of [
      "GitHub access",
      "Sign in to GitHub",
      "Authorized account",
      "Saved token",
      "Granted permissions",
    ]) {
      expect(screen.queryByText(text)).not.toBeInTheDocument();
    }
    expect(vi.mocked(lpmAction).mock.calls.some(([action]) => action.startsWith("github_"))).toBe(false);
    const toolList = screen.getByRole("list", { name: "Target tools" });
    expect(within(toolList).getAllByRole("listitem")).toHaveLength(5);
  });

  it("uses one connect-and-verify action and preserves the narrow binding interface", async () => {
    const initial = settings();
    const connected = settings("https://github.com/example/resources.git", "resources");
    vi.mocked(lpmAction).mockImplementation(async (action) => {
      if (action === "config_get") return initial;
      if (action === "git_credential_status") return credentialStatus();
      if (action === "config_bind_repo") return bindingResult(connected);
      throw new Error(`Unexpected action: ${action}`);
    });
    const { onChanged } = renderView();
    const user = userEvent.setup();

    const input = await screen.findByLabelText("Repository URL");
    await user.type(input, "https://github.com/example/resources");
    await user.click(screen.getByRole("button", { name: "Connect and verify repository" }));

    expect(await screen.findByText("Read and write credentials verified")).toBeVisible();
    expect(vi.mocked(lpmAction)).toHaveBeenCalledWith("config_bind_repo", {
      repo_url: "https://github.com/example/resources",
      expected_current_repo_url: "",
    });
    expect(vi.mocked(lpmAction).mock.calls.some(([action]) => action === "config_save")).toBe(false);
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
  });

  it("shows a Git/GCM prerequisite error and official installation entry without disabling retry", async () => {
    const status = credentialStatus({
      state: "gcm_missing",
      ready: false,
      gcm_available: false,
      gcm_configured: false,
      gcm_version: "",
      credential_helpers: [],
      detail: "Git Credential Manager was not found.",
    });
    mockInitial(settings(), status);
    renderView();
    const user = userEvent.setup();

    expect(await screen.findByText("Git Credential Manager was not found.")).toBeVisible();
    const connect = screen.getByRole("button", { name: "Connect and verify repository" });
    expect(connect).toBeDisabled();

    await user.type(screen.getByLabelText("Repository URL"), "https://github.com/example/resources");
    expect(connect).toBeEnabled();
    await user.click(screen.getByRole("button", { name: "Open installation guide" }));
    expect(openExternalUrl).toHaveBeenCalledWith(status.install_url);
  });

  it("keeps the full repository URL requirement visible", async () => {
    mockInitial();
    renderView();

    expect(await screen.findByText(/complete HTTPS repository URL/i)).toBeVisible();
    expect(screen.getByPlaceholderText("e.g. https://github.com/owner/repository")).toBeVisible();
  });

  it("updates one platform through platform_set_enabled", async () => {
    const initial = settings();
    const updated = settings();
    updated.config.platforms = updated.config.platforms.map((profile) => (
      profile.name === "windsurf" ? { ...profile, enabled: false } : profile
    ));
    vi.mocked(lpmAction).mockImplementation(async (action) => {
      if (action === "config_get") return initial;
      if (action === "git_credential_status") return credentialStatus();
      if (action === "platform_set_enabled") return updated;
      throw new Error(`Unexpected action: ${action}`);
    });
    renderView();
    const user = userEvent.setup();

    await user.click(await screen.findByRole("checkbox", { name: "Windsurf" }));

    expect(vi.mocked(lpmAction)).toHaveBeenCalledWith("platform_set_enabled", {
      name: "windsurf",
      enabled: false,
    });
  });

  it("keeps diagnostics collapsed and does not run them automatically", async () => {
    mockInitial();
    renderView();

    const title = await screen.findByText("Diagnostics");
    expect(title.closest("details")).not.toHaveAttribute("open");
    expect(vi.mocked(lpmAction).mock.calls.some(([action]) => action === "doctor")).toBe(false);
  });

  it("summarizes diagnostics and lists only warnings and errors", async () => {
    const checks: DoctorCheck[] = [
      { id: "git", label: "Git", ok: true, status: "ok", detail: "Ready" },
      { id: "config", label: "Config", ok: true, status: "skipped", detail: "Using defaults" },
      { id: "resource_repo", label: "Resource repo", ok: true, status: "warning", detail: "Remote differs" },
      { id: "platform:cursor", label: "Platform: cursor", ok: false, status: "error", detail: "Directory is not writable" },
    ];
    vi.mocked(lpmAction).mockImplementation(async (action) => {
      if (action === "config_get") return settings();
      if (action === "git_credential_status") return credentialStatus();
      if (action === "doctor") return { checks };
      throw new Error(`Unexpected action: ${action}`);
    });
    renderView();
    const user = userEvent.setup();

    await user.click(await screen.findByText("Diagnostics"));
    await user.click(screen.getByRole("button", { name: "Run diagnostics" }));

    expect(await screen.findByText("1 passed · 1 warnings · 1 errors · 1 skipped")).toBeVisible();
    const issues = screen.getByRole("list", { name: "Diagnostic issues" });
    expect(within(issues).getAllByRole("listitem")).toHaveLength(2);
  });
});
