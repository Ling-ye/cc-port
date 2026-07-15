import { useState } from "react";
import { AlertTriangle, CheckCircle2, Info, TerminalSquare, XCircle } from "lucide-react";
import { lpmAction } from "@/api/client";
import type { TFunction } from "@/app/i18n";
import { useTaskCenter } from "@/app/TaskCenterContext";
import type { DoctorCheck, DoctorStatus } from "@/types/lpm";

export function HealthView({ t }: { t: TFunction }) {
  const { runTask } = useTaskCenter();
  const [checks, setChecks] = useState<DoctorCheck[]>([]);
  const [busy, setBusy] = useState(false);

  async function runDoctor() {
    setBusy(true);
    try {
      await runTask({
        kind: "health-check",
        title: t("health.runChecks"),
        action: async () => {
          const data = await lpmAction<{ checks: DoctorCheck[] }>("doctor");
          setChecks(data.checks);
          return data;
        },
        successMessage: (data) => t("health.completed", { count: data.checks.length }),
        retryPolicy: "safe-read",
      });
    } catch {
      // TaskCenter owns feedback for tracked operations.
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="panel">
      <div className="panel-head">
        <div>
          <h2>{t("health.title")}</h2>
          <p>{t("health.description")}</p>
        </div>
        <button className="primary" onClick={runDoctor} disabled={busy}><TerminalSquare size={17} />{t("health.runChecks")}</button>
      </div>
      <div className="check-list">
        {checks.map((check) => (
          <div key={check.id} className={`check-row status-${statusOf(check)}`}>
            {iconForStatus(statusOf(check))}
            <strong>{check.label}</strong>
            <span>{check.detail}</span>
          </div>
        ))}
      </div>
    </section>
  );
}

function statusOf(check: DoctorCheck): DoctorStatus {
  return check.status ?? (check.ok ? "ok" : "error");
}

function iconForStatus(status: DoctorStatus) {
  switch (status) {
    case "ok":
      return <CheckCircle2 size={18} />;
    case "warning":
      return <AlertTriangle size={18} />;
    case "skipped":
      return <Info size={18} />;
    case "error":
    default:
      return <XCircle size={18} />;
  }
}
