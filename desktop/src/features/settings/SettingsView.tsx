import {
  AlertTriangle,
  BadgeCheck,
  ChevronDown,
  GitBranch,
  Link2,
  RefreshCcw,
  Save,
  ShieldCheck,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { lpmAction } from "@/api/client";
import type { TFunction } from "@/app/i18n";
import { useTaskCenter } from "@/app/TaskCenterContext";
import type {
  ConfigBindRepoResult,
  ConfigBranchOptions,
  ConfigSettings,
  EditableConfig,
  PlatformProfile,
  ResourceRepoBinding,
} from "@/types/lpm";

type SavePayload = {
  draft: EditableConfig;
  token_action: "preserve" | "replace" | "clear";
  new_token?: string;
};

export function SettingsView({
  t,
  onError,
  onChanged,
}: {
  t: TFunction;
  onError: (message: string) => void;
  onChanged: () => Promise<void> | void;
}) {
  const { runTask } = useTaskCenter();
  const [settings, setSettings] = useState<ConfigSettings | null>(null);
  const [draft, setDraft] = useState<EditableConfig | null>(null);
  const [bindUrl, setBindUrl] = useState("");
  const [lastBinding, setLastBinding] = useState<ResourceRepoBinding | null>(null);
  const [bindError, setBindError] = useState("");
  const [pendingRebindUrl, setPendingRebindUrl] = useState("");
  const [newToken, setNewToken] = useState("");
  const [clearToken, setClearToken] = useState(false);
  const [loading, setLoading] = useState(false);
  const [binding, setBinding] = useState(false);
  const [saving, setSaving] = useState(false);
  const [branchOptions, setBranchOptions] = useState<ConfigBranchOptions>(() => fallbackBranchOptions("main"));
  const [loadingBranches, setLoadingBranches] = useState(false);
  const draftRef = useRef<EditableConfig | null>(null);
  const newTokenRef = useRef("");
  const clearTokenRef = useRef(false);
  const branchRequestRef = useRef(0);

  const actionBusy = binding || saving;
  const anyBusy = loading || actionBusy;

  useEffect(() => {
    draftRef.current = draft;
  }, [draft]);

  useEffect(() => {
    newTokenRef.current = newToken;
  }, [newToken]);

  useEffect(() => {
    clearTokenRef.current = clearToken;
  }, [clearToken]);

  function applySettings(data: ConfigSettings, resetBindInput = true) {
    setSettings(data);
    setDraft(data.config);
    if (resetBindInput) setBindUrl(data.config.resources.repo_url);
    setBranchOptions(fallbackBranchOptions(data.config.resources.branch));
    setNewToken("");
    setClearToken(false);
  }

  async function loadSettings(track = false) {
    setLoading(true);
    try {
      const request = async () => {
        const data = await lpmAction<ConfigSettings>("config_get");
        applySettings(data);
        setLastBinding(null);
        setBindError("");
        return data;
      };
      if (track) {
        await runTask({
          kind: "settings-reload",
          title: t("common.reload"),
          action: request,
          retryPolicy: "safe-read",
        });
      } else {
        await request();
      }
    } catch (err) {
      if (!track) onError(errorMessage(err));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void loadSettings();
  }, []);

  function updateGithub(key: keyof EditableConfig["github"], value: string | boolean) {
    setDraft((current) => current && { ...current, github: { ...current.github, [key]: value } });
  }

  function updateInstall(key: keyof EditableConfig["install"], value: string) {
    setDraft((current) => current && { ...current, install: { ...current.install, [key]: value } });
  }

  function updateGit(key: keyof EditableConfig["git"], value: string) {
    setDraft((current) => current && { ...current, git: { ...current.git, [key]: value } });
  }

  function updateResources(key: keyof EditableConfig["resources"], value: string) {
    setDraft((current) => current && { ...current, resources: { ...current.resources, [key]: value } });
  }

  function updateState(key: keyof EditableConfig["state"], value: number) {
    setDraft((current) => current && {
      ...current,
      state: { ...current.state, [key]: value },
    });
  }

  function updatePlatform(index: number, patch: Partial<PlatformProfile>) {
    setDraft((current) => current && {
      ...current,
      platforms: current.platforms.map((platform, itemIndex) => (
        itemIndex === index ? { ...platform, ...patch } : platform
      )),
    });
  }

  function buildPayloadFromDraft(nextDraft: EditableConfig): SavePayload {
    const trimmedToken = newToken.trim();
    return {
      draft: nextDraft,
      token_action: clearToken ? "clear" : trimmedToken ? "replace" : "preserve",
      new_token: trimmedToken || undefined,
    };
  }

  async function loadBranchOptions(nextDraft = draft) {
    if (!nextDraft) return;
    const requestId = branchRequestRef.current + 1;
    branchRequestRef.current = requestId;
    setLoadingBranches(true);
    try {
      const options = await lpmAction<ConfigBranchOptions>(
        "config_branches",
        buildPayloadFromDraft(nextDraft),
      );
      if (branchRequestRef.current !== requestId) return;
      setBranchOptions({
        ...options,
        branches: branchChoices(options, nextDraft.resources.branch),
      });
    } catch (err) {
      if (branchRequestRef.current !== requestId) return;
      setBranchOptions({
        ...fallbackBranchOptions(nextDraft.resources.branch),
        warning: errorMessage(err),
      });
    } finally {
      if (branchRequestRef.current === requestId) setLoadingBranches(false);
    }
  }

  function requestBind() {
    if (!settings) return;
    const requestedUrl = bindUrl.trim();
    if (!requestedUrl) {
      setBindError(t("settings.bindUrlRequired"));
      return;
    }
    const currentUrl = settings.config.resources.repo_url.trim();
    if (currentUrl && requestedUrl !== currentUrl) {
      setPendingRebindUrl(requestedUrl);
      return;
    }
    void bindRepository(requestedUrl);
  }

  async function bindRepository(repoUrl: string) {
    if (!settings) return;
    const expectedCurrentUrl = settings.config.resources.repo_url;
    setPendingRebindUrl("");
    setBinding(true);
    setBindError("");
    try {
      const result = await runTask({
        kind: "settings-bind-repo",
        title: t("settings.bindingRepo"),
        context: repoUrl,
        action: () => lpmAction<ConfigBindRepoResult>("config_bind_repo", {
          repo_url: repoUrl,
          expected_current_repo_url: expectedCurrentUrl,
        }),
        successMessage: t("settings.bindSuccess"),
        retryPolicy: "none",
      });
      applySettings(result.settings);
      setLastBinding(result.binding);
      setBranchOptions({
        branches: branchChoices({
          branches: result.binding.branches,
          default_branch: result.binding.branch,
          selected_branch: result.binding.branch,
          warning: "",
        }, result.binding.branch),
        default_branch: result.binding.branch,
        selected_branch: result.binding.branch,
        warning: "",
      });
      void onChanged();
    } catch (err) {
      setBindError(errorMessage(err));
    } finally {
      setBinding(false);
    }
  }

  async function saveSettings() {
    if (!draft) return;
    const payload = buildPayloadFromDraft(draft);
    const savedToken = payload.new_token || "";
    const savedClearToken = payload.token_action === "clear";
    setSaving(true);
    try {
      const saved = await runTask({
        kind: "settings-save",
        title: t("settings.savingShort"),
        action: () => lpmAction<ConfigSettings>("config_save", payload),
        successMessage: t("settings.saved"),
        retryPolicy: "none",
      });
      setSettings(saved);
      setDraft((current) => (current === payload.draft ? saved.config : current));
      if (
        draftRef.current === payload.draft
        && newTokenRef.current.trim() === savedToken
        && clearTokenRef.current === savedClearToken
      ) {
        setNewToken("");
        setClearToken(false);
      }
      void onChanged();
    } catch {
      // TaskCenter owns feedback for tracked operations.
    } finally {
      setSaving(false);
    }
  }

  if (!draft || !settings) {
    return (
      <section className="panel">
        <div className="panel-head">
          <h2>{t("settings.title")}</h2>
          <button className="secondary" type="button" onClick={() => void loadSettings(true)} disabled={anyBusy}>
            <RefreshCcw size={17} />{t("common.refresh")}
          </button>
        </div>
      </section>
    );
  }

  const currentUrl = settings.config.resources.repo_url.trim();
  const isSameUrl = Boolean(currentUrl) && bindUrl.trim() === currentUrl;
  const bindButtonLabel = binding
    ? t("settings.binding")
    : isSameUrl
      ? t("settings.reverify")
      : currentUrl
        ? t("settings.rebind")
        : t("settings.bind");

  return (
    <section className="settings-view">
      <div className="panel settings-panel">
        <div className="panel-head">
          <div>
            <h2>{t("settings.title")}</h2>
            <p>{settings.path}</p>
          </div>
          <button className="secondary" type="button" onClick={() => void loadSettings(true)} disabled={anyBusy}>
            <RefreshCcw size={17} />{t("common.reload")}
          </button>
        </div>

        <section className="repo-binding-card" aria-labelledby="repo-binding-title">
          <div className="repo-binding-head">
            <div>
              <span className="repo-binding-icon"><Link2 size={20} /></span>
              <div>
                <h3 id="repo-binding-title">{t("settings.quickBindTitle")}</h3>
                <p>{t("settings.quickBindDescription")}</p>
              </div>
            </div>
            <span className={currentUrl ? "connection-pill connected" : "connection-pill"}>
              {currentUrl ? <BadgeCheck size={15} /> : null}
              {currentUrl ? t("settings.bound") : t("settings.unbound")}
            </span>
          </div>

          <div className="repo-bind-control">
            <label>
              <span>{t("settings.repoUrl")}</span>
              <input
                value={bindUrl}
                placeholder={t("settings.repoUrlPlaceholder")}
                onChange={(event) => {
                  setBindUrl(event.target.value);
                  setBindError("");
                  setLastBinding(null);
                }}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !anyBusy) requestBind();
                }}
                aria-invalid={Boolean(bindError)}
                autoComplete="url"
              />
            </label>
            <button className="primary" type="button" onClick={requestBind} disabled={anyBusy || !bindUrl.trim()}>
              {binding ? <RefreshCcw className="spin" size={17} /> : <Link2 size={17} />}
              {bindButtonLabel}
            </button>
          </div>

          {bindError ? <div className="repo-bind-error" role="alert">{bindError}</div> : null}

          {currentUrl ? (
            <div className="repo-binding-status" aria-live="polite">
              <div>
                <span>{t("settings.boundRepository")}</span>
                <strong>{settings.config.resources.repo_name}</strong>
                <small>{currentUrl}</small>
              </div>
              <div>
                <span>{t("settings.branch")}</span>
                <strong><GitBranch size={15} />{settings.config.resources.branch}</strong>
                <small>{credentialLabel(t, settings.config.resources.credential_mode, currentUrl)}</small>
              </div>
              <div>
                <span>{t("settings.localPath")}</span>
                <strong>{settings.config.resources.local_path || t("settings.createOnFirstPull")}</strong>
                <small>{t("settings.noTransferOnBind")}</small>
              </div>
            </div>
          ) : null}

          {lastBinding ? (
            <div className="repo-bind-success" role="status">
              <ShieldCheck size={18} />
              <div>
                <strong>{t("settings.readWriteVerified")}</strong>
                <span>{lastBinding.remote_empty ? t("settings.remoteEmpty") : t("settings.remoteReady")}</span>
              </div>
            </div>
          ) : null}
        </section>

        <details className="settings-advanced">
          <summary>
            <span>
              <ChevronDown size={18} />
              <span>
                <strong>{t("settings.advanced")}</strong>
                <small>{t("settings.advancedDescription")}</small>
              </span>
            </span>
          </summary>

          <div className="advanced-settings-body">
            <div className="advanced-settings-actions">
              <button className="primary" type="button" onClick={saveSettings} disabled={anyBusy}>
                <Save size={17} />{saving ? t("settings.savingShort") : t("settings.saveAdvanced")}
              </button>
            </div>

            <div className="settings-sections">
              <div className="settings-section">
                <h3>{t("settings.resourceRepository")}</h3>
                <div className="stack-form two-column">
                  <label>
                    <span>{t("settings.repoName")}</span>
                    <input value={draft.resources.repo_name} readOnly />
                  </label>
                  <label>
                    <span className="field-heading">
                      <span>{t("settings.branch")}</span>
                      <button
                        className="field-action"
                        type="button"
                        onClick={() => void loadBranchOptions()}
                        disabled={loadingBranches || actionBusy || !draft.resources.repo_url}
                        title={t("settings.branchRefresh")}
                      >
                        <RefreshCcw size={14} />
                        {loadingBranches ? t("settings.branchLoading") : t("settings.branchRefresh")}
                      </button>
                    </span>
                    <select
                      value={draft.resources.branch || branchOptions.selected_branch || "main"}
                      onChange={(event) => updateResources("branch", event.target.value)}
                      disabled={loadingBranches}
                    >
                      {branchChoices(branchOptions, draft.resources.branch).map((branch) => (
                        <option key={branch} value={branch}>{branch}</option>
                      ))}
                    </select>
                    {branchOptions.warning ? (
                      <small className="field-note">
                        {t("settings.branchLoadWarning", { message: branchOptions.warning })}
                      </small>
                    ) : null}
                  </label>
                  <label>
                    <span>{t("settings.credentialMode")}</span>
                    <select
                      value={draft.resources.credential_mode}
                      onChange={(event) => updateResources("credential_mode", event.target.value)}
                    >
                      <option value="native">{t("settings.credentialNative")}</option>
                      <option value="auto">{t("settings.credentialAuto")}</option>
                      <option value="token">{t("settings.credentialToken")}</option>
                    </select>
                  </label>
                  <label>
                    <span>{t("settings.localPath")}</span>
                    <input
                      value={draft.resources.local_path}
                      placeholder={t("settings.createOnFirstPull")}
                      onChange={(event) => updateResources("local_path", event.target.value)}
                    />
                  </label>
                </div>
              </div>

              <div className="settings-section">
                <h3>{t("settings.github")}</h3>
                {settings.env_token_active ? (
                  <div className="inline-warning">
                    <AlertTriangle size={17} />
                    <span>{t("settings.envTokenWarning")}</span>
                  </div>
                ) : null}
                <div className="stack-form two-column">
                  <label>
                    <span>{t("settings.tokenPreview")}</span>
                    <input value={settings.token_preview || t("settings.notConfigured")} readOnly />
                  </label>
                  <label>
                    <span>{t("settings.newToken")}</span>
                    <input
                      type="password"
                      value={newToken}
                      onChange={(event) => {
                        setNewToken(event.target.value);
                        if (event.target.value) setClearToken(false);
                      }}
                      disabled={clearToken}
                      autoComplete="off"
                    />
                  </label>
                  <label className="checkline">
                    <input
                      type="checkbox"
                      checked={clearToken}
                      onChange={(event) => {
                        setClearToken(event.target.checked);
                        if (event.target.checked) setNewToken("");
                      }}
                    />
                    <span>{t("settings.clearStoredToken")}</span>
                  </label>
                  <label className="checkline">
                    <input
                      type="checkbox"
                      checked={draft.github.default_private}
                      onChange={(event) => updateGithub("default_private", event.target.checked)}
                    />
                    <span>{t("settings.defaultPrivate")}</span>
                  </label>
                  <label>
                    <span>{t("settings.owner")}</span>
                    <input value={draft.github.owner} onChange={(event) => updateGithub("owner", event.target.value)} />
                  </label>
                  <label>
                    <span>{t("settings.repoPrefix")}</span>
                    <input
                      value={draft.github.repo_prefix}
                      onChange={(event) => updateGithub("repo_prefix", event.target.value)}
                    />
                  </label>
                </div>
              </div>

              <div className="settings-section">
                <h3>{t("settings.platforms")}</h3>
                <div className="platform-editor-list">
                  {draft.platforms.map((platform, index) => (
                    <div className="platform-editor" key={platform.name}>
                      <div className="platform-editor-head">
                        <div className="platform-editor-title">
                          <strong>{platform.name}</strong>
                          <span className={platform.enabled ? "platform-status enabled" : "platform-status disabled"}>
                            {platform.enabled ? t("settings.enabled") : t("settings.disabled")}
                          </span>
                        </div>
                        <label className="checkline">
                          <input
                            type="checkbox"
                            checked={platform.enabled}
                            onChange={(event) => updatePlatform(index, { enabled: event.target.checked })}
                          />
                          <span>{t("settings.enabled")}</span>
                        </label>
                      </div>
                      <div className="stack-form four-column">
                        <label>
                          <span>{t("settings.skills")}</span>
                          <input
                            value={platform.skills_dir}
                            placeholder={t("settings.notConfigured")}
                            onChange={(event) => updatePlatform(index, { skills_dir: event.target.value })}
                          />
                        </label>
                        <label>
                          <span>{t("settings.mcpJson")}</span>
                          <input
                            value={platform.mcp_json}
                            placeholder={t("settings.notConfigured")}
                            onChange={(event) => updatePlatform(index, { mcp_json: event.target.value })}
                          />
                        </label>
                        <label>
                          <span>{t("settings.rules")}</span>
                          <input
                            value={platform.rules_dir}
                            placeholder={t("settings.notConfigured")}
                            onChange={(event) => updatePlatform(index, { rules_dir: event.target.value })}
                          />
                        </label>
                        <label>
                          <span>{t("settings.plugins")}</span>
                          <input
                            value={platform.plugins_dir}
                            placeholder={t("settings.notConfigured")}
                            onChange={(event) => updatePlatform(index, { plugins_dir: event.target.value })}
                          />
                        </label>
                      </div>
                    </div>
                  ))}
                </div>
              </div>

              <div className="settings-section settings-grid-pair">
                <div>
                  <h3>{t("settings.install")}</h3>
                  <div className="stack-form">
                    <label>
                      <span>{t("settings.fallbackTarget")}</span>
                      <input value={draft.install.target} onChange={(event) => updateInstall("target", event.target.value)} />
                    </label>
                  </div>
                </div>
                <div>
                  <h3>{t("settings.gitRuntime")}</h3>
                  <div className="stack-form">
                    <label>
                      <span>{t("settings.gitExecutable")}</span>
                      <input
                        value={draft.git.executable}
                        placeholder={t("settings.gitExecutablePlaceholder")}
                        onChange={(event) => updateGit("executable", event.target.value)}
                      />
                      <small className="field-note">{t("settings.gitExecutableNote")}</small>
                    </label>
                  </div>
                </div>
              </div>

              <div className="settings-section">
                <h3>{t("settings.localState")}</h3>
                <div className="stack-form four-column">
                  <label>
                    <span>{t("settings.lockTimeout")}</span>
                    <input
                      type="number"
                      min="0.1"
                      step="0.1"
                      value={draft.state.lock_timeout_seconds}
                      onChange={(event) => updateState("lock_timeout_seconds", Math.max(0.1, Number(event.target.value) || 0.1))}
                    />
                  </label>
                  <label>
                    <span>{t("settings.retentionDays")}</span>
                    <input
                      type="number"
                      min="0"
                      value={draft.state.retention_days}
                      onChange={(event) => updateState("retention_days", Math.max(0, Number.parseInt(event.target.value, 10) || 0))}
                    />
                  </label>
                  <label>
                    <span>{t("settings.keepLatest")}</span>
                    <input
                      type="number"
                      min="0"
                      value={draft.state.keep_latest_operations}
                      onChange={(event) => updateState("keep_latest_operations", Math.max(0, Number.parseInt(event.target.value, 10) || 0))}
                    />
                  </label>
                  <label>
                    <span>{t("settings.maxBackupMb")}</span>
                    <input
                      type="number"
                      min="0"
                      value={draft.state.max_backup_mb}
                      onChange={(event) => updateState("max_backup_mb", Math.max(0, Number.parseInt(event.target.value, 10) || 0))}
                    />
                  </label>
                </div>
                <small className="field-note">{t("settings.stateNote")}</small>
              </div>
            </div>
          </div>
        </details>
      </div>

      {pendingRebindUrl ? (
        <RebindModal
          currentUrl={currentUrl}
          nextUrl={pendingRebindUrl}
          currentLocalPath={settings.config.resources.local_path}
          busy={binding}
          t={t}
          onCancel={() => setPendingRebindUrl("")}
          onConfirm={() => void bindRepository(pendingRebindUrl)}
        />
      ) : null}
    </section>
  );
}

