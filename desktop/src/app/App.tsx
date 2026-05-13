import { RefreshCcw } from "lucide-react";
import { useEffect, useState } from "react";
import { lpmAction } from "@/api/client";
import { navItems, type View } from "@/app/navigation";
import { Banner } from "@/components/Banner";
import { StatusDot } from "@/components/StatusDot";
import { AddResourceView } from "@/features/add/AddResourceView";
import { DashboardView } from "@/features/dashboard/DashboardView";
import { HealthView } from "@/features/health/HealthView";
import { PlatformsView } from "@/features/platforms/PlatformsView";
import { ResourcesView } from "@/features/resources/ResourcesView";
import { SettingsView } from "@/features/settings/SettingsView";
import { SyncView } from "@/features/sync/SyncView";
import type { PlatformProfile, RegistryItem, Summary } from "@/types/lpm";

export default function App() {
  const [view, setView] = useState<View>("dashboard");
  const [summary, setSummary] = useState<Summary | null>(null);
  const [items, setItems] = useState<RegistryItem[]>([]);
  const [platforms, setPlatforms] = useState<PlatformProfile[]>([]);
  const [selectedName, setSelectedName] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  async function refresh() {
    setBusy(true);
    setError("");
    try {
      const [nextSummary, itemData, platformData] = await Promise.all([
        lpmAction<Summary>("summary"),
        lpmAction<{ items: RegistryItem[] }>("list_items"),
        lpmAction<{ platforms: PlatformProfile[] }>("platforms"),
      ]);
      setSummary(nextSummary);
      setItems(itemData.items);
      setPlatforms(platformData.platforms);
      setSelectedName((current) => current || itemData.items[0]?.name || "");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    void refresh();
  }, []);

  const selected = items.find((item) => item.name === selectedName) || items[0];

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">L</div>
          <div>
            <strong>LPM</strong>
            <span>Desktop</span>
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
                title={item.label}
              >
                <Icon size={18} />
                <span>{item.label}</span>
              </button>
            );
          })}
        </nav>
      </aside>

      <main className="workspace">
        <Topbar summary={summary} busy={busy} onRefresh={refresh} />
        {error ? <Banner tone="danger" text={error} /> : null}
        {message ? <Banner tone="success" text={message} /> : null}

        {view === "dashboard" ? (
          <DashboardView summary={summary} items={items} onNavigate={setView} />
        ) : null}
        {view === "resources" ? (
          <ResourcesView items={items} selected={selected} onSelect={setSelectedName} onChanged={refresh} />
        ) : null}
        {view === "add" ? (
          <AddResourceView
            onDone={async (text) => {
              setMessage(text);
              await refresh();
            }}
            onError={setError}
          />
        ) : null}
        {view === "sync" ? (
          <SyncView
            platforms={platforms}
            onDone={async (text) => {
              setMessage(text);
              await refresh();
            }}
            onError={setError}
          />
        ) : null}
        {view === "health" ? <HealthView onError={setError} /> : null}
        {view === "platforms" ? <PlatformsView platforms={platforms} /> : null}
        {view === "settings" ? (
          <SettingsView
            onDone={setMessage}
            onError={setError}
            onChanged={refresh}
          />
        ) : null}
      </main>
    </div>
  );
}

function Topbar({ summary, busy, onRefresh }: { summary: Summary | null; busy: boolean; onRefresh: () => void }) {
  return (
    <header className="topbar">
      <div>
        <h1>{summary?.resource_repo.repo_name || "LingyePluginMarketplace"}</h1>
        <p>{summary?.registry_path || "Reading local configuration"}</p>
      </div>
      <div className="status-strip">
        <StatusDot ok={Boolean(summary?.config.exists)} label="Config" />
        <StatusDot ok={Boolean(summary?.config.github.token_configured)} label="GitHub" />
        <StatusDot ok={Boolean(summary?.resource_repo.is_git_repo)} label="Resource Repo" />
        <button className="icon-button" onClick={onRefresh} disabled={busy} title="Refresh">
          <RefreshCcw size={17} />
        </button>
      </div>
    </header>
  );
}
