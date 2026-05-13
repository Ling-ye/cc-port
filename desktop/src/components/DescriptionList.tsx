export function DescriptionList({ rows }: { rows: Array<[string, string]> }) {
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

