import { useMemo, useState } from "react";
import { CheckCircle2, FolderSync, Search } from "lucide-react";
import { lpmAction } from "@/api/client";
import { resourceKindLabel, type TFunction } from "@/app/i18n";
import { KindBadge } from "@/components/KindBadge";
import type {
  PlatformProfile,
  ResourceKind,
  SyncPreviewItem,
  SyncPreviewResult,
  SyncResultItem,
} from "@/types/lpm";

const kinds: Array<"all" | ResourceKind> = ["all", "skill", "mcp", "rule", "prompt", "plugin"];

export function SyncView({
  platforms,
  t,
  onDone,
  onError,
}: {
  platforms: PlatformProfile[];
  t: TFunction;
  onDone: (message: string) => void;
  onError: (message: string) => void;
}) {
  const [kind, setKind] = useState<"all" | ResourceKind>("all");
  const [platform, setPlatform] = useState("");
  const [busy, setBusy] = useState(false);
  const [preview, setPreview] = useState<SyncPreviewResult | null>(null);
  const [selectedNames, setSelectedNames] = useState<string[]>([]);
  const [results, setResults] = useState<SyncResultItem[]>([]);
  const [previewSummary, setPreviewSummary] = useState("");

  const enabledPlatforms = useMemo(() => platforms.filter((item) => item.enabled), [platforms]);
  const previewItems = preview?.items || [];
  const selectedSet = useMemo(() => new Set(selectedNames), [selectedNames]);
  const selectableItems = useMemo(() => previewItems.filter((item) => !item.blocked), [previewItems]);
  const groupedItems = useMemo(
    () =>
      kinds
        .filter((item): item is ResourceKind => item !== "all")
        .map((item) => [item, previewItems.filter((previewItem) => previewItem.kind === item)] as const)
        .filter(([, items]) => items.length > 0),
    [previewItems],
  );

  function resetPreview() {
    setPreview(null);
    setSelectedNames([]);
    setResults([]);
    setPreviewSummary("");
  }

  function buildSyncPayload(only?: string[]) {
    return {
      all_kinds: kind === "all" ? true : undefined,
      kind: kind === "all" ? undefined : kind,
      platform: platform || undefined,
      only,
    };
  }

  async function loadPreview() {
    if (!enabledPlatforms.length) {
      onError(t("sync.noEnabledPlatforms"));
      return;
    }

    setBusy(true);
    setResults([]);
    setPreviewSummary("");
    try {
      const data = await lpmAction<SyncPreviewResult>("sync_preview", buildSyncPayload());
      setPreview(data);
      setSelectedNames(data.items.filter((item) => !item.blocked).map((item) => item.name));
      setPreviewSummary(t("sync.previewReady", { count: data.items.length }));
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function syncSelected() {
    if (!selectedNames.length) {
      onError(t("sync.noSelectable"));
      return;
    }

    setBusy(true);
    try {
      const data = await lpmAction<{ results: SyncResultItem[] }>("sync", buildSyncPayload(selectedNames));
      setResults(data.results);
      onDone(t("sync.success", { count: data.results.length }));
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  function toggleItem(item: SyncPreviewItem) {
    if (item.blocked) return;
    setSelectedNames((current) =>
      current.includes(item.name) ? current.filter((name) => name !== item.name) : [...current, item.name],
    );
  }

  function selectAllAvailable() {
    setSelectedNames(selectableItems.map((item) => item.name));
  }

  return (
    <section className="panel form-panel sync-panel">
      <div className="panel-head">
        <div>
          <h2>{t("sync.title")}</h2>
          <p>{t("sync.description")}</p>
        </div>
      </div>
      <div className="stack-form">
        <div className="stack-form two-column">
          <label>
            <span>{t("sync.resourceType")}</span>
            <select
              value={kind}
              onChange={(event) => {
                setKind(event.target.value as "all" | ResourceKind);
                resetPreview();
              }}
            >
              {kinds.map((item) => (
                <option key={item} value={item}>{resourceKindLabel(item, t)}</option>
              ))}
            </select>
          </label>
          <label>
            <span>{t("sync.targetPlatform")}</span>
            <select
              value={platform}
              onChange={(event) => {
                setPlatform(event.target.value);
                resetPreview();
              }}
              disabled={!enabledPlatforms.length}
            >
              <option value="">{t("sync.allEnabledPlatforms")}</option>
              {enabledPlatforms.map((item) => (
                <option key={item.name} value={item.name}>{item.name}</option>
              ))}
            </select>
          </label>
        </div>

        {!enabledPlatforms.length ? <p className="discovery-warning">{t("sync.noEnabledPlatforms")}</p> : null}

        <div className="discovery-actions">
          <button className="primary" onClick={loadPreview} disabled={busy || !enabledPlatforms.length}>
            <Search size={17} />
            {busy && !preview ? t("sync.previewing") : t("sync.preview")}
          </button>
          {previewSummary ? <span>{previewSummary}</span> : null}
        </div>

        {preview ? (
          <>
            <div className="discovery-actions">
              <button className="secondary" onClick={selectAllAvailable} disabled={!selectableItems.length}>
                <CheckCircle2 size={17} />
                {t("sync.selectAll")}
              </button>
              <button className="secondary" onClick={() => setSelectedNames([])}>
                {t("sync.clearSelection")}
              </button>
              <span>{t("sync.selectedCount", { count: selectedNames.length })}</span>
            </div>

            {previewItems.length ? (
              <div className="sync-preview-list">
                {groupedItems.map(([groupKind, items]) => (
                  <div key={groupKind} className="sync-preview-group">
                    <h3>{resourceKindLabel(groupKind, t)}</h3>
                    <div className="discovery-list">
                      {items.map((item) => (
                        <div key={item.name} className={item.blocked ? "discovery-row sync-row blocked" : "discovery-row sync-row"}>
                          <input
                            type="checkbox"
                            checked={selectedSet.has(item.name)}
                            onChange={() => toggleItem(item)}
                            disabled={item.blocked}
                            aria-label={item.name}
                          />
                          <KindBadge kind={item.kind} label={resourceKindLabel(item.kind, t)} />
                          <div className="discovery-main">
                            <strong>{item.name}</strong>
                            <small>{item.source} / {t("sync.plannedAction")}: {item.planned_action}</small>
                            <small>{t("sync.installPath")}: {item.install_path}</small>
                            <small>
                              {t("sync.targetPaths")}:{" "}
                              {item.target_paths.length ? item.target_paths.join(", ") : t("sync.noTargetPaths")}
                            </small>
                            {item.blocked ? <p className="discovery-warning">{t("sync.blocked")}</p> : null}
                            {item.warnings.length ? <p className="discovery-warning">{item.warnings.join(" ")}</p> : null}
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <p className="empty">{t("sync.noPreview")}</p>
            )}

            <button className="primary" onClick={syncSelected} disabled={busy || selectedNames.length === 0}>
              <FolderSync size={17} />
              {busy && preview ? t("sync.syncing") : t("sync.confirm")}
            </button>
          </>
        ) : null}

        {results.length ? (
          <div className="sync-results">
            <h3>{t("sync.results")}</h3>
            <div className="discovery-list">
              {results.map((item) => (
                <div key={item.name} className="sync-result-row">
                  <strong>{item.name}</strong>
                  <span>{item.action}</span>
                  <small>{item.install_path}</small>
                  <small>{item.platforms_installed.join(", ") || "-"}</small>
                  {item.detail ? <p className="discovery-warning">{item.detail}</p> : null}
                </div>
              ))}
            </div>
          </div>
        ) : null}
      </div>
    </section>
  );
}
