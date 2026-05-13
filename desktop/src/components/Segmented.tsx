export function Segmented<T extends string>({
  value,
  values,
  onChange,
  getLabel = (item) => item,
}: {
  value: T;
  values: readonly T[];
  onChange: (value: T) => void;
  getLabel?: (value: T) => string;
}) {
  return (
    <div className="segmented">
      {values.map((item) => (
        <button key={item} className={value === item ? "active" : ""} onClick={() => onChange(item)}>
          {getLabel(item)}
        </button>
      ))}
    </div>
  );
}
