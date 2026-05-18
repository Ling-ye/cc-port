import { useMemo, useState } from "react";
import { Download, Eye, EyeOff, FolderOpen, Trash2, Unplug } from "lucide-react";
import { lpmAction, openPath } from "@/api/client";
import { resourceKindLabel, type TFunction } from "@/app/i18n";
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
  SyncResultItem,
} from "@/types/lpm";

const kinds: Array<"all" | ResourceKind> = ["all", "skill", "mcp", "rule", "prompt", "plugin"];
const statusFilters = ["available", "installed", "not-installed", "removed", "all"] as const;

export function ResourcesView({
  items,
  platforms,
  selected,
  t,
  onSelect,
  onChanged,
  onDone,
  onError,
}: {
  items: ResourceInventoryItem[];
  platforms: PlatformProfile[];
  selected?: ResourceInventoryItem;
  t: TFunction;
  onSelect: (name: string) => void;
  onChanged: () => Promise<void> | void;
  onDone: (message: string) => void;
  onError: (message: string) => void;
}) {
  const [filter, setFilter] = useState<(typeof kinds)[number]>("all");
  const [statusFilter, setStatusFilter] = useState<(typeof statusFilters)[number]>("available");
  const [platform, setPlatform] = useState("");
  const [busyAction, setBusyAction] = useState("");
  const [preview, setPreview] = useState<ResourcePreviewResult | null>(null);

  const enabledPlatforms = useMemo(() => platforms.filter((item) => item.enabled), [platforms]);
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
            (statusFilter === "not-installed" && lifecycle === "active" && !installed) ||
            (statusFilter === "removed" && lifecycle === "removed"))
        );
      }),
    [filter, items, statusFilter],
  );
  const installableVisible = useMemo(() => visible.filter((item) => item.actions.can_install), [visible]);
  const activePreview = selected && preview?.name === selected.entry.name ? preview : null;
  const busy = Boolean(busyAction);

  async function installSelected() {
    if (!selected) return;
    setBusyAction("install");
    try {
      const data = await lpmAction<SyncResultItem>("resource_install", {
        name: selected.entry.name,
        platform: platform || undefined,
      });
      onDone(t("resources.installDone", { name: data.name }));
      setPreview(null);
      await onChanged();
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyAction("");
    }
  }

  async function installVisible() {
    if (!installableVisible.length) {
      onError(t("resources.noVisibleInstall"));
      return;
    }
    setBusyAction("install-visible");
    try {
      for (const item of installableVisible) {
        await lpmAction<SyncResultItem>("resource_install", {
          name: item.entry.name,
          platform: platform || undefined,
        });
      }
      onDone(t("resources.installManyDone", { count: installableVisible.length }));
      setPreview(null);
      await onChanged();
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyAction("");
    }
  }

  async function uninstallSelected() {
    if (!selected) return;
    setBusyAction("uninstall");
    try {
      await lpmAction("resource_uninstall", { name: selected.entry.name });
      onDone(t("resources.uninstallDone", { name: selected.entry.name }));
      setPreview(null);
      await onChanged();
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyAction("");
    }
  }

  async function previewSelected() {
    if (!selected) return;
    if (activePreview) {
      setPreview(null);
      return;
    }
    setBusyAction("preview");
    try {
      const data = await lpmAction<ResourcePreviewResult>("resource_preview", { name: selected.entry.name });
      setPreview(data);
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyAction("");
    }
  }

  async function openSelectedDirectory() {
    if (!selected) return;
    setBusyAction("open");
    try {
      const data = await lpmAction<{ path: string }>("resource_open_path", { name: selected.entry.name });
      await openPath(data.path);
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyAction("");
    }
  }

  async function deleteSelected() {
    if (!selected) return;
    const name = selected.entry.name;
    const remoteDelete = selected.remote_state.can_delete_remote;
    let confirmName = "";
    if (remoteDelete) {
      if (!confirm(t("resources.remoteDeleteFirstConfirm", { name }))) return;
      confirmName = prompt(t("resources.remoteDeletePrompt", { name })) || "";
      if (confirmName !== name) {
        onError(t("resources.remoteDeleteMismatch"));
        return;
      }
    } else if (!confirm(t("resources.deleteConfirm", { name }))) {
      return;
    }

    setBusyAction("delete");
    try {
      const data = await lpmAction<ResourceDeleteResult>("resource_delete", {
        name,
        confirm_name: confirmName || undefined,
      });
      onDone(t("resources.deleteDone", { name: data.name }));
      setPreview(null);
      await onChanged();
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyAction("");
    }
  }

  return (
    <section className="split-view resources-workspace">
      <div className="panel list-panel">
        <div className="resource-filter-stack">
          <Segmented value={filter} values={kinds} onChange={setFilter} getLabel={(item) => resourceKindLabel(item, t)} />
          <Segmented
            value={statusFilter}
            values={statusFilters}
            onChange={setStatusFilter}
            getLabel={(item) => statusFilterLabel(item, t)}
          />
          <div className="stack-form">
            <label>
              <span>{t("sync.targetPlatform")}</span>
              <select value={platform} onChange={(event) => setPlatform(event.target.value)} disabled={!enabledPlatforms.length}>
                <option value="">{t("sync.allEnabledPlatforms")}</option>
                {enabledPlatforms.map((item) => <option key={item.name} value={item.name}>{item.name}</option>)}
              </select>
            </label>
          </div>
          <button className="secondary resource-wide-action" onClick={installVisible} disabled={busy || installableVisible.length === 0}>
            <Download size={16} />
            {busyAction === "install-visible" ? t("common.working") : t("resources.installVisible")}
          </button>
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
      <div className="panel detail-panel resource-detail-panel">
        {selected ? (
          <>
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
            <div className="resource-actions">
              <button className="primary" onClick={installSelected} disabled={busy || !selected.actions.can_install} title={selected.actions.install_reason}>
                <Download size={16} />
                {busyAction === "install" ? t("common.working") : t("resources.downloadRegister")}
              </button>
              <button className="secondary" onClick={uninstallSelected} disabled={busy || !selected.actions.can_uninstall}>
                <Unplug size={16} />
                {busyAction === "uninstall" ? t("common.working") : t("resources.uninstallLocal")}
              </button>
              <button className="secondary" onClick={previewSelected} disabled={busy || !selected.actions.can_preview}>
                {activePreview ? <EyeOff size={16} /> : <Eye size={16} />}
                {activePreview ? t("resources.hidePreview") : t("resources.previewContent")}
              </button>
              <button className="secondary" onClick={openSelectedDirectory} disabled={busy || !selected.actions.can_open}>
                <FolderOpen size={16} />
                {t("resources.openDirectory")}
              </button>
              <button className="danger" onClick={deleteSelected} disabled={busy || !selected.actions.can_delete_resource} title={selected.actions.delete_reason}>
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
          </>
        ) : (
          <EmptyState text={t("resources.noSelected")} />
        )}
      </div>
    </section>
  );
}

function statusFilterLabel(value: (typeof statusFilters)[number], t: TFunction) {
  if (value === "available") return t("resources.availableOnly");
  if (value === "installed") return t("status.installed");
  if (value === "not-installed") return t("status.notInstalled");
  if (value === "removed") return t("resources.lifecycleRemoved");
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
