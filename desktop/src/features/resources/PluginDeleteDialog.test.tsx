import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { lpmAction } from "@/api/client";
import { createTranslator } from "@/app/i18n";
import { TaskCenterProvider } from "@/app/TaskCenterContext";
import { PluginDeleteDialog } from "@/features/resources/PluginDeleteDialog";

vi.mock("@/api/client", () => ({ lpmAction: vi.fn() }));

const t = createTranslator("en");
const plan = {
  resource_key: "plugin:claude-marketplace-demo-acme",
  remote_commit: "abc123",
  selected_instance_ids: ["user-instance"],
  instances: [
    {
      id: "user-instance",
      platform: "claude-code",
      scope: "user",
      project_id: "",
      enabled: true,
      writable: true,
      selectable: true,
      method: "claude plugin uninstall --scope user",
      detail: "Uninstall the user-scoped plugin.",
    },
    {
      id: "managed-instance",
      platform: "claude-code",
      scope: "managed",
      project_id: "",
      enabled: true,
      writable: false,
      selectable: false,
      method: "manual",
      detail: "Managed by organization policy.",
    },
  ],
  plan_hash: "delete-plan-hash",
  blocked: false,
  blockers: [],
};

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("PluginDeleteDialog", () => {
  it("renders structured deletion details in Chinese", async () => {
    vi.mocked(lpmAction).mockResolvedValue({
      ...plan,
      instances: [
        {
          ...plan.instances[0],
          detail_ref: {
            code: "plugin.delete.detail.claude_cli",
            fallback: plan.instances[0].detail,
            params: { plugin_id: "demo" },
          },
        },
        {
          ...plan.instances[1],
          detail_ref: {
            code: "plugin.delete.detail.managed_policy",
            fallback: plan.instances[1].detail,
          },
        },
      ],
    } as never);

    render(
      <TaskCenterProvider>
        <PluginDeleteDialog
          resourceKey={plan.resource_key}
          t={createTranslator("zh")}
          onClose={vi.fn()}
          onDone={vi.fn()}
        />
      </TaskCenterProvider>,
    );

    expect(await screen.findByText("对 demo 执行带作用域的 Claude 插件卸载。")).toBeVisible();
    expect(screen.getByText("该实例由组织策略控制，LPM 无法卸载。")).toBeVisible();
  });

  it("keeps managed instances unselectable and applies the revalidated selected plan", async () => {
    const user = userEvent.setup();
    const onDone = vi.fn();
    vi.mocked(lpmAction).mockImplementation(async (action, payload) => {
      if (action === "plugin_delete_plan") {
        expect(payload).toEqual(payload && "instance_ids" in payload
          ? { resource_key: plan.resource_key, instance_ids: ["user-instance"] }
          : { resource_key: plan.resource_key });
        return plan as never;
      }
      if (action === "plugin_delete_apply") {
        expect(payload).toEqual({
          resource_key: plan.resource_key,
          instance_ids: ["user-instance"],
          plan_hash: "delete-plan-hash",
        });
        return {
          status: "succeeded",
          resource_key: plan.resource_key,
          plan_hash: "delete-plan-hash",
          results: [],
          remote_deleted: true,
          remote_commit: "def456",
        } as never;
      }
      throw new Error(`unexpected ${action}`);
    });

    render(
      <TaskCenterProvider>
        <PluginDeleteDialog resourceKey={plan.resource_key} t={t} onClose={vi.fn()} onDone={onDone} />
      </TaskCenterProvider>,
    );

    const checkboxes = await screen.findAllByRole("checkbox");
    expect(checkboxes[0]).toBeChecked();
    expect(checkboxes[1]).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Confirm" }));

    await waitFor(() => expect(onDone).toHaveBeenCalledOnce());
    expect(lpmAction).toHaveBeenCalledWith("plugin_delete_apply", {
      resource_key: plan.resource_key,
      instance_ids: ["user-instance"],
      plan_hash: "delete-plan-hash",
    });
  });
});
