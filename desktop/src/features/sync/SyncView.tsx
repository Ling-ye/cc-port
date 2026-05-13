import { useState } from "react";
import { FolderSync } from "lucide-react";
import { lpmAction } from "@/api/client";
import type { PlatformProfile } from "@/types/lpm";

export function SyncView({
  platforms,
  onDone,
  onError,
}: {
  platforms: PlatformProfile[];
  onDone: (message: string) => void;
  onError: (message: string) => void;
}) {
  const [allKinds, setAllKinds] = useState(false);
  const [platform, setPlatform] = useState("");
  const [busy, setBusy] = useState(false);

  async function sync() {
    setBusy(true);
    try {
      const data = await lpmAction<{ results: Array<{ action: string }> }>("sync", {
        all_kinds: allKinds,
        platform: platform || undefined,
      });
      onDone(`Sync completed with ${data.results.length} result(s)`);
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
          <h2>Sync installs</h2>
          <p>Install resources into enabled platform directories from the configured registry.</p>
        </div>
      </div>
      <div className="stack-form">
        <label className="checkline">
          <input type="checkbox" checked={allKinds} onChange={(event) => setAllKinds(event.target.checked)} />
          <span>Sync all resource types</span>
        </label>
        <label>
          <span>Target platform</span>
          <select value={platform} onChange={(event) => setPlatform(event.target.value)}>
            <option value="">All enabled platforms</option>
            {platforms.filter((item) => item.enabled).map((item) => (
              <option key={item.name} value={item.name}>{item.name}</option>
            ))}
          </select>
        </label>
        <button className="primary" onClick={sync} disabled={busy}>
          <FolderSync size={17} />
          {busy ? "Syncing..." : "Start sync"}
        </button>
      </div>
    </section>
  );
}

