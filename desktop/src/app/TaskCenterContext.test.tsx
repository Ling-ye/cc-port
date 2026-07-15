import { useState } from "react";
import { act, cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  TaskCenterProvider,
  useTaskCenter,
  type RunTaskInput,
} from "@/app/TaskCenterContext";
import { createTranslator } from "@/app/i18n";
import { TaskCenterPanel, ToastViewport } from "@/components/TaskFeedback";

type TaskCenter = ReturnType<typeof useTaskCenter>;
let center: TaskCenter;

function Probe({ panelOpen = true }: { panelOpen?: boolean }) {
  center = useTaskCenter();
  const t = createTranslator("en");
  return (
    <>
      <span data-testid="running-count">{center.runningCount}</span>
      <TaskCenterPanel open={panelOpen} t={t} onClose={() => undefined} />
      <ToastViewport t={t} />
    </>
  );
}

function renderTaskCenter(panelOpen = true) {
  return render(
    <TaskCenterProvider>
      <Probe panelOpen={panelOpen} />
    </TaskCenterProvider>,
  );
}

function InteractiveProbe() {
  center = useTaskCenter();
  const [open, setOpen] = useState(false);
  const t = createTranslator("en");
  return (
    <>
      <button type="button" onClick={() => setOpen((current) => !current)}>Toggle tasks</button>
      <TaskCenterPanel open={open} t={t} onClose={() => setOpen(false)} />
      <ToastViewport t={t} />
    </>
  );
}

function renderInteractiveTaskCenter() {
  return render(
    <TaskCenterProvider>
      <InteractiveProbe />
    </TaskCenterProvider>,
  );
}

async function run<T>(input: RunTaskInput<T>) {
  await act(async () => {
    await center.runTask(input);
  });
}

async function runFailure(input: RunTaskInput<unknown>) {
  await act(async () => {
    try {
      await center.runTask(input);
    } catch {
      // Expected by failure-path tests.
    }
  });
}

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("TaskCenterProvider", () => {
  it("records successful and failed tasks with accessible toasts", async () => {
    renderTaskCenter();

    await run({
      kind: "read",
      title: "Refresh",
      action: async () => 3,
      successMessage: (count) => `Loaded ${count}`,
      retryPolicy: "safe-read",
    });
    await runFailure({
      kind: "write",
      title: "Install",
      action: async () => { throw new Error("Install failed"); },
      retryPolicy: "none",
    });

    expect(center.tasks).toHaveLength(2);
    expect(center.tasks[0]).toMatchObject({ status: "failed", message: "Install failed", retryPolicy: "none" });
    expect(center.tasks[1]).toMatchObject({ status: "succeeded", message: "Loaded 3" });
    expect(screen.getByRole("alert")).toHaveTextContent("Install failed");
    expect(screen.getByRole("status")).toHaveTextContent("Loaded 3");
    expect(screen.queryByRole("button", { name: "Retry" })).not.toBeInTheDocument();

    await userEvent.click(screen.getAllByRole("button", { name: "Dismiss notification" })[0]);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("updates running counts as concurrent tasks finish independently", async () => {
    renderTaskCenter();
    let finishFirst: ((value: string) => void) | undefined;
    let finishSecond: ((value: string) => void) | undefined;
    let firstPromise!: Promise<string>;
    let secondPromise!: Promise<string>;

    act(() => {
      firstPromise = center.runTask({
        kind: "read",
        title: "First",
        action: () => new Promise<string>((resolve) => { finishFirst = resolve; }),
      });
      secondPromise = center.runTask({
        kind: "read",
        title: "Second",
        action: () => new Promise<string>((resolve) => { finishSecond = resolve; }),
      });
    });

    expect(center.runningCount).toBe(2);
    await act(async () => {
      finishSecond?.("second");
      await secondPromise;
    });
    expect(center.runningCount).toBe(1);
    expect(center.tasks.find((task) => task.title === "Second")?.status).toBe("succeeded");
    expect(center.tasks.find((task) => task.title === "First")?.status).toBe("running");

    await act(async () => {
      finishFirst?.("first");
      await firstPromise;
    });
    expect(center.runningCount).toBe(0);
    expect(center.tasks.every((task) => task.status === "succeeded")).toBe(true);
  });

  it("retries safe reads in the original task record", async () => {
    renderTaskCenter();
    let attempts = 0;
    await runFailure({
      kind: "read",
      title: "Discover",
      action: async () => {
        attempts += 1;
        if (attempts === 1) throw new Error("Temporary failure");
        return "ready";
      },
      successMessage: "Discovery ready",
      retryPolicy: "safe-read",
    });
    const taskId = center.tasks[0].id;

    await act(async () => {
      await center.retryTask(taskId);
    });

    expect(attempts).toBe(2);
    expect(center.tasks).toHaveLength(1);
    expect(center.tasks[0]).toMatchObject({ id: taskId, status: "succeeded", message: "Discovery ready" });
  });

  it("keeps all running tasks and only the newest 50 completed tasks", async () => {
    renderTaskCenter();
    let finishRunning: (() => void) | undefined;
    await act(async () => {
      void center.runTask({
        kind: "running",
        title: "Still running",
        action: () => new Promise<void>((resolve) => { finishRunning = resolve; }),
      });
      for (let index = 0; index < 52; index += 1) {
        await center.runTask({ kind: "complete", title: `Task ${index}`, action: async () => index });
      }
    });

    expect(center.tasks).toHaveLength(51);
    expect(center.tasks.filter((task) => task.status === "running")).toHaveLength(1);
    expect(screen.getByTestId("running-count")).toHaveTextContent("1");

    act(() => center.clearCompleted());
    expect(center.tasks).toHaveLength(1);
    expect(center.tasks[0].status).toBe("running");

    await act(async () => finishRunning?.());
  });

  it("limits visible toasts and applies success and failure timeouts", async () => {
    vi.useFakeTimers();
    renderTaskCenter(false);

    for (let index = 0; index < 4; index += 1) {
      await run({ kind: "read", title: `Success ${index}`, action: async () => index });
    }
    expect(screen.getAllByRole("status")).toHaveLength(3);

    act(() => vi.advanceTimersByTime(4000));
    expect(screen.queryByRole("status")).not.toBeInTheDocument();

    await runFailure({ kind: "read", title: "Failure", action: async () => { throw new Error("No response"); } });
    expect(screen.getByRole("alert")).toBeInTheDocument();
    act(() => vi.advanceTimersByTime(7999));
    expect(screen.getByRole("alert")).toBeInTheDocument();
    act(() => vi.advanceTimersByTime(1));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("opens, closes and clears the non-modal task panel", async () => {
    const user = userEvent.setup();
    renderInteractiveTaskCenter();
    await run({ kind: "read", title: "Refresh", action: async () => undefined });

    await user.click(screen.getByRole("button", { name: "Toggle tasks" }));
    expect(screen.getByRole("complementary", { name: "Task center" })).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Clear completed" }));
    expect(screen.getByText("No tasks in this session.")).toBeVisible();

    await user.click(screen.getByRole("button", { name: "Close" }));
    expect(screen.queryByRole("complementary", { name: "Task center" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Toggle tasks" }));
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("complementary", { name: "Task center" })).not.toBeInTheDocument();
  });
});
