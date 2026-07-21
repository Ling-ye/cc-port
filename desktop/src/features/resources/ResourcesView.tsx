import {
  Download,
  ExternalLink,
  FolderOpen,
  RefreshCcw,
  Upload,
  X,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { lpmAction, openPath } from "@/api/client";
import { resourceKindLabel, type TFunction } from "@/app/i18n";
import { useTaskCenter } from "@/app/TaskCenterContext";
import { Banner } from "@/components/Banner";
import { EmptyState } from "@/components/EmptyState";
import { KindBadge } from "@/components/KindBadge";
import type {
  AssetBatchChoice,
  AssetBatchPlan,
  AssetBatchResult,
  AssetInventory,
  AssetLocalStatus,
  AssetRemoteStatus,
  AssetResourceRow,
  AssetStatus,
  ConfigSettings,
  PlatformProfile,
  ResourceKind,
} from "@/types/lpm";

const kinds: Array<"all" | ResourceKind> = ["all", "skill", "mcp", "rule", "prompt", "plugin"];
const statuses: Array<"all" | AssetStatus> = [
  "all",
  "local-only",
  "remote-only",
  "same",
  "content-different",
  "metadata-only",
  "target-conflict",
  "uncomparable",
];
const localStatuses: Array<"all" | AssetLocalStatus> = [
  "all",
  "unknown",
  "missing",
  "single",
  "identical-copies",
  "variants",
];
const remoteStatuses: Array<"all" | AssetRemoteStatus> = [
  "all",
  "present",
  "missing",
  "read-only",
  "unavailable",
];

export function ResourcesView({
  inventory,
  selectedKey,
  t,
  onSelect,
  onInventory,
  onChanged,
  onError,
  onOpenSettings,
}: {
  inventory: AssetInventory | null;
  selectedKey?: string;
  t: TFunction;
  onSelect: (rowId: string) => void;
  onInventory: (inventory: AssetInventory) => void;
  onChanged: () => Promise<void> | void;
  onError: (message: string) => void;
  onOpenSettings: () => void;
}) {
  const { runTask } = useTaskCenter();
  const [kindFilter, setKindFilter] = useState<(typeof kinds)[number]>("all");
  const [statusFilter, setStatusFilter] = useState<(typeof statuses)[number]>("all");
  const [localFilter, setLocalFilter] = useState<(typeof localStatuses)[number]>("all");
  const [remoteFilter, setRemoteFilter] = useState<(typeof remoteStatuses)[number]>("all");
  const [toolFilter, setToolFilter] = useState("all");
  const [selectedKeys, setSelectedKeys] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [batchDirection, setBatchDirection] = useState<"upload" | "download" | null>(null);

  const resources = inventory?.resources ?? [];
  const tools = useMemo(
    () => Array.from(new Set(resources.flatMap((item) => item.local_instances.map((instance) => instance.platform)))).sort(),
    [resources],
  );
  const visible = useMemo(
    () => resources.filter((item) => (
      (kindFilter === "all" || item.kind === kindFilter)
      && (statusFilter === "all" || item.status === statusFilter)
      && (localFilter === "all" || item.local_status === localFilter)
      && (remoteFilter === "all" || item.remote_status === remoteFilter)
      && (toolFilter === "all" || item.local_instances.some((instance) => instance.platform === toolFilter))
    )),
    [kindFilter, localFilter, remoteFilter, resources, statusFilter, toolFilter],
  );
  const selectedSet = useMemo(() => new Set(selectedKeys), [selectedKeys]);
  const selectedResource = resources.find((item) => item.resource_key === selectedKey)
    ?? resources[0];

  useEffect(() => {
    const valid = new Set(resources.map((item) => item.resource_key));
    setSelectedKeys((current) => current.filter((key) => valid.has(key)));
  }, [resources]);

  async function scanLocal() {
    setBusy(true);
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
    } catch {
      // Task center owns tracked feedback.
    } finally {
      setBusy(false);
    }
  }

  function toggleSelected(resourceKey: string) {
    setSelectedKeys((current) => current.includes(resourceKey)
      ? current.filter((item) => item !== resourceKey)
      : [...current, resourceKey]);
  }

  function selectVisible() {
    setSelectedKeys((current) => Array.from(new Set([...current, ...visible.map((item) => item.resource_key)])));
  }

  function selectResource(resource: AssetResourceRow) {
    onSelect(resource.resource_key);
  }

  function openBatch(direction: "upload" | "download") {
    if (!selectedKeys.length) {
      onError(t("assets.noSelection"));
      return;
    }
    setBatchDirection(direction);
  }

  return (
    <section className="asset-unified-view">
      <div className="panel asset-inventory-panel">
        <div className="asset-unified-head">
          <div>
            <h2>{t("assets.title")}</h2>
            <small>{inventory?.branch || "-"} / {shortCommit(inventory?.remote_commit)}</small>
          </div>
          <div className="asset-toolbar">
            <button className="secondary" onClick={() => void scanLocal()} disabled={busy}>
              <RefreshCcw size={16} />{busy ? t("common.working") : t("assets.scanLocal")}
            </button>
            <button className="secondary" onClick={() => openBatch("upload")} disabled={busy || !selectedKeys.length}>
              <Upload size={16} />{t("assets.uploadSelected")}
            </button>
            <button className="primary" onClick={() => openBatch("download")} disabled={busy || !selectedKeys.length}>
              <Download size={16} />{t("assets.downloadSelected")}
            </button>
          </div>
        </div>

        {inventory?.remote_warning ? <Banner tone="danger" text={inventory.remote_warning} /> : null}
        {inventory?.legacy_write_blocker ? <Banner tone="danger" text={inventory.legacy_write_blocker} /> : null}

        <div className="asset-filter-grid">
          <Filter label={t("resources.filterKind")} value={kindFilter} onChange={setKindFilter}>
            {kinds.map((value) => <option key={value} value={value}>{resourceKindLabel(value, t)}</option>)}
          </Filter>
          <Filter label={t("resources.filterStatus")} value={statusFilter} onChange={setStatusFilter}>
            {statuses.map((value) => <option key={value} value={value}>{value === "all" ? t("assets.allValues") : assetStatusLabel(value, t)}</option>)}
          </Filter>
          <Filter label={t("assets.filterLocal")} value={localFilter} onChange={setLocalFilter}>
            {localStatuses.map((value) => <option key={value} value={value}>{value === "all" ? t("assets.allValues") : localStatusLabel(value, t)}</option>)}
          </Filter>
          <Filter label={t("assets.filterRemote")} value={remoteFilter} onChange={setRemoteFilter}>
            {remoteStatuses.map((value) => <option key={value} value={value}>{value === "all" ? t("assets.allValues") : remoteStatusLabel(value, t)}</option>)}
          </Filter>
          <Filter label={t("assets.filterTool")} value={toolFilter} onChange={setToolFilter}>
            <option value="all">{t("assets.allValues")}</option>
            {tools.map((value) => <option key={value} value={value}>{value}</option>)}
          </Filter>
        </div>

        <div className="asset-selection-bar">
          <button className="secondary" onClick={selectVisible}>{t("assets.selectVisible")}</button>
          <button className="secondary" onClick={() => setSelectedKeys([])}>{t("assets.clearSelection")}</button>
          <span>{t("assets.selectedCount", { count: selectedKeys.length })}</span>
        </div>

        <div className="asset-table-wrap">
          <table className="asset-resource-table">
            <thead>
              <tr>
                <th aria-label={t("assets.selectedCount", { count: selectedKeys.length })} />
                <th>{t("assets.title")}</th>
                <th>{t("assets.descriptionColumn")}</th>
                <th>{t("assets.localColumn")}</th>
                <th>{t("assets.remoteColumn")}</th>
                <th>{t("assets.overallColumn")}</th>
              </tr>
            </thead>
            <tbody>
              {visible.map((resource) => (
                <tr
                  key={resource.resource_key}
                  className={selectedResource?.resource_key === resource.resource_key ? "active" : ""}
                  onClick={() => selectResource(resource)}
                >
                  <td>
                    <input
                      type="checkbox"
                      checked={selectedSet.has(resource.resource_key)}
                      onClick={(event) => event.stopPropagation()}
                      onChange={() => toggleSelected(resource.resource_key)}
                      aria-label={resource.name}
                    />
                  </td>
                  <td>
                    <div className="asset-resource-name">
                      <KindBadge kind={resource.kind} label={resourceKindLabel(resource.kind, t)} />
                      <span><strong>{resource.name}</strong><small>{resource.resource_key}</small></span>
                    </div>
                  </td>
                  <td className="asset-description-cell">{resource.description || "-"}</td>
                  <td><StatusPill value={resource.local_status} label={localStatusLabel(resource.local_status, t)} /></td>
                  <td><StatusPill value={resource.remote_status} label={remoteStatusLabel(resource.remote_status, t)} /></td>
                  <td><StatusPill value={resource.status} label={assetStatusLabel(resource.status, t)} /></td>
                </tr>
              ))}
            </tbody>
          </table>
          {!visible.length ? <EmptyState text={t("assets.empty")} /> : null}
        </div>
      </div>

      <ResourceDetail resource={selectedResource} t={t} onError={onError} />

      {batchDirection ? (
        <BatchDialog
          direction={batchDirection}
          resourceKeys={selectedKeys}
          inventory={inventory}
          t={t}
          onClose={() => setBatchDirection(null)}
          onOpenSettings={onOpenSettings}
          onDone={async () => {
            setBatchDirection(null);
            await Promise.resolve(onChanged());
          }}
        />
      ) : null}
    </section>
  );
}

