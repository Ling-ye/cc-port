import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { lpmAction } from "@/api/client";
import { createTranslator } from "@/app/i18n";
import { TaskCenterProvider } from "@/app/TaskCenterContext";
import { ToastViewport } from "@/components/TaskFeedback";
import { CollectGithubDialog } from "@/features/resources/CollectGithubDialog";

vi.mock("@/api/client", () => ({
  lpmAction: vi.fn(),
}));

const t = createTranslator("en");

function renderDialog() {
  const onClose = vi.fn();
  const onAdded = vi.fn(async () => undefined);
  render(
    <TaskCenterProvider>
      <CollectGithubDialog t={t} onClose={onClose} onAdded={onAdded} />
      <ToastViewport t={t} />
    </TaskCenterProvider>,
  );
  return { onAdded, onClose };
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("CollectGithubDialog", () => {
  it("renders a dedicated GitHub form and submits a pinned reference", async () => {
    const user = userEvent.setup();
    vi.mocked(lpmAction).mockResolvedValue({ entry: { kind: "skill", name: "demo" } });
    const { onAdded } = renderDialog();

    expect(screen.getByRole("dialog", { name: "Collect from GitHub" })).toBeVisible();
    expect(screen.queryByRole("tab")).not.toBeInTheDocument();
    expect(screen.queryByText("Plugin reference")).not.toBeInTheDocument();
    await user.type(screen.getByLabelText("GitHub URL"), "https://github.com/example/demo");
    await user.click(screen.getByRole("button", { name: "Collect from GitHub" }));

    await waitFor(() => expect(onAdded).toHaveBeenCalledWith("skill:demo"));
    expect(lpmAction).toHaveBeenCalledWith("collect", {
      github_url: "https://github.com/example/demo",
      name: "",
      push: true,
    });
  });

  it("keeps the GitHub draft after a failed write and confirms dirty close", async () => {
    const user = userEvent.setup();
    vi.mocked(lpmAction).mockRejectedValue(new Error("write failed"));
    const { onClose } = renderDialog();

    await user.type(screen.getByLabelText("GitHub URL"), "https://github.com/example/demo");
    await user.click(screen.getByRole("button", { name: "Collect from GitHub" }));
    expect(await screen.findByText("write failed")).toBeVisible();
    expect(screen.getByLabelText("GitHub URL")).toHaveValue("https://github.com/example/demo");

    await user.keyboard("{Escape}");
    const confirmation = screen.getByRole("alertdialog", { name: "Discard this resource draft?" });
    await user.click(within(confirmation).getByRole("button", { name: "Cancel" }));
    expect(onClose).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Close" }));
    await user.click(screen.getByRole("button", { name: "Discard changes" }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("collects an explicit stdio MCP reference with portable arguments and placeholders", async () => {
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
    await user.click(screen.getByRole("button", { name: "Collect from GitHub" }));

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

  it("supports HTTP MCP configuration and rejects literal secret values", async () => {
    const user = userEvent.setup();
    renderDialog();

    await user.type(screen.getByLabelText("GitHub URL"), "https://github.com/example/mcp-server");
    await user.click(screen.getByText("Advanced settings"));
    await user.selectOptions(screen.getByLabelText("Type"), "mcp");
    await user.selectOptions(screen.getByLabelText("MCP transport"), "http");
    expect(screen.queryByLabelText("Command")).not.toBeInTheDocument();
    await user.type(screen.getByLabelText("Server URL"), "https://mcp.example.test/api");
    await user.type(screen.getByLabelText("Environment variables (one per line)"), "API_TOKEN=literal-secret");
    await user.click(screen.getByRole("button", { name: "Collect from GitHub" }));

    expect(await screen.findByText(
      "Use NAME or NAME=${PLACEHOLDER} for each environment variable; literal values are not allowed.",
    )).toBeVisible();
    expect(lpmAction).not.toHaveBeenCalled();
  });
});
