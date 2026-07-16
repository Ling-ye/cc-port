import { useMemo, useState } from "react";
import { AlertTriangle, Download, Eye, EyeOff, FolderOpen, Trash2, Unplug } from "lucide-react";
import { lpmAction, openPath } from "@/api/client";
import { resourceKindLabel, type TFunction } from "@/app/i18n";
import { useTaskCenter } from "@/app/TaskCenterContext";
import { DescriptionList } from "@/components/DescriptionList";
import { EmptyState } from "@/components/EmptyState";
import { KindBadge } from "@/components/KindBadge";
import { Segmented } from "@/components/Segmented";
import type {
  PlatformProfile,
  ResourceDeleteResult,
  ResourceInventoryItem,
  ResourceKind,
  ResourcePreviewResult,
  ResourceTargetState,
  SyncResultItem,
} from "@/types/lpm";

const kinds: Array<"all" | ResourceKind> = ["all", "skill", "mcp", "rule", "prompt", "plugin"];
const statusFilters = ["all", "available", "installed", "not-installed"] as const;
type TargetAction = "install" | "uninstall" | "preview" | "open";
const cachePreviewTarget = "__cache_preview__";

type DeleteDialogState = {
  item: ResourceInventoryItem;
  confirmName: string;
  error: string;
};

