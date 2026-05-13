import {
  Activity,
  CheckCircle2,
  Database,
  FolderSync,
  GitBranch,
  HardDrive,
  HeartPulse,
  PackagePlus,
  RefreshCcw,
  Search,
  Settings,
  TerminalSquare,
  Upload,
  XCircle,
} from "lucide-react";
import { FormEvent, useEffect, useMemo, useState } from "react";
import { lpmAction } from "./api";
import type { DoctorCheck, PlatformProfile, RegistryItem, ResourceKind, Summary } from "./types";

type View = "dashboard" | "resources" | "add" | "sync" | "health" | "platforms";

const kinds: Array<"all" | ResourceKind> = ["all", "skill", "mcp", "rule", "prompt", "plugin"];

const nav = [
  { id: "dashboard", label: "总览", icon: Activity },
  { id: "resources", label: "资源库", icon: Database },
  { id: "add", label: "添加资源", icon: PackagePlus },
  { id: "sync", label: "同步", icon: FolderSync },
  { id: "health", label: "检查", icon: HeartPulse },
  { id: "platforms", label: "平台", icon: Settings },
] satisfies Array<{ id: View; label: string; icon: typeof Activity }>;

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
          {nav.map((item) => {
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
          <Dashboard summary={summary} items={items} onNavigate={setView} />
        ) : null}
        {view === "resources" ? (
          <Resources items={items} selected={selected} onSelect={setSelectedName} onChanged={refresh} />
        ) : null}
        {view === "add" ? (
          <AddResource
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
      </main>
    </div>
  );
}

function Topbar({ summary, busy, onRefresh }: { summary: Summary | null; busy: boolean; onRefresh: () => void }) {
  return (
    <header className="topbar">
      <div>
        <h1>{summary?.resource_repo.repo_name || "LingyePluginMarketplace"}</h1>
        <p>{summary?.registry_path || "正在读取本地配置"}</p>
      </div>
      <div className="status-strip">
        <StatusDot ok={Boolean(summary?.config.exists)} label="Config" />
        <StatusDot ok={Boolean(summary?.config.github.token_configured)} label="GitHub" />
        <StatusDot ok={Boolean(summary?.resource_repo.is_git_repo)} label="Resource Repo" />
        <button className="icon-button" onClick={onRefresh} disabled={busy} title="刷新">
          <RefreshCcw size={17} />
        </button>
      </div>
    </header>
  );
}

function Dashboard({
  summary,
  items,
  onNavigate,
}: {
  summary: Summary | null;
  items: RegistryItem[];
  onNavigate: (view: View) => void;
}) {
  return (
    <section className="view-grid">
      <div className="metrics">
        <Metric label="资源总数" value={summary?.counts.total ?? 0} />
        <Metric label="已安装" value={summary?.installed ?? 0} />
        <Metric label="可更新" value={summary?.updates ?? 0} />
        <Metric label="平台" value={Object.keys(summary?.counts.by_source || {}).length} />
      </div>

      <div className="panel wide">
        <div className="panel-head">
          <div>
            <h2>日常操作</h2>
            <p>围绕资源收集、上传、同步和健康检查。</p>
          </div>
        </div>
        <div className="quick-actions">
          <button onClick={() => onNavigate("add")}><PackagePlus size={18} />添加资源</button>
          <button onClick={() => onNavigate("sync")}><FolderSync size={18} />同步安装</button>
          <button onClick={() => onNavigate("health")}><HeartPulse size={18} />环境检查</button>
          <button onClick={() => onNavigate("resources")}><Search size={18} />查看资源</button>
        </div>
      </div>

      <div className="panel">
        <div className="panel-head">
          <h2>资源类型</h2>
        </div>
        <KindBars counts={summary?.counts.by_kind || {}} />
      </div>

      <div className="panel">
        <div className="panel-head">
          <h2>最近资源</h2>
        </div>
        <div className="compact-list">
          {items.slice(0, 6).map((item) => (
            <div key={item.name} className="compact-row">
              <span className={`kind kind-${item.kind}`}>{item.kind}</span>
              <strong>{item.name}</strong>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

function Resources({
  items,
  selected,
  onSelect,
  onChanged,
}: {
  items: RegistryItem[];
  selected?: RegistryItem;
  onSelect: (name: string) => void;
  onChanged: () => void;
}) {
  const [filter, setFilter] = useState<(typeof kinds)[number]>("all");
  const visible = useMemo(
    () => items.filter((item) => filter === "all" || item.kind === filter),
    [filter, items],
  );

  async function removeSelected(uninstall: boolean) {
    if (!selected) return;
    if (!confirm(`确认移除 ${selected.name}？`)) return;
    await lpmAction("remove", { name: selected.name, uninstall });
    await onChanged();
  }

  return (
    <section className="split-view">
      <div className="panel list-panel">
        <div className="toolbar">
          <Segmented value={filter} values={kinds} onChange={setFilter} />
        </div>
        <div className="resource-list">
          {visible.map((item) => (
            <button
              key={item.name}
              className={selected?.name === item.name ? "resource-row active" : "resource-row"}
              onClick={() => onSelect(item.name)}
            >
              <span className={`kind kind-${item.kind}`}>{item.kind}</span>
              <span>
                <strong>{item.name}</strong>
                <small>{item.source} · {item.status?.installed ? "installed" : "not installed"}</small>
              </span>
            </button>
          ))}
        </div>
      </div>
      <div className="panel detail-panel">
        {selected ? (
          <>
            <div className="detail-title">
              <span className={`kind kind-${selected.kind}`}>{selected.kind}</span>
              <h2>{selected.name}</h2>
            </div>
            <DescriptionList
              rows={[
                ["来源", selected.source],
                ["Repo", selected.repo || "-"],
                ["Path", selected.path || "-"],
                ["Ref", selected.ref || "-"],
                ["Subdir", selected.subdir || "-"],
                ["安装路径", selected.status?.install_path || "-"],
                ["安装状态", selected.status?.installed ? "已安装" : "未安装"],
              ]}
            />
            <div className="tag-row">
              {selected.tags?.map((tag) => <span key={tag}>{tag}</span>)}
            </div>
            <div className="danger-row">
              <button className="secondary" onClick={() => removeSelected(false)}>只移除记录</button>
              <button className="danger" onClick={() => removeSelected(true)}>移除并卸载</button>
            </div>
          </>
        ) : (
          <Empty text="暂无资源" />
        )}
      </div>
    </section>
  );
}

function AddResource({
  onDone,
  onError,
}: {
  onDone: (message: string) => void;
  onError: (message: string) => void;
}) {
  const [mode, setMode] = useState<"collect" | "upload">("collect");
  const [value, setValue] = useState("");
  const [kind, setKind] = useState<"auto" | ResourceKind>("auto");
  const [name, setName] = useState("");
  const [push, setPush] = useState(false);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    try {
      const payload = { [mode === "collect" ? "github_url" : "path"]: value, kind: kind === "auto" ? undefined : kind, name, push };
      await lpmAction(mode, payload);
      onDone(mode === "collect" ? "资源已收集" : "资源已上传");
      setValue("");
      setName("");
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="panel form-panel">
      <div className="panel-head">
        <div>
          <h2>添加资源</h2>
          <p>通过 GitHub URL 记录外部资源，或上传本地资源到私有资源仓库。</p>
        </div>
      </div>
      <div className="mode-tabs">
        <button className={mode === "collect" ? "active" : ""} onClick={() => setMode("collect")}><GitBranch size={17} />Collect</button>
        <button className={mode === "upload" ? "active" : ""} onClick={() => setMode("upload")}><Upload size={17} />Upload</button>
      </div>
      <form onSubmit={submit} className="stack-form">
        <label>
          <span>{mode === "collect" ? "GitHub URL" : "本地路径"}</span>
          <input value={value} onChange={(event) => setValue(event.target.value)} required />
        </label>
        <label>
          <span>资源名称</span>
          <input value={name} onChange={(event) => setName(event.target.value)} placeholder="留空则自动推断" />
        </label>
        <label>
          <span>类型</span>
          <select value={kind} onChange={(event) => setKind(event.target.value as "auto" | ResourceKind)}>
            <option value="auto">自动检测</option>
            {kinds.filter((item) => item !== "all").map((item) => <option key={item} value={item}>{item}</option>)}
          </select>
        </label>
        <label className="checkline">
          <input type="checkbox" checked={push} onChange={(event) => setPush(event.target.checked)} />
          <span>完成后推送私有资源仓库</span>
        </label>
        <button className="primary" disabled={busy}>{busy ? "处理中" : "确认添加"}</button>
      </form>
    </section>
  );
}

function SyncView({
  platforms,
  onDone,
  onError,
}: {
  platforms: PlatformProfile[];
  onDone: (message: string) => void;
  onError: (message: string) => void;
}) {
  const [allKinds, setAllKinds] = useState(false);
  const [platform, setPlatform] = useState("");
  const [busy, setBusy] = useState(false);

  async function sync() {
    setBusy(true);
    try {
      const data = await lpmAction<{ results: Array<{ action: string }> }>("sync", {
        all_kinds: allKinds,
        platform: platform || undefined,
      });
      onDone(`同步完成：${data.results.length} 个结果`);
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="panel form-panel">
      <div className="panel-head">
        <div>
          <h2>同步安装</h2>
          <p>默认只同步 skill。启用全部类型会写入 MCP、规则、提示词或插件配置。</p>
        </div>
      </div>
      <div className="stack-form">
        <label className="checkline">
          <input type="checkbox" checked={allKinds} onChange={(event) => setAllKinds(event.target.checked)} />
          <span>同步全部资源类型</span>
        </label>
        <label>
          <span>目标平台</span>
          <select value={platform} onChange={(event) => setPlatform(event.target.value)}>
            <option value="">全部启用平台</option>
            {platforms.filter((item) => item.enabled).map((item) => (
              <option key={item.name} value={item.name}>{item.name}</option>
            ))}
          </select>
        </label>
        <button className="primary" onClick={sync} disabled={busy}>
          <FolderSync size={17} />
          {busy ? "同步中" : "开始同步"}
        </button>
      </div>
    </section>
  );
}

function HealthView({ onError }: { onError: (message: string) => void }) {
  const [checks, setChecks] = useState<DoctorCheck[]>([]);
  const [busy, setBusy] = useState(false);

  async function runDoctor() {
    setBusy(true);
    try {
      const data = await lpmAction<{ checks: DoctorCheck[] }>("doctor");
      setChecks(data.checks);
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="panel">
      <div className="panel-head">
        <div>
          <h2>环境检查</h2>
          <p>检查 Git、配置、Token、资源仓库和平台状态。</p>
        </div>
        <button className="primary" onClick={runDoctor} disabled={busy}><TerminalSquare size={17} />运行检查</button>
      </div>
      <div className="check-list">
        {checks.map((check) => (
          <div key={check.id} className="check-row">
            {check.ok ? <CheckCircle2 size={18} /> : <XCircle size={18} />}
            <strong>{check.label}</strong>
            <span>{check.detail}</span>
          </div>
        ))}
      </div>
    </section>
  );
}

function PlatformsView({ platforms }: { platforms: PlatformProfile[] }) {
  return (
    <section className="panel">
      <div className="panel-head">
        <h2>平台配置</h2>
      </div>
      <div className="platform-grid">
        {platforms.map((profile) => (
          <div key={profile.name} className="platform-row">
            <div>
              <strong>{profile.name}</strong>
              <span>{profile.enabled ? "enabled" : "disabled"}</span>
            </div>
            <DescriptionList
              rows={[
                ["Skills", profile.skills_dir || "-"],
                ["MCP", profile.mcp_json || "-"],
                ["Rules", profile.rules_dir || "-"],
              ]}
            />
          </div>
        ))}
      </div>
    </section>
  );
}

function Metric({ label, value }: { label: string; value: number }) {
  return (
    <div className="metric">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function StatusDot({ ok, label }: { ok: boolean; label: string }) {
  return <span className={ok ? "status ok" : "status warn"}>{label}</span>;
}

function Banner({ tone, text }: { tone: "success" | "danger"; text: string }) {
  return <div className={`banner ${tone}`}>{text}</div>;
}

function Segmented<T extends string>({
  value,
  values,
  onChange,
}: {
  value: T;
  values: readonly T[];
  onChange: (value: T) => void;
}) {
  return (
    <div className="segmented">
      {values.map((item) => (
        <button key={item} className={value === item ? "active" : ""} onClick={() => onChange(item)}>
          {item}
        </button>
      ))}
    </div>
  );
}

function KindBars({ counts }: { counts: Record<string, number> }) {
  const entries = Object.entries(counts);
  const max = Math.max(1, ...entries.map(([, value]) => value));
  return (
    <div className="kind-bars">
      {entries.map(([kind, value]) => (
        <div key={kind}>
          <span>{kind}</span>
          <div><i style={{ width: `${(value / max) * 100}%` }} /></div>
          <b>{value}</b>
        </div>
      ))}
    </div>
  );
}

function DescriptionList({ rows }: { rows: Array<[string, string]> }) {
  return (
    <dl className="description-list">
      {rows.map(([label, value]) => (
        <div key={label}>
          <dt>{label}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  );
}

function Empty({ text }: { text: string }) {
  return (
    <div className="empty">
      <HardDrive size={32} />
      <span>{text}</span>
    </div>
  );
}
