import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  listenForOAuthDeepLinks,
  lpmAction,
  openExternalUrl,
} from "@/api/client";
import { createTranslator } from "@/app/i18n";
import { TaskCenterProvider } from "@/app/TaskCenterContext";
import { SettingsView } from "@/features/settings/SettingsView";
import type {
  ConfigBindRepoResult,
  ConfigSettings,
  DoctorCheck,
  GithubAuthStatus,
  GithubWebAuthSession,
} from "@/types/lpm";

vi.mock("@/api/client", () => ({
  listenForOAuthDeepLinks: vi.fn(async () => () => undefined),
  lpmAction: vi.fn(),
  openExternalUrl: vi.fn(),
}));

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
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.clearAllMocks();
});

describe("SettingsView simplified settings", () => {
  it("loads on mount and refresh-version changes without rendering a page refresh button", async () => {
    mockInitial();
    const { rerender } = renderView();

    expect(screen.queryByRole("button", { name: "Refresh" })).not.toBeInTheDocument();
    await screen.findByText("Connect resource repository");
    expect(screen.queryByRole("button", { name: "Reload" })).not.toBeInTheDocument();
    expect(vi.mocked(lpmAction).mock.calls.filter(([action]) => action === "config_get")).toHaveLength(1);
    expect(vi.mocked(lpmAction).mock.calls.filter(([action]) => action === "github_auth_status")).toHaveLength(1);

    rerender(1);

    await waitFor(() => {
      expect(vi.mocked(lpmAction).mock.calls.filter(([action]) => action === "config_get")).toHaveLength(2);
      expect(vi.mocked(lpmAction).mock.calls.filter(([action]) => action === "github_auth_status")).toHaveLength(2);
    });
    expect(screen.queryByRole("button", { name: "Reload" })).not.toBeInTheDocument();
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
      if (action === "github_auth_status") return auth();
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
    expect(screen.getByText("https://github.com/example/new-settings.git")).toBeVisible();
  });

  it("removes owner and token controls and shows the five complete target presets", async () => {
    mockInitial();
    renderView();

    expect(await screen.findByText("Connect resource repository")).toBeVisible();
    expect(screen.queryByText("Advanced settings")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Branch")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Local path")).not.toBeInTheDocument();
    expect(screen.queryByText("Repository owner")).not.toBeInTheDocument();
    expect(screen.queryByText("Saved token")).not.toBeInTheDocument();
    expect(screen.queryByText("Active token source")).not.toBeInTheDocument();
    expect(screen.queryByText("Granted permissions")).not.toBeInTheDocument();
    expect(screen.queryByDisplayValue(/gho_/)).not.toBeInTheDocument();
    expect(screen.getByText("Lingye")).toBeVisible();
    expect(vi.mocked(lpmAction).mock.calls.some(([action]) => action === "github_owner_set")).toBe(false);
    expect(vi.mocked(lpmAction).mock.calls.some(([action]) => action === "github_token_reveal")).toBe(false);
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

  it("renders structured diagnostic details in Chinese", async () => {
    const checks: DoctorCheck[] = [{
      id: "resource_repo",
      label: "Resource repo",
      ok: true,
      status: "warning",
      detail: "Configured but local path does not exist: C:/resources",
      detail_ref: {
        code: "doctor.resource_repo.path_missing",
        fallback: "Configured but local path does not exist: C:/resources",
        params: { path: "C:/resources" },
      },
    }];
    vi.mocked(lpmAction).mockImplementation(async (action) => {
      if (action === "config_get") return settings();
      if (action === "github_auth_status") return auth();
      if (action === "doctor") return { checks };
      throw new Error(`Unexpected action: ${action}`);
    });
    renderView(0, "zh");
    const user = userEvent.setup();

    await user.click(await screen.findByText("诊断"));
    await user.click(screen.getByRole("button", { name: "运行诊断" }));

    expect(await screen.findByText("配置的资源路径不存在：C:/resources")).toBeVisible();
    expect(screen.queryByText(checks[0].detail)).not.toBeInTheDocument();
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
    expect(screen.queryByRole("button", { name: "Reload" })).not.toBeInTheDocument();

    rejectDoctor?.(new Error("diagnostics failed"));
    await waitFor(() => expect(screen.getByRole("button", { name: "Run diagnostics" })).toBeEnabled());
  });

  it("starts browser OAuth without showing or requesting a device code", async () => {
    const session: GithubWebAuthSession = {
      session_id: "session_1234567890",
      authorization_url: "https://github.com/login/oauth/authorize?client_id=client",
      expires_in: 600,
      interval: 2,
      purpose: "standard",
      scopes: ["repo"],
    };
    vi.mocked(lpmAction).mockImplementation(async (action) => {
      if (action === "config_get") return settings();
      if (action === "github_auth_status") return auth({ state: "missing", source: "none", login: "", scopes: [] });
      if (action === "github_web_auth_start") return session;
      throw new Error(`Unexpected action: ${action}`);
    });
    renderView();
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "Sign in to GitHub" }));

    expect(await screen.findByText("Waiting for GitHub authorization")).toBeVisible();
    expect(screen.queryByText(/one-time code/i)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Copy code" })).not.toBeInTheDocument();
    expect(vi.mocked(lpmAction)).toHaveBeenCalledWith("github_web_auth_start", { purpose: "standard" });
    expect(openExternalUrl).toHaveBeenCalledWith(session.authorization_url);
  });

  it("keeps the session when opening the browser fails so the user can reopen it", async () => {
    const session: GithubWebAuthSession = {
      session_id: "session_1234567890",
      authorization_url: "https://github.com/login/oauth/authorize?client_id=client",
      expires_in: 600,
      interval: 2,
      purpose: "standard",
      scopes: ["repo"],
    };
    vi.mocked(openExternalUrl).mockRejectedValueOnce(new Error("system opener failed"));
    vi.mocked(lpmAction).mockImplementation(async (action) => {
      if (action === "config_get") return settings();
      if (action === "github_auth_status") return auth({ state: "missing", login: "" });
      if (action === "github_web_auth_start") return session;
      throw new Error(`Unexpected action: ${action}`);
    });
    const { onError } = renderView();
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "Sign in to GitHub" }));

    expect(await screen.findByText("Waiting for GitHub authorization")).toBeVisible();
    expect(screen.getByRole("button", { name: "Reopen GitHub" })).toBeEnabled();
    expect(onError).toHaveBeenCalledWith(
      "The browser could not be opened. Use “Reopen GitHub” to try again.",
    );
  });

  it("offers a localized retry after the OAuth broker is unavailable", async () => {
    vi.mocked(lpmAction).mockImplementation(async (action) => {
      if (action === "config_get") return settings();
      if (action === "github_auth_status") return auth({ state: "missing", login: "" });
      if (action === "github_web_auth_start") {
        throw Object.assign(new Error("backend detail"), { code: "OAuthSessionError" });
      }
      throw new Error(`Unexpected action: ${action}`);
    });
    const { onError } = renderView();
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "Sign in to GitHub" }));

    expect(await screen.findByRole("button", { name: "Retry sign-in" })).toBeEnabled();
    expect(onError).toHaveBeenCalledWith(
      "GitHub sign-in is temporarily unavailable. Try signing in again.",
    );
  });

  it("reopens GitHub and cancels an in-progress browser authorization", async () => {
    const session: GithubWebAuthSession = {
      session_id: "session_1234567890",
      authorization_url: "https://github.com/login/oauth/authorize?client_id=client",
      expires_in: 600,
      interval: 2,
      purpose: "standard",
      scopes: ["repo"],
    };
    vi.mocked(lpmAction).mockImplementation(async (action) => {
      if (action === "config_get") return settings();
      if (action === "github_auth_status") return auth({ state: "missing", login: "" });
      if (action === "github_web_auth_start") return session;
      if (action === "github_web_auth_cancel") return { cancelled: true };
      throw new Error(`Unexpected action: ${action}`);
    });
    renderView();
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "Sign in to GitHub" }));
    await user.click(await screen.findByRole("button", { name: "Reopen GitHub" }));
    expect(openExternalUrl).toHaveBeenCalledTimes(2);
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(lpmAction).toHaveBeenCalledWith("github_web_auth_cancel", {
      session_id: "session_1234567890",
    });
    expect(screen.queryByText("Waiting for GitHub authorization")).not.toBeInTheDocument();
  });

  it("polls immediately when a matching OAuth deep link arrives", async () => {
    const session: GithubWebAuthSession = {
      session_id: "session_1234567890",
      authorization_url: "https://github.com/login/oauth/authorize?client_id=client",
      expires_in: 600,
      interval: 20,
      purpose: "standard",
      scopes: ["repo"],
    };
    let deepLinkHandler: ((urls: string[]) => void) | undefined;
    vi.mocked(listenForOAuthDeepLinks).mockImplementation(async (handler) => {
      deepLinkHandler = handler;
      return () => undefined;
    });
    let statusCalls = 0;
    vi.mocked(lpmAction).mockImplementation(async (action) => {
      if (action === "config_get") return settings();
      if (action === "github_auth_status") {
        statusCalls += 1;
        return statusCalls === 1
          ? auth({ state: "missing", source: "none", login: "", scopes: [] })
          : auth();
      }
      if (action === "github_web_auth_start") return session;
      if (action === "github_web_auth_poll") {
        return { state: "authorized", login: "Lingye", scopes: ["repo"] };
      }
      throw new Error(`Unexpected action: ${action}`);
    });
    renderView();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Sign in to GitHub" }));

    await act(async () => {
      deepLinkHandler?.([
        "lingye-lpm://oauth/complete?session_id=session_1234567890&result=success",
      ]);
    });

    await waitFor(() => expect(lpmAction).toHaveBeenCalledWith("github_web_auth_poll", {
      session_id: "session_1234567890",
      immediate: true,
    }));
    expect(await screen.findByText("Lingye")).toBeVisible();
  });

  it("removes only the local authorization after confirmation", async () => {
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

    await user.click(await screen.findByRole("button", { name: "Remove local authorization" }));

    expect(lpmAction).toHaveBeenCalledWith("github_token_clear");
    expect(screen.getByText("Not connected")).toBeVisible();
  });

  it("disables browser authorization when OAuth is not configured", async () => {
    mockInitial(settings(), auth({
      state: "invalid",
      login: "",
      oauth_configured: false,
      error: "OAuth client is unavailable.",
    }));
    renderView();

    expect(await screen.findByText("Needs attention")).toBeVisible();
    expect(screen.getByText("OAuth client is unavailable.")).toBeVisible();
    expect(screen.getByRole("button", { name: "Sign in to GitHub" })).toBeDisabled();
  });

  it("shows environment-managed access without local authorization actions", async () => {
    mockInitial(settings(), auth({
      source: "env",
      login: "EnvironmentUser",
      env_override: true,
      can_clear: true,
    }));
    renderView();

    expect(await screen.findByText("EnvironmentUser")).toBeVisible();
    expect(screen.getByText(/managed outside this app/)).toBeVisible();
    expect(screen.getByRole("button", { name: "Authorize again" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Remove local authorization" })).not.toBeInTheDocument();
  });
});
