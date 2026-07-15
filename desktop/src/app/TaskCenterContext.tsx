import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

export type OperationTaskStatus = "running" | "succeeded" | "failed";
export type TaskRetryPolicy = "safe-read" | "none";

export interface OperationTask {
  id: string;
  kind: string;
  title: string;
  context: string;
  status: OperationTaskStatus;
  retryPolicy: TaskRetryPolicy;
  startedAt: number;
  finishedAt?: number;
  message: string;
}

export interface TaskToast {
  id: string;
  taskId: string;
  tone: "success" | "danger";
  title: string;
  message: string;
}

export interface RunTaskInput<T> {
  kind: string;
  title: string;
  context?: string;
  action: () => Promise<T>;
  successMessage?: string | ((result: T) => string);
  failureMessage?: string | ((error: unknown) => string);
  retryPolicy?: TaskRetryPolicy;
}

interface TaskCenterValue {
  tasks: OperationTask[];
  runningCount: number;
  toasts: TaskToast[];
  runTask: <T>(input: RunTaskInput<T>) => Promise<T>;
  retryTask: (id: string) => Promise<void>;
  clearCompleted: () => void;
  dismissToast: (id: string) => void;
}

const completedHistoryLimit = 50;
const visibleToastLimit = 3;
const TaskCenterContext = createContext<TaskCenterValue | null>(null);

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function trimCompletedHistory(tasks: OperationTask[]): OperationTask[] {
  let completed = 0;
  return tasks.filter((task) => {
    if (task.status === "running") return true;
    completed += 1;
    return completed <= completedHistoryLimit;
  });
}

export function TaskCenterProvider({ children }: { children: ReactNode }) {
  const [tasks, setTasks] = useState<OperationTask[]>([]);
  const [toasts, setToasts] = useState<TaskToast[]>([]);
  const taskSequence = useRef(0);
  const toastSequence = useRef(0);
  const retryInputs = useRef(new Map<string, RunTaskInput<unknown>>());
  const toastTimers = useRef(new Map<string, number>());

  const dismissToast = useCallback((id: string) => {
    const timer = toastTimers.current.get(id);
    if (timer !== undefined) window.clearTimeout(timer);
    toastTimers.current.delete(id);
    setToasts((current) => current.filter((toast) => toast.id !== id));
  }, []);

  const dismissTaskToasts = useCallback((taskId: string) => {
    setToasts((current) => {
      const removed = current.filter((toast) => toast.taskId === taskId);
      removed.forEach((toast) => {
        const timer = toastTimers.current.get(toast.id);
        if (timer !== undefined) window.clearTimeout(timer);
        toastTimers.current.delete(toast.id);
      });
      return current.filter((toast) => toast.taskId !== taskId);
    });
  }, []);

  const enqueueToast = useCallback((toast: Omit<TaskToast, "id">) => {
    toastSequence.current += 1;
    const id = `toast-${toastSequence.current}`;
    const nextToast: TaskToast = { ...toast, id };

    setToasts((current) => {
      const next = [nextToast, ...current];
      next.slice(visibleToastLimit).forEach((removed) => {
        const timer = toastTimers.current.get(removed.id);
        if (timer !== undefined) window.clearTimeout(timer);
        toastTimers.current.delete(removed.id);
      });
      return next.slice(0, visibleToastLimit);
    });

    const timeout = toast.tone === "success" ? 4000 : 8000;
    const timer = window.setTimeout(() => dismissToast(id), timeout);
    toastTimers.current.set(id, timer);
  }, [dismissToast]);

  useEffect(() => () => {
    toastTimers.current.forEach((timer) => window.clearTimeout(timer));
    toastTimers.current.clear();
  }, []);

  const executeTask = useCallback(async (
    input: RunTaskInput<unknown>,
    existingId?: string,
  ): Promise<unknown> => {
    const retryPolicy = input.retryPolicy || "none";
    let id = existingId;
    if (!id) {
      taskSequence.current += 1;
      id = `task-${taskSequence.current}`;
    }

    const startedAt = Date.now();
    const runningTask: OperationTask = {
      id,
      kind: input.kind,
      title: input.title,
      context: input.context || "",
      status: "running",
      retryPolicy,
      startedAt,
      message: "",
    };

    dismissTaskToasts(id);
    setTasks((current) => {
      const withoutCurrent = current.filter((task) => task.id !== id);
      return trimCompletedHistory([runningTask, ...withoutCurrent]);
    });

    if (retryPolicy === "safe-read") retryInputs.current.set(id, input);
    else retryInputs.current.delete(id);

    try {
      const result = await input.action();
      const message = typeof input.successMessage === "function"
        ? input.successMessage(result)
        : input.successMessage || "";
      const finishedAt = Date.now();
      setTasks((current) => trimCompletedHistory(current.map((task) => (
        task.id === id ? { ...task, status: "succeeded", finishedAt, message } : task
      ))));
      retryInputs.current.delete(id);
      enqueueToast({ taskId: id, tone: "success", title: input.title, message });
      return result;
    } catch (error) {
      const message = typeof input.failureMessage === "function"
        ? input.failureMessage(error)
        : input.failureMessage || errorMessage(error);
      const finishedAt = Date.now();
      setTasks((current) => trimCompletedHistory(current.map((task) => (
        task.id === id ? { ...task, status: "failed", finishedAt, message } : task
      ))));
      if (retryPolicy !== "safe-read") retryInputs.current.delete(id);
      enqueueToast({ taskId: id, tone: "danger", title: input.title, message });
      throw error;
    }
  }, [dismissTaskToasts, enqueueToast]);

  const runTask = useCallback(<T,>(input: RunTaskInput<T>): Promise<T> => (
    executeTask(input as unknown as RunTaskInput<unknown>) as Promise<T>
  ), [executeTask]);

  const retryTask = useCallback(async (id: string) => {
    const input = retryInputs.current.get(id);
    if (!input) return;
    try {
      await executeTask(input, id);
    } catch {
      // The failed retry is already represented by the task and its toast.
    }
  }, [executeTask]);

  const clearCompleted = useCallback(() => {
    const completedIds = new Set(tasks.filter((task) => task.status !== "running").map((task) => task.id));
    completedIds.forEach((id) => retryInputs.current.delete(id));
    setTasks((current) => current.filter((task) => task.status === "running"));
    setToasts((current) => {
      current.filter((toast) => completedIds.has(toast.taskId)).forEach((toast) => {
        const timer = toastTimers.current.get(toast.id);
        if (timer !== undefined) window.clearTimeout(timer);
        toastTimers.current.delete(toast.id);
      });
      return current.filter((toast) => !completedIds.has(toast.taskId));
    });
  }, [tasks]);

  const value = useMemo<TaskCenterValue>(() => ({
    tasks,
    runningCount: tasks.filter((task) => task.status === "running").length,
    toasts,
    runTask,
    retryTask,
    clearCompleted,
    dismissToast,
  }), [clearCompleted, dismissToast, retryTask, runTask, tasks, toasts]);

  return <TaskCenterContext.Provider value={value}>{children}</TaskCenterContext.Provider>;
}

export function useTaskCenter(): TaskCenterValue {
  const value = useContext(TaskCenterContext);
  if (!value) throw new Error("useTaskCenter must be used inside TaskCenterProvider");
  return value;
}

