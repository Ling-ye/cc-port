import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ccPortAction, selectDirectory } from "@/api/client";
import { createTranslator } from "@/app/i18n";
import { TaskCenterProvider } from "@/app/TaskCenterContext";
import { ToastViewport } from "@/components/TaskFeedback";
import { ImportLocalDialog } from "@/features/resources/ImportLocalDialog";

vi.mock("@/api/client", () => ({
  ccPortAction: vi.fn(),
  selectDirectory: vi.fn(),
}));

const t = createTranslator("en");

function renderDialog() {
  const onClose = vi.fn();
  const onAdded = vi.fn(async () => undefined);
  render(
    <TaskCenterProvider>
      <ImportLocalDialog t={t} onClose={onClose} onAdded={onAdded} />
      <ToastViewport t={t} />
    </TaskCenterProvider>,
  );
  return { onAdded, onClose };
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("ImportLocalDialog", () => {
  it("keeps instruction and memory out of manual import until a tool binding is available", async () => {
    const user = userEvent.setup();
    renderDialog();

    await user.click(screen.getByText("Advanced settings"));
    const type = screen.getByLabelText("Type");
    expect(within(type).queryByRole("option", { name: "instruction" })).not.toBeInTheDocument();
    expect(within(type).queryByRole("option", { name: "memory" })).not.toBeInTheDocument();
  });

  it("renders a dedicated local form and keeps manual input when folder selection is cancelled", async () => {
    const user = userEvent.setup();
    vi.mocked(selectDirectory)
      .mockResolvedValueOnce(null)
      .mockResolvedValueOnce("D:/resources/from-picker");
    renderDialog();

    expect(screen.getByRole("dialog", { name: "Import local folder" })).toBeVisible();
    expect(screen.queryByRole("tab")).not.toBeInTheDocument();
    await user.type(screen.getByLabelText("Local path"), "D:/resources/manual");
    await user.click(screen.getByRole("button", { name: "Choose folder" }));
    expect(screen.getByLabelText("Local path")).toHaveValue("D:/resources/manual");
    await user.click(screen.getByRole("button", { name: "Choose folder" }));
    expect(screen.getByLabelText("Local path")).toHaveValue("D:/resources/from-picker");
  });

  it("maps disabled push to no_push and reports the imported resource key", async () => {
    const user = userEvent.setup();
    vi.mocked(ccPortAction).mockResolvedValue({ entry: { kind: "rule", name: "local-rule" } });
    const { onAdded } = renderDialog();

    await user.type(screen.getByLabelText("Local path"), "D:/resources/rule");
    await user.click(screen.getByText("Advanced settings"));
    await user.selectOptions(screen.getByLabelText("Type"), "rule");
    await user.click(screen.getByRole("checkbox", { name: "Push private resource repo after completion" }));
    await user.click(screen.getByRole("button", { name: "Import local folder" }));

    await waitFor(() => expect(onAdded).toHaveBeenCalledWith("rule:local-rule"));
    expect(ccPortAction).toHaveBeenCalledWith("upload", {
      path: "D:/resources/rule",
      kind: "rule",
      name: "",
      no_push: true,
    });
  });

  it("keeps the local draft after a failed write and confirms dirty close", async () => {
    const user = userEvent.setup();
    vi.mocked(ccPortAction).mockRejectedValue(new Error("import failed"));
    const { onClose } = renderDialog();

    await user.type(screen.getByLabelText("Local path"), "D:/resources/rule");
    await user.click(screen.getByRole("button", { name: "Import local folder" }));
    expect(await screen.findByText("import failed")).toBeVisible();
    expect(screen.getByLabelText("Local path")).toHaveValue("D:/resources/rule");

    await user.keyboard("{Escape}");
    const confirmation = screen.getByRole("alertdialog", { name: "Discard this resource draft?" });
    await user.click(within(confirmation).getByRole("button", { name: "Cancel" }));
    expect(onClose).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Close" }));
    await user.click(screen.getByRole("button", { name: "Discard changes" }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
