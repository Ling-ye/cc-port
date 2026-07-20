import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { lpmAction } from "@/api/client";
import { createTranslator } from "@/app/i18n";
import { TaskCenterProvider } from "@/app/TaskCenterContext";
import { SettingsView } from "@/features/settings/SettingsView";
import type { ConfigBindRepoResult, ConfigSettings } from "@/types/lpm";

vi.mock("@/api/client", () => ({
  lpmAction: vi.fn(),
}));

const t = createTranslator("en");

function settings(repoUrl = "", repoName = "LingyeAIResources"): ConfigSettings {
  return {
    path: "C:/Users/test/.config/lpm/config.toml",
    exists: true,
    token_source: "none",
    token_preview: "",
    config_token_preview: "",
    env_token_active: false,
    config: {
      github: {
        owner: "",
        repo_prefix: "cursor-skill-",
        default_private: false,
      },
      git: { executable: "" },
      install: { target: "~/.cursor/skills" },
      resources: {
        repo_name: repoName,
        repo_url: repoUrl,
        local_path: "",
        branch: "main",
        credential_mode: repoUrl ? "native" : "auto",
      },
      state: {
        lock_timeout_seconds: 10,
        retention_days: 90,
        keep_latest_operations: 20,
        max_backup_mb: 2048,
      },
      platforms: [],
    },
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

function renderView() {
  const onChanged = vi.fn(async () => undefined);
  render(
    <TaskCenterProvider>
      <SettingsView t={t} onError={vi.fn()} onChanged={onChanged} />
    </TaskCenterProvider>,
  );
  return { onChanged };
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("SettingsView resource repository binding", () => {
  it("shows quick binding first and keeps advanced settings collapsed", async () => {
    vi.mocked(lpmAction).mockResolvedValue(settings());
    renderView();

    expect(await screen.findByText("Connect resource repository")).toBeVisible();
    expect(screen.getByText("Not bound")).toBeVisible();
    expect(screen.getByText("Advanced settings").closest("details")).not.toHaveAttribute("open");
  });

  it("binds a repository directly without running the legacy prepare flow", async () => {
    const initial = settings();
    const connected = settings("https://github.com/example/resources.git", "resources");
    vi.mocked(lpmAction).mockImplementation(async (action) => {
      if (action === "config_get") return initial;
      if (action === "config_bind_repo") return bindingResult(connected);
      throw new Error(`Unexpected action: ${action}`);
    });
    const { onChanged } = renderView();
    const user = userEvent.setup();

    const input = await screen.findByLabelText("Repo URL");
    await user.type(input, "https://github.com/example/resources");
    await user.click(screen.getByRole("button", { name: "Bind repository" }));

    expect(await screen.findByText("Read and write credentials verified")).toBeVisible();
    expect(vi.mocked(lpmAction)).toHaveBeenCalledWith("config_bind_repo", {
      repo_url: "https://github.com/example/resources",
      expected_current_repo_url: "",
    });
    expect(vi.mocked(lpmAction).mock.calls.some(([action]) => action === "config_check")).toBe(false);
    expect(vi.mocked(lpmAction).mock.calls.some(([action]) => action === "config_save")).toBe(false);
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
  });

  it("requires confirmation before replacing an existing binding", async () => {
    const currentUrl = "https://github.com/example/old.git";
    const initial = settings(currentUrl, "old");
    const connected = settings("https://github.com/example/new.git", "new");
    vi.mocked(lpmAction).mockImplementation(async (action) => {
      if (action === "config_get") return initial;
      if (action === "config_bind_repo") return bindingResult(connected);
      throw new Error(`Unexpected action: ${action}`);
    });
    renderView();
    const user = userEvent.setup();

    const input = await screen.findByLabelText("Repo URL");
    await user.clear(input);
    await user.type(input, "git@github.com:example/new.git");
    await user.click(screen.getByRole("button", { name: "Bind another repository" }));

    expect(screen.getByRole("dialog", { name: "Replace the bound repository?" })).toBeVisible();
    expect(vi.mocked(lpmAction).mock.calls.some(([action]) => action === "config_bind_repo")).toBe(false);

    await user.click(screen.getByRole("button", { name: "Verify and replace" }));
    await waitFor(() => expect(vi.mocked(lpmAction)).toHaveBeenCalledWith("config_bind_repo", {
      repo_url: "git@github.com:example/new.git",
      expected_current_repo_url: currentUrl,
    }));
  });
});
