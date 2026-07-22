import { FolderInput, FolderOpen, GitBranch, Plug, X } from "lucide-react";
import { type FormEvent, useEffect, useMemo, useState } from "react";
import { lpmAction, selectDirectory } from "@/api/client";
import { resourceKindLabel, type TFunction } from "@/app/i18n";
import { useTaskCenter } from "@/app/TaskCenterContext";
import { Banner } from "@/components/Banner";
import type {
  AddResourceResult,
  CollectResourcePayload,
  McpTransport,
  PluginOriginType,
  PluginPlatform,
  PluginProject,
  PluginReferenceResult,
  PluginScope,
  PortableMcpConfig,
  ResourceKind,
} from "@/types/lpm";

type AddMode = "collect" | "import" | "plugin-reference";
type AddKind = "auto" | ResourceKind;

interface AddDraft {
  value: string;
  name: string;
  kind: AddKind;
  pluginId: string;
  platform: PluginPlatform;
  originType: Exclude<PluginOriginType, "local">;
  marketplace: string;
  source: string;
  packageName: string;
  repo: string;
  selector: string;
  scope: PluginScope;
  projectId: string;
  enabled: boolean;
  mcpTransport: McpTransport;
  mcpCommand: string;
  mcpArgs: string;
  mcpUrl: string;
  mcpEnv: string;
}

const kinds: ResourceKind[] = ["skill", "mcp", "rule", "prompt", "plugin"];

function emptyDraft(): AddDraft {
  return {
    value: "",
    name: "",
    kind: "auto",
    pluginId: "",
    platform: "codex",
    originType: "marketplace",
    marketplace: "",
    source: "",
    packageName: "",
    repo: "",
    selector: "",
    scope: "user",
    projectId: "",
    enabled: true,
    mcpTransport: "stdio",
    mcpCommand: "",
    mcpArgs: "",
    mcpUrl: "",
    mcpEnv: "",
  };
}

function nonEmptyLines(value: string): string[] {
  return value
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
}

function parseMcpEnvironment(value: string): Record<string, string> | null {
  const env: Record<string, string> = {};
  for (const line of nonEmptyLines(value)) {
    const nameOnly = /^([A-Za-z_][A-Za-z0-9_]*)$/.exec(line);
    const placeholderOnly = /^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$/.exec(line);
    const mapping = /^([A-Za-z_][A-Za-z0-9_]*)=(\$\{[A-Za-z_][A-Za-z0-9_]*\})$/.exec(line);
    if (nameOnly) {
      env[nameOnly[1]] = `\${${nameOnly[1]}}`;
    } else if (placeholderOnly) {
      env[placeholderOnly[1]] = placeholderOnly[0];
    } else if (mapping) {
      env[mapping[1]] = mapping[2];
    } else {
      return null;
    }
  }
  return env;
}

function buildMcpConfig(draft: AddDraft, env: Record<string, string>): PortableMcpConfig {
  const withEnvironment = Object.keys(env).length ? { env } : {};
  if (draft.mcpTransport === "http") {
    return {
      type: "http",
      url: draft.mcpUrl.trim(),
      ...withEnvironment,
    };
  }
  const args = nonEmptyLines(draft.mcpArgs);
  return {
    command: draft.mcpCommand.trim(),
    ...(args.length ? { args } : {}),
    ...withEnvironment,
  };
}

