import { useState } from "react";
import { CheckCircle2, TerminalSquare, XCircle } from "lucide-react";
import { lpmAction } from "@/api/client";
import type { DoctorCheck } from "@/types/lpm";

export function HealthView({ onError }: { onError: (message: string) => void }) {
  const [checks, setChecks] = useState<DoctorCheck[]>([]);
  const [busy, setBusy] = useState(false);

  async function runDoctor() {
    setBusy(true);
    try {
      const data = await lpmAction<{ checks: DoctorCheck[] }>("doctor");
      setChecks(data.checks);
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="panel">
      <div className="panel-head">
        <div>
          <h2>Health checks</h2>
          <p>Check Git, config, token, resource repo, and platform state.</p>
        </div>
        <button className="primary" onClick={runDoctor} disabled={busy}><TerminalSquare size={17} />Run checks</button>
      </div>
      <div className="check-list">
        {checks.map((check) => (
          <div key={check.id} className="check-row">
            {check.ok ? <CheckCircle2 size={18} /> : <XCircle size={18} />}
            <strong>{check.label}</strong>
            <span>{check.detail}</span>
          </div>
        ))}
      </div>
    </section>
  );
}

