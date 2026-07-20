import {
  AlertTriangle,
  Copy,
  Download,
  FolderOpen,
  PencilLine,
  RefreshCcw,
  Trash2,
  Unplug,
  Upload,
  X,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { copyText, lpmAction, openExternalUrl, openPath } from "@/api/client";
import { resourceKindLabel, type TFunction } from "@/app/i18n";
import { useTaskCenter } from "@/app/TaskCenterContext";
import { Banner } from "@/components/Banner";
import { EmptyState } from "@/components/EmptyState";
import { KindBadge } from "@/components/KindBadge";
import { Segmented } from "@/components/Segmented";
import type {
  AssetAction,
  AssetActionPlan,
  AssetActionResult,
  AssetInventory,
  AssetPlatformRow,
  AssetStatus,
  GithubAuthPollResult,
  GithubAuthSession,
  GithubAuthStatus,
  ResourceKind,
} from "@/types/lpm";

const kinds: Array<"all" | ResourceKind> = [
  "all",
  "skill",
  "mcp",
  "rule",
  "prompt",
  "plugin",
];
const statusFilters: Array<"all" | AssetStatus> = [
  "all",
  "remote-only",
  "local-only",
  "same",
  "content-different",
  "metadata-only",
  "read-only-reference",
  "target-conflict",
  "uncomparable",
];
const safeNamePattern = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

type ActionDialogState = {
  action: AssetAction;
  row: AssetPlatformRow;
  newName: string;
  installName: string;
  confirmed: boolean;
  plan: AssetActionPlan | null;
  error: string;
};

type DeleteDialogState = {
  row: AssetPlatformRow;
  confirmName: string;
  error: string;
};

export function ResourcesView({
  inventory,
  selected,
  t,
  onSelect,
  onInventory,
  onChanged,
  onError,
}: {
  inventory: AssetInventory | null;
  selected?: AssetPlatformRow;
  t: TFunction;
  onSelect: (rowId: string) => void;
  onInventory: (inventory: AssetInventory) => void;
  onChanged: () => Promise<void> | void;
  onError: (message: string) => void;
}) {
  const { runTask } = useTaskCenter();
  const [kindFilter, setKindFilter] = useState<(typeof kinds)[number]>("all");
  const [statusFilter, setStatusFilter] = useState<(typeof statusFilters)[number]>("all");
  const [busyAction, setBusyAction] = useState("");
  const [dialog, setDialog] = useState<ActionDialogState | null>(null);
  const [deleteDialog, setDeleteDialog] = useState<DeleteDialogState | null>(null);
  const [deleteAuthSession, setDeleteAuthSession] = useState<GithubAuthSession | null>(null);
  const [deleteAuthMessage, setDeleteAuthMessage] = useState("");
  const deleteAuthSessionRef = useRef<GithubAuthSession | null>(null);

  const visibleRows = useMemo(
    () => (inventory?.rows || []).filter((row) => (
      (kindFilter === "all" || row.kind === kindFilter)
      && (statusFilter === "all" || row.status === statusFilter)
    )),
    [inventory, kindFilter, statusFilter],
  );

  useEffect(() => {
    deleteAuthSessionRef.current = deleteAuthSession;
  }, [deleteAuthSession]);

  useEffect(() => () => {
    const sessionId = deleteAuthSessionRef.current?.session_id;
    if (sessionId) {
      void lpmAction("github_auth_cancel", { session_id: sessionId }).catch(() => undefined);
    }
  }, []);

  useEffect(() => {
    if (!deleteAuthSession) return;
    let stopped = false;
    let timer = 0;

    const poll = (delaySeconds: number) => {
      timer = window.setTimeout(async () => {
        try {
          const result = await lpmAction<GithubAuthPollResult>("github_auth_poll", {
            session_id: deleteAuthSession.session_id,
          });
          if (stopped) return;
          if (result.state === "pending" || result.state === "slow_down") {
            poll(result.retry_after || deleteAuthSession.interval);
            return;
          }
          setDeleteAuthSession(null);
          if (result.state === "authorized") {
            setDeleteAuthMessage(t("assets.deleteScopeGranted"));
            return;
          }
          setDeleteDialog((current) => current ? {
            ...current,
            error: result.state === "denied" ? t("settings.authDenied") : t("settings.authExpired"),
          } : current);
        } catch (error) {
          if (!stopped) {
            setDeleteAuthSession(null);
            setDeleteDialog((current) => current ? { ...current, error: errorMessage(error) } : current);
          }
        }
      }, Math.max(1, delaySeconds) * 1000);
    };

    poll(deleteAuthSession.interval);
    return () => {
      stopped = true;
      window.clearTimeout(timer);
    };
  }, [deleteAuthSession, t]);

  async function scanLocal() {
    setBusyAction("scan");
    try {
      const next = await runTask({
        kind: "asset-scan-local",
        title: t("assets.scanLocal"),
        action: () => lpmAction<AssetInventory>("asset_inventory", {
          scan_local: true,
          refresh_remote: true,
        }),
        successMessage: t("assets.scanComplete"),
        retryPolicy: "safe-read",
      });
      onInventory(next);
      if (next.rows.length && !next.rows.some((row) => rowId(row) === rowId(selected))) {
        onSelect(rowId(next.rows[0]));
      }
    } catch {
      // The task center owns error reporting.
    } finally {
      setBusyAction("");
    }
  }

  function openAction(row: AssetPlatformRow, action: AssetAction) {
    setDialog({
      action,
      row,
      newName: "",
      installName: row.install_name,
      confirmed: false,
      plan: null,
      error: "",
    });
  }

  async function uninstallLocal(row: AssetPlatformRow) {
    setBusyAction(`uninstall:${rowId(row)}`);
    try {
      await runTask({
        kind: "resource-uninstall",
        title: t("assets.uninstall"),
        context: `${row.resource_key} / ${row.platform}`,
        action: () => lpmAction("resource_uninstall", {
          kind: row.kind,
          name: row.name,
          platform: row.platform,
        }),
        successMessage: t("assets.uninstallComplete"),
        retryPolicy: "none",
      });
      await onChanged();
    } catch {
      await Promise.resolve(onChanged());
    } finally {
      setBusyAction("");
    }
  }

  async function removeResource() {
    if (!deleteDialog) return;
    if (deleteDialog.confirmName.trim() !== deleteDialog.row.name) {
      setDeleteDialog((current) => (
        current ? { ...current, error: t("assets.nameMismatch") } : current
      ));
      return;
    }
    setBusyAction("delete");
    try {
      if (deletesOwnedRepository(deleteDialog.row)) {
        const status = await lpmAction<GithubAuthStatus>("github_auth_status");
        if (status.state !== "connected") {
          throw new Error(status.error || t("assets.connectBeforeRemoteDelete"));
        }
        if (!status.scopes.includes("delete_repo")) {
          const session = await lpmAction<GithubAuthSession>("github_auth_start", {
            purpose: "remote_delete",
          });
          setDeleteAuthSession(session);
          setDeleteAuthMessage("");
          await openExternalUrl(session.verification_uri);
          return;
        }
      }
      await runTask({
        kind: "resource-delete",
        title: t("assets.deleteResource"),
        context: deleteDialog.row.resource_key,
        action: () => lpmAction("resource_delete", {
          kind: deleteDialog.row.kind,
          name: deleteDialog.row.name,
          confirm_name: deleteDialog.confirmName.trim(),
        }),
        successMessage: t("assets.deleteComplete"),
        retryPolicy: "none",
      });
      setDeleteDialog(null);
      await onChanged();
    } catch (error) {
      setDeleteDialog((current) => current ? { ...current, error: errorMessage(error) } : current);
    } finally {
      setBusyAction("");
    }
  }

  async function closeDeleteDialog() {
    const sessionId = deleteAuthSession?.session_id;
    setDeleteAuthSession(null);
    setDeleteAuthMessage("");
    setDeleteDialog(null);
    if (sessionId) {
      try {
        await lpmAction("github_auth_cancel", { session_id: sessionId });
      } catch {
        // Session expiry/cancellation is already safe and local.
      }
    }
  }

  return (
    <section className="split-view resources-workspace asset-workspace">
      <div className="panel list-panel">
        <div className="asset-inventory-head">
          <div>
            <strong>{t("assets.title")}</strong>
            <small>
              {inventory?.branch || "-"} / {shortCommit(inventory?.remote_commit)}
            </small>
          </div>
          <button
            className="secondary"
            type="button"
            onClick={() => void scanLocal()}
            disabled={Boolean(busyAction)}
          >
            <RefreshCcw size={16} />
            {busyAction === "scan" ? t("common.working") : t("assets.scanLocal")}
          </button>
        </div>
        {inventory?.remote_warning ? <Banner tone="danger" text={inventory.remote_warning} /> : null}
        {inventory?.legacy_write_blocker ? (
          <Banner tone="danger" text={inventory.legacy_write_blocker} />
        ) : null}
        <div className="resource-filter-stack">
          <div className="resource-filter-group">
            <span>{t("resources.filterKind")}</span>
            <Segmented
              value={kindFilter}
              values={kinds}
              onChange={setKindFilter}
              getLabel={(value) => resourceKindLabel(value, t)}
            />
          </div>
          <div className="resource-filter-group">
            <span>{t("resources.filterStatus")}</span>
            <Segmented
              value={statusFilter}
              values={statusFilters}
              onChange={setStatusFilter}
              getLabel={(value) => value === "all" ? t("assets.status.all") : assetStatusLabel(value, t)}
            />
          </div>
        </div>
        <div className="resource-list asset-row-list">
          {visibleRows.map((row) => (
            <button
              key={rowId(row)}
              className={rowId(selected) === rowId(row) ? "resource-row asset-row active" : "resource-row asset-row"}
              onClick={() => onSelect(rowId(row))}
            >
              <KindBadge kind={row.kind} label={resourceKindLabel(row.kind, t)} />
              <span>
                <strong>{row.name}</strong>
                <small>{row.platform} / {row.install_name}</small>
              </span>
              <span className={`asset-status status-${row.status}`}>
                {assetStatusLabel(row.status, t)}
              </span>
            </button>
          ))}
          {!visibleRows.length ? <EmptyState text={t("assets.empty")} /> : null}
        </div>
      </div>

      <div className="resource-side-panel">
        {selected ? (
          <>
            <AssetActionPanel
              row={selected}
              busy={Boolean(busyAction)}
              t={t}
              onAction={openAction}
              onDelete={() => setDeleteDialog({
                row: selected,
                confirmName: "",
                error: "",
              })}
              onOpen={() => {
                const path = selected.local_path || selected.target_path;
                if (path) void openPath(path).catch((error) => onError(String(error)));
              }}
              onUninstall={() => void uninstallLocal(selected)}
            />
            <AssetDetailPanel row={selected} t={t} />
          </>
        ) : (
          <div className="panel detail-panel resource-detail-panel">
            <EmptyState text={t("resources.noSelected")} />
          </div>
        )}
      </div>

      {dialog ? (
        <AssetActionDialog
          state={dialog}
          t={t}
          onCancel={() => setDialog(null)}
          onChange={(changes) => setDialog((current) => (
            current ? { ...current, ...changes, plan: null, error: "" } : current
          ))}
          onPlan={(plan) => setDialog((current) => (
            current ? { ...current, plan, error: "" } : current
          ))}
          onError={(error) => setDialog((current) => (
            current ? { ...current, error } : current
          ))}
          onApplied={async () => {
            setDialog(null);
            await onChanged();
          }}
        />
      ) : null}

      {deleteDialog ? (
        <div className="modal-backdrop" role="presentation">
          <div className="modal resource-delete-modal" role="dialog" aria-modal="true">
            <div className="modal-head danger-head">
              <Trash2 size={20} />
              <h2>{t("assets.deleteResource")}</h2>
              <button className="icon-button" type="button" onClick={() => void closeDeleteDialog()}>
                <X size={17} />
              </button>
            </div>
            <div className="delete-modal-body">
              <p>{t("assets.deleteWarning", { name: deleteDialog.row.name })}</p>
              <label className="stack-form">
                <span>{t("assets.typeName", { name: deleteDialog.row.name })}</span>
                <input
                  value={deleteDialog.confirmName}
                  onChange={(event) => setDeleteDialog((current) => (
                    current ? { ...current, confirmName: event.target.value, error: "" } : current
                  ))}
                />
              </label>
              {deleteAuthSession ? (
                <div className="oauth-device-panel" role="status">
                  <p>{t("assets.authorizeRemoteDelete")}</p>
                  <strong>{deleteAuthSession.user_code}</strong>
                  <div>
                    <button className="secondary" type="button" onClick={() => void copyText(deleteAuthSession.user_code)}>
                      <Copy size={16} />{t("settings.copyCode")}
                    </button>
                    <button className="secondary" type="button" onClick={() => void closeDeleteDialog()}>
                      {t("common.cancel")}
                    </button>
                  </div>
                </div>
              ) : null}
              {deleteAuthMessage ? <p className="delete-auth-success">{deleteAuthMessage}</p> : null}
              {deleteDialog.error ? <p className="delete-modal-error">{deleteDialog.error}</p> : null}
            </div>
            <div className="modal-actions">
              <button className="secondary" type="button" onClick={() => void closeDeleteDialog()}>
                {t("common.cancel")}
              </button>
              <button
                className="danger"
                type="button"
                onClick={() => void removeResource()}
                disabled={busyAction === "delete" || Boolean(deleteAuthSession)}
              >
                {busyAction === "delete" || deleteAuthSession ? t("common.working") : t("common.confirm")}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </section>
  );
}

function deletesOwnedRepository(row: AssetPlatformRow): boolean {
  return row.entry?.source === "owned" && Boolean(row.entry.repo);
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function AssetActionPanel({
  row,
  busy,
  t,
  onAction,
  onDelete,
  onOpen,
  onUninstall,
}: {
  row: AssetPlatformRow;
  busy: boolean;
  t: TFunction;
  onAction: (row: AssetPlatformRow, action: AssetAction) => void;
  onDelete: () => void;
  onOpen: () => void;
  onUninstall: () => void;
}) {
  return (
    <div className="panel resource-action-panel">
      <div className="resource-actions asset-actions">
        {row.available_actions.map((action) => (
          <button
            className={action === "download" || action === "upload" ? "primary" : "secondary"}
            key={action}
            type="button"
            onClick={() => onAction(row, action)}
            disabled={busy}
          >
            {assetActionIcon(action)}
            {assetActionLabel(action, t)}
          </button>
        ))}
        <button
          className="secondary"
          type="button"
          onClick={onOpen}
          disabled={busy || !(row.local_path || row.target_path)}
        >
          <FolderOpen size={16} />
          {t("assets.open")}
        </button>
        <button
          className="secondary"
          type="button"
          onClick={onUninstall}
          disabled={busy || !row.local_exists || row.ownership !== "managed" || !row.entry}
        >
          <Unplug size={16} />
          {t("assets.uninstall")}
        </button>
        <button
          className="danger"
          type="button"
          onClick={onDelete}
          disabled={busy || !row.remote_exists || !row.entry}
        >
          <Trash2 size={16} />
          {t("assets.deleteResource")}
        </button>
      </div>
    </div>
  );
}

function AssetDetailPanel({ row, t }: { row: AssetPlatformRow; t: TFunction }) {
  return (
    <div className="panel detail-panel resource-detail-panel">
      <div className="detail-title">
        <div className="detail-title-main">
          <KindBadge kind={row.kind} label={resourceKindLabel(row.kind, t)} />
          <h2>{row.name}</h2>
        </div>
        <span className={`asset-status status-${row.status}`}>
          {assetStatusLabel(row.status, t)}
        </span>
      </div>
      <dl className="description-list asset-description-list">
        <div><dt>{t("assets.resourceKey")}</dt><dd>{row.resource_key}</dd></div>
        <div><dt>{t("assets.platform")}</dt><dd>{row.platform}</dd></div>
        <div><dt>{t("assets.installName")}</dt><dd>{row.install_name}</dd></div>
        <div><dt>{t("assets.path")}</dt><dd>{row.local_path || row.target_path || "-"}</dd></div>
        <div><dt>{t("assets.ownership")}</dt><dd>{row.ownership}</dd></div>
        <div><dt>{t("assets.remoteCommit")}</dt><dd>{shortCommit(row.remote_commit)}</dd></div>
        {row.reference_commit ? (
          <div><dt>{t("assets.referenceCommit")}</dt><dd>{shortCommit(row.reference_commit)}</dd></div>
        ) : null}
        <div>
          <dt>{t("assets.platformState")}</dt>
          <dd>
            {row.configured ? t("assets.configured") : t("assets.detectedOnly")}
            {" / "}
            {row.enabled ? t("assets.enabled") : t("assets.disabled")}
          </dd>
        </div>
      </dl>
      <div className="asset-diff-preview">
        <h3>{t("assets.diffPreview")}</h3>
        {row.diff_summary.length ? (
          <ul>{row.diff_summary.map((item) => <li key={item}>{item}</li>)}</ul>
        ) : (
          <p>{t("assets.noDiff")}</p>
        )}
        {row.metadata_differences.length ? (
          <p>{t("assets.metadataFields")}: {row.metadata_differences.join(", ")}</p>
        ) : null}
      </div>
      {row.warnings.map((warning) => (
        <Banner key={warning} tone="danger" text={warning} />
      ))}
      {row.blockers.map((blocker) => (
        <Banner key={blocker} tone="danger" text={blocker} />
      ))}
    </div>
  );
}

function AssetActionDialog({
  state,
  t,
  onCancel,
  onChange,
  onPlan,
  onError,
  onApplied,
}: {
  state: ActionDialogState;
  t: TFunction;
  onCancel: () => void;
  onChange: (changes: Partial<ActionDialogState>) => void;
  onPlan: (plan: AssetActionPlan) => void;
  onError: (error: string) => void;
  onApplied: () => Promise<void>;
}) {
  const { runTask } = useTaskCenter();
  const [busy, setBusy] = useState(false);
  const needsName = state.action === "copy-to-local" || state.action === "copy-to-remote";
  const needsInstallName = state.action === "set-platform-install-name";
  const needsConfirmation = (
    (state.action === "download" && state.row.local_exists)
    || (state.action === "upload" && state.row.remote_exists)
  );
  const liveName = needsInstallName ? state.installName : state.newName;
  const nameInvalid = (needsName || needsInstallName) && !safeNamePattern.test(liveName.trim());

  async function createPlan() {
    if (nameInvalid) {
      onError(t("assets.invalidName"));
      return;
    }
    if (needsConfirmation && !state.confirmed) {
      onError(t("assets.confirmReplacement"));
      return;
    }
    setBusy(true);
    try {
      const plan = await runTask({
        kind: "asset-action-plan",
        title: t("assets.createPlan"),
        context: `${state.row.resource_key} / ${state.row.platform}`,
        action: () => lpmAction<AssetActionPlan>("asset_action_plan", {
          action: state.action,
          kind: state.row.kind,
          name: state.row.name,
          platform: state.row.platform,
          local_instance_id: state.row.local_instance_id,
          new_name: state.newName.trim(),
          new_install_name: state.installName.trim(),
          overwrite_unmanaged: (
            state.action === "download"
            && state.row.ownership !== "managed"
            && state.confirmed
          ),
        }),
        successMessage: t("assets.planReady"),
        retryPolicy: "none",
      });
      onPlan(plan);
    } catch (error) {
      onError(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  async function applyPlan() {
    if (!state.plan || state.plan.blocked) return;
    setBusy(true);
    try {
      await runTask({
        kind: `asset-${state.action}`,
        title: assetActionLabel(state.action, t),
        context: `${state.plan.target_resource_key} / ${state.row.platform}`,
        action: async () => {
          const result = await lpmAction<AssetActionResult>("asset_action_apply", {
            operation_id: state.plan?.operation_id,
          });
          if (!["succeeded", "unchanged"].includes(result.status)) {
            throw new Error(result.message || result.status);
          }
          return result;
        },
        successMessage: (result) => result.message,
        retryPolicy: "none",
      });
      await onApplied();
    } catch (error) {
      onError(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="modal-backdrop" role="presentation">
      <div className="modal asset-action-modal" role="dialog" aria-modal="true">
        <div className="modal-head">
          {assetActionIcon(state.action)}
          <h2>{assetActionLabel(state.action, t)}</h2>
          <button className="icon-button" type="button" onClick={onCancel}>
            <X size={17} />
          </button>
        </div>
        <p>{state.row.resource_key} / {state.row.platform}</p>
        {needsName ? (
          <label className="stack-form">
            <span>{t("assets.newName")}</span>
            <input
              value={state.newName}
              onChange={(event) => onChange({ newName: event.target.value })}
              placeholder={t("assets.namePlaceholder")}
            />
          </label>
        ) : null}
        {needsInstallName ? (
          <label className="stack-form">
            <span>{t("assets.installName")}</span>
            <input
              value={state.installName}
              onChange={(event) => onChange({ installName: event.target.value })}
              placeholder={t("assets.namePlaceholder")}
            />
          </label>
        ) : null}
        {needsConfirmation ? (
          <label className="asset-confirm-choice">
            <input
              type="checkbox"
              checked={state.confirmed}
              onChange={(event) => onChange({ confirmed: event.target.checked })}
            />
            <span>
              {state.action === "download"
                ? t("assets.confirmLocalReplace")
                : t("assets.confirmRemoteReplace")}
            </span>
          </label>
        ) : null}
        {nameInvalid && liveName ? <Banner tone="danger" text={t("assets.invalidName")} /> : null}
        {state.error ? <Banner tone="danger" text={state.error} /> : null}
        {state.plan?.warnings.map((warning) => (
          <div className="asset-plan-message warning" key={warning}>
            <AlertTriangle size={16} />
            <span>{warning}</span>
          </div>
        ))}
        {state.plan?.blockers.map((blocker) => (
          <div className="asset-plan-message blocker" key={blocker}>
            <AlertTriangle size={16} />
            <span>{blocker}</span>
          </div>
        ))}
        {state.plan ? (
          <dl className="description-list asset-plan-summary">
            <div><dt>{t("assets.operationId")}</dt><dd>{state.plan.operation_id}</dd></div>
            <div><dt>{t("assets.target")}</dt><dd>{state.plan.target_resource_key}</dd></div>
            <div><dt>{t("assets.remoteCommit")}</dt><dd>{shortCommit(state.plan.remote_commit)}</dd></div>
          </dl>
        ) : null}
        <div className="modal-actions">
          <button className="secondary" type="button" onClick={onCancel} disabled={busy}>
            {t("common.cancel")}
          </button>
          <button
            className="secondary"
            type="button"
            onClick={() => void createPlan()}
            disabled={busy || nameInvalid}
          >
            {busy ? t("common.working") : t("assets.createPlan")}
          </button>
          <button
            className="primary"
            type="button"
            onClick={() => void applyPlan()}
            disabled={busy || !state.plan || state.plan.blocked}
          >
            {t("assets.apply")}
          </button>
        </div>
      </div>
    </div>
  );
}

function rowId(row?: AssetPlatformRow): string {
  return row ? `${row.resource_key}|${row.platform}|${row.local_instance_id}` : "";
}

function shortCommit(commit?: string | null): string {
  return commit ? commit.slice(0, 12) : "-";
}

function assetStatusLabel(status: AssetStatus, t: TFunction): string {
  return t(`assets.status.${status}` as Parameters<TFunction>[0]);
}

function assetActionLabel(action: AssetAction, t: TFunction): string {
  return t(`assets.action.${action}` as Parameters<TFunction>[0]);
}

function assetActionIcon(action: AssetAction) {
  if (action === "download") return <Download size={16} />;
  if (action === "upload") return <Upload size={16} />;
  if (action === "set-platform-install-name") return <PencilLine size={16} />;
  return <Copy size={16} />;
}