export function AddResourceDialog({
  t,
  onClose,
  onAdded,
}: {
  t: TFunction;
  onClose: () => void;
  onAdded: (resourceKey: string) => Promise<void> | void;
}) {
  const { runTask } = useTaskCenter();
  const [mode, setMode] = useState<AddMode>("collect");
  const [drafts, setDrafts] = useState<Record<AddMode, AddDraft>>({
    collect: emptyDraft(),
    import: emptyDraft(),
    "plugin-reference": emptyDraft(),
  });
  const [projects, setProjects] = useState<PluginProject[]>([]);
  const [pushAfterCompletion, setPushAfterCompletion] = useState(true);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [discardOpen, setDiscardOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [pathError, setPathError] = useState("");
  const draft = drafts[mode];
  const dirty = useMemo(() => (
    !pushAfterCompletion
    || Object.values(drafts).some((item) => (
      Boolean(item.value.trim())
      || Boolean(item.name.trim())
      || item.kind !== "auto"
      || Boolean(item.pluginId.trim())
      || Boolean(item.marketplace.trim())
      || Boolean(item.source.trim())
      || Boolean(item.packageName.trim())
      || Boolean(item.repo.trim())
      || Boolean(item.selector.trim())
      || item.platform !== "codex"
      || item.originType !== "marketplace"
      || item.scope !== "user"
      || Boolean(item.projectId)
      || !item.enabled
      || item.mcpTransport !== "stdio"
      || Boolean(item.mcpCommand.trim())
      || Boolean(item.mcpArgs.trim())
      || Boolean(item.mcpUrl.trim())
      || Boolean(item.mcpEnv.trim())
    ))
  ), [drafts, pushAfterCompletion]);

  useEffect(() => {
    if (mode !== "plugin-reference" || projects.length) return;
    void lpmAction<{ projects: PluginProject[] }>("plugin_projects_list")
      .then((result) => setProjects(result.projects))
      .catch((error) => setPathError(error instanceof Error ? error.message : String(error)));
  }, [mode, projects.length]);

  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key !== "Escape" || busy) return;
      if (discardOpen) {
        setDiscardOpen(false);
      } else if (dirty) {
        setDiscardOpen(true);
      } else {
        onClose();
      }
    }
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [busy, dirty, discardOpen, onClose]);

  function updateDraft(targetMode: AddMode, changes: Partial<AddDraft>) {
    setDrafts((current) => ({
      ...current,
      [targetMode]: { ...current[targetMode], ...changes },
    }));
  }

  function requestClose() {
    if (busy) return;
    if (dirty) setDiscardOpen(true);
    else onClose();
  }

  async function chooseDirectory() {
    setPathError("");
    try {
      const selected = await selectDirectory();
      if (selected) updateDraft("import", { value: selected });
    } catch (error) {
      setPathError(error instanceof Error ? error.message : String(error));
    }
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    setPathError("");
    const collectMode = mode === "collect";
    const pluginMode = mode === "plugin-reference";
    let collectPayload: CollectResourcePayload | undefined;
    if (collectMode) {
      let mcpConfig: PortableMcpConfig | undefined;
      if (draft.kind === "mcp") {
        const env = parseMcpEnvironment(draft.mcpEnv);
        if (env === null) {
          setPathError(t("add.mcpEnvInvalid"));
          return;
        }
        mcpConfig = buildMcpConfig(draft, env);
      }
      collectPayload = {
        github_url: draft.value,
        ...(draft.kind === "auto" ? {} : { kind: draft.kind }),
        name: draft.name,
        push: pushAfterCompletion,
        ...(mcpConfig ? { mcp_config: mcpConfig } : {}),
      };
    }
    setBusy(true);
    try {
      const result = await runTask<PluginReferenceResult | AddResourceResult>({
        kind: pluginMode ? "plugin-reference-add" : collectMode ? "resource-collect" : "resource-import",
        title: pluginMode ? t("add.modePluginReference") : collectMode ? t("add.modeCollect") : t("add.modeImport"),
        context: draft.name || draft.pluginId || draft.value,
        action: () => pluginMode
          ? lpmAction<PluginReferenceResult>("plugin_reference_add", {
              platform: draft.platform,
              plugin_id: draft.pluginId,
              origin_type: draft.originType,
              marketplace: draft.marketplace,
              source: draft.source,
              package: draft.packageName,
              repo: draft.repo,
              selector: draft.selector,
              scope: draft.scope,
              project_id: draft.projectId,
              enabled: draft.enabled,
              name: draft.name,
              push: pushAfterCompletion,
            })
          : lpmAction<AddResourceResult>(collectMode ? "collect" : "upload", collectMode ? collectPayload! : {
              path: draft.value,
              kind: draft.kind === "auto" ? undefined : draft.kind,
              name: draft.name,
              no_push: !pushAfterCompletion,
            }),
        successMessage: pluginMode ? t("add.successPluginReference") : collectMode ? t("add.successCollected") : t("add.successImported"),
        retryPolicy: "none",
      });
      await Promise.resolve(onAdded(pluginMode
        ? (result as PluginReferenceResult).resource_key
        : `${(result as AddResourceResult).entry.kind}:${(result as AddResourceResult).entry.name}`));
    } catch {
      // TaskCenter owns feedback for tracked operations.
    } finally {
      setBusy(false);
    }
  }

  const modeTitle = mode === "collect"
    ? t("add.modeCollect")
    : mode === "import"
      ? t("add.modeImport")
      : t("add.modePluginReference");
  const ModeIcon = mode === "collect" ? GitBranch : mode === "import" ? FolderInput : Plug;

  return (
    <>
      <div
        className="modal-backdrop"
        role="presentation"
        onMouseDown={(event) => {
          if (event.target === event.currentTarget) requestClose();
        }}
      >
        <div className="modal add-resource-modal" role="dialog" aria-modal="true" aria-labelledby="add-resource-title">
          <div className="modal-head">
            <ModeIcon size={19} />
            <h2 id="add-resource-title">{t("add.title")}</h2>
            <button className="icon-button modal-close" type="button" onClick={requestClose} aria-label={t("common.close")} disabled={busy}>
              <X size={17} />
            </button>
          </div>

          <p className="add-resource-description">{t("add.description")}</p>

          <div className="mode-tabs add-resource-modes" role="tablist" aria-label={t("add.title")}>
            <button type="button" role="tab" aria-selected={mode === "collect"} className={mode === "collect" ? "active" : ""} onClick={() => setMode("collect")}>
              <GitBranch size={17} />{t("add.modeCollect")}
            </button>
            <button type="button" role="tab" aria-selected={mode === "import"} className={mode === "import" ? "active" : ""} onClick={() => setMode("import")}>
              <FolderInput size={17} />{t("add.modeImport")}
            </button>
            <button type="button" role="tab" aria-selected={mode === "plugin-reference"} className={mode === "plugin-reference" ? "active" : ""} onClick={() => setMode("plugin-reference")}>
              <Plug size={17} />{t("add.modePluginReference")}
            </button>
          </div>

          <form onSubmit={submit} className="stack-form add-resource-form">
            {mode !== "plugin-reference" ? <label>
              <span>{mode === "collect" ? t("add.githubUrl") : t("add.localPath")}</span>
              {mode === "collect" ? (
                <input
                  value={draft.value}
                  onChange={(event) => updateDraft("collect", { value: event.target.value })}
                  autoFocus
                  required
                />
              ) : (
                <div className="path-input-row">
                  <input
                    value={draft.value}
                    onChange={(event) => {
                      setPathError("");
                      updateDraft("import", { value: event.target.value });
                    }}
                    required
                  />
                  <button className="secondary" type="button" onClick={() => void chooseDirectory()} disabled={busy}>
                    <FolderOpen size={16} />{t("add.browse")}
                  </button>
                </div>
              )}
            </label> : (
              <div className="plugin-reference-fields">
                <label>
                  <span>{t("plugin.platform")}</span>
                  <select value={draft.platform} onChange={(event) => updateDraft(mode, { platform: event.target.value as PluginPlatform })}>
                    <option value="codex">Codex</option>
                    <option value="claude-code">Claude Code</option>
                    <option value="opencode">OpenCode</option>
                  </select>
                </label>
                <label>
                  <span>{t("plugin.pluginId")}</span>
                  <input value={draft.pluginId} onChange={(event) => updateDraft(mode, { pluginId: event.target.value })} required autoFocus />
                </label>
                <label>
                  <span>{t("plugin.originType")}</span>
                  <select value={draft.originType} onChange={(event) => updateDraft(mode, { originType: event.target.value as AddDraft["originType"] })}>
                    <option value="marketplace">marketplace</option>
                    <option value="npm">npm</option>
                    <option value="git">Git</option>
                  </select>
                </label>
                {draft.originType === "marketplace" ? (
                  <label><span>{t("plugin.marketplace")}</span><input value={draft.marketplace} onChange={(event) => updateDraft(mode, { marketplace: event.target.value })} required /></label>
                ) : null}
                {draft.originType === "npm" ? (
                  <label><span>{t("plugin.package")}</span><input value={draft.packageName} onChange={(event) => updateDraft(mode, { packageName: event.target.value })} required /></label>
                ) : null}
                {draft.originType === "git" ? (
                  <label><span>{t("plugin.repo")}</span><input value={draft.repo} onChange={(event) => updateDraft(mode, { repo: event.target.value })} required /></label>
                ) : null}
                <label><span>{t("plugin.selector")}</span><input value={draft.selector} onChange={(event) => updateDraft(mode, { selector: event.target.value })} placeholder={t("plugin.selectorHint")} /></label>
                <label>
                  <span>{t("plugin.scope")}</span>
                  <select value={draft.scope} onChange={(event) => updateDraft(mode, { scope: event.target.value as PluginScope, projectId: "" })}>
                    <option value="user">user</option>
                    <option value="project">project</option>
                    <option value="local">local</option>
                    <option value="managed">managed</option>
                  </select>
                </label>
                {draft.scope === "project" || draft.scope === "local" ? (
                  <label>
                    <span>{t("plugin.project")}</span>
                    <select value={draft.projectId} onChange={(event) => updateDraft(mode, { projectId: event.target.value })} required>
                      <option value="">-</option>
                      {projects.filter((project) => project.portable).map((project) => <option value={project.id} key={project.id}>{project.path}</option>)}
                    </select>
                  </label>
                ) : null}
                <label className="checkline">
                  <input type="checkbox" checked={draft.enabled} onChange={(event) => updateDraft(mode, { enabled: event.target.checked })} />
                  <span>{t("plugin.desiredEnabled")}</span>
                </label>
              </div>
            )}

            {pathError ? <Banner tone="danger" text={pathError} /> : null}

            <details className="add-resource-advanced" open={advancedOpen} onToggle={(event) => setAdvancedOpen(event.currentTarget.open)}>
              <summary>{t("add.advanced")}</summary>
              <div className="add-resource-advanced-fields">
                <label>
                  <span>{t("add.resourceName")}</span>
                  <input value={draft.name} onChange={(event) => updateDraft(mode, { name: event.target.value })} placeholder={t("add.inferPlaceholder")} />
                </label>
                {mode !== "plugin-reference" ? <label>
                  <span>{t("add.type")}</span>
                  <select value={draft.kind} onChange={(event) => updateDraft(mode, { kind: event.target.value as AddKind })}>
                    <option value="auto">{t("kind.auto")}</option>
                    {kinds.map((item) => <option key={item} value={item}>{resourceKindLabel(item, t)}</option>)}
                  </select>
                </label> : (
                  <label><span>{t("plugin.source")}</span><input value={draft.source} onChange={(event) => updateDraft(mode, { source: event.target.value })} /></label>
                )}
              </div>
            </details>

            {mode === "collect" && draft.kind === "auto" ? (
              <p className="field-note add-mcp-auto-note">{t("add.mcpAutoWarning")}</p>
            ) : null}

            {mode === "collect" && draft.kind === "mcp" ? (
              <fieldset className="add-mcp-config">
                <legend>{t("add.mcpConfig")}</legend>
                <p>{t("add.mcpReferenceHint")}</p>
                <div className="add-mcp-config-grid">
                  <label>
                    <span>{t("add.mcpTransport")}</span>
                    <select
                      value={draft.mcpTransport}
                      onChange={(event) => {
                        setPathError("");
                        updateDraft("collect", { mcpTransport: event.target.value as McpTransport });
                      }}
                    >
                      <option value="stdio">{t("add.mcpStdio")}</option>
                      <option value="http">{t("add.mcpHttp")}</option>
                    </select>
                  </label>
                  {draft.mcpTransport === "stdio" ? (
                    <>
                      <label>
                        <span>{t("add.mcpCommand")}</span>
                        <input
                          value={draft.mcpCommand}
                          onChange={(event) => {
                            setPathError("");
                            updateDraft("collect", { mcpCommand: event.target.value });
                          }}
                          required
                        />
                      </label>
                      <label className="add-mcp-wide-field">
                        <span>{t("add.mcpArgs")}</span>
                        <textarea
                          value={draft.mcpArgs}
                          onChange={(event) => updateDraft("collect", { mcpArgs: event.target.value })}
                        />
                      </label>
                    </>
                  ) : (
                    <label>
                      <span>{t("add.mcpUrl")}</span>
                      <input
                        type="url"
                        value={draft.mcpUrl}
                        onChange={(event) => {
                          setPathError("");
                          updateDraft("collect", { mcpUrl: event.target.value });
                        }}
                        required
                      />
                    </label>
                  )}
                  <label className="add-mcp-wide-field">
                    <span>{t("add.mcpEnv")}</span>
                    <textarea
                      aria-label={t("add.mcpEnv")}
                      value={draft.mcpEnv}
                      onChange={(event) => {
                        setPathError("");
                        updateDraft("collect", { mcpEnv: event.target.value });
                      }}
                      placeholder={t("add.mcpEnvPlaceholder")}
                    />
                    <small>{t("add.mcpEnvHint")}</small>
                  </label>
                </div>
              </fieldset>
            ) : null}

            <label className="add-push-option">
              <input type="checkbox" checked={pushAfterCompletion} onChange={(event) => setPushAfterCompletion(event.target.checked)} />
              <span>{t("add.pushAfterCompletion")}</span>
            </label>

            <div className="modal-actions">
              <button className="secondary" type="button" onClick={requestClose} disabled={busy}>{t("common.cancel")}</button>
              <button className="primary" disabled={busy}>{busy ? t("common.working") : modeTitle}</button>
            </div>
          </form>
        </div>
      </div>

      {discardOpen ? (
        <div className="modal-backdrop add-discard-backdrop" role="presentation" onMouseDown={(event) => {
          if (event.target === event.currentTarget) setDiscardOpen(false);
        }}>
          <div className="modal add-discard-modal" role="alertdialog" aria-modal="true" aria-labelledby="discard-add-title" aria-describedby="discard-add-description">
            <div className="modal-head danger-head">
              <X size={19} />
              <h2 id="discard-add-title">{t("add.discardTitle")}</h2>
            </div>
            <p id="discard-add-description">{t("add.discardDescription")}</p>
            <div className="modal-actions">
              <button className="secondary" type="button" onClick={() => setDiscardOpen(false)}>{t("common.cancel")}</button>
              <button className="danger" type="button" onClick={onClose}>{t("add.discard")}</button>
            </div>
          </div>
        </div>
      ) : null}
    </>
  );
}
