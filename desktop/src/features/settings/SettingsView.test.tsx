import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ccPortAction, openExternalUrl } from "@/api/client";
import { createTranslator } from "@/app/i18n";
import { TaskCenterProvider } from "@/app/TaskCenterContext";
import { SettingsView } from "@/features/settings/SettingsView";
import type {
  ConfigBindRepoResult,
  ConfigSettings,
  DiagnosticsState,
  DoctorCheck,
  GitCredentialStatus,
} from "@/types/cc-port";

vi.mock("@/api/client", () => ({
  ccPortAction: vi.fn(),
  openExternalUrl: vi.fn(),
}));

const platformNames = ["codex", "claude-code", "cursor", "windsurf", "opencode"];

function settings(repoUrl = "", repoName = "LingyeAIResources"): ConfigSettings {
  return {
    path: "C:/Users/test/.config/cc-port/config.toml",
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
        prompts_dir: `~/.${name}/commands`,
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
  vi.mocked(ccPortAction).mockImplementation(async (action) => {
    if (action === "config_get") return data;
    if (action === "git_credential_status") return status;
    if (action === "ai_integration_status") return { profiles: [] };
    if (action === "approval_requests") return { requests: [] };
    throw new Error(`Unexpected action: ${action}`);
  });
}

function renderView(
  refreshVersion = 0,
  language: "en" | "zh" = "en",
  initialDiagnostics: DiagnosticsState = { phase: "idle", checks: null, error: "" },
  onRunDiagnostics = vi.fn(async () => undefined),
) {
  const onChanged = vi.fn(async () => undefined);
  const onError = vi.fn();
  let currentVersion = refreshVersion;
  let diagnostics = initialDiagnostics;
  const view = () => (
    <TaskCenterProvider>
      <SettingsView
        t={createTranslator(language)}
        refreshVersion={currentVersion}
        onError={onError}
        onChanged={onChanged}
        diagnostics={diagnostics}
        onRunDiagnostics={onRunDiagnostics}
      />
    </TaskCenterProvider>
  );
  const rendered = render(view());
  return {
    onChanged,
    onError,
    onRunDiagnostics,
    rerender: (version: number) => {
      currentVersion = version;
      rendered.rerender(view());
    },
    rerenderDiagnostics: (next: DiagnosticsState) => {
      diagnostics = next;
      rendered.rerender(view());
    },
  };
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.clearAllMocks();
});

