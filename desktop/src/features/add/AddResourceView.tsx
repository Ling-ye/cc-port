import { FormEvent, useState } from "react";
import { GitBranch, Upload } from "lucide-react";
import { lpmAction } from "@/api/client";
import { resourceKindLabel, type TFunction } from "@/app/i18n";
import type { ResourceKind } from "@/types/lpm";

const kinds: ResourceKind[] = ["skill", "mcp", "rule", "prompt", "plugin"];

export function AddResourceView({
  t,
  onDone,
  onError,
}: {
  t: TFunction;
  onDone: (message: string) => void;
  onError: (message: string) => void;
}) {
  const [mode, setMode] = useState<"collect" | "upload">("collect");
  const [value, setValue] = useState("");
  const [kind, setKind] = useState<"auto" | ResourceKind>("auto");
  const [name, setName] = useState("");
  const [push, setPush] = useState(false);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    try {
      const payload = {
        [mode === "collect" ? "github_url" : "path"]: value,
        kind: kind === "auto" ? undefined : kind,
        name,
        push,
      };
      await lpmAction(mode, payload);
      onDone(mode === "collect" ? t("add.successCollected") : t("add.successUploaded"));
      setValue("");
      setName("");
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="panel form-panel">
      <div className="panel-head">
        <div>
          <h2>{t("add.title")}</h2>
          <p>{t("add.description")}</p>
        </div>
      </div>
      <div className="mode-tabs">
        <button className={mode === "collect" ? "active" : ""} onClick={() => setMode("collect")}><GitBranch size={17} />{t("add.modeCollect")}</button>
        <button className={mode === "upload" ? "active" : ""} onClick={() => setMode("upload")}><Upload size={17} />{t("add.modeUpload")}</button>
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
        <label className="checkline">
          <input type="checkbox" checked={push} onChange={(event) => setPush(event.target.checked)} />
          <span>{t("add.pushAfterCompletion")}</span>
        </label>
        <button className="primary" disabled={busy}>{busy ? t("common.working") : t("common.confirm")}</button>
      </form>
    </section>
  );
}
