export function StatusDot({ ok, label }: { ok: boolean; label: string }) {
  return <span className={ok ? "status ok" : "status warn"}>{label}</span>;
}