export function ResourcesView({
  items,
  platforms,
  selected,
  t,
  onSelect,
  onChanged,
  onError,
}: {
  items: ResourceInventoryItem[];
  platforms: PlatformProfile[];
  selected?: ResourceInventoryItem;
  t: TFunction;
  onSelect: (name: string) => void;
  onChanged: () => Promise<void> | void;
  onError: (message: string) => void;
}) {
  const { runTask } = useTaskCenter();
  const [filter, setFilter] = useState<(typeof kinds)[number]>("all");
  const [statusFilter, setStatusFilter] = useState<(typeof statusFilters)[number]>("available");
  const [busyAction, setBusyAction] = useState("");
  const [preview, setPreview] = useState<ResourcePreviewResult | null>(null);
  const [targetAction, setTargetAction] = useState<TargetAction | null>(null);
  const [selectedTargets, setSelectedTargets] = useState<string[]>([]);
  const [deleteDialog, setDeleteDialog] = useState<DeleteDialogState | null>(null);

  const visible = useMemo(
    () =>
      items.filter((item) => {
        const lifecycle = item.entry.lifecycle || "active";
        const installed = item.local_state.installed;
        return (
          (filter === "all" || item.entry.kind === filter) &&
          (statusFilter === "all" ||
            (statusFilter === "available" && lifecycle === "active") ||
            (statusFilter === "installed" && lifecycle === "active" && installed) ||
            (statusFilter === "not-installed" && lifecycle === "active" && !installed))
        );
      }),
    [filter, items, statusFilter],
  );
  const activePreview = selected && preview?.name === selected.entry.name ? preview : null;
  const busy = Boolean(busyAction);
  const targets = selected?.local_state.targets || [];
  const installTargets = useMemo(() => targets.filter((item) => item.supported), [targets]);
  const installedTargets = useMemo(() => installTargets.filter((item) => item.installed), [installTargets]);
  const uninstallTargets = useMemo(() => targets.filter((item) => item.installed), [targets]);
  const openTargets = useMemo(() => installTargets.filter((item) => item.exists && item.installed), [installTargets]);
  const modalOptions = selected && targetAction
    ? targetOptions(targetAction, installTargets, uninstallTargets, installedTargets, openTargets, t)
    : [];
  const modalMulti = targetAction === "install" || targetAction === "uninstall";
  const canSubmitModal = selectedTargets.length > 0 && !modalOptions.every((item) => item.disabled);

  function openTargetModal(action: TargetAction) {
    if (!selected) return;
    if (action === "preview" && activePreview) {
      setPreview(null);
      return;
    }
    const nextOptions = targetOptions(action, installTargets, uninstallTargets, installedTargets, openTargets, t);
    setSelectedTargets(defaultTargetSelection(action, nextOptions));
    setTargetAction(action);
  }

  async function installSelected(platformsToInstall: string[]) {
    if (!selected) return;
    setBusyAction("install");
    try {
      await runTask({
        kind: "resource-install",
        title: t("resources.downloadRegister"),
        context: `${selected.entry.name} · ${platformsToInstall.length}`,
        action: async () => {
          let result: SyncResultItem | null = null;
          for (const targetPlatform of platformsToInstall) {
            result = await lpmAction<SyncResultItem>("resource_install", {
              name: selected.entry.name,
              platform: targetPlatform,
            });
          }
          return result;
        },
        successMessage: (result) => t("resources.installTargetsDone", {
          name: result?.name || selected.entry.name,
          count: platformsToInstall.length,
        }),
        retryPolicy: "none",
      });
      setPreview(null);
      await onChanged();
    } catch {
      await Promise.resolve(onChanged());
    } finally {
      setBusyAction("");
    }
  }

  async function uninstallSelected(platformsToUninstall: string[]) {
    if (!selected) return;
    setBusyAction("uninstall");
    try {
      await runTask({
        kind: "resource-uninstall",
        title: t("resources.uninstallLocal"),
        context: `${selected.entry.name} · ${platformsToUninstall.length}`,
        action: async () => {
          for (const targetPlatform of platformsToUninstall) {
            await lpmAction("resource_uninstall", { name: selected.entry.name, platform: targetPlatform });
          }
        },
        successMessage: t("resources.uninstallTargetsDone", {
          name: selected.entry.name,
          count: platformsToUninstall.length,
        }),
        retryPolicy: "none",
      });
      setPreview(null);
      await onChanged();
    } catch {
      await Promise.resolve(onChanged());
    } finally {
      setBusyAction("");
    }
  }

  async function previewSelected(target: string) {
    if (!selected) return;
    setBusyAction("preview");
    try {
      const data = await lpmAction<ResourcePreviewResult>("resource_preview", {
        name: selected.entry.name,
        platform: target === cachePreviewTarget ? undefined : target,
      });
      setPreview(data);
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyAction("");
    }
  }

  async function openSelectedDirectory(target: string) {
    if (!selected) return;
    setBusyAction("open");
    try {
      const data = await lpmAction<{ path: string }>("resource_open_path", {
        name: selected.entry.name,
        platform: target,
      });
      await openPath(data.path);
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyAction("");
    }
  }

  async function confirmTargetAction() {
    if (!targetAction || !selectedTargets.length) return;
    const action = targetAction;
    const targetsToUse = [...selectedTargets];
    setTargetAction(null);
    if (action === "install") {
      await installSelected(targetsToUse);
    } else if (action === "uninstall") {
      await uninstallSelected(targetsToUse);
    } else if (action === "preview") {
      await previewSelected(targetsToUse[0]);
    } else {
      await openSelectedDirectory(targetsToUse[0]);
    }
  }

  function openDeleteDialog() {
    if (!selected) return;
    setDeleteDialog({ item: selected, confirmName: "", error: "" });
  }

  async function confirmDeleteResource() {
    if (!deleteDialog) return;
    const name = deleteDialog.item.entry.name;
    const remoteDelete = deleteDialog.item.remote_state.can_delete_remote;
    const confirmName = deleteDialog.confirmName.trim();
    if (remoteDelete) {
      if (confirmName !== name) {
        setDeleteDialog((current) => (current ? { ...current, error: t("resources.remoteDeleteMismatch") } : current));
        return;
      }
    }

    setBusyAction("delete");
    try {
      await runTask({
        kind: "resource-delete",
        title: deleteButtonLabel(deleteDialog.item, t),
        context: name,
        action: () => lpmAction<ResourceDeleteResult>("resource_delete", {
          name,
          confirm_name: confirmName || undefined,
        }),
        successMessage: t("resources.deleteDone", { name }),
        retryPolicy: "none",
      });
      setPreview(null);
      setDeleteDialog(null);
      await onChanged();
    } catch {
      await Promise.resolve(onChanged());
    } finally {
      setBusyAction("");
    }
  }

  return (
    <section className="split-view resources-workspace">
      <div className="panel list-panel">
        <div className="resource-filter-stack">
          <div className="resource-filter-group">
            <span>{t("resources.filterKind")}</span>
            <Segmented value={filter} values={kinds} onChange={setFilter} getLabel={(item) => resourceKindLabel(item, t)} />
          </div>
          <div className="resource-filter-group">
            <span>{t("resources.filterStatus")}</span>
            <Segmented
              value={statusFilter}
              values={statusFilters}
              onChange={setStatusFilter}
              getLabel={(item) => statusFilterLabel(item, t)}
            />
          </div>
        </div>
        <div className="resource-list">
          {visible.map((item) => (
            <button
              key={item.entry.name}
              className={selected?.entry.name === item.entry.name ? "resource-row active" : "resource-row"}
              onClick={() => {
                onSelect(item.entry.name);
                setPreview(null);
              }}
            >
              <KindBadge kind={item.entry.kind} label={resourceKindLabel(item.entry.kind, t)} />
              <span>
                <strong>{item.entry.name}</strong>
                <small>
                  {item.entry.source} / {item.local_state.installed ? t("status.installed") : t("status.notInstalled")} /{" "}
                  {lifecycleLabel(item.entry.lifecycle || "active", t)}
                </small>
              </span>
            </button>
          ))}
        </div>
      </div>
      <div className="resource-side-panel">
        {selected ? (
          <>
            <ResourceActionPanel
              activePreview={activePreview}
              busy={busy}
              busyAction={busyAction}
              installTargets={installTargets}
              uninstallTargets={uninstallTargets}
              openTargets={openTargets}
              selected={selected}
              t={t}
              onDelete={openDeleteDialog}
              onOpenTargetModal={openTargetModal}
            />
            <ResourceDetailPanel selected={selected} t={t} />
          </>
        ) : (
          <div className="panel detail-panel resource-detail-panel">
            <EmptyState text={t("resources.noSelected")} />
          </div>
        )}
      </div>
      {selected && targetAction ? (
        <ResourceTargetModal
          action={targetAction}
          busy={busy}
          multi={modalMulti}
          options={modalOptions}
          selected={selectedTargets}
          t={t}
          onCancel={() => setTargetAction(null)}
          onConfirm={confirmTargetAction}
          onToggle={(id) => {
            setSelectedTargets((current) => {
              if (!modalMulti) return [id];
              return current.includes(id) ? current.filter((item) => item !== id) : [...current, id];
            });
          }}
          confirmDisabled={!canSubmitModal}
        />
      ) : null}
      {deleteDialog ? (
        <ResourceDeleteModal
          busy={busyAction === "delete"}
          state={deleteDialog}
          t={t}
          onCancel={() => setDeleteDialog(null)}
          onConfirm={confirmDeleteResource}
          onNameChange={(confirmName) => setDeleteDialog((current) => (current ? { ...current, confirmName, error: "" } : current))}
        />
      ) : null}
    </section>
  );
}

