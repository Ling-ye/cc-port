export function Segmented<T extends string>({
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

