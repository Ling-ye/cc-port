import { type FormEvent, useState } from "react";
import { GitBranch, Upload } from "lucide-react";
import { lpmAction } from "@/api/client";
import { resourceKindLabel, type TFunction } from "@/app/i18n";
import { useTaskCenter } from "@/app/TaskCenterContext";
import type { ResourceKind } from "@/types/lpm";

const kinds: ResourceKind[] = ["skill", "mcp", "rule", "prompt", "plugin"];

export function AddResourceView({
  t,
  onChanged,
}: {
  t: TFunction;
  onChanged: () => Promise<void> | void;
  onError: (message: string) => void;
}) {
  const { runTask } = useTaskCenter();
  const [mode, setMode] = useState<"collect" | "upload">("collect");
  const [value, setValue] = useState("");
  const [kind, setKind] = useState<"auto" | ResourceKind>("auto");
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    try {
      const collectMode = mode === "collect";
      await runTask({
        kind: collectMode ? "resource-collect" : "resource-upload",
        title: collectMode ? t("add.modeCollect") : t("add.modeUpload"),
        context: name || value,
        action: () => lpmAction(collectMode ? "collect" : "upload", {
          [collectMode ? "github_url" : "path"]: value,
          kind: kind === "auto" ? undefined : kind,
          name,
          push: true,
        }),
        successMessage: collectMode ? t("add.successCollected") : t("add.successUploaded"),
        retryPolicy: "none",
      });
      setValue("");
      setName("");
      await Promise.resolve(onChanged());
    } catch {
      // TaskCenter owns feedback for tracked operations.
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="panel form-panel add-resource-panel">
      <div className="panel-head">
        <div>
          <h2>{t("add.title")}</h2>
          <p>{t("add.description")}</p>
        </div>
      </div>
      <div className="mode-tabs">
        <button className={mode === "collect" ? "active" : ""} onClick={() => setMode("collect")}>
          <GitBranch size={17} />{t("add.modeCollect")}
        </button>
        <button className={mode === "upload" ? "active" : ""} onClick={() => setMode("upload")}>
          <Upload size={17} />{t("add.modeUpload")}
        </button>
      </div>
      <form onSubmit={submit} className="stack-form">
        <label>
          <span>{mode === "collect" ? t("add.githubUrl") : t("add.localPath")}</span>
          <input value={value} onChange={(event) => setValue(event.target.value)} required />
        </label>
        <label>
          <span>{t("add.resourceName")}</span>
          <input value={name} onChange={(event) => setName(event.target.value)} placeholder={t("add.inferPlaceholder")} />
        </label>
        <label>
          <span>{t("add.type")}</span>
          <select value={kind} onChange={(event) => setKind(event.target.value as "auto" | ResourceKind)}>
            <option value="auto">{t("kind.auto")}</option>
            {kinds.map((item) => <option key={item} value={item}>{resourceKindLabel(item, t)}</option>)}
          </select>
        </label>
        <button className="primary" disabled={busy}>{busy ? t("common.working") : t("common.confirm")}</button>
      </form>
    </section>
  );
}
