import { useMemo, useState } from "react";
import { lpmAction } from "@/api/client";
import { resourceKindLabel, type TFunction } from "@/app/i18n";
import { DescriptionList } from "@/components/DescriptionList";
import { EmptyState } from "@/components/EmptyState";
import { KindBadge } from "@/components/KindBadge";
import { Segmented } from "@/components/Segmented";
import type { RegistryItem, ResourceKind } from "@/types/lpm";

const kinds: Array<"all" | ResourceKind> = ["all", "skill", "mcp", "rule", "prompt", "plugin"];

export function ResourcesView({
  items,
  selected,
  t,
  onSelect,
  onChanged,
}: {
  items: RegistryItem[];
  selected?: RegistryItem;
  t: TFunction;
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
    if (!confirm(t("resources.removeConfirm", { name: selected.name }))) return;
    await lpmAction("remove", { name: selected.name, uninstall });
    await onChanged();
  }

  return (
    <section className="split-view">
      <div className="panel list-panel">
        <div className="toolbar">
          <Segmented value={filter} values={kinds} onChange={setFilter} getLabel={(item) => resourceKindLabel(item, t)} />
        </div>
        <div className="resource-list">
          {visible.map((item) => (
            <button
              key={item.name}
              className={selected?.name === item.name ? "resource-row active" : "resource-row"}
              onClick={() => onSelect(item.name)}
            >
              <KindBadge kind={item.kind} label={resourceKindLabel(item.kind, t)} />
              <span>
                <strong>{item.name}</strong>
                <small>{item.source} / {item.status?.installed ? t("status.installed") : t("status.notInstalled")}</small>
              </span>
            </button>
          ))}
        </div>
      </div>
      <div className="panel detail-panel">
        {selected ? (
          <>
            <div className="detail-title">
              <KindBadge kind={selected.kind} label={resourceKindLabel(selected.kind, t)} />
              <h2>{selected.name}</h2>
            </div>
            <DescriptionList
              rows={[
                [t("resources.source"), selected.source],
                [t("resources.repo"), selected.repo || "-"],
                [t("resources.path"), selected.path || "-"],
                [t("resources.ref"), selected.ref || "-"],
                [t("resources.subdir"), selected.subdir || "-"],
                [t("resources.installPath"), selected.status?.install_path || "-"],
                [t("resources.installState"), selected.status?.installed ? t("status.installed") : t("status.notInstalled")],
              ]}
            />
            <div className="tag-row">
              {selected.tags?.map((tag) => <span key={tag}>{tag}</span>)}
            </div>
            <div className="danger-row">
              <button className="secondary" onClick={() => removeSelected(false)}>{t("resources.removeRecord")}</button>
              <button className="danger" onClick={() => removeSelected(true)}>{t("resources.removeAndUninstall")}</button>
            </div>
          </>
        ) : (
          <EmptyState text={t("resources.noSelected")} />
        )}
      </div>
    </section>
  );
}