type TargetOption = {
  id: string;
  label: string;
  path: string;
  installed: boolean;
  exists: boolean;
  disabled?: boolean;
  note: string;
};

function ResourceActionPanel({
  activePreview,
  busy,
  busyAction,
  installTargets,
  uninstallTargets,
  openTargets,
  selected,
  t,
  onDelete,
  onOpenTargetModal,
}: {
  activePreview: ResourcePreviewResult | null;
  busy: boolean;
  busyAction: string;
  installTargets: ResourceTargetState[];
  uninstallTargets: ResourceTargetState[];
  openTargets: ResourceTargetState[];
  selected: ResourceInventoryItem;
  t: TFunction;
  onDelete: () => void;
  onOpenTargetModal: (action: TargetAction) => void;
}) {
  return (
    <div className="panel resource-action-panel">
      <div className="resource-actions">
        <button
          className="primary"
          onClick={() => onOpenTargetModal("install")}
          disabled={busy || !selected.actions.can_install || installTargets.length === 0}
          title={selected.actions.install_reason}
        >
          <Download size={16} />
          {busyAction === "install" ? t("common.working") : t("resources.downloadRegister")}
        </button>
        <button className="secondary" onClick={() => onOpenTargetModal("uninstall")} disabled={busy || uninstallTargets.length === 0}>
          <Unplug size={16} />
          {busyAction === "uninstall" ? t("common.working") : t("resources.uninstallLocal")}
        </button>
        <button className="secondary" onClick={() => onOpenTargetModal("preview")} disabled={busy || !selected.actions.can_preview}>
          {activePreview ? <EyeOff size={16} /> : <Eye size={16} />}
          {activePreview ? t("resources.hidePreview") : t("resources.previewContent")}
        </button>
        <button className="secondary" onClick={() => onOpenTargetModal("open")} disabled={busy || openTargets.length === 0}>
          <FolderOpen size={16} />
          {t("resources.openDirectory")}
        </button>
        <button className="danger" onClick={onDelete} disabled={busy || !selected.actions.can_delete_resource} title={selected.actions.delete_reason}>
          <Trash2 size={16} />
          {deleteButtonLabel(selected, t)}
        </button>
      </div>
      {activePreview ? (
        <div className="preview-panel resource-preview">
          <strong>{activePreview.path}</strong>
          {activePreview.warning ? <p className="discovery-warning">{activePreview.warning}</p> : null}
          <pre>{activePreview.text}{activePreview.truncated ? "\n..." : ""}</pre>
        </div>
      ) : null}
    </div>
  );
}

