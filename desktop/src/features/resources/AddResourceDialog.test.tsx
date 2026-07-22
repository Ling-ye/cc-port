import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { lpmAction, selectDirectory } from "@/api/client";
import { createTranslator } from "@/app/i18n";
import { TaskCenterProvider } from "@/app/TaskCenterContext";
import { ToastViewport } from "@/components/TaskFeedback";
import { AddResourceDialog } from "@/features/resources/AddResourceDialog";

vi.mock("@/api/client", () => ({
  lpmAction: vi.fn(),
  selectDirectory: vi.fn(),
}));

const t = createTranslator("en");

function renderDialog() {
  const onClose = vi.fn();
  const onAdded = vi.fn(async () => undefined);
  render(
    <TaskCenterProvider>
      <AddResourceDialog t={t} onClose={onClose} onAdded={onAdded} />
      <ToastViewport t={t} />
    </TaskCenterProvider>,
  );
  return { onAdded, onClose };
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("AddResourceDialog", () => {
  it("defaults to GitHub and keeps separate drafts while selecting or pasting a local path", async () => {
    const user = userEvent.setup();
    vi.mocked(selectDirectory)
      .mockResolvedValueOnce(null)
      .mockResolvedValueOnce("D:/resources/from-picker");
    renderDialog();

    expect(screen.getByRole("tab", { name: "Collect from GitHub" })).toHaveAttribute("aria-selected", "true");
    await user.type(screen.getByLabelText("GitHub URL"), "https://github.com/example/demo");
    await user.click(screen.getByRole("tab", { name: "Import local" }));
    await user.type(screen.getByLabelText("Local path"), "D:/resources/manual");
    await user.click(screen.getByRole("button", { name: "Choose folder" }));
    expect(screen.getByLabelText("Local path")).toHaveValue("D:/resources/manual");
    await user.click(screen.getByRole("button", { name: "Choose folder" }));
    expect(screen.getByLabelText("Local path")).toHaveValue("D:/resources/from-picker");

    await user.click(screen.getByText("Advanced settings"));
    await user.type(screen.getByLabelText("Resource name"), "local-name");
    await user.selectOptions(screen.getByLabelText("Type"), "skill");
    await user.click(screen.getByRole("tab", { name: "Collect from GitHub" }));
    expect(screen.getByLabelText("GitHub URL")).toHaveValue("https://github.com/example/demo");
    expect(screen.getByLabelText("Resource name")).toHaveValue("");
    await user.click(screen.getByRole("tab", { name: "Import local" }));
    expect(screen.getByLabelText("Resource name")).toHaveValue("local-name");
    expect(screen.getByLabelText("Type")).toHaveValue("skill");
  });

  it("maps disabled push to no_push for local import and reports the new resource key", async () => {
    const user = userEvent.setup();
    vi.mocked(lpmAction).mockResolvedValue({ entry: { kind: "rule", name: "local-rule" } });
    const { onAdded } = renderDialog();

    await user.click(screen.getByRole("tab", { name: "Import local" }));
    await user.type(screen.getByLabelText("Local path"), "D:/resources/rule");
    await user.click(screen.getByRole("checkbox", { name: "Push private resource repo after completion" }));
    const submitButtons = screen.getAllByRole("button", { name: "Import local" });
    await user.click(submitButtons[submitButtons.length - 1]);

    await waitFor(() => expect(onAdded).toHaveBeenCalledWith("rule:local-rule"));
    expect(lpmAction).toHaveBeenCalledWith("upload", expect.objectContaining({
      path: "D:/resources/rule",
      no_push: true,
    }));
  });

  it("asks before discarding a dirty draft", async () => {
    const user = userEvent.setup();
    const { onClose } = renderDialog();

    await user.type(screen.getByLabelText("GitHub URL"), "https://github.com/example/demo");
    await user.keyboard("{Escape}");
    const confirmation = screen.getByRole("alertdialog", { name: "Discard this resource draft?" });
    expect(confirmation).toBeVisible();
    await user.click(within(confirmation).getByRole("button", { name: "Cancel" }));
    expect(onClose).not.toHaveBeenCalled();
    expect(screen.getByLabelText("GitHub URL")).toHaveValue("https://github.com/example/demo");

    await user.click(screen.getByRole("button", { name: "Close" }));
    await user.click(screen.getByRole("button", { name: "Discard changes" }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("keeps the dialog and draft after a failed write", async () => {
    const user = userEvent.setup();
    vi.mocked(lpmAction).mockRejectedValue(new Error("write failed"));
    renderDialog();

    await user.type(screen.getByLabelText("GitHub URL"), "https://github.com/example/demo");
    const submitButtons = screen.getAllByRole("button", { name: "Collect from GitHub" });
    await user.click(submitButtons[submitButtons.length - 1]);

    expect(await screen.findByText("write failed")).toBeVisible();
    expect(screen.getByRole("dialog", { name: "Add resource" })).toBeVisible();
    expect(screen.getByLabelText("GitHub URL")).toHaveValue("https://github.com/example/demo");
  });

  it("collects an explicit stdio MCP reference with portable arguments and env placeholders", async () => {
    const user = userEvent.setup();
    vi.mocked(lpmAction).mockResolvedValue({ entry: { kind: "mcp", name: "github-server" } });
    const { onAdded } = renderDialog();

    await user.type(screen.getByLabelText("GitHub URL"), "https://github.com/example/mcp-server");
    await user.click(screen.getByText("Advanced settings"));
    await user.selectOptions(screen.getByLabelText("Type"), "mcp");
    await user.type(screen.getByLabelText("Command"), "npx");
    await user.type(screen.getByLabelText("Arguments (one per line)"), "-y\n@acme/github-server");
    fireEvent.change(screen.getByLabelText("Environment variables (one per line)"), {
      target: { value: "GITHUB_TOKEN\nCACHE_TOKEN=${SHARED_TOKEN}" },
    });
    const submitButtons = screen.getAllByRole("button", { name: "Collect from GitHub" });
    await user.click(submitButtons[submitButtons.length - 1]);

    await waitFor(() => expect(onAdded).toHaveBeenCalledWith("mcp:github-server"));
    expect(lpmAction).toHaveBeenCalledWith("collect", {
      github_url: "https://github.com/example/mcp-server",
      kind: "mcp",
      name: "",
      push: true,
      mcp_config: {
        command: "npx",
        args: ["-y", "@acme/github-server"],
        env: {
          GITHUB_TOKEN: "${GITHUB_TOKEN}",
          CACHE_TOKEN: "${SHARED_TOKEN}",
        },
      },
    });
  });

  it("collects an explicit HTTP MCP reference without carrying stdio fields", async () => {
    const user = userEvent.setup();
    vi.mocked(lpmAction).mockResolvedValue({ entry: { kind: "mcp", name: "remote-server" } });
    renderDialog();

    await user.type(screen.getByLabelText("GitHub URL"), "https://github.com/example/remote-mcp");
    await user.click(screen.getByText("Advanced settings"));
    await user.selectOptions(screen.getByLabelText("Type"), "mcp");
    await user.selectOptions(screen.getByLabelText("MCP transport"), "http");
    expect(screen.queryByLabelText("Command")).not.toBeInTheDocument();
    await user.type(screen.getByLabelText("Server URL"), "https://mcp.example.test/api");
    const submitButtons = screen.getAllByRole("button", { name: "Collect from GitHub" });
    await user.click(submitButtons[submitButtons.length - 1]);

    await waitFor(() => expect(lpmAction).toHaveBeenCalledWith("collect", expect.objectContaining({
      kind: "mcp",
      mcp_config: {
        type: "http",
        url: "https://mcp.example.test/api",
      },
    })));
  });

  it("warns for auto detection and blocks literal MCP env values", async () => {
    const user = userEvent.setup();
    renderDialog();

    await user.click(screen.getByText("Advanced settings"));
    expect(screen.getByText(/Auto-detected MCP resources still require portable configuration/)).toBeVisible();
    await user.selectOptions(screen.getByLabelText("Type"), "mcp");
    await user.type(screen.getByLabelText("GitHub URL"), "https://github.com/example/mcp-server");
    await user.type(screen.getByLabelText("Command"), "node");
    await user.type(screen.getByLabelText("Environment variables (one per line)"), "API_TOKEN=literal-secret");
    const submitButtons = screen.getAllByRole("button", { name: "Collect from GitHub" });
    await user.click(submitButtons[submitButtons.length - 1]);

    expect(await screen.findByText(
      "Use NAME or NAME=${PLACEHOLDER} for each environment variable; literal values are not allowed.",
    )).toBeVisible();
    expect(lpmAction).not.toHaveBeenCalled();
  });

  it("adds a plugin reference with selector and desired state without uploading content", async () => {
    const user = userEvent.setup();
    vi.mocked(lpmAction).mockImplementation(async (action) => {
      if (action === "plugin_projects_list") return { projects: [] } as never;
      if (action === "plugin_reference_add") {
        return { resource_key: "plugin:opencode-npm-acme-tool", status: "succeeded" } as never;
      }
      throw new Error(`unexpected ${action}`);
    });
    const { onAdded } = renderDialog();

    await user.click(screen.getByRole("tab", { name: "Plugin reference" }));
    await user.selectOptions(screen.getByLabelText("Platform"), "opencode");
    await user.type(screen.getByLabelText("Plugin id"), "@acme/tool");
    await user.selectOptions(screen.getByLabelText("Origin type"), "npm");
    await user.type(screen.getByLabelText("npm package"), "@acme/tool");
    await user.type(screen.getByLabelText("Version policy / selector"), "^2.0.0");
    const submitButtons = screen.getAllByRole("button", { name: "Plugin reference" });
    await user.click(submitButtons[submitButtons.length - 1]);

    await waitFor(() => expect(onAdded).toHaveBeenCalledWith("plugin:opencode-npm-acme-tool"));
    expect(lpmAction).toHaveBeenCalledWith("plugin_reference_add", expect.objectContaining({
      platform: "opencode",
      origin_type: "npm",
      package: "@acme/tool",
      selector: "^2.0.0",
      enabled: true,
    }));
  });
});
