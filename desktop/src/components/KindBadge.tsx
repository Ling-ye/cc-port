import type { ResourceKind } from "@/types/lpm";

export function KindBadge({ kind }: { kind: ResourceKind }) {
  return <span className={`kind kind-${kind}`}>{kind}</span>;
}