function Filter<T extends string>({
  label,
  value,
  onChange,
  children,
}: {
  label: string;
  value: T;
  onChange: (value: T) => void;
  children: React.ReactNode;
}) {
  return (
    <label><span>{label}</span><select value={value} onChange={(event) => onChange(event.target.value as T)}>{children}</select></label>
  );
}

function ResourceDetail({
  resource,
  t,
  onError,
}: {
  resource?: AssetResourceRow;
  t: TFunction;
  onError: (message: string) => void;
}) {
  if (!resource) {
    return <aside className="panel asset-unified-detail"><EmptyState text={t("assets.noSelection")} /></aside>;
  }
  return (
    <aside className="panel asset-unified-detail">
      <div className="detail-title">
        <div className="detail-title-main">
          <KindBadge kind={resource.kind} label={resourceKindLabel(resource.kind, t)} />
          <h2>{resource.name}</h2>
        </div>
        <StatusPill value={resource.status} label={assetStatusLabel(resource.status, t)} />
      </div>
      <p className="asset-detail-description">{resource.description || "-"}</p>
      <dl className="description-list asset-description-list">
        <div><dt>{t("assets.resourceKey")}</dt><dd>{resource.resource_key}</dd></div>
        <div><dt>{t("assets.remoteCommit")}</dt><dd>{shortCommit(resource.remote.commit)}</dd></div>
        <div><dt>{t("assets.remotePath")}</dt><dd>{resource.remote.path || "-"}</dd></div>
        <div><dt>{t("assets.remoteColumn")}</dt><dd>{remoteStatusLabel(resource.remote_status, t)}</dd></div>
        <div><dt>{t("assets.localColumn")}</dt><dd>{localStatusLabel(resource.local_status, t)}</dd></div>
      </dl>
      {resource.metadata_differences.includes("description") ? (
        <div className="asset-description-compare">
          <div><strong>{t("assets.remoteDescription")}</strong><p>{resource.remote.description || "-"}</p></div>
          <div>
            <strong>{t("assets.localDescriptions")}</strong>
            {resource.local_instances.map((instance) => (
              <p key={instance.id}>{instance.platform}: {instance.description || "-"}</p>
            ))}
          </div>
        </div>
      ) : null}
      <div className="asset-detail-diff">
        <h3>{t("assets.diffPreview")}</h3>
        <ul>{resource.diff_summary.map((item) => <li key={item}>{item}</li>)}</ul>
        {resource.metadata_differences.length ? <p>{t("assets.metadataFields")}: {resource.metadata_differences.join(", ")}</p> : null}
      </div>
      <div className="asset-instance-list">
        {resource.local_instances.map((instance) => (
          <div className="asset-instance-card" key={instance.id}>
            <div><strong>{instance.platform}</strong><StatusPill value={instance.status} label={assetStatusLabel(instance.status, t)} /></div>
            <small>{instance.install_name}</small>
            <small>{instance.path || "-"}</small>
            <small>{instance.ownership}</small>
            {[...instance.warnings, ...instance.blockers].map((message) => (
              <small className="asset-instance-warning" key={message}>{message}</small>
            ))}
            {instance.path ? (
              <button className="secondary" onClick={() => void openPath(instance.path || "").catch((error) => onError(String(error)))}>
                <FolderOpen size={15} />{t("assets.open")}
              </button>
            ) : null}
          </div>
        ))}
        {!resource.local_instances.length ? <EmptyState text={localStatusLabel(resource.local_status, t)} /> : null}
      </div>
      {[...resource.warnings, ...resource.blockers].map((message) => <Banner key={message} tone="danger" text={message} />)}
    </aside>
  );
}

