import type { ResourceKind } from "@/types/cc-port";

export function KindBadge({ kind, label }: { kind: ResourceKind; label?: string }) {
  return <span className={`asset-pill kind kind-${kind}`}>{label || kind}</span>;
}
