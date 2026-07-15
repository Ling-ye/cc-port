import { Activity, Languages, RefreshCcw } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
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
import { AboutView } from "@/features/about/AboutView";
import { AddResourceView } from "@/features/add/AddResourceView";
import { DashboardView } from "@/features/dashboard/DashboardView";
import { EnvironmentView } from "@/features/environment/EnvironmentView";
import { GuideView } from "@/features/guide/GuideView";
import { HealthView } from "@/features/health/HealthView";
import { ResourcesView } from "@/features/resources/ResourcesView";
import { SettingsView } from "@/features/settings/SettingsView";
import type { PlatformProfile, RegistryItem, ResourceInventoryResult, ResourceInventoryItem, Summary } from "@/types/lpm";

export default function App() {
  const { runTask, runningCount } = useTaskCenter();
  const [view, setView] = useState<View>("dashboard");
  const [language, setLanguage] = useState<Language>(() => readStoredLanguage());
  const [summary, setSummary] = useState<Summary | null>(null);
  const [items, setItems] = useState<RegistryItem[]>([]);
  const [resourceItems, setResourceItems] = useState<ResourceInventoryItem[]>([]);
  const [platforms, setPlatforms] = useState<PlatformProfile[]>([]);
  const [selectedName, setSelectedName] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [taskPanelOpen, setTaskPanelOpen] = useState(false);
  const t = useMemo(() => createTranslator(language), [language]);

  async function loadData() {
    const [nextSummary, itemData, platformData] = await Promise.all([
      lpmAction<Summary>("summary"),
      lpmAction<ResourceInventoryResult>("resource_inventory"),
      lpmAction<{ platforms: PlatformProfile[] }>("platforms"),
    ]);
    setSummary(nextSummary);
    setResourceItems(itemData.items);
    setItems(itemData.items.map((item) => item.entry));
    setPlatforms(platformData.platforms);
    setSelectedName((current) => current || itemData.items[0]?.entry.name || "");
  }

  async function refresh(track = true) {
    setBusy(true);
    setError("");
    try {
      if (track) {
        await runTask({
          kind: "refresh",
          title: t("common.refresh"),
          action: loadData,
          retryPolicy: "safe-read",
        });
      } else {
        await loadData();
      }
    } catch (err) {
      if (!track) setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    void refresh(false);
  }, []);

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

  const selected = resourceItems.find((item) => item.entry.name === selectedName) || resourceItems[0];

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
                onClick={() => setView(item.id)}
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
          summary={summary}
          busy={busy}
          language={language}
          runningCount={runningCount}
          t={t}
          taskPanelOpen={taskPanelOpen}
          onRefresh={() => void refresh()}
          onTasks={() => setTaskPanelOpen((current) => !current)}
          onToggleLanguage={toggleLanguage}
        />
        {error ? <Banner tone="danger" text={error} /> : null}

        {view === "dashboard" ? (
          <DashboardView summary={summary} items={items} t={t} />
        ) : null}
        {view === "resources" ? (
          <ResourcesView
            items={resourceItems}
            platforms={platforms}
            selected={selected}
            t={t}
            onSelect={setSelectedName}
            onChanged={() => refresh(false)}
            onError={setError}
          />
        ) : null}
        {view === "environment" ? (
          <EnvironmentView
            t={t}
            onChanged={() => refresh(false)}
          />
        ) : null}
        {view === "add" ? (
          <AddResourceView
            t={t}
            onChanged={() => refresh(false)}
            onError={setError}
          />
        ) : null}
        {view === "health" ? <HealthView t={t} /> : null}
        {view === "settings" ? (
          <SettingsView
            t={t}
            onError={setError}
            onChanged={() => refresh(false)}
          />
        ) : null}
        {view === "guide" ? <GuideView t={t} /> : null}
        {view === "about" ? <AboutView t={t} /> : null}
      </main>
      <TaskCenterPanel open={taskPanelOpen} t={t} onClose={() => setTaskPanelOpen(false)} />
      <ToastViewport t={t} />
    </div>
  );
}

function Topbar({
  summary,
  busy,
  language,
  runningCount,
  t,
  taskPanelOpen,
  onRefresh,
  onTasks,
  onToggleLanguage,
}: {
  summary: Summary | null;
  busy: boolean;
  language: Language;
  runningCount: number;
  t: TFunction;
  taskPanelOpen: boolean;
  onRefresh: () => void;
  onTasks: () => void;
  onToggleLanguage: () => void;
}) {
  const languageTitle = language === "zh" ? t("topbar.switchToEnglish") : t("topbar.switchToChinese");

  return (
    <header className="topbar">
      <div>
        <h1>{summary?.resource_repo_display_name || t("settings.notConfigured")}</h1>
        <p>{summary?.registry_path || t("topbar.loadingConfig")}</p>
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
        <button className="icon-button" onClick={onRefresh} disabled={busy} title={t("common.refresh")}>
          <RefreshCcw size={17} />
        </button>
      </div>
    </header>
  );
}