describe("SettingsView native Git settings", () => {
  it("renders the complete disabled settings shell before initial reads finish", () => {
    vi.mocked(ccPortAction).mockImplementation(() => new Promise(() => undefined));

    renderView();

    expect(screen.getByText("Connect resource repository")).toBeVisible();
    expect(screen.getByText("Target tools")).toBeVisible();
    expect(screen.getByText("Diagnostics")).toBeVisible();
    expect(screen.getByText(
      "Reading settings and Git credential status",
      { selector: ".settings-load-state strong" },
    )).toBeVisible();
    expect(screen.getByLabelText("Repository URL")).toBeDisabled();
    expect(screen.getByRole("button", { name: "Connect and verify repository" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Diagnostics" })).toBeDisabled();

    const toolList = screen.getByRole("list", { name: "Target tools" });
    expect(within(toolList).getAllByRole("listitem")).toHaveLength(5);
    expect(within(toolList).queryAllByRole("checkbox")).toHaveLength(0);
  });

  it("loads config and Git credential status on mount and refresh", async () => {
    mockInitial();
    const { rerender } = renderView();

    await screen.findByText("Connect resource repository");
    expect(screen.queryByRole("button", { name: "Reload" })).not.toBeInTheDocument();
    expect(vi.mocked(ccPortAction).mock.calls.filter(([action]) => action === "config_get")).toHaveLength(1);
    expect(vi.mocked(ccPortAction).mock.calls.filter(([action]) => action === "git_credential_status")).toHaveLength(1);

    rerender(1);

    await waitFor(() => {
      expect(vi.mocked(ccPortAction).mock.calls.filter(([action]) => action === "config_get")).toHaveLength(2);
      expect(vi.mocked(ccPortAction).mock.calls.filter(([action]) => action === "git_credential_status")).toHaveLength(2);
    });
  });

  it("keeps the settings shell disabled after a load failure and retries both reads", async () => {
    let attempt = 0;
    vi.mocked(ccPortAction).mockImplementation(async (action) => {
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
    expect(screen.getByRole("button", { name: "Diagnostics" })).toBeDisabled();
    expect(onError).toHaveBeenCalledWith("Unable to read settings.");

    await user.click(screen.getByRole("button", { name: "Retry" }));

    expect(await screen.findByRole("checkbox", { name: "Codex" })).toBeEnabled();
    expect(screen.getByLabelText("Repository URL")).toBeEnabled();
    expect(screen.queryAllByText("Settings could not be loaded")).toHaveLength(0);
    expect(vi.mocked(ccPortAction).mock.calls.filter(([action]) => action === "config_get")).toHaveLength(2);
    expect(vi.mocked(ccPortAction).mock.calls.filter(([action]) => action === "git_credential_status")).toHaveLength(2);
  });

  it("ignores an older settings response when a newer refresh finishes first", async () => {
    let resolveFirstConfig: (value: ConfigSettings) => void = () => undefined;
    const firstConfig = new Promise<ConfigSettings>((resolve) => {
      resolveFirstConfig = resolve;
    });
    let configCalls = 0;
    vi.mocked(ccPortAction).mockImplementation(async (action) => {
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
    expect(vi.mocked(ccPortAction).mock.calls.some(([action]) => action.startsWith("github_"))).toBe(false);
    const toolList = screen.getByRole("list", { name: "Target tools" });
    expect(within(toolList).getAllByRole("listitem")).toHaveLength(5);
  });

  it("uses one connect-and-verify action and preserves the narrow binding interface", async () => {
    const initial = settings();
    const connected = settings("https://github.com/example/resources.git", "resources");
    vi.mocked(ccPortAction).mockImplementation(async (action) => {
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
    expect(vi.mocked(ccPortAction)).toHaveBeenCalledWith("config_bind_repo", {
      repo_url: "https://github.com/example/resources",
      expected_current_repo_url: "",
    });
    expect(vi.mocked(ccPortAction).mock.calls.some(([action]) => action === "config_save")).toBe(false);
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
    vi.mocked(ccPortAction).mockImplementation(async (action) => {
      if (action === "config_get") return initial;
      if (action === "git_credential_status") return credentialStatus();
      if (action === "platform_set_enabled") return updated;
      throw new Error(`Unexpected action: ${action}`);
    });
    renderView();
    const user = userEvent.setup();

    await user.click(await screen.findByRole("checkbox", { name: "Windsurf" }));

    expect(vi.mocked(ccPortAction)).toHaveBeenCalledWith("platform_set_enabled", {
      name: "windsurf",
      enabled: false,
    });
  });

  it("renders Windows and WSL profiles separately and toggles the stable profile id", async () => {
    const initial = settings();
    const claudeBase = initial.config.platforms.find((profile) => profile.name === "claude-code")!;
    initial.config.platforms = [
      ...initial.config.platforms.filter((profile) => profile.name !== "claude-code"),
      {
        ...claudeBase,
        name: "profile-a",
        tool_id: "claude-code",
        environment_kind: "windows",
        environment_name: "Windows",
        display_name: "Claude Code",
        instructions_path: "C:/Users/test/.claude/CLAUDE.md",
        memories_dir: "C:/Users/test/.claude/projects",
        settings_path: "C:/Users/test/.claude/settings.json",
      },
      {
        ...claudeBase,
        name: "profile-b",
        tool_id: "claude-code",
        environment_kind: "wsl",
        environment_name: "Ubuntu-24.04",
        display_name: "Claude Code",
        instructions_path: "/home/test/.claude/CLAUDE.md",
        memories_dir: "/home/test/.claude/projects",
        settings_path: "/home/test/.claude/settings.json",
      },
    ];
    vi.mocked(ccPortAction).mockImplementation(async (action) => {
      if (action === "config_get") return initial;
      if (action === "git_credential_status") return credentialStatus();
      if (action === "platform_set_enabled") return initial;
      throw new Error(`Unexpected action: ${action}`);
    });
    renderView();
    const user = userEvent.setup();

    const windows = await screen.findByRole("checkbox", {
      name: "Claude Code · Windows native",
    });
    const wsl = screen.getByRole("checkbox", {
      name: "Claude Code · WSL · Ubuntu-24.04",
    });
    expect(windows).toBeEnabled();
    expect(wsl).toBeEnabled();
    expect(screen.getByText("Windows native", { selector: ".platform-environment-badge" }))
      .toBeVisible();
    expect(screen.getByText("WSL · Ubuntu-24.04", { selector: ".platform-environment-badge" }))
      .toBeVisible();
    expect(screen.getByText("profile-a", { selector: ".platform-toggle-identity small" }))
      .toBeVisible();
    expect(screen.getByText("profile-b", { selector: ".platform-toggle-identity small" }))
      .toBeVisible();
    expect(screen.getByText("C:/Users/test/.claude/CLAUDE.md")).toBeVisible();
    expect(screen.getByText("/home/test/.claude/CLAUDE.md")).toBeVisible();
    expect(screen.getByText("C:/Users/test/.claude/projects")).toBeVisible();
    expect(screen.getByText("/home/test/.claude/projects")).toBeVisible();
    expect(screen.getByText("C:/Users/test/.claude/settings.json")).toBeVisible();
    expect(screen.getByText("/home/test/.claude/settings.json")).toBeVisible();

    await user.click(wsl);

    expect(vi.mocked(ccPortAction)).toHaveBeenCalledWith("platform_set_enabled", {
      name: "profile-b",
      enabled: false,
    });
  });

  it("renders diagnostics as a button and status line without expandable content", async () => {
    mockInitial();
    renderView();

    expect(await screen.findByRole("button", { name: "Diagnostics" })).toBeEnabled();
    expect(screen.getByText("Not checked yet")).toBeVisible();
    expect(document.querySelector("details")).not.toBeInTheDocument();
    expect(document.querySelector("summary")).not.toBeInTheDocument();
    expect(vi.mocked(ccPortAction).mock.calls.some(([action]) => action === "doctor")).toBe(false);
  });

  it("opens a loading dialog and delegates diagnostics without disabling the running button", async () => {
    mockInitial();
    const onRunDiagnostics = vi.fn(async () => undefined);
    const { rerenderDiagnostics } = renderView(
      0,
      "en",
      { phase: "running", checks: null, error: "" },
      onRunDiagnostics,
    );
    const user = userEvent.setup();

    const button = await screen.findByRole("button", { name: "Diagnostics" });
    expect(button).toBeEnabled();
    expect(screen.getByText("Checking...")).toBeVisible();
    await user.click(button);

    const dialog = screen.getByRole("dialog", { name: "Diagnostics" });
    expect(within(dialog).getByText(
      "Checking the environment. Results will appear here when complete.",
    )).toBeVisible();
    expect(onRunDiagnostics).toHaveBeenCalledTimes(1);

    rerenderDiagnostics({
      phase: "healthy",
      checks: [
        { id: "git", label: "Git", ok: true, status: "ok", detail: "Ready" },
      ],
      error: "",
    });
    expect(within(dialog).getByText("Environment is healthy · 1 passed · 0 skipped")).toBeVisible();

    await user.click(within(dialog).getByRole("button", { name: "Close" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("summarizes diagnostic results and lists only warnings and errors", async () => {
    const checks: DoctorCheck[] = [
      { id: "git", label: "Git", ok: true, status: "ok", detail: "Ready" },
      { id: "config", label: "Config", ok: true, status: "skipped", detail: "Using defaults" },
      { id: "resource_repo", label: "Resource repo", ok: true, status: "warning", detail: "Remote differs" },
      { id: "platform:cursor", label: "Platform: cursor", ok: false, status: "error", detail: "Directory is not writable" },
    ];
    mockInitial();
    renderView(0, "en", { phase: "issues", checks, error: "" });
    const user = userEvent.setup();

    expect(await screen.findByText("Issues found")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Diagnostics" }));

    expect(await screen.findByText("1 passed · 1 warnings · 1 errors · 1 skipped")).toBeVisible();
    const issues = screen.getByRole("list", { name: "Diagnostic issues" });
    expect(within(issues).getAllByRole("listitem")).toHaveLength(2);
  });

  it("shows a diagnostic failure inside the dialog", async () => {
    mockInitial();
    renderView(0, "en", {
      phase: "failed",
      checks: null,
      error: "Sidecar unavailable",
    });
    const user = userEvent.setup();

    expect(await screen.findByText("Check failed")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Diagnostics" }));

    expect(within(screen.getByRole("dialog")).getByRole("alert")).toHaveTextContent(
      "Diagnostics could not be completed: Sidecar unavailable",
    );
  });

  it("reviews and applies an exact-profile AI integration plan", async () => {
    let connected = false;
    vi.mocked(ccPortAction).mockImplementation(async (action, payload) => {
      if (action === "config_get") return settings();
      if (action === "git_credential_status") return credentialStatus();
      if (action === "approval_requests") return { requests: [] };
      if (action === "ai_integration_status") {
        return {
          profiles: [{
            profile_id: "codex",
            configured: connected,
            installed: connected,
            managed: connected,
            skill_managed: connected,
            mcp_managed: connected,
            skill_ready: connected,
            mcp_registered: connected,
            transport_verified: connected,
            transport_status: connected ? "verified" : "unknown",
            managed_actions_available: connected ? ["uninstall"] : [],
            tool_count: connected ? 12 : 0,
            tools: [],
            problems: [],
          }],
        };
      }
      if (action === "ai_integration_plan") {
        expect(payload).toEqual({
          profile_id: "codex",
          action: "install",
          overwrite_unmanaged: false,
        });
        return {
          operation_id: "a".repeat(32),
          action: "install",
          profile_id: "codex",
          command: "C:/Program Files/CC Port/cc-port.exe",
          command_args: ["mcp", "--stdio"],
          command_source: "bundled-sibling",
          target: {
            profile_id: "codex",
            tool_id: "codex",
            display_name: "Codex",
            environment_kind: "windows",
            environment_name: "Windows",
            available: true,
            skill_path: "C:/Users/test/.codex/skills/cc-port",
            mcp_config_path: "C:/Users/test/.codex/config.toml",
            mcp_config_format: "codex-toml",
            skill_status: "missing",
            mcp_status: "missing",
            actions: ["install-skill", "register-mcp"],
            blockers: [],
            current_skill_hash: "",
            current_config_hash: "",
            desired_skill_hash: "skill-hash",
            desired_entry_hash: "entry-hash",
          },
          plan_hash: "plan-hash",
          blocked: false,
          blockers: [],
          requires_approval: true,
          approval_id: "approval-1",
          overwrite_unmanaged: false,
          schema_version: 1,
        };
      }
      if (action === "ai_integration_approve_apply") {
        expect(payload).toEqual({
          operation_id: "a".repeat(32),
          plan_hash: "plan-hash",
          approval_id: "approval-1",
        });
        connected = true;
        return {
          status: "succeeded",
          operation_id: "a".repeat(32),
          plan_hash: "plan-hash",
          profile_id: "codex",
          changed: true,
          verified: true,
          approval_id: "approval-1",
          blockers: [],
          message: "done",
        };
      }
      throw new Error(`Unexpected action: ${action}`);
    });
    const { onChanged } = renderView();
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "Review enable plan" }));

    const dialog = await screen.findByRole("dialog", { name: "Review AI integration plan" });
    expect(within(dialog).getByText("C:/Users/test/.codex/skills/cc-port")).toBeVisible();
    expect(within(dialog).getByText("C:/Users/test/.codex/config.toml")).toBeVisible();
    expect(within(dialog).getByText("install-skill")).toBeVisible();
    expect(within(dialog).getByText("register-mcp")).toBeVisible();

    await user.click(within(dialog).getByRole("button", { name: "Approve and enable" }));

    await waitFor(() => {
      expect(vi.mocked(ccPortAction).mock.calls.some(
        ([action]) => action === "ai_integration_approve_apply",
      )).toBe(true);
      expect(onChanged).toHaveBeenCalledTimes(1);
    });
    expect(screen.queryByRole("dialog", { name: "Review AI integration plan" }))
      .not.toBeInTheDocument();
  });

  it("lets a human approve a pending MCP write exactly once", async () => {
    const pending = {
      approval_id: "approval-asset-1",
      kind: "asset-action",
      operation_id: "operation-1",
      plan_hash: "asset-plan-hash",
      scope_hash: "scope-hash",
      summary: "Download skill:demo to codex",
      status: "pending",
      created_at: "2026-08-11T12:00:00Z",
      expires_at: "2026-08-11T12:30:00Z",
      approved_at: "",
      consumed_at: "",
      rejected_at: "",
      metadata: {
        choices: [
          { note: "x".repeat(1_200) },
          { overwrite_unmanaged: true, link_target_confirmed: true },
        ],
      },
      revision: "revision-1",
      schema_version: 2,
    };
    let requests = [pending];
    vi.mocked(ccPortAction).mockImplementation(async (action, payload) => {
      if (action === "config_get") return settings();
      if (action === "git_credential_status") return credentialStatus();
      if (action === "ai_integration_status") return { profiles: [] };
      if (action === "approval_requests") return { requests };
      if (action === "approval_approve") {
        expect(payload).toEqual({
          approval_id: "approval-asset-1",
          operation_id: "operation-1",
          plan_hash: "asset-plan-hash",
          scope_hash: "scope-hash",
          revision: "revision-1",
        });
        requests = [{ ...pending, status: "approved", approved_at: "2026-08-11T12:01:00Z" }];
        return requests[0];
      }
      throw new Error(`Unexpected action: ${action}`);
    });
    renderView();
    const user = userEvent.setup();

    expect(await screen.findByText("Download skill:demo to codex")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Review approval" }));

    const dialog = screen.getByRole("dialog", { name: "Review exact AI write scope" });
    expect(within(dialog).getByText("operation-1")).toBeVisible();
    expect(within(dialog).getByText("asset-plan-hash")).toBeVisible();
    expect(within(dialog).getByText("scope-hash")).toBeVisible();
    expect(within(dialog).getByText(/overwrite_unmanaged/)).toHaveTextContent(
      /link_target_confirmed/,
    );
    const approve = within(dialog).getByRole("button", { name: "Approve once" });
    expect(approve).toBeDisabled();
    await user.click(within(dialog).getByRole("checkbox", {
      name: "I reviewed the complete scope shown above and approve this exact write",
    }));
    await user.click(approve);

    await waitFor(() => {
      expect(vi.mocked(ccPortAction)).toHaveBeenCalledWith("approval_approve", {
        approval_id: "approval-asset-1",
        operation_id: "operation-1",
        plan_hash: "asset-plan-hash",
        scope_hash: "scope-hash",
        revision: "revision-1",
      });
      expect(screen.queryByRole("button", { name: "Approve once" })).not.toBeInTheDocument();
    });
  });
});
