import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { copyText, lpmAction, openExternalUrl } from "@/api/client";
import { createTranslator } from "@/app/i18n";
import { TaskCenterProvider } from "@/app/TaskCenterContext";
import { SettingsView } from "@/features/settings/SettingsView";
import type {
  ConfigBindRepoResult,
  ConfigSettings,
  DoctorCheck,
  GithubAuthSession,
  GithubAuthStatus,
} from "@/types/lpm";

vi.mock("@/api/client", () => ({
  copyText: vi.fn(),
  lpmAction: vi.fn(),
  openExternalUrl: vi.fn(),
  LpmApiError: class LpmApiError extends Error {
    code: string;

    constructor(code: string, message: string) {
      super(message);
      this.code = code;
    }
  },
}));

const t = createTranslator("en");
const platformNames = ["codex", "claude-code", "cursor", "windsurf", "opencode"];

function settings(repoUrl = "", repoName = "LingyeAIResources"): ConfigSettings {
  return {
    path: "C:/Users/test/.config/lpm/config.toml",
    exists: true,
    token_source: "config",
    token_preview: "gho_********cdef",
    config_token_preview: "gho_********cdef",
    env_token_active: false,
    config: {
      github: {
        owner: "Lingye",
        repo_prefix: "lpm-",
        default_private: true,
      },
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

function auth(overrides: Partial<GithubAuthStatus> = {}): GithubAuthStatus {
  return {
    state: "connected",
    source: "config",
    login: "Lingye",
    scopes: ["repo"],
    token_preview: "gho_********cdef",
    config_token_preview: "gho_********cdef",
    can_reveal: true,
    can_clear: true,
    env_override: false,
    oauth_configured: true,
    error: "",
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

function mockInitial(data = settings(), status = auth()) {
  vi.mocked(lpmAction).mockImplementation(async (action) => {
    if (action === "config_get") return data;
    if (action === "github_auth_status") return status;
    throw new Error(`Unexpected action: ${action}`);
  });
}

function renderView() {
  const onChanged = vi.fn(async () => undefined);
  const onError = vi.fn();
  render(
    <TaskCenterProvider>
      <SettingsView t={t} onError={onError} onChanged={onChanged} />
    </TaskCenterProvider>,
  );
  return { onChanged, onError };
}

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.clearAllMocks();
});

describe("SettingsView simplified settings", () => {
  it("removes advanced controls and shows the five complete target presets", async () => {
    mockInitial();
    renderView();

    expect(await screen.findByText("Connect resource repository")).toBeVisible();
    expect(screen.queryByText("Advanced settings")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Branch")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Local path")).not.toBeInTheDocument();
    const toolList = screen.getByRole("list", { name: "Target tools" });
    expect(within(toolList).getAllByRole("listitem")).toHaveLength(5);
    expect(within(toolList).queryByText("Automatic paths")).not.toBeInTheDocument();
    for (const name of ["Codex", "Claude Code", "Cursor", "Windsurf", "opencode"]) {
      expect(within(toolList).getByRole("checkbox", { name })).toBeChecked();
    }
  });

  it("does not expose branch, credential mode, or local path for an existing binding", async () => {
    mockInitial(settings("https://github.com/example/resources.git", "resources"));
    renderView();

    expect(await screen.findByText("https://github.com/example/resources.git")).toBeVisible();
    expect(screen.queryByText("Branch")).not.toBeInTheDocument();
    expect(screen.queryByText("Local path")).not.toBeInTheDocument();
    expect(screen.queryByText("Resource repository credentials")).not.toBeInTheDocument();
  });

  it("binds a repository without invoking the full config_save interface", async () => {
    const initial = settings();
    const connected = settings("https://github.com/example/resources.git", "resources");
    vi.mocked(lpmAction).mockImplementation(async (action) => {
      if (action === "config_get") return initial;
      if (action === "github_auth_status") return auth();
      if (action === "config_bind_repo") return bindingResult(connected);
      throw new Error(`Unexpected action: ${action}`);
    });
    const { onChanged } = renderView();
    const user = userEvent.setup();

    const input = await screen.findByLabelText("Repo URL");
    await user.type(input, "https://github.com/example/resources");
    await user.click(screen.getByRole("button", { name: "Bind repository" }));

    expect(await screen.findByText("Read and write credentials verified")).toBeVisible();
    expect(screen.getByText("The remote repository is ready and will be used on the next remote refresh.")).toBeVisible();
    expect(screen.queryByText(/first pull|explicit pull/i)).not.toBeInTheDocument();
    expect(vi.mocked(lpmAction)).toHaveBeenCalledWith("config_bind_repo", {
      repo_url: "https://github.com/example/resources",
      expected_current_repo_url: "",
    });
    expect(vi.mocked(lpmAction).mock.calls.some(([action]) => action === "config_save")).toBe(false);
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
  });

  it("updates only one platform through platform_set_enabled", async () => {
    const initial = settings();
    const updated = settings();
    updated.config.platforms = updated.config.platforms.map((profile) => (
      profile.name === "windsurf" ? { ...profile, enabled: false } : profile
    ));
    vi.mocked(lpmAction).mockImplementation(async (action) => {
      if (action === "config_get") return initial;
      if (action === "github_auth_status") return auth();
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
    await waitFor(() => expect(screen.getByRole("checkbox", { name: "Windsurf" })).not.toBeChecked());
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
      if (action === "github_auth_status") return auth();
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
    expect(within(issues).getByText("Resource repository")).toBeVisible();
    expect(within(issues).getByText("AI tool: Cursor")).toBeVisible();
    expect(within(issues).queryByText("Ready")).not.toBeInTheDocument();
    expect(within(issues).queryByText("Using defaults")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run again" })).toBeEnabled();
  });

  it("clears stale diagnostics on failure without disabling other settings", async () => {
    const checks: DoctorCheck[] = [
      { id: "git", label: "Git", ok: true, status: "ok", detail: "Ready" },
    ];
    let doctorCalls = 0;
    let rejectDoctor: ((reason: Error) => void) | undefined;
    const failedRun = new Promise<never>((_, reject) => {
      rejectDoctor = reject;
    });
    vi.mocked(lpmAction).mockImplementation(async (action) => {
      if (action === "config_get") return settings();
      if (action === "github_auth_status") return auth();
      if (action === "doctor") {
        doctorCalls += 1;
        if (doctorCalls === 1) return { checks };
        return failedRun;
      }
      throw new Error(`Unexpected action: ${action}`);
    });
    renderView();
    const user = userEvent.setup();

    await user.click(await screen.findByText("Diagnostics"));
    await user.click(screen.getByRole("button", { name: "Run diagnostics" }));
    expect(await screen.findByText("Environment is healthy · 1 passed · 0 skipped")).toBeVisible();

    await user.click(screen.getByRole("button", { name: "Run again" }));
    expect(screen.queryByText("Environment is healthy · 1 passed · 0 skipped")).not.toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "Codex" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Reload" })).toBeEnabled();

    rejectDoctor?.(new Error("diagnostics failed"));
    await waitFor(() => expect(screen.getByRole("button", { name: "Run diagnostics" })).toBeEnabled());
  });

  it("starts the fixed standard OAuth device flow and opens GitHub", async () => {
    const session: GithubAuthSession = {
      session_id: "session_1234567890",
      user_code: "ABCD-EFGH",
      verification_uri: "https://github.com/login/device",
      expires_in: 900,
      interval: 5,
      purpose: "standard",
      scopes: ["repo"],
    };
    vi.mocked(lpmAction).mockImplementation(async (action) => {
      if (action === "config_get") return settings();
      if (action === "github_auth_status") return auth({ state: "missing", source: "none", login: "", scopes: [] });
      if (action === "github_auth_start") return session;
      throw new Error(`Unexpected action: ${action}`);
    });
    renderView();
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "Connect GitHub" }));

    expect(await screen.findByText("ABCD-EFGH")).toBeVisible();
    expect(vi.mocked(lpmAction)).toHaveBeenCalledWith("github_auth_start", { purpose: "standard" });
    expect(openExternalUrl).toHaveBeenCalledWith("https://github.com/login/device");
  });

  it("reveals a config token for only 30 seconds and copies it explicitly", async () => {
    vi.mocked(lpmAction).mockImplementation(async (action) => {
      if (action === "config_get") return settings();
      if (action === "github_auth_status") return auth();
      if (action === "github_token_reveal") return { token: "gho_plaintext_secret" };
      throw new Error(`Unexpected action: ${action}`);
    });
    renderView();
    await screen.findByText("GitHub access");
    vi.useFakeTimers();

    fireEvent.click(screen.getByTitle("Reveal token for 30 seconds"));
    await act(async () => undefined);
    expect(screen.getByDisplayValue("gho_plaintext_secret")).toBeVisible();

    fireEvent.click(screen.getByTitle("Copy"));
    await act(async () => undefined);
    expect(copyText).toHaveBeenCalledWith("gho_plaintext_secret");

    act(() => vi.advanceTimersByTime(30_000));
    expect(screen.queryByDisplayValue("gho_plaintext_secret")).not.toBeInTheDocument();
  });

  it("clears only the local config token after confirmation", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    vi.mocked(lpmAction).mockImplementation(async (action) => {
      if (action === "config_get") return settings();
      if (action === "github_auth_status") return auth();
      if (action === "github_token_clear") return auth({
        state: "missing",
        source: "none",
        login: "",
        scopes: [],
        token_preview: "",
        config_token_preview: "",
        can_reveal: false,
        can_clear: false,
      });
      throw new Error(`Unexpected action: ${action}`);
    });
    renderView();
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "Remove local token" }));

    expect(lpmAction).toHaveBeenCalledWith("github_token_clear");
    expect(screen.getByText("Not connected")).toBeVisible();
  });
});
