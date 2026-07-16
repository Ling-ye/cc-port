import { GitCompareArrows, RefreshCcw, Send, ShieldCheck, X } from "lucide-react";
import { useMemo, useState } from "react";
import { lpmAction } from "@/api/client";
import type { TFunction } from "@/app/i18n";
import { useTaskCenter } from "@/app/TaskCenterContext";
import { Banner } from "@/components/Banner";
import type {
  ResourceCommitPlan,
  ResourceSyncConflict,
  ResourceSyncPlan,
} from "@/types/lpm";

export function RepositorySyncView({ t }: { t: TFunction }) {
  const { runTask } = useTaskCenter();
  const [plan, setPlan] = useState<ResourceSyncPlan | null>(null);
  const [commitPlan, setCommitPlan] = useState<ResourceCommitPlan | null>(null);
  const [commitMessage, setCommitMessage] = useState("");
  const [choices, setChoices] = useState<Record<string, "local" | "incoming">>({});
  const [busy, setBusy] = useState(false);

  const conflicts = useMemo(() => groupConflicts(plan?.conflicts || []), [plan]);
  const allConflictsSelected = conflicts.every((conflict) => choices[conflict.id]);
  const canApply = Boolean(
    plan?.operation_id
      && ["behind", "unborn", "ready"].includes(plan.status),
  );
  const canPush = Boolean(
    plan && ["clean", "ahead", "no-remote", "applied"].includes(plan.status),
  );
  const canCancel = Boolean(
    plan?.operation_id && ["conflict", "ready"].includes(plan.status),
  );

  async function execute(
    kind: string,
    title: string,
    action: () => Promise<ResourceSyncPlan>,
    successMessage: string,
    retryPolicy: "safe-read" | "none" = "none",
  ) {
    setBusy(true);
    try {
      const result = await runTask({
        kind,
        title,
        action,
        successMessage,
        retryPolicy,
      });
      setPlan(result);
      if (result.status !== "conflict") setChoices({});
    } catch {
      // The task center owns error reporting.
    } finally {
      setBusy(false);
    }
  }

  function refreshStatus() {
    return execute(
      "resource-sync-status",
      t("repoSync.refresh"),
      async () => {
        const result = await lpmAction<ResourceSyncPlan>("resource_sync_status", { fetch: true });
        if (result.status === "dirty") {
          const localPlan = await lpmAction<ResourceCommitPlan>("resource_commit_plan");
          setCommitPlan(localPlan);
          setCommitMessage(localPlan.suggested_message);
        } else {
          setCommitPlan(null);
          setCommitMessage("");
        }
        return result;
      },
      t("repoSync.refreshed"),
      "safe-read",
    );
  }

  function buildPlan() {
    return execute(
      "resource-sync-plan",
      t("repoSync.plan"),
      () => lpmAction<ResourceSyncPlan>("resource_sync_plan"),
      t("repoSync.planReady"),
    );
  }

  function resolvePlan() {
    if (!plan?.operation_id || !allConflictsSelected) return Promise.resolve();
    return execute(
      "resource-sync-resolve",
      t("repoSync.resolve"),
      () => lpmAction<ResourceSyncPlan>("resource_sync_resolve", {
        operation_id: plan.operation_id,
        choices,
      }),
      t("repoSync.resolved"),
    );
  }

  function applyPlan() {
    if (!plan?.operation_id) return Promise.resolve();
    return execute(
      "resource-sync-apply",
      t("repoSync.apply"),
      () => lpmAction<ResourceSyncPlan>("resource_sync_apply", {
        operation_id: plan.operation_id,
      }),
      t("repoSync.applied"),
    );
  }

  function cancelPlan() {
    if (!plan?.operation_id) return Promise.resolve();
    return execute(
      "resource-sync-cancel",
      t("repoSync.cancel"),
      () => lpmAction<ResourceSyncPlan>("resource_sync_cancel", {
        operation_id: plan.operation_id,
      }),
      t("repoSync.cancelled"),
    );
  }

  function push() {
    return execute(
      "resource-sync-push",
      t("repoSync.push"),
      () => lpmAction<ResourceSyncPlan>("resource_sync_push"),
      t("repoSync.pushed"),
    );
  }

  function commitAndPush() {
    if (!commitPlan || commitPlan.blocked || !commitPlan.managed_paths.length) {
      return Promise.resolve();
    }
    return execute(
      "resource-commit-push",
      t("repoSync.commitPush"),
      async () => {
        await lpmAction("resource_commit_push", { message: commitMessage });
        const result = await lpmAction<ResourceSyncPlan>("resource_sync_status", { fetch: true });
        setCommitPlan(null);
        setCommitMessage("");
        return result;
      },
      t("repoSync.commitPushed"),
    );
  }

  return (
    <section className="panel repository-sync-panel">
      <div className="panel-head">
        <div>
          <h2>{t("repoSync.title")}</h2>
          <p>{t("repoSync.description")}</p>
        </div>
        <div className="repository-sync-actions">
          <button className="secondary" onClick={() => void refreshStatus()} disabled={busy}>
            <RefreshCcw size={17} />
            {t("repoSync.refresh")}
          </button>
          <button
            className="primary"
            onClick={() => void buildPlan()}
            disabled={busy || canCancel}
          >
            <GitCompareArrows size={17} />
            {t("repoSync.plan")}
          </button>
        </div>
      </div>

      {plan ? (
        <>
          {["dirty", "wrong-branch"].includes(plan.status) ? (
            <Banner tone="danger" text={plan.detail || t("repoSync.blocked")} />
          ) : null}

          {plan.status === "dirty" && commitPlan ? (
            <div className="repository-conflicts">
              <h3>{t("repoSync.localChanges", { count: commitPlan.resources.length })}</h3>
              {commitPlan.resources.map((change) => (
                <div className="repository-conflict-row" key={`${change.kind}:${change.name}`}>
                  <div>
                    <strong>{change.name}</strong>
                    <small>{change.kind} · {change.action}</small>
                    <p>{change.paths.join(", ")}</p>
                  </div>
                </div>
              ))}
              {[...commitPlan.blocked_paths, ...commitPlan.secret_findings].map((issue) => (
                <Banner
                  key={`${issue.path}:${issue.reason}`}
                  tone="danger"
                  text={`${issue.path}: ${issue.reason}`}
                />
              ))}
              <label className="stack-form">
                <span>{t("repoSync.commitMessage")}</span>
                <input
                  value={commitMessage}
                  onChange={(event) => setCommitMessage(event.target.value)}
                />
              </label>
              <button
                className="primary"
                onClick={() => void commitAndPush()}
                disabled={
                  busy
                  || commitPlan.blocked
                  || !commitPlan.managed_paths.length
                  || !commitMessage.trim()
                }
              >
                <Send size={17} />
                {t("repoSync.commitPush")}
              </button>
            </div>
          ) : null}

          <dl className="description-list repository-sync-summary">
            <div><dt>{t("repoSync.repo")}</dt><dd>{plan.repo_path}</dd></div>
            <div><dt>{t("repoSync.branch")}</dt><dd>{plan.branch}</dd></div>
            <div><dt>{t("repoSync.status")}</dt><dd>{statusLabel(plan.status, t)}</dd></div>
            <div>
              <dt>{t("repoSync.divergence")}</dt>
              <dd>{t("repoSync.aheadBehind", { ahead: plan.ahead, behind: plan.behind })}</dd>
            </div>
            <div><dt>{t("repoSync.localCommit")}</dt><dd>{shortCommit(plan.local_commit)}</dd></div>
            <div><dt>{t("repoSync.remoteCommit")}</dt><dd>{shortCommit(plan.remote_commit)}</dd></div>
            {plan.operation_id ? (
              <div><dt>{t("repoSync.operation")}</dt><dd>{plan.operation_id}</dd></div>
            ) : null}
            {plan.detail ? <div><dt>{t("repoSync.detail")}</dt><dd>{plan.detail}</dd></div> : null}
          </dl>

          {conflicts.length ? (
            <div className="repository-conflicts">
              <h3>{t("repoSync.conflicts", { count: conflicts.length })}</h3>
              {conflicts.map((conflict) => (
                <div className="repository-conflict-row" key={conflict.id}>
                  <div>
                    <strong>{conflict.resource || conflict.id}</strong>
                    <small>{conflict.paths.join(", ")}</small>
                    <p>{conflict.reasons.join(" ")}</p>
                  </div>
                  <div className="repository-choice">
                    <button
                      className={choices[conflict.id] === "local" ? "secondary active" : "secondary"}
                      onClick={() => setChoices((current) => ({ ...current, [conflict.id]: "local" }))}
                    >
                      {t("repoSync.choiceLocal")}
                    </button>
                    <button
                      className={choices[conflict.id] === "incoming" ? "secondary active" : "secondary"}
                      onClick={() => setChoices((current) => ({ ...current, [conflict.id]: "incoming" }))}
                    >
                      {t("repoSync.choiceIncoming")}
                    </button>
                  </div>
                </div>
              ))}
              <button
                className="primary"
                onClick={() => void resolvePlan()}
                disabled={busy || !allConflictsSelected}
              >
                <ShieldCheck size={17} />
                {t("repoSync.resolve")}
              </button>
            </div>
          ) : null}

          <div className="repository-sync-actions footer-actions">
            {canApply ? (
              <button className="primary" onClick={() => void applyPlan()} disabled={busy}>
                <ShieldCheck size={17} />
                {t("repoSync.apply")}
              </button>
            ) : null}
            {canPush ? (
              <button className="primary" onClick={() => void push()} disabled={busy}>
                <Send size={17} />
                {t("repoSync.push")}
              </button>
            ) : null}
            {canCancel ? (
              <button className="secondary" onClick={() => void cancelPlan()} disabled={busy}>
                <X size={17} />
                {t("repoSync.cancel")}
              </button>
            ) : null}
          </div>
        </>
      ) : (
        <p className="empty">{t("repoSync.empty")}</p>
      )}
    </section>
  );
}

