import { Activity, Languages } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { lpmAction } from "@/api/client";
import {
  createTranslator,
  displayError,
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
import type { AssetInventory, DiagnosticsState, DoctorCheck } from "@/types/lpm";

type RemoteInventoryStatus = Pick<
  AssetInventory,
  "remote_available" | "remote_warning" | "remote_warning_ref"
>;

interface RemoteRefreshOutcome {
  inventory: AssetInventory;
  previousCommit: string;
}

function copyScanScope(scope: ScanScope | null): ScanScope | null {
  return scope ? {
    scan_global: scope.scan_global,
    project_ids: [...scope.project_ids],
  } : null;
}

export default function App() {
  const { runTask, runningCount } = useTaskCenter();
  const [view, setView] = useState<View>("resources");
  const [language, setLanguage] = useState<Language>(() => readStoredLanguage());
  const [assetInventory, setAssetInventory] = useState<AssetInventory | null>(null);
  const [remoteCheckedAt, setRemoteCheckedAt] = useState<string | null>(null);
  const [localScannedAt, setLocalScannedAt] = useState<string | null>(null);
  const [selectedResourceKey, setSelectedResourceKey] = useState<string>("");
  const [remoteRefreshBusy, setRemoteRefreshBusy] = useState(false);
  const [localScanBusy, setLocalScanBusy] = useState(false);
  const [error, setError] = useState("");
  const [taskPanelOpen, setTaskPanelOpen] = useState(false);
  const [settingsRefreshVersion, setSettingsRefreshVersion] = useState(0);
  const [diagnostics, setDiagnostics] = useState<DiagnosticsState>({
    phase: "idle",
    checks: null,
    error: "",
  });
  const startupRequestedRef = useRef(false);
  const assetInventoryRef = useRef<AssetInventory | null>(null);
  const scanScopeRef = useRef<ScanScope | null>(null);
  const remoteStatusRef = useRef<RemoteInventoryStatus | null>(null);
  const localCompletionVersionRef = useRef(0);
  const remoteRefreshPromiseRef = useRef<Promise<RemoteRefreshOutcome> | null>(null);
  const localScanPromiseRef = useRef<Promise<AssetInventory> | null>(null);
  const diagnosticsPromiseRef = useRef<Promise<void> | null>(null);
  const pendingRemoteRefreshRef = useRef(false);
  const t = useMemo(() => createTranslator(language), [language]);

  function applyInventory(inventory: AssetInventory) {
    assetInventoryRef.current = inventory;
    setAssetInventory(inventory);
    setSelectedResourceKey((current) => (
      inventory.resources.some((resource) => resource.resource_key === current)
        ? current
        : inventory.resources[0]
          ? inventory.resources[0].resource_key
          : ""
    ));
  }

  function rememberRemoteStatus(inventory: AssetInventory) {
    remoteStatusRef.current = {
      remote_available: inventory.remote_available,
      remote_warning: inventory.remote_warning,
      remote_warning_ref: inventory.remote_warning_ref,
    };
  }

  function withLatestRemoteStatus(inventory: AssetInventory): AssetInventory {
    return remoteStatusRef.current
      ? { ...inventory, ...remoteStatusRef.current }
      : inventory;
  }

  async function requestInventory(scope: ScanScope | null, refreshRemote: boolean) {
    return lpmAction<AssetInventory>("asset_inventory", {
      scan_local: scope !== null,
      ...(scope || {}),
      refresh_remote: refreshRemote,
    });
  }

  async function reconcileLatestLocal(
    remoteInventory: AssetInventory,
    expectedLocalVersion: number,
  ): Promise<AssetInventory> {
    let version = expectedLocalVersion;
    let scope = copyScanScope(scanScopeRef.current);
    let inventory = remoteInventory;

    while (version !== localCompletionVersionRef.current) {
      version = localCompletionVersionRef.current;
      scope = copyScanScope(scanScopeRef.current);
      inventory = scope
        ? await requestInventory(scope, false)
        : remoteInventory;
    }
    return withLatestRemoteStatus(inventory);
  }

  function performRemoteRefresh(): Promise<RemoteRefreshOutcome> {
    const inFlight = remoteRefreshPromiseRef.current;
    if (inFlight) return inFlight;

    setRemoteRefreshBusy(true);
    setError("");
    const operation = (async () => {
      const previousCommit = assetInventoryRef.current?.remote_commit || "";
      const localVersion = localCompletionVersionRef.current;
      const scope = copyScanScope(scanScopeRef.current);
      const remoteInventory = await requestInventory(scope, true);
      rememberRemoteStatus(remoteInventory);
      const inventory = await reconcileLatestLocal(remoteInventory, localVersion);
      applyInventory(inventory);
      setRemoteCheckedAt(new Date().toISOString());
      return { inventory, previousCommit };
    })();
    remoteRefreshPromiseRef.current = operation;
    void operation.finally(() => {
      if (remoteRefreshPromiseRef.current !== operation) return;
      remoteRefreshPromiseRef.current = null;
      setRemoteRefreshBusy(false);
      if (pendingRemoteRefreshRef.current) {
        pendingRemoteRefreshRef.current = false;
        void refreshRemote(false);
      }
    }).catch(() => {
      // The original caller owns error handling; this only handles the finalizer chain.
    });
    return operation;
  }

  async function refreshRemote(track = true) {
    if (track) {
      try {
        await runTask({
          kind: "asset-refresh-remote",
          title: t("assets.refreshRemote"),
          action: performRemoteRefresh,
          successMessage: ({ inventory, previousCommit }) => {
            if (!inventory.remote_available) return t("assets.remoteCacheFallback");
            return previousCommit && previousCommit === inventory.remote_commit
              ? t("assets.remoteUpToDate")
              : t("assets.remoteUpdated");
          },
          failureMessage: (error) => displayError(error, t),
          retryPolicy: "safe-read",
        });
      } catch {
        // Task center owns tracked failures.
      }
      return;
    }
    try {
      await performRemoteRefresh();
    } catch (err) {
      setError(displayError(err, t));
    }
  }

  function runDiagnostics(): Promise<void> {
    const inFlight = diagnosticsPromiseRef.current;
    if (inFlight) return inFlight;

    setDiagnostics({ phase: "running", checks: null, error: "" });
    const operation = (async () => {
      try {
        const result = await lpmAction<{ checks: DoctorCheck[] }>("doctor");
        const hasIssues = result.checks.some(
          (check) => check.status === "warning" || check.status === "error",
        );
        setDiagnostics({
          phase: hasIssues ? "issues" : "healthy",
          checks: result.checks,
          error: "",
        });
      } catch (diagnosticsError) {
        setDiagnostics({
          phase: "failed",
          checks: null,
          error: displayError(diagnosticsError, t),
        });
      }
    })();
    diagnosticsPromiseRef.current = operation;
    void operation.finally(() => {
      if (diagnosticsPromiseRef.current === operation) {
        diagnosticsPromiseRef.current = null;
      }
    });
    return operation;
  }

  useEffect(() => {
    if (startupRequestedRef.current) return;
    startupRequestedRef.current = true;
    void refreshRemote(false);
    void runDiagnostics();
  }, []);

  function performLocalScan(scope: ScanScope): Promise<AssetInventory> {
    const inFlight = localScanPromiseRef.current;
    if (inFlight) return inFlight;

    setLocalScanBusy(true);
    setError("");
    const operation = (async () => {
      const inventory = await requestInventory(scope, false);
      scanScopeRef.current = copyScanScope(scope);
      localCompletionVersionRef.current += 1;
      const integrated = withLatestRemoteStatus(inventory);
      applyInventory(integrated);
      setLocalScannedAt(new Date().toISOString());
      return integrated;
    })();
    localScanPromiseRef.current = operation;
    void operation.finally(() => {
      if (localScanPromiseRef.current !== operation) return;
      localScanPromiseRef.current = null;
      setLocalScanBusy(false);
    }).catch(() => {
      // The original caller owns error handling; this only handles the finalizer chain.
    });
    return operation;
  }

  async function scanLocal(scope: ScanScope) {
    const requestedScope = copyScanScope(scope) as ScanScope;
    try {
      await runTask({
        kind: "asset-scan-local",
        title: t("assets.scanLocal"),
        action: () => performLocalScan(requestedScope),
        successMessage: t("assets.scanComplete"),
        failureMessage: (scanError) => displayError(scanError, t),
        retryPolicy: "safe-read",
      });
    } catch {
      // Task center owns tracked failures.
    }
  }

  async function refreshAfterChange() {
    if (remoteRefreshPromiseRef.current) {
      pendingRemoteRefreshRef.current = true;
      return;
    }
    await refreshRemote(false);
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
            remoteRefreshBusy={remoteRefreshBusy}
            localScanBusy={localScanBusy}
            remoteCheckedAt={remoteCheckedAt}
            localScannedAt={localScannedAt}
            onSelect={setSelectedResourceKey}
            onRefreshRemote={() => refreshRemote(true)}
            onScanLocal={scanLocal}
            onChanged={refreshAfterChange}
            onError={setError}
            onOpenSettings={() => setView("settings")}
          />
        ) : null}
        {view === "settings" ? (
          <SettingsView
            t={t}
            refreshVersion={settingsRefreshVersion}
            diagnostics={diagnostics}
            onError={setError}
            onChanged={refreshAfterChange}
            onRunDiagnostics={runDiagnostics}
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
