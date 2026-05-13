import { FolderSync, HeartPulse, PackagePlus, Search } from "lucide-react";
import type { View } from "@/app/navigation";
import { KindBadge } from "@/components/KindBadge";
import type { RegistryItem, Summary } from "@/types/lpm";

export function DashboardView({
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
        <Metric label="Total resources" value={summary?.counts.total ?? 0} />
        <Metric label="Installed" value={summary?.installed ?? 0} />
        <Metric label="Updates" value={summary?.updates ?? 0} />
        <Metric label="Sources" value={Object.keys(summary?.counts.by_source || {}).length} />
      </div>

      <div className="panel wide">
        <div className="panel-head">
          <div>
            <h2>Daily operations</h2>
            <p>Collect, upload, sync, and inspect resources from one desktop surface.</p>
          </div>
        </div>
        <div className="quick-actions">
          <button onClick={() => onNavigate("add")}><PackagePlus size={18} />Add resource</button>
          <button onClick={() => onNavigate("sync")}><FolderSync size={18} />Sync installs</button>
          <button onClick={() => onNavigate("health")}><HeartPulse size={18} />Run checks</button>
          <button onClick={() => onNavigate("resources")}><Search size={18} />Browse resources</button>
        </div>
      </div>

      <div className="panel">
        <div className="panel-head">
          <h2>Resource types</h2>
        </div>
        <KindBars counts={summary?.counts.by_kind || {}} />
      </div>

      <div className="panel">
        <div className="panel-head">
          <h2>Recent resources</h2>
        </div>
        <div className="compact-list">
          {items.slice(0, 6).map((item) => (
            <div key={item.name} className="compact-row">
              <KindBadge kind={item.kind} />
              <strong>{item.name}</strong>
            </div>
          ))}
        </div>
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

