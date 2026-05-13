import { FormEvent, useMemo, useState } from "react";
import { Eye, FolderSearch, GitBranch, Search, Upload } from "lucide-react";
import { lpmAction } from "@/api/client";
import { resourceKindLabel, type TFunction } from "@/app/i18n";
import { KindBadge } from "@/components/KindBadge";
import type {
  DiscoveredResource,
  DiscoveryReadResult,
  DiscoveryScope,
  DiscoveryUploadResult,
  ResourceKind,
} from "@/types/lpm";

const kinds: ResourceKind[] = ["skill", "mcp", "rule", "prompt", "plugin"];
const filterKinds: Array<"all" | ResourceKind> = ["all", "skill", "rule", "prompt"];

export function AddResourceView({
  t,
  onDone,
  onError,
}: {
  t: TFunction;
  onDone: (message: string) => void;
  onError: (message: string) => void;
}) {
  const [mode, setMode] = useState<"collect" | "upload" | "discover">("collect");
  const [value, setValue] = useState("");
  const [kind, setKind] = useState<"auto" | ResourceKind>("auto");
  const [name, setName] = useState("");
  const [push, setPush] = useState(false);
  const [overwrite, setOverwrite] = useState(false);
  const [scope, setScope] = useState<DiscoveryScope>("global");
  const [directory, setDirectory] = useState("");
  const [candidates, setCandidates] = useState<DiscoveredResource[]>([]);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [candidateNames, setCandidateNames] = useState<Record<string, string>>({});
  const [kindFilter, setKindFilter] = useState<"all" | ResourceKind>("all");
  const [toolFilter, setToolFilter] = useState("all");
  const [preview, setPreview] = useState<DiscoveryReadResult | null>(null);
  const [previewBusy, setPreviewBusy] = useState(false);
  const [scanSummary, setScanSummary] = useState("");
  const [busy, setBusy] = useState(false);

  const selectedSet = useMemo(() => new Set(selectedIds), [selectedIds]);
  const tools = useMemo(
    () => Array.from(new Set(candidates.map((candidate) => candidate.tool))).sort(),
    [candidates],
  );
  const visibleCandidates = useMemo(
    () =>
      candidates.filter(
        (candidate) =>
          (kindFilter === "all" || candidate.kind === kindFilter) &&
          (toolFilter === "all" || candidate.tool === toolFilter),
      ),
    [candidates, kindFilter, toolFilter],
  );

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
      await lpmAction(mode === "collect" ? "collect" : "upload", payload);
      onDone(mode === "collect" ? t("add.successCollected") : t("add.successUploaded"));
      setValue("");
      setName("");
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function scan() {
    if (scope === "directory" && !directory.trim()) {
      onError(t("add.discoverDirectoryRequired"));
      return;
    }
    setBusy(true);
    setPreview(null);
    setScanSummary("");
    try {
      const data = await lpmAction<{ items: DiscoveredResource[] }>("discover_resources", discoveryPayload());
      setCandidates(data.items);
      setSelectedIds([]);
      setCandidateNames(Object.fromEntries(data.items.map((item) => [item.id, item.name_hint])));
      setScanSummary(t("add.discoverFound", { count: data.items.length }));
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function readPreview(candidate: DiscoveredResource) {
    setPreviewBusy(true);
    try {
      const data = await lpmAction<DiscoveryReadResult>("read_discovered_resource", {
        ...discoveryPayload(),
        id: candidate.id,
      });
      setPreview(data);
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    } finally {
      setPreviewBusy(false);
    }
  }

  async function uploadSelected() {
    setBusy(true);
    try {
      const result = await lpmAction<DiscoveryUploadResult>("upload_discovered_resources", {
        ...discoveryPayload(),
        items: selectedIds.map((id) => ({ id, name: candidateNames[id] })),
        overwrite,
        push,
      });
      const errors = result.results.filter((item) => !item.ok).map((item) => `${item.name}: ${item.error}`);
      if (errors.length) onError(errors.slice(0, 3).join("\n"));
      onDone(t("add.discoverUploaded", { count: result.imported, failed: result.failed }));
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  function discoveryPayload() {
    return {
      scope,
      root_path: scope === "directory" ? directory : undefined,
    };
  }

  function toggleCandidate(id: string) {
    setSelectedIds((current) =>
      current.includes(id) ? current.filter((item) => item !== id) : [...current, id],
    );
  }

  function selectVisible() {
    setSelectedIds(Array.from(new Set([...selectedIds, ...visibleCandidates.map((item) => item.id)])));
  }

  function updateCandidateName(id: string, nextName: string) {
    setCandidateNames((current) => ({ ...current, [id]: nextName }));
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
        <button className={mode === "discover" ? "active" : ""} onClick={() => setMode("discover")}><FolderSearch size={17} />{t("add.modeDiscover")}</button>
      </div>
      {mode === "discover" ? (
        <div className="stack-form discovery-panel">
          <div>
            <span className="field-label">{t("add.discoverScope")}</span>
            <div className="segmented">
              <button className={scope === "global" ? "active" : ""} onClick={() => setScope("global")}>{t("add.discoverGlobal")}</button>
              <button className={scope === "directory" ? "active" : ""} onClick={() => setScope("directory")}>{t("add.discoverDirectory")}</button>
            </div>
          </div>
          {scope === "directory" ? (
            <label>
              <span>{t("add.discoverDirectoryPath")}</span>
              <input value={directory} onChange={(event) => setDirectory(event.target.value)} placeholder={t("add.localPath")} />
            </label>
          ) : null}
          <div className="discovery-actions">
            <button className="primary" onClick={scan} disabled={busy}><Search size={17} />{busy ? t("common.working") : t("add.discoverScan")}</button>
            {scanSummary ? <span>{scanSummary}</span> : null}
          </div>
          {candidates.length ? (
            <>
              <div className="stack-form two-column">
                <label>
                  <span>{t("add.discoverKindFilter")}</span>
                  <select value={kindFilter} onChange={(event) => setKindFilter(event.target.value as "all" | ResourceKind)}>
                    {filterKinds.map((item) => <option key={item} value={item}>{resourceKindLabel(item, t)}</option>)}
                  </select>
                </label>
                <label>
                  <span>{t("add.discoverToolFilter")}</span>
                  <select value={toolFilter} onChange={(event) => setToolFilter(event.target.value)}>
                    <option value="all">{t("kind.all")}</option>
                    {tools.map((tool) => <option key={tool} value={tool}>{tool}</option>)}
                  </select>
                </label>
              </div>
              <div className="discovery-actions">
                <button className="secondary" onClick={selectVisible}>{t("add.discoverSelectVisible")}</button>
                <button className="secondary" onClick={() => setSelectedIds([])}>{t("add.discoverClearSelection")}</button>
                <span>{t("add.discoverSelected", { count: selectedIds.length })}</span>
              </div>
              <div className="discovery-list">
                {visibleCandidates.map((candidate) => (
                  <div key={candidate.id} className={candidate.status === "conflict" ? "discovery-row conflict" : "discovery-row"}>
                    <input
                      type="checkbox"
                      checked={selectedSet.has(candidate.id)}
                      onChange={() => toggleCandidate(candidate.id)}
                      aria-label={candidate.name_hint}
                    />
                    <KindBadge kind={candidate.kind} label={resourceKindLabel(candidate.kind, t)} />
                    <div className="discovery-main">
                      <input value={candidateNames[candidate.id] || ""} onChange={(event) => updateCandidateName(candidate.id, event.target.value)} />
                      <small>{candidate.tool} / {candidate.path}</small>
                      {candidate.description ? <p>{candidate.description}</p> : null}
                      {candidate.warnings.length ? <p className="discovery-warning">{candidate.warnings.join(" ")}</p> : null}
                    </div>
                    <button className="icon-button" onClick={() => readPreview(candidate)} disabled={previewBusy} title={t("add.discoverPreview")}>
                      <Eye size={16} />
                    </button>
                  </div>
                ))}
              </div>
              {preview ? (
                <div className="preview-panel">
                  <strong>{preview.path}</strong>
                  {preview.warning ? <p className="discovery-warning">{preview.warning}</p> : null}
                  <pre>{preview.text}{preview.truncated ? "\n..." : ""}</pre>
                </div>
              ) : null}
              <label className="checkline">
                <input type="checkbox" checked={overwrite} onChange={(event) => setOverwrite(event.target.checked)} />
                <span>{t("add.discoverOverwrite")}</span>
              </label>
              <label className="checkline">
                <input type="checkbox" checked={push} onChange={(event) => setPush(event.target.checked)} />
                <span>{t("add.pushAfterCompletion")}</span>
              </label>
              <button className="primary" onClick={uploadSelected} disabled={busy || selectedIds.length === 0}>{busy ? t("common.working") : t("add.discoverUploadSelected")}</button>
            </>
          ) : null}
        </div>
      ) : (
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
      )}
    </section>
  );
}
