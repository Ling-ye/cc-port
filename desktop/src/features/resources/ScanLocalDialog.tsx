import { FolderOpen, RefreshCcw, Trash2, X } from "lucide-react";
import { useEffect, useState } from "react";
import { lpmAction, selectDirectory } from "@/api/client";
import type { TFunction } from "@/app/i18n";
import { useTaskCenter } from "@/app/TaskCenterContext";
import { Banner } from "@/components/Banner";
import type { AssetInventory, PluginProject } from "@/types/lpm";

export interface ScanScope {
  scan_global: boolean;
  project_ids: string[];
}

export function ScanLocalDialog({
  t,
  onClose,
  onScanned,
}: {
  t: TFunction;
  onClose: () => void;
  onScanned: (inventory: AssetInventory, scope: ScanScope) => void;
}) {
  const { runTask } = useTaskCenter();
  const [scanGlobal, setScanGlobal] = useState(true);
  const [projects, setProjects] = useState<PluginProject[]>([]);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    void lpmAction<{ projects: PluginProject[] }>("plugin_projects_list")
      .then((result) => {
        setProjects(result.projects);
        setSelectedIds(result.projects.filter((item) => item.exists).map((item) => item.id));
      })
      .catch((reason) => setError(reason instanceof Error ? reason.message : String(reason)));
  }, []);

  async function addProject() {
    setError("");
    try {
      const path = await selectDirectory();
      if (!path) return;
      const project = await lpmAction<PluginProject>("plugin_projects_add", { path });
      setProjects((current) => [...current.filter((item) => item.id !== project.id), project]);
      setSelectedIds((current) => Array.from(new Set([...current, project.id])));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }

  async function removeProject(projectId: string) {
    setError("");
    try {
      await lpmAction<PluginProject>("plugin_projects_remove", { project_id: projectId });
      setProjects((current) => current.filter((item) => item.id !== projectId));
      setSelectedIds((current) => current.filter((item) => item !== projectId));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }

  async function scan() {
    setBusy(true);
    setError("");
    try {
      const inventory = await runTask({
        kind: "asset-scan-local",
        title: t("assets.scanLocal"),
        action: () => lpmAction<AssetInventory>("asset_inventory", {
          scan_local: true,
          scan_global: scanGlobal,
          project_ids: selectedIds,
          refresh_remote: false,
        }),
        successMessage: t("assets.scanComplete"),
        retryPolicy: "safe-read",
      });
      onScanned(inventory, { scan_global: scanGlobal, project_ids: [...selectedIds] });
    } catch {
      // Task center owns tracked failures.
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={(event) => {
      if (event.target === event.currentTarget && !busy) onClose();
    }}>
      <div className="modal scan-local-modal" role="dialog" aria-modal="true" aria-labelledby="scan-local-title" aria-describedby="scan-local-description" aria-busy={busy}>
        <div className="modal-head">
          <RefreshCcw size={19} />
          <h2 id="scan-local-title">{t("scan.title")}</h2>
          <button className="icon-button" onClick={onClose} disabled={busy} aria-label={t("common.close")}><X size={17} /></button>
        </div>
        <p id="scan-local-description">{t("scan.description")}</p>
        <label className="checkline scan-global-option">
          <input type="checkbox" checked={scanGlobal} disabled={busy} onChange={(event) => setScanGlobal(event.target.checked)} />
          <span>{t("scan.global")}</span>
        </label>
        <div className="scan-project-head">
          <div><strong>{t("scan.projects")}</strong><small>{t("scan.projectsHint")}</small></div>
          <button className="secondary" type="button" onClick={() => void addProject()} disabled={busy}>
            <FolderOpen size={15} />{t("scan.addProject")}
          </button>
        </div>
        <div className="scan-project-list" role="group" aria-label={t("scan.projects")}>
          {projects.map((project) => (
            <div className="scan-project-row" key={project.id}>
              <label className={!project.exists ? "disabled" : ""}>
                <input
                  type="checkbox"
                  checked={selectedIds.includes(project.id)}
                  disabled={busy || !project.exists}
                  onChange={() => setSelectedIds((current) => current.includes(project.id)
                    ? current.filter((item) => item !== project.id)
                    : [...current, project.id])}
                />
                <span><strong>{project.path}</strong><small>{project.portable ? `${project.repo}${project.subdir ? `/${project.subdir}` : ""}` : t("scan.observeOnly")}</small></span>
              </label>
              <button className="icon-button" type="button" onClick={() => void removeProject(project.id)} disabled={busy} aria-label={`${t("scan.removeProject")}: ${project.path}`}><Trash2 size={15} /></button>
            </div>
          ))}
          {!projects.length ? <small>{t("scan.noProjects")}</small> : null}
        </div>
        {error ? <Banner tone="danger" text={error} /> : null}
        <div className="modal-actions">
          <button className="secondary" onClick={onClose} disabled={busy}>{t("common.cancel")}</button>
          <button className="primary" onClick={() => void scan()} disabled={busy || (!scanGlobal && !selectedIds.length)}>
            {busy ? t("common.working") : t("assets.scanLocal")}
          </button>
        </div>
      </div>
    </div>
  );
}
