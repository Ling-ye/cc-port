import { useEffect } from "react";
import { CheckCircle2, RefreshCcw, RotateCcw, X, XCircle } from "lucide-react";
import { useTaskCenter, type OperationTask, type OperationTaskStatus } from "@/app/TaskCenterContext";
import type { TFunction } from "@/app/i18n";

export function ToastViewport({ t }: { t: TFunction }) {
  const { toasts, dismissToast } = useTaskCenter();
  return (
    <div className="task-toast-viewport" aria-label={t("taskCenter.notifications")}>
      {toasts.map((toast) => (
        <div
          key={toast.id}
          className={`task-toast ${toast.tone}`}
          role={toast.tone === "danger" ? "alert" : "status"}
          aria-live={toast.tone === "danger" ? "assertive" : "polite"}
        >
          {toast.tone === "danger" ? <XCircle size={19} /> : <CheckCircle2 size={19} />}
          <div>
            <strong>{toast.title}</strong>
            {toast.message ? <span>{toast.message}</span> : null}
          </div>
          <button
            className="task-feedback-close"
            type="button"
            onClick={() => dismissToast(toast.id)}
            aria-label={t("taskCenter.dismiss")}
          >
            <X size={15} />
          </button>
        </div>
      ))}
    </div>
  );
}

export function TaskCenterPanel({ open, t, onClose }: { open: boolean; t: TFunction; onClose: () => void }) {
  const { tasks, retryTask, clearCompleted } = useTaskCenter();

  useEffect(() => {
    if (!open) return undefined;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [onClose, open]);

  if (!open) return null;

  return (
    <aside className="task-center-panel" aria-label={t("taskCenter.title")}>
      <header className="task-center-header">
        <div>
          <strong>{t("taskCenter.title")}</strong>
          <span>{t("taskCenter.sessionOnly")}</span>
        </div>
        <button className="task-feedback-close" type="button" onClick={onClose} aria-label={t("common.close")}>
          <X size={17} />
        </button>
      </header>
      <div className="task-center-toolbar">
        <span>{t("taskCenter.taskCount", { count: tasks.length })}</span>
        {tasks.some((task) => task.status !== "running") ? (
          <button className="secondary" type="button" onClick={clearCompleted}>{t("taskCenter.clearCompleted")}</button>
        ) : null}
      </div>
      <div className="task-center-list" aria-live="polite">
        {tasks.map((task) => (
          <TaskRow key={task.id} task={task} t={t} onRetry={() => void retryTask(task.id)} />
        ))}
        {!tasks.length ? <p className="task-center-empty">{t("taskCenter.empty")}</p> : null}
      </div>
    </aside>
  );
}

function TaskRow({ task, t, onRetry }: { task: OperationTask; t: TFunction; onRetry: () => void }) {
  return (
    <article className={`task-center-row ${task.status}`}>
      <TaskStatusIcon status={task.status} />
      <div className="task-center-copy">
        <div>
          <strong>{task.title}</strong>
          <span>{t(`taskCenter.${task.status}` as "taskCenter.running" | "taskCenter.succeeded" | "taskCenter.failed")}</span>
        </div>
        {task.context ? <small>{task.context}</small> : null}
        {task.message ? <p>{task.message}</p> : null}
        <time dateTime={new Date(task.startedAt).toISOString()}>{formatTaskTime(task)}</time>
      </div>
      {task.status === "failed" && task.retryPolicy === "safe-read" ? (
        <button className="task-retry-button" type="button" onClick={onRetry}>
          <RotateCcw size={14} />{t("common.retry")}
        </button>
      ) : null}
    </article>
  );
}

function TaskStatusIcon({ status }: { status: OperationTaskStatus }) {
  if (status === "succeeded") return <CheckCircle2 className="task-status-success" size={19} aria-hidden="true" />;
  if (status === "failed") return <XCircle className="task-status-failed" size={19} aria-hidden="true" />;
  return <RefreshCcw className="spin task-status-running" size={19} aria-hidden="true" />;
}

function formatTaskTime(task: OperationTask): string {
  const started = new Date(task.startedAt).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  if (!task.finishedAt) return started;
  const seconds = Math.max(0, Math.round((task.finishedAt - task.startedAt) / 1000));
  return `${started} · ${seconds}s`;
}