interface GroupedConflict {
  id: string;
  resource: string;
  paths: string[];
  reasons: string[];
}

function groupConflicts(conflicts: ResourceSyncConflict[]): GroupedConflict[] {
  const grouped = new Map<string, GroupedConflict>();
  conflicts.forEach((conflict) => {
    const current = grouped.get(conflict.id) || {
      id: conflict.id,
      resource: conflict.resource,
      paths: [],
      reasons: [],
    };
    if (!current.paths.includes(conflict.path)) current.paths.push(conflict.path);
    if (conflict.reason && !current.reasons.includes(conflict.reason)) {
      current.reasons.push(conflict.reason);
    }
    grouped.set(conflict.id, current);
  });
  return [...grouped.values()];
}

function shortCommit(commit?: string | null): string {
  return commit ? commit.slice(0, 12) : "-";
}

function statusLabel(status: string, t: TFunction): string {
  const keys = {
    clean: "repoSync.status.clean",
    ahead: "repoSync.status.ahead",
    behind: "repoSync.status.behind",
    diverged: "repoSync.status.diverged",
    unborn: "repoSync.status.unborn",
    "no-remote": "repoSync.status.no-remote",
    "wrong-branch": "repoSync.status.wrong-branch",
    dirty: "repoSync.status.dirty",
    conflict: "repoSync.status.conflict",
    ready: "repoSync.status.ready",
    applied: "repoSync.status.applied",
    cancelled: "repoSync.status.cancelled",
    abandoned: "repoSync.status.abandoned",
  } as const;
  return status in keys ? t(keys[status as keyof typeof keys]) : status;
}
