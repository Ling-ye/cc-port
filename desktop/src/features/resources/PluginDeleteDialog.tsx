import { Trash2, X } from "lucide-react";
import { useEffect, useState } from "react";
import { lpmAction } from "@/api/client";
import type { TFunction } from "@/app/i18n";
import { useTaskCenter } from "@/app/TaskCenterContext";
import { Banner } from "@/components/Banner";
import type { PluginDeletePlan, PluginDeleteResult } from "@/types/lpm";

export function PluginDeleteDialog({
  resourceKey,
  t,
  onClose,
  onDone,
}: {
  resourceKey: string;
  t: TFunction;
  onClose: () => void;
  onDone: () => Promise<void> | void;
}) {
  const { runTask } = useTaskCenter();
  const [plan, setPlan] = useState<PluginDeletePlan | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    setBusy(true);
    void lpmAction<PluginDeletePlan>("plugin_delete_plan", { resource_key: resourceKey })
      .then((next) => {
        setPlan(next);
        setSelected(next.selected_instance_ids);
      })
      .catch((reason) => setError(reason instanceof Error ? reason.message : String(reason)))
      .finally(() => setBusy(false));
  }, [resourceKey]);

  async function refreshPlan() {
    setBusy(true);
    setError("");
    try {
      const next = await lpmAction<PluginDeletePlan>("plugin_delete_plan", {
        resource_key: resourceKey,
        instance_ids: selected,
      });
      setPlan(next);
      return next;
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
      return null;
    } finally {
      setBusy(false);
    }
  }

  async function apply() {
    const current = await refreshPlan();
    if (!current || current.blocked || !selected.length) return;
    setBusy(true);
    try {
      const result = await runTask({
        kind: "plugin-delete",
        title: t("plugin.delete"),
        context: resourceKey,
        action: () => lpmAction<PluginDeleteResult>("plugin_delete_apply", {
          resource_key: resourceKey,
          instance_ids: selected,
          plan_hash: current.plan_hash,
        }),
        successMessage: (value) => value.status === "succeeded" ? t("plugin.deleteComplete") : t("plugin.deleteNeedsAction"),
        retryPolicy: "none",
      });
      if (result.status === "stale-plan" && result.stale_plan) {
        setPlan(result.stale_plan);
        setError(t("assets.stalePlan"));
        return;
      }
      if (result.status !== "succeeded") {
        setError(result.results.map((item) => item.message).join("; "));
        return;
      }
      await Promise.resolve(onDone());
    } catch {
      // Task center owns tracked failures.
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="modal-backdrop" role="presentation">
      <div className="modal plugin-delete-modal" role="dialog" aria-modal="true" aria-labelledby="plugin-delete-title">
        <div className="modal-head danger-head">
          <Trash2 size={19} />
          <h2 id="plugin-delete-title">{t("plugin.delete")}</h2>
          <button className="icon-button" onClick={onClose} disabled={busy}><X size={17} /></button>
        </div>
        <p>{t("plugin.deleteDescription")}</p>
        <div className="plugin-delete-instances">
          {plan?.instances.map((instance) => (
            <label className={!instance.selectable ? "disabled" : ""} key={instance.id}>
              <input
                type="checkbox"
                disabled={!instance.selectable}
                checked={selected.includes(instance.id)}
                onChange={() => setSelected((current) => current.includes(instance.id)
                  ? current.filter((item) => item !== instance.id)
                  : [...current, instance.id])}
              />
              <span>
                <strong>{instance.platform} / {instance.scope}</strong>
                <small>{instance.method}</small>
                <small>{instance.detail}</small>
              </span>
            </label>
          ))}
        </div>
        {plan?.blockers.map((message) => <Banner tone="danger" text={message} key={message} />)}
        {error ? <Banner tone="danger" text={error} /> : null}
        <div className="modal-actions">
          <button className="secondary" onClick={onClose} disabled={busy}>{t("common.cancel")}</button>
          <button className="danger" onClick={() => void apply()} disabled={busy || !plan || !selected.length || plan.blocked}>
            {busy ? t("common.working") : t("common.confirm")}
          </button>
        </div>
      </div>
    </div>
  );
}