function ResourceDetailPanel({ selected, t }: { selected: ResourceInventoryItem; t: TFunction }) {
  return (
    <div className="panel detail-panel resource-detail-panel">
      <div className="detail-title">
        <div className="detail-title-main">
          <KindBadge kind={selected.entry.kind} label={resourceKindLabel(selected.entry.kind, t)} />
          <h2>{selected.entry.name}</h2>
        </div>
        <span className={selected.entry.lifecycle === "removed" ? "lifecycle-pill removed" : "lifecycle-pill"}>
          {lifecycleLabel(selected.entry.lifecycle || "active", t)}
        </span>
      </div>
      <DescriptionList
        rows={[
          [t("resources.source"), selected.entry.source],
          [t("resources.lifecycle"), lifecycleLabel(selected.entry.lifecycle || "active", t)],
          [t("resources.platforms"), selected.entry.platforms?.length ? selected.entry.platforms.join(", ") : t("resources.allEnabledPlatforms")],
          [t("resources.repo"), selected.entry.repo || "-"],
          [t("resources.path"), selected.entry.path || "-"],
          [t("resources.ref"), selected.entry.ref || "-"],
          [t("resources.subdir"), selected.entry.subdir || "-"],
          [t("resources.remoteState"), remoteStateLabel(selected.remote_state.reachable, t)],
          [t("resources.sourcePath"), selected.local_state.source_path || "-"],
          [t("resources.sourceState"), selected.local_state.source_exists ? t("resources.sourceExists") : t("resources.sourceMissing")],
          [t("resources.installPath"), selected.local_state.install_path || "-"],
          [t("resources.installState"), selected.local_state.installed ? t("status.installed") : t("status.notInstalled")],
          [t("sync.plannedAction"), selected.sync_preview?.planned_action || "-"],
          [t("sync.targetPaths"), selected.local_state.target_paths.length ? selected.local_state.target_paths.join(", ") : "-"],
          [t("resources.removedAt"), selected.entry.removed_at || "-"],
          [t("resources.removedEffect"), selected.entry.removed_effect || "-"],
        ]}
      />
      <div className="tag-row">
        {selected.entry.tags?.map((tag) => <span key={tag}>{tag}</span>)}
      </div>
      {selected.sync_preview?.warnings.length ? (
        <p className="discovery-warning">{selected.sync_preview.warnings.join(" ")}</p>
      ) : null}
    </div>
  );
}

function targetOptions(
  action: TargetAction,
  installTargets: ResourceTargetState[],
  uninstallTargets: ResourceTargetState[],
  installedTargets: ResourceTargetState[],
  openTargets: ResourceTargetState[],
  t: TFunction,
): TargetOption[] {
  if (action === "install") return installTargets.map((item) => targetOption(item, t));
  if (action === "uninstall") return uninstallTargets.map((item) => targetOption(item, t));
  if (action === "open") return openTargets.map((item) => targetOption(item, t));
  if (installedTargets.length) return installedTargets.map((item) => targetOption(item, t));
  return [
    {
      id: cachePreviewTarget,
      label: t("resources.cacheRemotePreview"),
      path: t("resources.cacheRemotePreviewPath"),
      installed: false,
      exists: true,
      note: t("resources.cacheRemotePreviewNote"),
    },
  ];
}

function targetOption(item: ResourceTargetState, t: TFunction): TargetOption {
  return {
    id: item.platform,
    label: item.platform,
    path: item.path,
    installed: item.installed,
    exists: item.exists,
    note: item.installed ? t("status.installed") : t("status.notInstalled"),
  };
}

function defaultTargetSelection(action: TargetAction, options: TargetOption[]): string[] {
  const enabled = options.filter((item) => !item.disabled);
  if (action === "install" || action === "uninstall") return enabled.map((item) => item.id);
  return enabled[0] ? [enabled[0].id] : [];
}

