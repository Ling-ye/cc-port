import type { ReactNode } from "react";

export function DescriptionList({ rows }: { rows: Array<[string, ReactNode]> }) {
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

