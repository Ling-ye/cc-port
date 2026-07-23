import { Activity, Languages } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { lpmAction } from "@/api/client";
import {
  createTranslator,
  nextLanguage,
  readStoredLanguage,
  storeLanguage,
  type Language,
  type TFunction,
} from "@/app/i18n";
import { navItems, type View } from "@/app/navigation";
import { useTaskCenter } from "@/app/TaskCenterContext";
import { Banner } from "@/components/Banner";
import { TaskCenterPanel, ToastViewport } from "@/components/TaskFeedback";
import { GuideView } from "@/features/guide/GuideView";
import { ResourcesView } from "@/features/resources/ResourcesView";
import type { ScanScope } from "@/features/resources/ScanLocalDialog";
import { SettingsView } from "@/features/settings/SettingsView";
import type { AssetInventory } from "@/types/lpm";

export default function App() {
  const { runTask, runningCount } = useTaskCenter();
  const [view, setView] = useState<View>("resources");
  const [language, setLanguage] = useState<Language>(() => readStoredLanguage());
  const [assetInventory, setAssetInventory] = useState<AssetInventory | null>(null);
  const [scanScope, setScanScope] = useState<ScanScope | null>(null);
  const [remoteCheckedAt, setRemoteCheckedAt] = useState<string | null>(null);
  const [localScannedAt, setLocalScannedAt] = useState<string | null>(null);
  const [selectedResourceKey, setSelectedResourceKey] = useState<string>("");
  const [refreshBusy, setRefreshBusy] = useState(false);
  const [error, setError] = useState("");
  const [taskPanelOpen, setTaskPanelOpen] = useState(false);
  const [settingsRefreshVersion, setSettingsRefreshVersion] = useState(0);
  const startupRequestedRef = useRef(false);
  const refreshInFlightRef = useRef(false);
  const t = useMemo(() => createTranslator(language), [language]);

  function applyInventory(inventory: AssetInventory) {
    setAssetInventory(inventory);
    setSelectedResourceKey((current) => (
      inventory.resources.some((resource) => resource.resource_key === current)
        ? current
        : inventory.resources[0]
          ? inventory.resources[0].resource_key
          : ""
    ));
  }

  async function loadInventory(scope: ScanScope | null) {
    const inventory = await lpmAction<AssetInventory>("asset_inventory", {
      scan_local: scope !== null,
      ...(scope || {}),
      refresh_remote: true,
    });
    applyInventory(inventory);
    const completedAt = new Date().toISOString();
    setRemoteCheckedAt(completedAt);
    if (scope) setLocalScannedAt(completedAt);
    return inventory;
  }

  async function refreshRemote(track = true, scope = scanScope) {
    if (refreshInFlightRef.current) return;
    refreshInFlightRef.current = true;
    setRefreshBusy(true);
    setError("");
    const previousCommit = assetInventory?.remote_commit || "";
    try {
      if (track) {
        await runTask({
          kind: "asset-refresh-remote",
          title: t("assets.refreshRemote"),
          action: () => loadInventory(scope),
          successMessage: (inventory) => {
            if (!inventory.remote_available) return t("assets.remoteCacheFallback");
            return previousCommit && previousCommit === inventory.remote_commit
              ? t("assets.remoteUpToDate")
              : t("assets.remoteUpdated");
          },
          retryPolicy: "safe-read",
        });
      } else {
        await loadInventory(scope);
      }
    } catch (err) {
      if (!track) setError(err instanceof Error ? err.message : String(err));
    } finally {
      refreshInFlightRef.current = false;
      setRefreshBusy(false);
    }
  }

  useEffect(() => {
    if (startupRequestedRef.current) return;
    startupRequestedRef.current = true;
    void refreshRemote(false, null);
  }, []);

  function handleLocalScanned(inventory: AssetInventory, scope: ScanScope) {
    applyInventory(assetInventory ? {
      ...inventory,
      remote_available: assetInventory.remote_available,
      remote_warning: assetInventory.remote_warning,
    } : inventory);
    setScanScope({
      scan_global: scope.scan_global,
      project_ids: [...scope.project_ids],
    });
    setLocalScannedAt(new Date().toISOString());
  }

  async function refreshAfterChange() {
    await refreshRemote(false, scanScope);
  }

  useEffect(() => {
    document.documentElement.lang = language === "zh" ? "zh-CN" : "en";
  }, [language]);

  function toggleLanguage() {
    setLanguage((current) => {
      const next = nextLanguage(current);
      storeLanguage(next);
      return next;
    });
  }

  function navigateTo(nextView: View) {
    if (nextView === "settings") {
      setSettingsRefreshVersion((current) => current + 1);
    }
    setView(nextView);
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">L</div>
          <div>
            <strong>LPM</strong>
            <span>{t("brand.subtitle")}</span>
          </div>
        </div>
        <nav>
          {navItems.map((item) => {
            const Icon = item.icon;
            return (
              <button
                key={item.id}
                className={view === item.id ? "nav-item active" : "nav-item"}
                onClick={() => navigateTo(item.id)}
                title={t(item.labelKey)}
              >
                <Icon size={18} />
                <span>{t(item.labelKey)}</span>
              </button>
            );
          })}
        </nav>
      </aside>

      <main className="workspace">
        <Topbar
          view={view}
          language={language}
          runningCount={runningCount}
          t={t}
          taskPanelOpen={taskPanelOpen}
          onTasks={() => setTaskPanelOpen((current) => !current)}
          onToggleLanguage={toggleLanguage}
        />
        {error ? <Banner tone="danger" text={error} /> : null}

        {view === "resources" ? (
          <ResourcesView
            inventory={assetInventory}
            selectedKey={selectedResourceKey}
            t={t}
            refreshBusy={refreshBusy}
            remoteCheckedAt={remoteCheckedAt}
            localScannedAt={localScannedAt}
            onSelect={setSelectedResourceKey}
            onRefreshRemote={() => refreshRemote(true)}
            onLocalScanned={handleLocalScanned}
            onChanged={refreshAfterChange}
            onError={setError}
            onOpenSettings={() => setView("settings")}
          />
        ) : null}
        {view === "settings" ? (
          <SettingsView
            t={t}
            refreshVersion={settingsRefreshVersion}
            onError={setError}
            onChanged={refreshAfterChange}
          />
        ) : null}
        {view === "guide" ? <GuideView t={t} /> : null}
      </main>
      <TaskCenterPanel open={taskPanelOpen} t={t} onClose={() => setTaskPanelOpen(false)} />
      <ToastViewport t={t} />
    </div>
  );
}