function BatchDialog({
  direction,
  resourceKeys,
  inventory,
  t,
  onClose,
  onOpenSettings,
  onDone,
}: {
  direction: "upload" | "download";
  resourceKeys: string[];
  inventory: AssetInventory | null;
  t: TFunction;
  onClose: () => void;
  onOpenSettings: () => void;
  onDone: () => Promise<void>;
}) {
  const { runTask } = useTaskCenter();
  const [platforms, setPlatforms] = useState<PlatformProfile[]>([]);
  const [targetPlatforms, setTargetPlatforms] = useState<string[]>([]);
  const [choices, setChoices] = useState<Record<string, AssetBatchChoice>>({});
  const [plan, setPlan] = useState<AssetBatchPlan | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (direction === "download") {
      void lpmAction<ConfigSettings>("config_get")
        .then((settings) => setPlatforms(settings.config.platforms))
        .catch((reason) => setError(String(reason)));
    } else {
      void createPlan();
    }
  }, []);

  function updateChoice(
    resourceKey: string,
    platform: string,
    changes: Partial<AssetBatchChoice>,
    slot = "",
  ) {
    const id = batchChoiceId(resourceKey, platform, slot);
    setChoices((current) => ({
      ...current,
      [id]: {
        ...current[id],
        resource_key: resourceKey,
        platform,
        resolution: current[id]?.resolution ?? "overwrite",
        ...changes,
      },
    }));
    setPlan(null);
  }

  function toggleSeparateVariants(resource: AssetResourceRow, enabled: boolean) {
    setChoices((current) => {
      const next = { ...current };
      Object.keys(next)
        .filter((key) => key.startsWith(`${resource.resource_key}||`))
        .forEach((key) => delete next[key]);
      if (enabled) {
        resource.local_instances.forEach((instance, index) => {
          next[batchChoiceId(resource.resource_key, "", instance.id)] = {
            resource_key: resource.resource_key,
            platform: "",
            local_instance_id: instance.id,
            resolution: "rename",
            new_name: `${resource.name}-${safeNamePart(instance.platform)}-${index + 1}`,
          };
        });
      }
      return next;
    });
    setPlan(null);
  }

  function payloadChoices(): AssetBatchChoice[] {
    return Object.values(choices);
  }

  async function createPlan() {
    if (direction === "download" && !targetPlatforms.length) return;
    setBusy(true);
    setError("");
    try {
      const next = await runTask({
        kind: `asset-batch-${direction}-plan`,
        title: t("assets.batchPlan"),
        action: () => lpmAction<AssetBatchPlan>("asset_batch_plan", {
          direction,
          resource_keys: resourceKeys,
          target_platforms: targetPlatforms,
          choices: payloadChoices(),
        }),
        successMessage: (value) => t("assets.batchPlanReady", {
          executable: value.executable_count,
          blocked: value.blocked_count,
          skipped: value.skipped_count,
        }),
        retryPolicy: "safe-read",
      });
      setPlan(next);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  }

  async function applyPlan() {
    if (!plan || !plan.executable_count || plan.blocked_count) return;
    setBusy(true);
    setError("");
    try {
      const result = await runTask({
        kind: `asset-batch-${direction}`,
        title: direction === "upload" ? t("assets.uploadSelected") : t("assets.downloadSelected"),
        action: () => lpmAction<AssetBatchResult>("asset_batch_apply", {
          direction,
          resource_keys: resourceKeys,
          target_platforms: targetPlatforms,
          choices: payloadChoices(),
          plan_hash: plan.plan_hash,
        }),
        successMessage: (value) => t("assets.batchComplete", { count: value.results.length }),
        retryPolicy: "none",
      });
      if (result.status === "stale-plan" && result.stale_plan) {
        setPlan(result.stale_plan);
        setError(t("assets.stalePlan"));
        return;
      }
      await onDone();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  }

  const selectedResources = resourceKeys
    .map((key) => inventory?.resources.find((item) => item.resource_key === key))
    .filter((item): item is AssetResourceRow => Boolean(item));
  const planGroups = plan
    ? (["create", "update", "rename", "unchanged", "skip", "blocked"] as const)
        .map((disposition) => ({
          disposition,
          items: plan.items.filter((item) => item.disposition === disposition),
        }))
        .filter((group) => group.items.length)
    : [];

  return (
    <div className="modal-backdrop" role="presentation">
      <div className="modal asset-batch-modal" role="dialog" aria-modal="true">
        <div className="modal-head">
          {direction === "upload" ? <Upload size={19} /> : <Download size={19} />}
          <h2>{direction === "upload" ? t("assets.uploadSelected") : t("assets.downloadSelected")}</h2>
          <button className="icon-button" onClick={onClose}><X size={17} /></button>
        </div>

        {direction === "download" ? (
          <div className="asset-platform-picker">
            <div><strong>{t("assets.targetTools")}</strong><small>{t("assets.targetToolsHint")}</small></div>
            {platforms.map((platform) => (
              <label key={platform.name} className={!platform.enabled ? "disabled" : ""}>
                <input
                  type="checkbox"
                  checked={targetPlatforms.includes(platform.name)}
                  disabled={!platform.enabled}
                  onChange={() => {
                    setTargetPlatforms((current) => current.includes(platform.name)
                      ? current.filter((item) => item !== platform.name)
                      : [...current, platform.name]);
                    setPlan(null);
                  }}
                />
                <span>{platform.name}</span>
                <small>{platform.enabled ? t("assets.enabled") : t("assets.disabled")}</small>
              </label>
            ))}
            {platforms.some((platform) => !platform.enabled) ? (
              <button className="secondary" onClick={onOpenSettings}><ExternalLink size={15} />{t("assets.goSettings")}</button>
            ) : null}
          </div>
        ) : null}

        <div className="asset-batch-choices">
          {selectedResources.map((resource) => (
            <BatchChoiceEditor
              key={resource.resource_key}
              resource={resource}
              direction={direction}
              platforms={targetPlatforms}
              choices={choices}
              t={t}
              onChange={updateChoice}
              onToggleSeparate={toggleSeparateVariants}
            />
          ))}
        </div>

        {plan ? (
          <div className="asset-plan-review">
            <p>{t("assets.batchPlanReady", {
              executable: plan.executable_count,
              blocked: plan.blocked_count,
              skipped: plan.skipped_count,
            })}</p>
            <div className="asset-plan-items">
              {planGroups.map((group) => (
                <section className="asset-plan-group" key={group.disposition}>
                  <h3>{batchDispositionLabel(group.disposition, t)} ({group.items.length})</h3>
                  {group.items.map((item) => (
                    <div key={item.id} className={`asset-plan-item disposition-${item.disposition}`}>
                      <strong>{item.resource_key}</strong>
                      <span>{item.platform || "-"}</span>
                      <span>{batchDispositionLabel(item.disposition, t)}</span>
                      <small>{item.reason || item.target_resource_key}</small>
                      {item.blockers.some((blocker) => blocker.toLowerCase().includes("unmanaged")) ? (
                        <label className="checkline">
                          <input
                            type="checkbox"
                            checked={Boolean(choices[batchChoiceId(item.resource_key, item.platform)]?.overwrite_unmanaged)}
                            onChange={(event) => updateChoice(item.resource_key, item.platform, { overwrite_unmanaged: event.target.checked })}
                          />
                          <span>{t("assets.confirmLocalReplace")}</span>
                        </label>
                      ) : null}
                    </div>
                  ))}
                </section>
              ))}
            </div>
          </div>
        ) : null}

        {error ? <Banner tone="danger" text={error} /> : null}
        <div className="modal-actions">
          <button className="secondary" onClick={onClose} disabled={busy}>{t("common.cancel")}</button>
          <button
            className="secondary"
            onClick={() => void createPlan()}
            disabled={busy || (direction === "download" && !targetPlatforms.length)}
          >
            {busy ? t("common.working") : plan ? t("assets.refreshPlan") : t("assets.createPlan")}
          </button>
          <button
            className="primary"
            onClick={() => void applyPlan()}
            disabled={busy || !plan || !plan.executable_count || Boolean(plan.blocked_count)}
          >
            {t("assets.applyBatch")}
          </button>
        </div>
      </div>
    </div>
  );
}

function BatchChoiceEditor({
  resource,
  direction,
  platforms,
  choices,
  t,
  onChange,
  onToggleSeparate,
}: {
  resource: AssetResourceRow;
  direction: "upload" | "download";
  platforms: string[];
  choices: Record<string, AssetBatchChoice>;
  t: TFunction;
  onChange: (
    resourceKey: string,
    platform: string,
    changes: Partial<AssetBatchChoice>,
    slot?: string,
  ) => void;
  onToggleSeparate: (resource: AssetResourceRow, enabled: boolean) => void;
}) {
  const targetRows = direction === "download" ? (platforms.length ? platforms : [""]) : [""];
  const canSeparate = direction === "upload" && resource.local_status === "variants";
  const separate = canSeparate && resource.local_instances.length > 1
    && resource.local_instances.every((instance) => (
      choices[batchChoiceId(resource.resource_key, "", instance.id)]?.resolution === "rename"
    ));
  return (
    <div className="asset-batch-choice-card">
      <div><KindBadge kind={resource.kind} label={resourceKindLabel(resource.kind, t)} /><strong>{resource.name}</strong></div>
      {canSeparate ? (
        <label className="checkline">
          <input
            type="checkbox"
            checked={separate}
            onChange={(event) => onToggleSeparate(resource, event.target.checked)}
          />
          <span>{t("assets.separateVariants")}</span>
        </label>
      ) : null}
      {separate ? resource.local_instances.map((instance) => {
        const id = batchChoiceId(resource.resource_key, "", instance.id);
        const choice = choices[id];
        return (
          <div className="asset-choice-fields" key={id}>
            <strong>{instance.platform} / {instance.install_name}</strong>
            <label>
              <span>{t("assets.newName")}</span>
              <input
                value={choice.new_name || ""}
                onChange={(event) => onChange(
                  resource.resource_key,
                  "",
                  { new_name: event.target.value, local_instance_id: instance.id, resolution: "rename" },
                  instance.id,
                )}
              />
            </label>
          </div>
        );
      }) : targetRows.map((platform) => {
        const id = batchChoiceId(resource.resource_key, platform);
        const choice = choices[id] ?? { resource_key: resource.resource_key, platform, resolution: "overwrite" as const };
        return (
          <div className="asset-choice-fields" key={id}>
            {platform ? <strong>{platform}</strong> : null}
            {direction === "upload" && resource.local_instances.length > 1 ? (
              <label>
                <span>{t("assets.sourceInstance")}</span>
                <select
                  value={choice.local_instance_id || ""}
                  onChange={(event) => onChange(resource.resource_key, platform, { local_instance_id: event.target.value })}
                >
                  <option value="">{resource.local_status === "variants" ? "-" : resource.local_instances[0]?.platform || "-"}</option>
                  {resource.local_instances.map((instance) => (
                    <option value={instance.id} key={instance.id}>{instance.platform} / {instance.install_name}</option>
                  ))}
                </select>
              </label>
            ) : null}
            <label>
              <span>{t("assets.resolution")}</span>
              <select
                value={choice.resolution || "overwrite"}
                onChange={(event) => onChange(resource.resource_key, platform, { resolution: event.target.value as "overwrite" | "rename" })}
              >
                <option value="overwrite">{t("assets.overwrite")}</option>
                <option value="rename">{t("assets.rename")}</option>
              </select>
            </label>
            {choice.resolution === "rename" ? (
              <label>
                <span>{t("assets.newName")}</span>
                <input value={choice.new_name || ""} onChange={(event) => onChange(resource.resource_key, platform, { new_name: event.target.value })} />
              </label>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}

function StatusPill({ value, label }: { value: string; label: string }) {
  return <span className={`asset-status status-${value}`}>{label}</span>;
}

function batchChoiceId(resourceKey: string, platform = "", slot = ""): string {
  return `${resourceKey}|${platform}|${slot}`;
}

function safeNamePart(value: string): string {
  return value.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "local";
}

function shortCommit(commit?: string | null): string {
  return commit ? commit.slice(0, 12) : "-";
}

function assetStatusLabel(status: AssetStatus, t: TFunction): string {
  return t(`assets.status.${status}` as Parameters<TFunction>[0]);
}

function localStatusLabel(status: AssetLocalStatus, t: TFunction): string {
  return t(`assets.localStatus.${status}` as Parameters<TFunction>[0]);
}

function remoteStatusLabel(status: AssetRemoteStatus, t: TFunction): string {
  return t(`assets.remoteStatus.${status}` as Parameters<TFunction>[0]);
}

function batchDispositionLabel(value: AssetBatchPlan["items"][number]["disposition"], t: TFunction): string {
  const key = {
    create: "assets.batchCreate",
    update: "assets.batchUpdate",
    rename: "assets.batchRename",
    unchanged: "assets.batchUnchanged",
    skip: "assets.batchSkip",
    blocked: "assets.batchBlocked",
  }[value];
  return t(key as Parameters<TFunction>[0]);
}
