import {
  Archive,
  ChevronLeft,
  ChevronRight,
  Download,
  Eye,
  FileJson,
  History,
  RefreshCcw,
  RotateCcw,
  Trash2,
} from "lucide-react";
import { useEffect, useState } from "react";
import { ccPortAction } from "@/api/client";
import { displayError, translateMessage, type TFunction } from "@/app/i18n";
import { useTaskCenter } from "@/app/TaskCenterContext";
import type {
  MaintenanceAudit,
  MaintenanceAuditDetail,
  MaintenanceAuditList,
  OperationHistoryEntry,
  OperationHistoryPage,
  OperationHistorySummary,
  OperationRestoreResult,
  OrphanBackup,
  OrphanBackupExport,
  OrphanBackupResult,
  OrphanDeleteResult,
  OrphanQuarantine,
  OrphanQuarantineList,
  OrphanQuarantineResult,
  ResourceSyncPlan,
  StatePruneResult,
  StateRetentionPlan,
  StaleResourceSyncPlan,
  StaleResourceSyncResult,
} from "@/types/cc-port";

const HISTORY_PAGE_SIZE = 20;

export function OperationsView({ t }: { t: TFunction }) {
  const { runTask } = useTaskCenter();
  const failureMessage = (error: unknown) => displayError(error, t);
  const [operations, setOperations] = useState<OperationHistorySummary[]>([]);
  const [historyTotal, setHistoryTotal] = useState(0);
  const [historyOffset, setHistoryOffset] = useState(0);
  const [historyHasMore, setHistoryHasMore] = useState(false);
  const [selectedOperation, setSelectedOperation] = useState<OperationHistoryEntry | null>(null);
  const [stalePlans, setStalePlans] = useState<StaleResourceSyncPlan[]>([]);
  const [retentionPlan, setRetentionPlan] = useState<StateRetentionPlan | null>(null);
  const [retentionDays, setRetentionDays] = useState(90);
  const [keepLatest, setKeepLatest] = useState(20);
  const [maxBackupMb, setMaxBackupMb] = useState(2048);
  const [selectedPruneIds, setSelectedPruneIds] = useState<string[]>([]);
  const [orphans, setOrphans] = useState<OrphanBackup[]>([]);
  const [selectedOrphanNames, setSelectedOrphanNames] = useState<string[]>([]);
  const [quarantines, setQuarantines] = useState<OrphanQuarantine[]>([]);
  const [audits, setAudits] = useState<MaintenanceAudit[]>([]);
  const [selectedAudit, setSelectedAudit] = useState<MaintenanceAuditDetail | null>(null);
  const [forceRestore, setForceRestore] = useState(false);
  const [busy, setBusy] = useState(false);

  async function load(track = true, requestedOffset = historyOffset) {
    const action = async () => {
      const [historyPage, stale, retention, orphanResult, quarantineResult, auditResult] =
        await Promise.all([
          ccPortAction<OperationHistoryPage>("operation_history_page", {
            offset: requestedOffset,
            limit: HISTORY_PAGE_SIZE,
          }),
          ccPortAction<StaleResourceSyncResult>("resource_sync_stale", {
            min_age_hours: 24,
          }),
          ccPortAction<StateRetentionPlan>("state_retention_plan"),
          ccPortAction<OrphanBackupResult>("orphan_backups"),
          ccPortAction<OrphanQuarantineList>("orphan_quarantines"),
          ccPortAction<MaintenanceAuditList>("maintenance_audits", { limit: 30 }),
        ]);
      setOperations(historyPage.operations);
      setHistoryTotal(historyPage.total);
      setHistoryOffset(historyPage.offset);
      setHistoryHasMore(historyPage.has_more);
      setSelectedOperation(null);
      setStalePlans(stale.plans);
      setRetentionPlan(retention);
      setRetentionDays(retention.policy.retention_days);
      setKeepLatest(retention.policy.keep_latest_operations);
      setMaxBackupMb(retention.policy.max_backup_mb);
      setSelectedPruneIds(retention.candidates.map((item) => item.operation_id));
      setOrphans(orphanResult.orphans);
      setSelectedOrphanNames((current) => current.filter(
        (name) => orphanResult.orphans.some((item) => item.name === name),
      ));
      setQuarantines(quarantineResult.quarantines);
      setAudits(auditResult.audits);
      setSelectedAudit(null);
      return historyPage;
    };
    if (track) {
      await runTask({
        kind: "operation-history",
        title: t("operations.refresh"),
        action,
        successMessage: t("operations.refreshed"),
        failureMessage,
        retryPolicy: "safe-read",
      });
    } else {
      await action();
    }
  }

  useEffect(() => {
    void load(false, 0);
  }, []);

  async function changeHistoryPage(requestedOffset: number) {
    setBusy(true);
    try {
      const page = await runTask({
        kind: "operation-history-page",
        title: t("operations.refresh"),
        action: () => ccPortAction<OperationHistoryPage>("operation_history_page", {
          offset: requestedOffset,
          limit: HISTORY_PAGE_SIZE,
        }),
        failureMessage,
        retryPolicy: "safe-read",
      });
      setOperations(page.operations);
      setHistoryTotal(page.total);
      setHistoryOffset(page.offset);
      setHistoryHasMore(page.has_more);
      setSelectedOperation(null);
    } catch {
      // Task center owns error feedback.
    } finally {
      setBusy(false);
    }
  }

  async function viewOperation(item: OperationHistorySummary) {
    setBusy(true);
    try {
      const detail = await runTask({
        kind: "operation-detail",
        title: t("operations.viewDetails"),
        context: item.operation_id,
        action: () => ccPortAction<OperationHistoryEntry>("operation_detail", {
          operation_id: item.operation_id,
        }),
        failureMessage,
        retryPolicy: "safe-read",
      });
      setSelectedOperation(detail);
    } catch {
      // Task center owns error feedback.
    } finally {
      setBusy(false);
    }
  }

  async function restore(item: OperationHistorySummary) {
    if (!window.confirm(t("operations.restoreConfirm", { id: item.operation_id }))) return;
    setBusy(true);
    try {
      await runTask({
        kind: "operation-restore",
        title: t("operations.restore"),
        context: item.operation_id,
        action: () => ccPortAction<OperationRestoreResult>("operation_restore", {
          operation_id: item.operation_id,
          force: forceRestore,
        }),
        successMessage: t("operations.restored"),
        failureMessage,
        retryPolicy: "none",
      });
      await load(false, historyOffset);
    } catch {
      // Task center owns error feedback.
    } finally {
      setBusy(false);
    }
  }

  async function cleanup(item: StaleResourceSyncPlan) {
    if (!window.confirm(t("operations.cleanupConfirm", { id: item.operation_id }))) return;
    setBusy(true);
    try {
      await runTask({
        kind: "resource-sync-cleanup",
        title: t("operations.cleanup"),
        context: item.operation_id,
        action: () => ccPortAction<ResourceSyncPlan>("resource_sync_cleanup", {
          operation_id: item.operation_id,
        }),
        successMessage: t("operations.cleaned"),
        failureMessage,
        retryPolicy: "none",
      });
      await load(false, historyOffset);
    } catch {
      // Task center owns error feedback.
    } finally {
      setBusy(false);
    }
  }

  async function previewRetention() {
    setBusy(true);
    try {
      const data = await runTask({
        kind: "state-retention-plan",
        title: t("operations.retentionPreview"),
        action: () => ccPortAction<StateRetentionPlan>("state_retention_plan", {
          retention_days: retentionDays,
          keep_latest_operations: keepLatest,
          max_backup_mb: maxBackupMb,
        }),
        successMessage: t("operations.retentionReady"),
        failureMessage,
        retryPolicy: "safe-read",
      });
      setRetentionPlan(data);
      setSelectedPruneIds(data.candidates.map((item) => item.operation_id));
    } catch {
      // Task center owns error feedback.
    } finally {
      setBusy(false);
    }
  }

  async function applyRetention() {
    if (!selectedPruneIds.length) return;
    if (!window.confirm(t("operations.pruneConfirm", { count: selectedPruneIds.length }))) {
      return;
    }
    setBusy(true);
    try {
      await runTask({
        kind: "state-prune",
        title: t("operations.prune"),
        action: () => ccPortAction<StatePruneResult>("state_prune", {
          operation_ids: selectedPruneIds,
          retention_days: retentionDays,
          keep_latest_operations: keepLatest,
          max_backup_mb: maxBackupMb,
        }),
        successMessage: (result) => t("operations.pruned", {
          count: result.deleted_operation_ids.length,
          size: formatBytes(result.reclaimed_bytes),
        }),
        failureMessage,
        retryPolicy: "none",
      });
      await load(false, 0);
    } catch {
      // Task center owns error feedback.
    } finally {
      setBusy(false);
    }
  }

  async function exportOrphan(item: OrphanBackup) {
    setBusy(true);
    try {
      await runTask({
        kind: "orphan-export",
        title: t("operations.orphanExport"),
        context: item.name,
        action: () => ccPortAction<OrphanBackupExport>("orphan_export", {
          name: item.name,
        }),
        successMessage: (result) => t("operations.orphanExported", {
          path: result.output_path,
        }),
        failureMessage,
        retryPolicy: "none",
      });
    } catch {
      // Task center owns error feedback.
    } finally {
      setBusy(false);
    }
  }

  async function quarantineSelectedOrphans() {
    if (!selectedOrphanNames.length) return;
    if (!window.confirm(t("operations.orphanQuarantineConfirm", {
      count: selectedOrphanNames.length,
    }))) return;
    setBusy(true);
    try {
      await runTask({
        kind: "orphan-quarantine",
        title: t("operations.orphanQuarantine"),
        action: () => ccPortAction<OrphanQuarantineResult>("orphan_quarantine", {
          names: selectedOrphanNames,
        }),
        successMessage: (result) => t("operations.orphanQuarantined", {
          count: result.quarantine.item_count,
        }),
        failureMessage,
        retryPolicy: "none",
      });
      setSelectedOrphanNames([]);
      await load(false, historyOffset);
    } catch {
      // Task center owns error feedback.
    } finally {
      setBusy(false);
    }
  }

  async function deleteQuarantine(item: OrphanQuarantine) {
    if (!window.confirm(t("operations.quarantineDeleteConfirm", {
      id: item.quarantine_id,
    }))) return;
    setBusy(true);
    try {
      await runTask({
        kind: "orphan-quarantine-delete",
        title: t("operations.quarantineDelete"),
        context: item.quarantine_id,
        action: () => ccPortAction<OrphanDeleteResult>("orphan_quarantine_delete", {
          quarantine_id: item.quarantine_id,
        }),
        successMessage: (result) => t("operations.quarantineDeleted", {
          size: formatBytes(result.reclaimed_bytes),
        }),
        failureMessage,
        retryPolicy: "none",
      });
      await load(false, historyOffset);
    } catch {
      // Task center owns error feedback.
    } finally {
      setBusy(false);
    }
  }

  async function viewAudit(item: MaintenanceAudit) {
    setBusy(true);
    try {
      const detail = await runTask({
        kind: "maintenance-audit",
        title: t("operations.auditDetails"),
        context: item.audit_id,
        action: () => ccPortAction<MaintenanceAuditDetail>("maintenance_audit", {
          audit_id: item.audit_id,
        }),
        failureMessage,
        retryPolicy: "safe-read",
      });
      setSelectedAudit(detail);
    } catch {
      // Task center owns error feedback.
    } finally {
      setBusy(false);
    }
  }

  function togglePrune(operationId: string) {
    setSelectedPruneIds((current) => (
      current.includes(operationId)
        ? current.filter((item) => item !== operationId)
        : [...current, operationId]
    ));
  }

  function toggleOrphan(name: string) {
    setSelectedOrphanNames((current) => (
      current.includes(name)
        ? current.filter((item) => item !== name)
        : [...current, name]
    ));
  }

  return (
    <div className="operations-view">
      <section className="panel operations-panel">
        <div className="panel-head">
          <div>
            <h2>{t("operations.title")}</h2>
            <p>{t("operations.description")}</p>
          </div>
          <button className="secondary" onClick={() => void load()} disabled={busy}>
            <RefreshCcw size={17} />
            {t("operations.refresh")}
          </button>
        </div>
        <label className="operations-force">
          <input
            type="checkbox"
            checked={forceRestore}
            onChange={(event) => setForceRestore(event.target.checked)}
          />
          <span>
            <strong>{t("operations.force")}</strong>
            <small>{t("operations.forceDescription")}</small>
          </span>
        </label>
      </section>

      <section className="panel operations-panel">
        <div className="panel-head">
          <div>
            <h2>{t("operations.retentionTitle")}</h2>
            <p>{t("operations.retentionDescription")}</p>
          </div>
          <div className="repository-sync-actions">
            <button className="secondary" onClick={() => void previewRetention()} disabled={busy}>
              <RefreshCcw size={17} />
              {t("operations.retentionPreview")}
            </button>
            <button
              className="secondary danger"
              onClick={() => void applyRetention()}
              disabled={busy || !selectedPruneIds.length}
            >
              <Trash2 size={17} />
              {t("operations.prune")}
            </button>
          </div>
        </div>
        <div className="stack-form three-column operations-retention-controls">
          <label>
            <span>{t("operations.retentionDays")}</span>
            <input
              type="number"
              min="0"
              value={retentionDays}
              onChange={(event) => setRetentionDays(
                Math.max(0, Number.parseInt(event.target.value, 10) || 0),
              )}
            />
          </label>
          <label>
            <span>{t("operations.keepLatest")}</span>
            <input
              type="number"
              min="0"
              value={keepLatest}
              onChange={(event) => setKeepLatest(
                Math.max(0, Number.parseInt(event.target.value, 10) || 0),
              )}
            />
          </label>
          <label>
            <span>{t("operations.maxBackupMb")}</span>
            <input
              type="number"
              min="0"
              value={maxBackupMb}
              onChange={(event) => setMaxBackupMb(
                Math.max(0, Number.parseInt(event.target.value, 10) || 0),
              )}
            />
          </label>
        </div>
        {retentionPlan ? (
          <>
            <div className="environment-metrics operations-retention-metrics">
              <div className="metric">
                <span>{t("operations.backupUsage")}</span>
                <strong>{formatBytes(retentionPlan.backup_bytes)}</strong>
              </div>
              <div className="metric">
                <span>{t("operations.candidates")}</span>
                <strong>{retentionPlan.candidate_count}</strong>
              </div>
              <div className="metric">
                <span>{t("operations.reclaimable")}</span>
                <strong>{formatBytes(retentionPlan.reclaimable_bytes)}</strong>
              </div>
              <div className="metric">
                <span>{t("operations.orphans")}</span>
                <strong>{retentionPlan.orphan_backup_count}</strong>
              </div>
            </div>
            <div className="operation-list">
              {retentionPlan.candidates.map((item) => (
                <label className="operation-row operation-prune-row" key={item.operation_id}>
                  <input
                    type="checkbox"
                    checked={selectedPruneIds.includes(item.operation_id)}
                    onChange={() => togglePrune(item.operation_id)}
                  />
                  <div className="operation-main">
                    <strong>{item.kind}</strong>
                    <code>{item.operation_id}</code>
                    <small>
                      {t("operations.retentionCandidate", {
                        age: item.age_days,
                        size: formatBytes(item.reclaimable_bytes),
                        reason: item.reasons.join(", "),
                      })}
                    </small>
                  </div>
                </label>
              ))}
              {!retentionPlan.candidates.length ? (
                <p className="empty">{t("operations.noCandidates")}</p>
              ) : null}
            </div>
          </>
        ) : null}
      </section>

      <section className="panel operations-panel">
        <div className="panel-head">
          <div>
            <h2>{t("operations.orphanTitle")}</h2>
            <p>{t("operations.orphanDescription")}</p>
          </div>
          <button
            className="secondary"
            onClick={() => void quarantineSelectedOrphans()}
            disabled={busy || !selectedOrphanNames.length}
          >
            <Archive size={17} />
            {t("operations.orphanQuarantine")}
          </button>
        </div>
        <div className="operation-list">
          {orphans.map((item) => (
            <div className="operation-row operation-prune-row" key={item.name}>
              <input
                type="checkbox"
                checked={selectedOrphanNames.includes(item.name)}
                onChange={() => toggleOrphan(item.name)}
              />
              <div className="operation-main">
                <strong>{item.name}</strong>
                <span>{item.kind}</span>
                <small>{formatBytes(item.size_bytes)} · {formatDate(item.modified_at)}</small>
                <small>{item.path}</small>
              </div>
              <button
                className="secondary"
                disabled={busy}
                onClick={() => void exportOrphan(item)}
              >
                <Download size={16} />
                {t("operations.orphanExport")}
              </button>
            </div>
          ))}
          {!orphans.length ? <p className="empty">{t("operations.noOrphans")}</p> : null}
        </div>
        <div className="operations-subsection">
          <h3>{t("operations.quarantineTitle")}</h3>
          <div className="operation-list">
            {quarantines.map((item) => (
              <article className="operation-row" key={item.quarantine_id}>
                <div className="operation-main">
                  <strong>{item.quarantine_id}</strong>
                  <small>{t("operations.quarantineSummary", {
                    count: item.item_count,
                    size: formatBytes(item.size_bytes),
                  })}</small>
                  <small>{formatDate(item.created_at)}</small>
                </div>
                <button
                  className="secondary danger"
                  disabled={busy}
                  onClick={() => void deleteQuarantine(item)}
                >
                  <Trash2 size={16} />
                  {t("operations.quarantineDelete")}
                </button>
              </article>
            ))}
            {!quarantines.length ? (
              <p className="empty">{t("operations.noQuarantines")}</p>
            ) : null}
          </div>
        </div>
      </section>

      <section className="panel operations-panel">
        <div className="panel-head">
          <div>
            <h2>{t("operations.history")}</h2>
            <p>{t("operations.historyRange", {
              start: historyTotal ? historyOffset + 1 : 0,
              end: historyOffset + operations.length,
              total: historyTotal,
            })}</p>
          </div>
          <History size={20} />
        </div>
        <div className="operation-list">
          {operations.map((item) => (
            <article className="operation-row" key={item.operation_id}>
              <div className="operation-main">
                <strong>{item.kind}</strong>
                <code>{item.operation_id}</code>
                <span>{item.status}</span>
                <small>{formatDate(item.started_at)} · {t("operations.changed", {
                  changed: item.changed_target_count,
                  total: item.target_count,
                })}</small>
                {item.message ? (
                  <small className="operation-message">
                    {translateMessage(item.message_ref, t, item.message)}
                  </small>
                ) : null}
              </div>
              <div className="operation-actions">
                <button
                  className="secondary"
                  disabled={busy}
                  onClick={() => void viewOperation(item)}
                >
                  <Eye size={16} />
                  {t("operations.viewDetails")}
                </button>
                <button
                  className="secondary"
                  disabled={busy || !item.restorable}
                  onClick={() => void restore(item)}
                  title={!item.restorable ? t("operations.notRestorable") : undefined}
                >
                  <RotateCcw size={16} />
                  {t("operations.restore")}
                </button>
              </div>
            </article>
          ))}
          {!operations.length ? <p className="empty">{t("operations.empty")}</p> : null}
        </div>
        <div className="operations-pagination">
          <button
            className="secondary"
            disabled={busy || historyOffset === 0}
            onClick={() => void changeHistoryPage(
              Math.max(0, historyOffset - HISTORY_PAGE_SIZE),
            )}
          >
            <ChevronLeft size={16} />
            {t("operations.previous")}
          </button>
          <button
            className="secondary"
            disabled={busy || !historyHasMore}
            onClick={() => void changeHistoryPage(historyOffset + HISTORY_PAGE_SIZE)}
          >
            {t("operations.next")}
            <ChevronRight size={16} />
          </button>
        </div>
        {selectedOperation ? (
          <div className="operation-detail">
            <h3>{t("operations.operationDetail")}</h3>
            <dl className="environment-operation-meta">
              <dt>{t("operations.operationId")}</dt>
              <dd>{selectedOperation.operation_id}</dd>
              <dt>{t("operations.status")}</dt>
              <dd>{selectedOperation.status}</dd>
            </dl>
            <pre>{JSON.stringify(selectedOperation.metadata, null, 2)}</pre>
            <div className="operation-list">
              {selectedOperation.targets.map((target) => (
                <article className="operation-row" key={`${target.path}:${target.action}`}>
                  <div className="operation-main">
                    <strong>{target.change_action || target.action}</strong>
                    <small>{target.path}</small>
                    <small>{target.verified ? t("operations.verified") : t("operations.unverified")}</small>
                  </div>
                </article>
              ))}
            </div>
          </div>
        ) : null}
      </section>

      <section className="panel operations-panel">
        <div className="panel-head">
          <div>
            <h2>{t("operations.auditTitle")}</h2>
            <p>{t("operations.auditDescription")}</p>
          </div>
          <FileJson size={20} />
        </div>
        <div className="operation-list">
          {audits.map((item) => (
            <article className="operation-row" key={item.audit_id}>
              <div className="operation-main">
                <strong>{item.action}</strong>
                <code>{item.audit_id}</code>
                <span>{item.status}</span>
                <small>{t("operations.auditSummary", {
                  count: item.item_count,
                  size: formatBytes(item.reclaimed_bytes),
                })}</small>
                <small>{formatDate(item.created_at)}</small>
              </div>
              <button
                className="secondary"
                disabled={busy}
                onClick={() => void viewAudit(item)}
              >
                <Eye size={16} />
                {t("operations.auditDetails")}
              </button>
            </article>
          ))}
          {!audits.length ? <p className="empty">{t("operations.noAudits")}</p> : null}
        </div>
        {selectedAudit ? (
          <div className="operation-detail">
            <pre>{JSON.stringify(selectedAudit.audit, null, 2)}</pre>
          </div>
        ) : null}
      </section>

      <section className="panel operations-panel">
        <div className="panel-head">
          <div>
            <h2>{t("operations.staleTitle")}</h2>
            <p>{t("operations.staleDescription")}</p>
          </div>
        </div>
        <div className="operation-list">
          {stalePlans.map((item) => (
            <article className="operation-row" key={item.operation_id}>
              <div className="operation-main">
                <strong>{item.status}</strong>
                <code>{item.operation_id}</code>
                <small>{t("operations.age", { hours: item.age_hours })}</small>
                <small>{item.worktree_path}</small>
              </div>
              <button
                className="secondary danger"
                disabled={busy}
                onClick={() => void cleanup(item)}
              >
                <Trash2 size={16} />
                {t("operations.cleanup")}
              </button>
            </article>
          ))}
          {!stalePlans.length ? <p className="empty">{t("operations.noStale")}</p> : null}
        </div>
      </section>
    </div>
  );
}

function formatDate(value: string): string {
  if (!value) return "-";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KiB`;
  if (value < 1024 * 1024 * 1024) {
    return `${(value / (1024 * 1024)).toFixed(1)} MiB`;
  }
  return `${(value / (1024 * 1024 * 1024)).toFixed(2)} GiB`;
}