function ResourceTargetModal({
  action,
  busy,
  multi,
  options,
  selected,
  t,
  onCancel,
  onConfirm,
  onToggle,
  confirmDisabled,
}: {
  action: TargetAction;
  busy: boolean;
  multi: boolean;
  options: TargetOption[];
  selected: string[];
  t: TFunction;
  onCancel: () => void;
  onConfirm: () => void;
  onToggle: (id: string) => void;
  confirmDisabled: boolean;
}) {
  return (
    <div className="modal-backdrop">
      <div className="modal resource-target-modal">
        <div className="modal-head">
          <FolderOpen size={20} />
          <h2>{targetModalTitle(action, t)}</h2>
        </div>
        {options.length ? (
          <div className="target-list">
            {options.map((item) => (
              <label key={item.id} className={item.disabled ? "target-row disabled" : "target-row"}>
                <input
                  type={multi ? "checkbox" : "radio"}
                  checked={selected.includes(item.id)}
                  disabled={item.disabled || busy}
                  onChange={() => onToggle(item.id)}
                  name="resource-target"
                />
                <span>
                  <strong>{item.label}</strong>
                  <small>{item.path}</small>
                  <small>{item.note}</small>
                </span>
              </label>
            ))}
          </div>
        ) : (
          <p className="empty compact-empty">{emptyTargetText(action, t)}</p>
        )}
        <div className="modal-actions">
          <button className="secondary" type="button" onClick={onCancel} disabled={busy}>{t("common.cancel")}</button>
          <button className="primary" type="button" onClick={onConfirm} disabled={busy || confirmDisabled || !options.length}>
            {targetConfirmLabel(action, t, selected.length)}
          </button>
        </div>
      </div>
    </div>
  );
}

function ResourceDeleteModal({
  busy,
  state,
  t,
  onCancel,
  onConfirm,
  onNameChange,
}: {
  busy: boolean;
  state: DeleteDialogState;
  t: TFunction;
  onCancel: () => void;
  onConfirm: () => void;
  onNameChange: (value: string) => void;
}) {
  const name = state.item.entry.name;
  const remoteDelete = state.item.remote_state.can_delete_remote;
  const confirmDisabled = remoteDelete && state.confirmName.trim() !== name;
  return (
    <div className="modal-backdrop">
      <div className="modal resource-delete-modal">
        <div className="modal-head danger-head">
          <AlertTriangle size={20} />
          <h2>{remoteDelete ? t("resources.deleteRemoteTitle") : t("resources.deleteTitle")}</h2>
        </div>
        <div className="delete-modal-body">
          <p>{remoteDelete ? t("resources.deleteRemoteDescription", { name }) : t("resources.deleteDescription", { name })}</p>
          {remoteDelete ? (
            <label className="confirm-name-field">
              <span>{t("resources.deleteConfirmNameLabel", { name })}</span>
              <input value={state.confirmName} onChange={(event) => onNameChange(event.target.value)} disabled={busy} autoFocus />
            </label>
          ) : null}
          {state.error ? <p className="delete-modal-error">{state.error}</p> : null}
        </div>
        <div className="modal-actions">
          <button className="secondary" type="button" onClick={onCancel} disabled={busy}>{t("common.cancel")}</button>
          <button className="danger solid-danger" type="button" onClick={onConfirm} disabled={busy || confirmDisabled}>
            {busy ? t("common.working") : t("resources.confirmDeleteResource")}
          </button>
        </div>
      </div>
    </div>
  );
}

function targetModalTitle(action: TargetAction, t: TFunction): string {
  if (action === "install") return t("resources.selectInstallTargets");
  if (action === "uninstall") return t("resources.selectUninstallTargets");
  if (action === "preview") return t("resources.selectPreviewTarget");
  return t("resources.selectOpenTarget");
}

function targetConfirmLabel(action: TargetAction, t: TFunction, count: number): string {
  if (action === "install") return t("resources.confirmInstallTargets", { count });
  if (action === "uninstall") return t("resources.confirmUninstallTargets", { count });
  if (action === "preview") return t("resources.confirmPreviewTarget");
  return t("resources.confirmOpenTarget");
}

function emptyTargetText(action: TargetAction, t: TFunction): string {
  if (action === "install") return t("resources.noInstallTargets");
  if (action === "uninstall") return t("resources.noUninstallTargets");
  if (action === "preview") return t("resources.noPreviewTargets");
  return t("resources.noOpenTargets");
}

function statusFilterLabel(value: (typeof statusFilters)[number], t: TFunction) {
  if (value === "available") return t("resources.availableOnly");
  if (value === "installed") return t("status.installed");
  if (value === "not-installed") return t("status.notInstalled");
  return t("kind.all");
}

function lifecycleLabel(value: "active" | "removed", t: TFunction) {
  return value === "removed" ? t("resources.lifecycleRemoved") : t("resources.lifecycleActive");
}

function remoteStateLabel(value: boolean | null | undefined, t: TFunction) {
  if (value === true) return t("resources.reachable");
  if (value === false) return t("resources.unreachable");
  return t("resources.unknown");
}

function deleteButtonLabel(item: ResourceInventoryItem, t: TFunction) {
  if (item.remote_state.can_delete_remote) return t("resources.deleteRemote");
  if (item.entry.source === "local") return t("resources.deleteUploaded");
  return t("resources.removeIndex");
}