function Topbar({
  view,
  language,
  runningCount,
  t,
  taskPanelOpen,
  onTasks,
  onToggleLanguage,
}: {
  view: View;
  language: Language;
  runningCount: number;
  t: TFunction;
  taskPanelOpen: boolean;
  onTasks: () => void;
  onToggleLanguage: () => void;
}) {
  const languageTitle = language === "zh" ? t("topbar.switchToEnglish") : t("topbar.switchToChinese");
  const activeItem = navItems.find((item) => item.id === view) || navItems[0];

  return (
    <header className="topbar">
      <div>
        <h1>{t(activeItem.labelKey)}</h1>
      </div>
      <div className="topbar-actions">
        <button
          className="icon-button language-toggle"
          onClick={onToggleLanguage}
          title={languageTitle}
          aria-label={languageTitle}
        >
          <Languages size={17} />
          <span>{language === "zh" ? "EN" : "中"}</span>
        </button>
        <button
          className="icon-button task-center-trigger"
          type="button"
          onClick={onTasks}
          title={t("topbar.taskCenter")}
          aria-label={t("topbar.taskCenter")}
          aria-expanded={taskPanelOpen}
        >
          <Activity size={17} />
          {runningCount ? <span className="task-running-badge">{runningCount}</span> : null}
        </button>
      </div>
    </header>
  );
}
