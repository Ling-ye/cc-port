import { useMemo, useState } from "react";
import { lpmAction } from "@/api/client";
import { DescriptionList } from "@/components/DescriptionList";
import { EmptyState } from "@/components/EmptyState";
import { KindBadge } from "@/components/KindBadge";
import { Segmented } from "@/components/Segmented";
import type { RegistryItem, ResourceKind } from "@/types/lpm";

const kinds: Array<"all" | ResourceKind> = ["all", "skill", "mcp", "rule", "prompt", "plugin"];

export function ResourcesView({
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
    if (!confirm(`Remove ${selected.name}?`)) return;
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
              <KindBadge kind={item.kind} />
              <span>
                <strong>{item.name}</strong>
                <small>{item.source} / {item.status?.installed ? "installed" : "not installed"}</small>
              </span>
            </button>
          ))}
        </div>
      </div>
      <div className="panel detail-panel">
        {selected ? (
          <>
            <div className="detail-title">
              <KindBadge kind={selected.kind} />
              <h2>{selected.name}</h2>
            </div>
            <DescriptionList
              rows={[
                ["Source", selected.source],
                ["Repo", selected.repo || "-"],
                ["Path", selected.path || "-"],
                ["Ref", selected.ref || "-"],
                ["Subdir", selected.subdir || "-"],
                ["Install path", selected.status?.install_path || "-"],
                ["Install state", selected.status?.installed ? "installed" : "not installed"],
              ]}
            />
            <div className="tag-row">
              {selected.tags?.map((tag) => <span key={tag}>{tag}</span>)}
            </div>
            <div className="danger-row">
              <button className="secondary" onClick={() => removeSelected(false)}>Remove record</button>
              <button className="danger" onClick={() => removeSelected(true)}>Remove and uninstall</button>
            </div>
          </>
        ) : (
          <EmptyState text="No resource selected" />
        )}
      </div>
    </section>
  );
}

