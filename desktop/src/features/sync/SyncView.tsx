import { useState } from "react";
import { FolderSync } from "lucide-react";
import { lpmAction } from "@/api/client";
import type { TFunction } from "@/app/i18n";
import type { PlatformProfile } from "@/types/lpm";

export function SyncView({
  platforms,
  t,
  onDone,
  onError,
}: {
  platforms: PlatformProfile[];
  t: TFunction;
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
      onDone(t("sync.success", { count: data.results.length }));
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
          <h2>{t("sync.title")}</h2>
          <p>{t("sync.description")}</p>
        </div>
      </div>
      <div className="stack-form">
        <label className="checkline">
          <input type="checkbox" checked={allKinds} onChange={(event) => setAllKinds(event.target.checked)} />
          <span>{t("sync.allKinds")}</span>
        </label>
        <label>
          <span>{t("sync.targetPlatform")}</span>
          <select value={platform} onChange={(event) => setPlatform(event.target.value)}>
            <option value="">{t("sync.allEnabledPlatforms")}</option>
            {platforms.filter((item) => item.enabled).map((item) => (
              <option key={item.name} value={item.name}>{item.name}</option>
            ))}
          </select>
        </label>
        <button className="primary" onClick={sync} disabled={busy}>
          <FolderSync size={17} />
          {busy ? t("sync.syncing") : t("sync.start")}
        </button>
      </div>
    </section>
  );
}
