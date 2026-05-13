import { FormEvent, useState } from "react";
import { GitBranch, Upload } from "lucide-react";
import { lpmAction } from "@/api/client";
import type { ResourceKind } from "@/types/lpm";

const kinds: ResourceKind[] = ["skill", "mcp", "rule", "prompt", "plugin"];

export function AddResourceView({
  onDone,
  onError,
}: {
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
      onDone(mode === "collect" ? "Resource collected" : "Resource uploaded");
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
          <h2>Add resource</h2>
          <p>Collect a GitHub resource by reference, or upload a local resource to your private repo.</p>
        </div>
      </div>
      <div className="mode-tabs">
        <button className={mode === "collect" ? "active" : ""} onClick={() => setMode("collect")}><GitBranch size={17} />Collect</button>
        <button className={mode === "upload" ? "active" : ""} onClick={() => setMode("upload")}><Upload size={17} />Upload</button>
      </div>
      <form onSubmit={submit} className="stack-form">
        <label>
          <span>{mode === "collect" ? "GitHub URL" : "Local path"}</span>
          <input value={value} onChange={(event) => setValue(event.target.value)} required />
        </label>
        <label>
          <span>Resource name</span>
          <input value={name} onChange={(event) => setName(event.target.value)} placeholder="Leave empty to infer" />
        </label>
        <label>
          <span>Type</span>
          <select value={kind} onChange={(event) => setKind(event.target.value as "auto" | ResourceKind)}>
            <option value="auto">Auto detect</option>
            {kinds.map((item) => <option key={item} value={item}>{item}</option>)}
          </select>
        </label>
        <label className="checkline">
          <input type="checkbox" checked={push} onChange={(event) => setPush(event.target.checked)} />
          <span>Push private resource repo after completion</span>
        </label>
        <button className="primary" disabled={busy}>{busy ? "Working..." : "Confirm"}</button>
      </form>
    </section>
  );
}