function RebindModal({
  currentUrl,
  nextUrl,
  currentLocalPath,
  busy,
  t,
  onCancel,
  onConfirm,
}: {
  currentUrl: string;
  nextUrl: string;
  currentLocalPath: string;
  busy: boolean;
  t: TFunction;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  return (
    <div className="modal-backdrop">
      <div className="modal rebind-modal" role="dialog" aria-modal="true" aria-labelledby="rebind-title">
        <div className="modal-head">
          <AlertTriangle size={20} />
          <h2 id="rebind-title">{t("settings.rebindTitle")}</h2>
        </div>
        <p>{t("settings.rebindDescription")}</p>
        <dl className="rebind-comparison">
          <div><dt>{t("settings.currentRepository")}</dt><dd>{currentUrl}</dd></div>
          <div><dt>{t("settings.newRepository")}</dt><dd>{nextUrl}</dd></div>
          <div><dt>{t("settings.existingLocalPath")}</dt><dd>{currentLocalPath || t("settings.notCreated")}</dd></div>
        </dl>
        <div className="modal-actions">
          <button className="secondary" type="button" onClick={onCancel} disabled={busy}>{t("common.cancel")}</button>
          <button className="primary" type="button" onClick={onConfirm} disabled={busy}>
            {busy ? t("settings.binding") : t("settings.confirmRebind")}
          </button>
        </div>
      </div>
    </div>
  );
}

function fallbackBranchOptions(selectedBranch: string): ConfigBranchOptions {
  const selected = selectedBranch.trim() || "main";
  return {
    branches: branchChoices({ branches: ["main"], default_branch: "main", selected_branch: selected, warning: "" }, selected),
    default_branch: "main",
    selected_branch: selected,
    warning: "",
  };
}

function branchChoices(options: ConfigBranchOptions, currentBranch: string): string[] {
  const out: string[] = [];
  for (const branch of ["main", options.default_branch, options.selected_branch, currentBranch, ...options.branches]) {
    const value = branch.trim();
    if (value && !out.includes(value)) out.push(value);
  }
  return out;
}

function credentialLabel(
  t: TFunction,
  mode: EditableConfig["resources"]["credential_mode"],
  repoUrl: string,
): string {
  if (mode === "native") {
    return repoUrl.startsWith("git@") ? t("settings.credentialSsh") : t("settings.credentialGcm");
  }
  if (mode === "token") return t("settings.credentialToken");
  return t("settings.credentialAuto");
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
