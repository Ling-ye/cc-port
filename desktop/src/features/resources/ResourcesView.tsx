import {
  Cloud,
  Download,
  ExternalLink,
  FolderInput,
  FolderOpen,
  Github,
  Monitor,
  PackagePlus,
  RefreshCcw,
  Search,
  SlidersHorizontal,
  Upload,
  Trash2,
  X,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { lpmAction, openPath } from "@/api/client";
import {
  displayError,
  resourceKindLabel,
  translateMessage,
  translateMessageList,
  type TFunction,
} from "@/app/i18n";
import { useTaskCenter } from "@/app/TaskCenterContext";
import { Banner } from "@/components/Banner";
import { EmptyState } from "@/components/EmptyState";
import { KindBadge } from "@/components/KindBadge";
import { CollectGithubDialog } from "@/features/resources/CollectGithubDialog";
import { ImportLocalDialog } from "@/features/resources/ImportLocalDialog";
import { ScanLocalDialog, type ScanScope } from "@/features/resources/ScanLocalDialog";
import { PluginDeleteDialog } from "@/features/resources/PluginDeleteDialog";
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
const emptyResources: AssetResourceRow[] = [];

export function ResourcesView({
  inventory,
  selectedKey,
  t,
  onSelect,
  remoteRefreshBusy,
  localScanBusy,
  remoteCheckedAt,
  localScannedAt,
  onRefreshRemote,
  onScanLocal,
  onChanged,
  onError,
  onOpenSettings,
}: {
  inventory: AssetInventory | null;
  selectedKey?: string;
  t: TFunction;
  onSelect: (rowId: string) => void;
  remoteRefreshBusy: boolean;
  localScanBusy: boolean;
  remoteCheckedAt: string | null;
  localScannedAt: string | null;
  onRefreshRemote: () => Promise<void> | void;
  onScanLocal: (scope: ScanScope) => Promise<void> | void;
  onChanged: () => Promise<void> | void;
  onError: (message: string) => void;
  onOpenSettings: () => void;
}) {
  const [kindFilter, setKindFilter] = useState<(typeof kinds)[number]>("all");
  const [statusFilter, setStatusFilter] = useState<(typeof statuses)[number]>("all");
  const [localFilter, setLocalFilter] = useState<(typeof localStatuses)[number]>("all");
  const [remoteFilter, setRemoteFilter] = useState<(typeof remoteStatuses)[number]>("all");
  const [toolFilter, setToolFilter] = useState("all");
  const [query, setQuery] = useState("");
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [selectedKeys, setSelectedKeys] = useState<string[]>([]);
  const [batchDirection, setBatchDirection] = useState<"upload" | "download" | null>(null);
  const [entryDialog, setEntryDialog] = useState<"collect" | "import" | null>(null);
  const [scanDialogOpen, setScanDialogOpen] = useState(false);
  const selectVisibleRef = useRef<HTMLInputElement>(null);

  const resources = inventory?.resources ?? emptyResources;
  const tools = useMemo(
    () => Array.from(new Set(resources.flatMap((item) => item.local_instances.map((instance) => instance.platform)))).sort(),
    [resources],
  );
  const visible = useMemo(
    () => {
      const normalizedQuery = query.trim().toLocaleLowerCase();
      return resources.filter((item) => (
        (!normalizedQuery || [item.name, item.resource_key, item.description]
          .some((value) => value.toLocaleLowerCase().includes(normalizedQuery)))
        && (kindFilter === "all" || item.kind === kindFilter)
        && (statusFilter === "all" || item.status === statusFilter)
        && (localFilter === "all" || item.local_status === localFilter)
        && (remoteFilter === "all" || item.remote_status === remoteFilter)
        && (toolFilter === "all" || item.local_instances.some((instance) => instance.platform === toolFilter))
      ));
    },
    [kindFilter, localFilter, query, remoteFilter, resources, statusFilter, toolFilter],
  );
  const selectedSet = useMemo(() => new Set(selectedKeys), [selectedKeys]);
  const visibleKeySet = useMemo(() => new Set(visible.map((item) => item.resource_key)), [visible]);
  const visibleSelectedCount = visible.reduce(
    (count, item) => count + (selectedSet.has(item.resource_key) ? 1 : 0),
    0,
  );
  const allVisibleSelected = visible.length > 0 && visibleSelectedCount === visible.length;
  const hiddenSelectedCount = selectedKeys.length - visibleSelectedCount;
  const activeFilterCount = [kindFilter, statusFilter, localFilter, remoteFilter, toolFilter]
    .filter((value) => value !== "all").length + (query.trim() ? 1 : 0);
  const localInstanceCount = resources.reduce((count, item) => count + item.local_instances.length, 0);
  const repoConfigured = Boolean(inventory?.repo_url);
  const inventoryBusy = remoteRefreshBusy || localScanBusy;
  const legacyWriteBlocker = translateMessage(
    inventory?.legacy_write_blocker_ref,
    t,
    inventory?.legacy_write_blocker || "",
  );
  const remoteWarning = translateMessage(
    inventory?.remote_warning_ref,
    t,
    inventory?.remote_warning || "",
  );
  const entryBlocker = inventoryBusy
    ? t("add.refreshInProgress")
    : !inventory
      ? t("add.repositoryLoading")
      : !repoConfigured
        ? t("add.repositoryRequired")
        : legacyWriteBlocker
          ? legacyWriteBlocker
          : !inventory.remote_available
            ? t("add.remoteUnavailable")
            : "";
  const selectedResource = resources.find((item) => item.resource_key === selectedKey)
    ?? resources[0];

  useEffect(() => {
    const valid = new Set(resources.map((item) => item.resource_key));
    setSelectedKeys((current) => {
      const next = current.filter((key) => valid.has(key));
      return next.length === current.length ? current : next;
    });
  }, [resources]);

  useEffect(() => {
    if (selectVisibleRef.current) {
      selectVisibleRef.current.indeterminate = visibleSelectedCount > 0 && !allVisibleSelected;
    }
  }, [allVisibleSelected, visibleSelectedCount]);

  function toggleSelected(resourceKey: string) {
    setSelectedKeys((current) => current.includes(resourceKey)
      ? current.filter((item) => item !== resourceKey)
      : [...current, resourceKey]);
  }

  function toggleVisibleSelection() {
    setSelectedKeys((current) => {
      if (allVisibleSelected) return current.filter((key) => !visibleKeySet.has(key));
      return Array.from(new Set([...current, ...visible.map((item) => item.resource_key)]));
    });
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

  async function handleResourceAdded(resourceKey: string) {
    setEntryDialog(null);
    setKindFilter("all");
    setStatusFilter("all");
    setLocalFilter("all");
    setRemoteFilter("all");
    setToolFilter("all");
    setQuery("");
    setAdvancedOpen(false);
    setSelectedKeys([]);
    onSelect(resourceKey);
    await Promise.resolve(onChanged());
  }

  return (
    <section className="asset-unified-view">
      <div className="panel asset-inventory-panel">
        <div className="asset-source-strip" aria-label={t("assets.sourceStatus")}>
          <section className="asset-source-card asset-remote-source-card">
            <div className="asset-source-card-head">
              <span className="asset-source-title"><Cloud size={17} />{t("assets.remoteSource")}</span>
              <span className={`asset-pill asset-source-state state-${repoConfigured ? (inventory?.remote_available ? "online" : "cache") : "unconfigured"}`}>
                {repoConfigured
                  ? (inventory?.remote_available ? t("assets.remoteOnline") : t("assets.remoteCache"))
                  : t("assets.remoteNotConfigured")}
              </span>
            </div>
            <dl className="asset-source-metadata">
              <div className="asset-source-repository"><dt>{t("assets.repository")}</dt><dd title={inventory?.repo_url || "-"}>{inventory?.repo_url || "-"}</dd></div>
              <div><dt>{t("assets.branch")}</dt><dd>{inventory?.branch || "-"}</dd></div>
              <div><dt>{t("assets.commit")}</dt><dd>{shortCommit(inventory?.remote_commit)}</dd></div>
              <div><dt>{t("assets.lastChecked")}</dt><dd>{formatTimestamp(remoteCheckedAt, t)}</dd></div>
            </dl>
            {repoConfigured ? (
              <button className="secondary" type="button" onClick={() => void Promise.resolve(onRefreshRemote())} disabled={remoteRefreshBusy}>
                <RefreshCcw size={15} className={remoteRefreshBusy ? "spin" : undefined} />{t("assets.refreshRemote")}
              </button>
            ) : (
              <button className="secondary" type="button" onClick={onOpenSettings} disabled={inventoryBusy}>
                {t("assets.configureRepository")}
              </button>
            )}
          </section>
          <section className="asset-source-card asset-local-source-card">
            <div className="asset-source-card-head">
              <span className="asset-source-title"><Monitor size={17} />{t("assets.localSource")}</span>
              <span className={`asset-pill asset-source-state state-${inventory?.scanned_local ? "online" : "unconfigured"}`}>
                {inventory?.scanned_local ? t("assets.localScanned") : t("assets.localNotScanned")}
              </span>
            </div>
            <div className="asset-local-summary">
              {inventory?.scanned_local
                ? t("assets.localSummary", { tools: tools.length, instances: localInstanceCount })
                : t("assets.localScanHint")}
            </div>
            <div className="asset-source-time">
              <span>{t("assets.lastScanned")}</span>
              <span>{formatTimestamp(localScannedAt, t)}</span>
            </div>
            <button className="secondary" type="button" onClick={() => setScanDialogOpen(true)} disabled={localScanBusy}>
              <RefreshCcw size={15} className={localScanBusy ? "spin" : undefined} />{t("assets.scanLocal")}
            </button>
          </section>
        </div>

        <section className="asset-entry-strip" aria-labelledby="asset-entry-title">
          <div className="asset-entry-copy">
            <PackagePlus size={19} />
            <div>
              <h3 id="asset-entry-title">{t("add.collectionTitle")}</h3>
              <p>{t("add.collectionDescription")}</p>
              {entryBlocker ? <small id="asset-entry-blocker">{entryBlocker}</small> : null}
            </div>
          </div>
          <div className="asset-entry-actions">
            <button
              className="primary"
              type="button"
              onClick={() => setEntryDialog("collect")}
              disabled={Boolean(entryBlocker)}
              aria-describedby={entryBlocker ? "asset-entry-blocker" : undefined}
            >
              <Github size={16} />{t("add.modeCollect")}
            </button>
            <button
              className="secondary"
              type="button"
              onClick={() => setEntryDialog("import")}
              disabled={Boolean(entryBlocker)}
              aria-describedby={entryBlocker ? "asset-entry-blocker" : undefined}
            >
              <FolderInput size={16} />{t("add.modeImport")}
            </button>
            {!inventoryBusy && inventory && !repoConfigured ? (
              <button className="secondary asset-entry-recovery" type="button" onClick={onOpenSettings}>
                {t("assets.configureRepository")}
              </button>
            ) : null}
            {!remoteRefreshBusy && inventory && repoConfigured && !inventory.remote_available ? (
              <button
                className="secondary asset-entry-recovery"
                type="button"
                onClick={() => void Promise.resolve(onRefreshRemote())}
              >
                <RefreshCcw size={15} />{t("assets.refreshRemote")}
              </button>
            ) : null}
          </div>
        </section>

        {remoteWarning ? <Banner tone="danger" text={remoteWarning} /> : null}
        {legacyWriteBlocker ? <Banner tone="danger" text={legacyWriteBlocker} /> : null}

        <div className="asset-filter-toolbar">
          <label className="asset-search-field">
            <span>{t("assets.searchLabel")}</span>
            <div><Search size={15} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder={t("assets.searchPlaceholder")} /></div>
          </label>
          <Filter label={t("resources.filterKind")} value={kindFilter} onChange={setKindFilter}>
            {kinds.map((value) => <option key={value} value={value}>{resourceKindLabel(value, t)}</option>)}
          </Filter>
          <Filter label={t("resources.filterStatus")} value={statusFilter} onChange={setStatusFilter}>
            {statuses.map((value) => <option key={value} value={value}>{value === "all" ? t("assets.allValues") : assetStatusLabel(value, t)}</option>)}
          </Filter>
          <button
            className="secondary asset-more-filters"
            type="button"
            aria-expanded={advancedOpen}
            aria-controls="asset-advanced-filters"
            onClick={() => setAdvancedOpen((current) => !current)}
          >
            <SlidersHorizontal size={15} />{t("assets.moreFilters")}
            {activeFilterCount ? <span className="asset-filter-count">{activeFilterCount}</span> : null}
          </button>
        </div>

        {advancedOpen ? (
          <div className="asset-filter-grid" id="asset-advanced-filters">
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
            <button
              className="secondary asset-clear-filters"
              type="button"
              disabled={!activeFilterCount}
              onClick={() => {
                setQuery("");
                setKindFilter("all");
                setStatusFilter("all");
                setLocalFilter("all");
                setRemoteFilter("all");
                setToolFilter("all");
              }}
            >
              {t("assets.clearFilters")}
            </button>
          </div>
        ) : null}

        {selectedKeys.length ? (
          <div className="asset-selection-bar" role="toolbar" aria-label={t("assets.selectionActions")}>
            <span>{t("assets.selectedCount", { count: selectedKeys.length })}</span>
            {hiddenSelectedCount ? <span>{t("assets.hiddenSelected", { count: hiddenSelectedCount })}</span> : null}
            <div className="asset-selection-actions">
              <button className="secondary" onClick={() => openBatch("upload")} disabled={inventoryBusy}>
                <Upload size={16} />{t("assets.uploadToRepository")}
              </button>
              <button className="primary" onClick={() => openBatch("download")} disabled={inventoryBusy}>
                <Download size={16} />{t("assets.installToTools")}
              </button>
              <button className="secondary" onClick={() => setSelectedKeys([])} disabled={inventoryBusy}>{t("assets.clearSelection")}</button>
            </div>
          </div>
        ) : null}

        <div className="asset-table-wrap">
          <table className="asset-resource-table">
            <colgroup>
              <col className="asset-select-column" />
              <col className="asset-name-column" />
              <col className="asset-description-column" />
              <col className="asset-status-column" />
            </colgroup>
            <thead>
              <tr>
                <th>
                  <input
                    ref={selectVisibleRef}
                    type="checkbox"
                    checked={allVisibleSelected}
                    disabled={!visible.length}
                    onChange={toggleVisibleSelection}
                    aria-label={t("assets.selectVisible")}
                  />
                </th>
                <th>{t("assets.title")}</th>
                <th>{t("assets.descriptionColumn")}</th>
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
                  <td className="asset-description-cell" title={resource.description || "-"}>
                    <span>{resource.description || "-"}</span>
                  </td>
                  <td><StatusPill value={resource.status} label={assetStatusLabel(resource.status, t)} /></td>
                </tr>
              ))}
            </tbody>
          </table>
          {!visible.length ? <EmptyState text={t("assets.empty")} /> : null}
        </div>
      </div>

      <ResourceDetail
        resource={selectedResource}
        refreshBusy={inventoryBusy}
        t={t}
        onError={onError}
        onChanged={onChanged}
      />

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

      {entryDialog === "collect" ? (
        <CollectGithubDialog
          t={t}
          onClose={() => setEntryDialog(null)}
          onAdded={handleResourceAdded}
        />
      ) : null}

      {entryDialog === "import" ? (
        <ImportLocalDialog
          t={t}
          onClose={() => setEntryDialog(null)}
          onAdded={handleResourceAdded}
        />
      ) : null}

      {scanDialogOpen ? (
        <ScanLocalDialog
          t={t}
          onClose={() => setScanDialogOpen(false)}
          onScan={(scope) => {
            setScanDialogOpen(false);
            void Promise.resolve(onScanLocal(scope));
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
  children: ReactNode;
}) {
  return (
    <label><span>{label}</span><select value={value} onChange={(event) => onChange(event.target.value as T)}>{children}</select></label>
  );
}

function ResourceDetail({
  resource,
  refreshBusy,
  t,
  onError,
  onChanged,
}: {
  resource?: AssetResourceRow;
  refreshBusy: boolean;
  t: TFunction;
  onError: (message: string) => void;
  onChanged: () => Promise<void> | void;
}) {
  const [deleteOpen, setDeleteOpen] = useState(false);
  if (!resource) {
    return <aside className="panel asset-unified-detail"><EmptyState text={t("assets.noSelection")} /></aside>;
  }
  return (
    <aside className="panel asset-unified-detail">
      <header className="asset-detail-header">
        <div className="detail-title">
          <div className="detail-title-main">
            <KindBadge kind={resource.kind} label={resourceKindLabel(resource.kind, t)} />
            <h2>{resource.name}</h2>
          </div>
          <StatusPill value={resource.status} label={assetStatusLabel(resource.status, t)} />
        </div>
        <p className="asset-detail-description">{resource.description || "-"}</p>
      </header>

      {[
        ...translateMessageList(resource.warning_refs, resource.warnings, t),
        ...translateMessageList(resource.blocker_refs, resource.blockers, t),
      ].map((message) => <Banner key={message} tone="danger" text={message} />)}

      <section className="asset-detail-section asset-detail-diff">
        <h3>{t("assets.diffPreview")}</h3>
        <ul>
          {translateMessageList(resource.diff_summary_refs, resource.diff_summary, t)
            .map((item) => <li key={item}>{item}</li>)}
        </ul>
        {resource.metadata_differences.length ? <p>{t("assets.metadataFields")}: {resource.metadata_differences.join(", ")}</p> : null}
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
      </section>

      <section className="asset-detail-section asset-source-sync-section">
        <h3>{t("assets.sourceAndSync")}</h3>
        <dl className="description-list asset-description-list">
          <div><dt>{t("assets.resourceKey")}</dt><dd>{resource.resource_key}</dd></div>
          <div><dt>{t("assets.remoteCommit")}</dt><dd>{shortCommit(resource.remote.commit)}</dd></div>
          <div><dt>{t("assets.remotePath")}</dt><dd>{resource.remote.path || "-"}</dd></div>
          <div><dt>{t("assets.remoteColumn")}</dt><dd>{remoteStatusLabel(resource.remote_status, t)}</dd></div>
          <div><dt>{t("assets.localColumn")}</dt><dd>{localStatusLabel(resource.local_status, t)}</dd></div>
        </dl>
        {resource.kind === "plugin" ? (
          <dl className="description-list asset-description-list plugin-description-list">
            <div><dt>{t("plugin.track")}</dt><dd>{resource.plugin_track === "content" ? t("plugin.trackContent") : t("plugin.trackReference")}</dd></div>
            <div><dt>{t("plugin.platform")}</dt><dd>{resource.plugin_platform || "-"}</dd></div>
            <div><dt>{t("plugin.originType")}</dt><dd>{resource.plugin_source_kind || "-"}</dd></div>
            <div><dt>{t("plugin.source")}</dt><dd>{resource.plugin_source_id || "-"}</dd></div>
            <div><dt>{t("plugin.selector")}</dt><dd>{resource.plugin_selector || t("plugin.floating")}</dd></div>
            <div><dt>{t("plugin.observedVersion")}</dt><dd>{resource.plugin_observed_version || "-"}</dd></div>
          </dl>
        ) : null}
      </section>

      <section className="asset-detail-section asset-local-instances-section">
        <h3>{t("assets.localInstances")}</h3>
        <div className="asset-instance-list">
          {resource.local_instances.map((instance) => (
            <div className="asset-instance-card" key={instance.id}>
              <div><strong>{instance.platform}</strong><StatusPill value={instance.status} label={assetStatusLabel(instance.status, t)} /></div>
              <small>{instance.install_name}</small>
              <small>{instance.path || "-"}</small>
              <small>{instance.ownership}</small>
              {resource.kind === "plugin" ? (
                <>
                  <small>{instance.track === "content" ? t("plugin.trackContent") : t("plugin.trackReference")} / {instance.scope || "-"}</small>
                  <small>{instance.source_kind || "-"}: {instance.source_id || "-"}</small>
                  <small>{instance.selector || t("plugin.floating")} · {instance.observed_version || "-"}</small>
                </>
              ) : null}
              {[
                ...translateMessageList(instance.warning_refs, instance.warnings, t),
                ...translateMessageList(instance.blocker_refs, instance.blockers, t),
              ].map((message) => (
                <small className="asset-instance-warning" key={message}>{message}</small>
              ))}
              {instance.path ? (
                <button className="secondary" onClick={() => void openPath(instance.path || "").catch((error) => onError(displayError(error, t)))}>
                  <FolderOpen size={15} />{t("assets.open")}
                </button>
              ) : null}
            </div>
          ))}
          {!resource.local_instances.length ? <div className="compact-empty"><EmptyState text={localStatusLabel(resource.local_status, t)} /></div> : null}
        </div>
      </section>

      {resource.kind === "plugin" && resource.remote.exists ? (
        <section className="asset-detail-section asset-danger-zone">
          <h3>{t("assets.dangerZone")}</h3>
          <button className="danger" type="button" onClick={() => setDeleteOpen(true)} disabled={refreshBusy}>
            <Trash2 size={15} />{t("plugin.delete")}
          </button>
        </section>
      ) : null}

      {deleteOpen ? (
        <PluginDeleteDialog
          resourceKey={resource.resource_key}
          t={t}
          onClose={() => setDeleteOpen(false)}
          onDone={async () => {
            setDeleteOpen(false);
            await Promise.resolve(onChanged());
          }}
        />
      ) : null}
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
      .catch((reason) => setError(displayError(reason, t)));
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
        failureMessage: (error) => displayError(error, t),
        retryPolicy: "safe-read",
      });
      setPlan(next);
    } catch (reason) {
      setError(displayError(reason, t));
    } finally {
      setBusy(false);
    }
  }

  async function applyPlan() {
    const hasManual = Boolean(plan?.items.some((item) => item.disposition === "manual"));
    if (!plan || (!plan.executable_count && !hasManual) || plan.blocked_count) return;
    setBusy(true);
    setError("");
    try {
      const result = await runTask({
        kind: `asset-batch-${direction}`,
        title: direction === "upload" ? t("assets.uploadToRepository") : t("assets.installToTools"),
        action: () => lpmAction<AssetBatchResult>("asset_batch_apply", {
          direction,
          resource_keys: resourceKeys,
          target_platforms: targetPlatforms,
          choices: payloadChoices(),
          plan_hash: plan.plan_hash,
        }),
        successMessage: (value) => t("assets.batchComplete", { count: value.results.length }),
        failureMessage: (error) => displayError(error, t),
        retryPolicy: "none",
      });
      if (result.status === "stale-plan" && result.stale_plan) {
        setPlan(result.stale_plan);
        setError(t("assets.stalePlan"));
        return;
      }
      if (result.status === "needs-action" || result.status === "partial") {
        setError(result.results
          .filter((item) => item.status === "needs-action")
          .map((item) => translateMessage(item.message_ref, t, item.message))
          .join("; "));
        return;
      }
      await onDone();
    } catch (reason) {
      setError(displayError(reason, t));
    } finally {
      setBusy(false);
    }
  }

  const selectedResources = resourceKeys
    .map((key) => inventory?.resources.find((item) => item.resource_key === key))
    .filter((item): item is AssetResourceRow => Boolean(item));
  const planGroups = plan
    ? (["create", "update", "rename", "unchanged", "skip", "manual", "blocked"] as const)
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
          <h2>{direction === "upload" ? t("assets.uploadToRepository") : t("assets.installToTools")}</h2>
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
                      <small>{translateMessage(item.reason_ref, t, item.reason) || item.target_resource_key}</small>
                      {item.plan?.target_exists && !item.plan.target_managed ? (
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
            disabled={busy || !plan || (!plan.executable_count && !plan.items.some((item) => item.disposition === "manual")) || Boolean(plan.blocked_count)}
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
            {direction === "upload" && resource.kind === "plugin" && !resource.remote.exists && resource.plugin_track === "content" ? (
              <div className="plugin-ownership-choice">
                <label>
                  <span>{t("plugin.firstUploadChoice")}</span>
                  <select
                    value={choice.plugin_track || ""}
                    onChange={(event) => onChange(resource.resource_key, platform, {
                      plugin_track: event.target.value as AssetBatchChoice["plugin_track"],
                      ownership_confirmed: false,
                    })}
                  >
                    <option value="">-</option>
                    <option value="content">{t("plugin.confirmOwnedSource")}</option>
                    <option value="reference">{t("plugin.saveAsReference")}</option>
                    <option value="skip">{t("plugin.skip")}</option>
                  </select>
                </label>
                {choice.plugin_track === "content" ? (
                  <>
                    <label className="checkline">
                      <input type="checkbox" checked={Boolean(choice.ownership_confirmed)} onChange={(event) => onChange(resource.resource_key, platform, { ownership_confirmed: event.target.checked })} />
                      <span>{t("plugin.ownershipConfirmation")}</span>
                    </label>
                    {resource.plugin_platform === "opencode" ? (
                      <label>
                        <span>{t("plugin.dependencies")}</span>
                        <textarea
                          value={dependencyText(choice.plugin_dependencies)}
                          placeholder="package=^1.0.0"
                          onChange={(event) => onChange(resource.resource_key, platform, { plugin_dependencies: parseDependencies(event.target.value) })}
                        />
                      </label>
                    ) : null}
                  </>
                ) : null}
                {choice.plugin_track === "reference" ? (
                  <div className="plugin-reference-origin-inline">
                    <label>
                      <span>{t("plugin.originType")}</span>
                      <select value={choice.reference_origin?.type || "marketplace"} onChange={(event) => onChange(resource.resource_key, platform, { reference_origin: { ...choice.reference_origin, type: event.target.value } })}>
                        <option value="marketplace">marketplace</option><option value="npm">npm</option><option value="git">Git</option>
                      </select>
                    </label>
                    <label><span>{t("plugin.source")}</span><input value={referenceOriginValue(choice)} onChange={(event) => onChange(resource.resource_key, platform, { reference_origin: updateReferenceOrigin(choice.reference_origin, event.target.value) })} /></label>
                    <label><span>{t("plugin.selector")}</span><input value={choice.reference_origin?.selector || ""} onChange={(event) => onChange(resource.resource_key, platform, { reference_origin: { ...choice.reference_origin, selector: event.target.value } })} /></label>
                  </div>
                ) : null}
              </div>
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
  return <span className={`asset-pill asset-status status-${value}`}>{label}</span>;
}

function referenceOriginValue(choice: AssetBatchChoice): string {
  const origin = choice.reference_origin ?? {};
  if (origin.type === "npm") return origin.package ?? "";
  if (origin.type === "git") return origin.repo ?? "";
  return origin.marketplace ?? "";
}

function updateReferenceOrigin(
  current: Record<string, string> | undefined,
  value: string,
): Record<string, string> {
  const origin: Record<string, string> = { ...current, type: current?.type || "marketplace" };
  if (origin.type === "npm") origin.package = value;
  else if (origin.type === "git") origin.repo = value;
  else origin.marketplace = value;
  return origin;
}

function dependencyText(dependencies?: Record<string, string>): string {
  return Object.entries(dependencies ?? {}).map(([name, selector]) => `${name}=${selector}`).join("\n");
}

function parseDependencies(value: string): Record<string, string> {
  return Object.fromEntries(value.split(/\r?\n/).map((line) => line.trim()).filter(Boolean).map((line) => {
    const marker = line.indexOf("=");
    return marker > 0 ? [line.slice(0, marker).trim(), line.slice(marker + 1).trim()] : [line, ""];
  }).filter(([, selector]) => Boolean(selector)));
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

function formatTimestamp(value: string | null, t: TFunction): string {
  if (!value) return t("assets.never");
  const timestamp = new Date(value);
  return Number.isNaN(timestamp.getTime()) ? value : timestamp.toLocaleString();
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
    manual: "assets.batchManual",
    blocked: "assets.batchBlocked",
  }[value];
  return t(key as Parameters<TFunction>[0]);
}
