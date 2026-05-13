import { resourceKindLabel, type TFunction } from "@/app/i18n";
import { KindBadge } from "@/components/KindBadge";
import type { RegistryItem, Summary } from "@/types/lpm";

export function DashboardView({
  summary,
  items,
  t,
}: {
  summary: Summary | null;
  items: RegistryItem[];
  t: TFunction;
}) {
  return (
    <section className="view-grid">
      <div className="metrics">
        <Metric label={t("dashboard.totalResources")} value={summary?.counts.total ?? 0} />
        <Metric label={t("dashboard.installed")} value={summary?.installed ?? 0} />
        <Metric label={t("dashboard.updates")} value={summary?.updates ?? 0} />
        <Metric label={t("dashboard.sources")} value={Object.keys(summary?.counts.by_source || {}).length} />
      </div>

      <div className="panel">
        <div className="panel-head">
          <h2>{t("dashboard.resourceTypes")}</h2>
        </div>
        <KindBars counts={summary?.counts.by_kind || {}} t={t} />
      </div>

      <div className="panel">
        <div className="panel-head">
          <h2>{t("dashboard.recentResources")}</h2>
        </div>
        <div className="compact-list">
          {items.slice(0, 6).map((item) => (
            <div key={item.name} className="compact-row">
              <KindBadge kind={item.kind} label={resourceKindLabel(item.kind, t)} />
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

function KindBars({ counts, t }: { counts: Record<string, number>; t: TFunction }) {
  const entries = Object.entries(counts);
  const max = Math.max(1, ...entries.map(([, value]) => value));
  return (
    <div className="kind-bars">
      {entries.map(([kind, value]) => (
        <div key={kind}>
          <span>{resourceKindLabel(kind, t)}</span>
          <div><i style={{ width: `${(value / max) * 100}%` }} /></div>
          <b>{value}</b>
        </div>
      ))}
    </div>
  );
}
