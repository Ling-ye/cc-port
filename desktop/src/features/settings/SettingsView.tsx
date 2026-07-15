import { AlertTriangle, RefreshCcw, Save } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { lpmAction } from "@/api/client";
import type { TFunction } from "@/app/i18n";
import { useTaskCenter } from "@/app/TaskCenterContext";
import type {
  ConfigBranchOptions,
  ConfigCheckResult,
  ConfigSettings,
  EditableConfig,
  PlatformProfile,
} from "@/types/lpm";

type SavePayload = {
  draft: EditableConfig;
  token_action: "preserve" | "replace" | "clear";
  new_token?: string;
  prepare_resource_repo?: boolean;
};

type PendingSave = {
  check: ConfigCheckResult;
  payload: SavePayload;
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
  const [newToken, setNewToken] = useState("");
  const [clearToken, setClearToken] = useState(false);
  const [loading, setLoading] = useState(false);
  const [checking, setChecking] = useState(false);
  const [saving, setSaving] = useState(false);
  const [preparing, setPreparing] = useState(false);
  const [pendingSave, setPendingSave] = useState<PendingSave | null>(null);
  const [branchOptions, setBranchOptions] = useState<ConfigBranchOptions>(() => fallbackBranchOptions("main"));
  const [loadingBranches, setLoadingBranches] = useState(false);
  const draftRef = useRef<EditableConfig | null>(null);
  const newTokenRef = useRef("");
  const clearTokenRef = useRef(false);
  const branchRequestRef = useRef(0);

  const actionBusy = checking || saving || preparing;
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

  async function loadSettings(track = false) {
    setLoading(true);
    try {
      const request = async () => {
        const data = await lpmAction<ConfigSettings>("config_get");
        setSettings(data);
        setDraft(data.config);
        setNewToken("");
        setClearToken(false);
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
      if (!track) onError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void loadSettings();
  }, []);

  useEffect(() => {
    if (!draft) return;
    const timer = window.setTimeout(() => {
      void loadBranchOptions(draft);
    }, 350);
    return () => window.clearTimeout(timer);
  }, [
    draft?.github.owner,
    draft?.resources.repo_name,
    draft?.resources.repo_url,
    newToken,
    clearToken,
  ]);

  function updateGithub(key: keyof EditableConfig["github"], value: string | boolean) {
    setDraft((current) => current && { ...current, github: { ...current.github, [key]: value } });
  }

  function updateInstall(key: keyof EditableConfig["install"], value: string) {
    setDraft((current) => current && { ...current, install: { ...current.install, [key]: value } });
  }

  function updateResources(key: keyof EditableConfig["resources"], value: string) {
    setDraft((current) => current && { ...current, resources: { ...current.resources, [key]: value } });
  }

  function updatePlatform(index: number, patch: Partial<PlatformProfile>) {
    setDraft((current) => current && {
      ...current,
      platforms: current.platforms.map((platform, itemIndex) => (
        itemIndex === index ? { ...platform, ...patch } : platform
      )),
    });
  }

  function buildPayloadFromDraft(nextDraft: EditableConfig, prepare = false): SavePayload {
    const trimmedToken = newToken.trim();
    return {
      draft: nextDraft,
      token_action: clearToken ? "clear" : trimmedToken ? "replace" : "preserve",
      new_token: trimmedToken || undefined,
      prepare_resource_repo: prepare,
    };
  }

  function buildPayload(prepare = false): SavePayload | null {
    if (!draft) return null;
    return buildPayloadFromDraft(draft, prepare);
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
      const message = err instanceof Error ? err.message : String(err);
      setBranchOptions({
        ...fallbackBranchOptions(nextDraft.resources.branch),
        warning: message,
      });
    } finally {
      if (branchRequestRef.current === requestId) {
        setLoadingBranches(false);
      }
    }
  }

  async function saveSettings() {
    const payload = buildPayload(false);
    if (!payload) return;
    setChecking(true);
    try {
      const check = await runTask({
        kind: "settings-check",
        title: t("settings.checkingShort"),
        action: async () => {
          const result = await lpmAction<ConfigCheckResult>("config_check", payload);
          if (result.missing.length > 0) setPendingSave({ check: result, payload });
          return result;
        },
        successMessage: t("settings.checkCompleted"),
        retryPolicy: "safe-read",
      });
      if (check.missing.length > 0) {
        return;
      }
      setChecking(false);
      await commitSave(payload);
    } catch {
      // TaskCenter owns feedback for tracked operations.
    } finally {
      setChecking(false);
    }
  }

  async function commitSave(payload: SavePayload, mode: "saving" | "preparing" = "saving") {
    const savedToken = payload.new_token || "";
    const savedClearToken = payload.token_action === "clear";
    const setModeBusy = mode === "preparing" ? setPreparing : setSaving;
    setModeBusy(true);
    try {
      const saved = await runTask({
        kind: mode === "preparing" ? "settings-prepare" : "settings-save",
        title: mode === "preparing" ? t("settings.preparingShort") : t("settings.savingShort"),
        action: () => lpmAction<ConfigSettings>("config_save", payload),
        successMessage: t("settings.saved"),
        retryPolicy: "none",
      });
      setSettings(saved);
      setDraft((current) => (current === payload.draft ? saved.config : current));
      if (draftRef.current === payload.draft) {
        if (newTokenRef.current.trim() === savedToken && clearTokenRef.current === savedClearToken) {
          setNewToken("");
          setClearToken(false);
        }
      }
      setPendingSave(null);
      void onChanged();
    } catch {
      // TaskCenter owns feedback for tracked operations.
    } finally {
      setModeBusy(false);
    }
  }

  async function confirmPrepareAndSave() {
    if (!pendingSave) return;
    await commitSave({ ...pendingSave.payload, prepare_resource_repo: true }, "preparing");
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

  return (
    <section className="settings-view">
      <div className="panel settings-panel">
        <div className="panel-head">
          <div>
            <h2>{t("settings.title")}</h2>
            <p>{settings.path}</p>
          </div>
          <div className="settings-actions">
            <button className="secondary" type="button" onClick={() => void loadSettings(true)} disabled={anyBusy}>
              <RefreshCcw size={17} />{t("common.reload")}
            </button>
            <button className="primary" type="button" onClick={saveSettings} disabled={anyBusy}>
              <Save size={17} />{saveLabel(t, { checking, saving, preparing })}
            </button>
          </div>
        </div>

        {settings.env_token_active ? (
          <div className="inline-warning">
            <AlertTriangle size={17} />
            <span>{t("settings.envTokenWarning")}</span>
          </div>
        ) : null}

        <div className="settings-sections">
          <div className="settings-section">
            <h3>{t("settings.github")}</h3>
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
                <input
                  value={draft.github.owner}
                  onChange={(event) => updateGithub("owner", event.target.value)}
                />
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
            <h3>{t("settings.resourceRepository")}</h3>
            <div className="stack-form two-column">
              <label>
                <span>{t("settings.repoName")}</span>
                <input
                  value={draft.resources.repo_name}
                  onChange={(event) => updateResources("repo_name", event.target.value)}
                />
              </label>
              <label>
                <span className="field-heading">
                  <span>{t("settings.branch")}</span>
                  <button
                    className="field-action"
                    type="button"
                    onClick={() => loadBranchOptions()}
                    disabled={loadingBranches || actionBusy}
                    title={t("settings.branchRefresh")}
                  >
                    <RefreshCcw size={14} />{loadingBranches ? t("settings.branchLoading") : t("settings.branchRefresh")}
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
              <label className="span-2">
                <span>{t("settings.repoUrl")}</span>
                <input
                  value={draft.resources.repo_url}
                  placeholder={t("settings.repoUrlPlaceholder")}
                  onChange={(event) => updateResources("repo_url", event.target.value)}
                />
              </label>
              <label className="span-2">
                <span>{t("settings.localPath")}</span>
                <input
                  value={draft.resources.local_path}
                  onChange={(event) => updateResources("local_path", event.target.value)}
                />
              </label>
            </div>
          </div>

          <div className="settings-section">
            <h3>{t("settings.install")}</h3>
            <div className="stack-form">
              <label>
                <span>{t("settings.fallbackTarget")}</span>
                <input
                  value={draft.install.target}
                  onChange={(event) => updateInstall("target", event.target.value)}
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
        </div>
      </div>

      {pendingSave ? (
        <PrepareModal
          check={pendingSave.check}
          busy={actionBusy}
          t={t}
          onCancel={() => setPendingSave(null)}
          onConfirm={confirmPrepareAndSave}
        />
      ) : null}
    </section>
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
    if (value && !out.includes(value)) {
      out.push(value);
    }
  }
  return out;
}

function saveLabel(
  t: TFunction,
  state: { checking: boolean; saving: boolean; preparing: boolean },
): string {
  if (state.preparing) return t("settings.preparingShort");
  if (state.saving) return t("settings.savingShort");
  if (state.checking) return t("settings.checkingShort");
  return t("common.save");
}

function PrepareModal({
  check,
  busy,
  t,
  onCancel,
  onConfirm,
}: {
  check: ConfigCheckResult;
  busy: boolean;
  t: TFunction;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  return (
    <div className="modal-backdrop">
      <div className="modal">
        <div className="modal-head">
          <AlertTriangle size={20} />
          <h2>{t("settings.modalTitle")}</h2>
        </div>
        <div className="modal-list">
          {check.missing.map((item) => (
            <div key={item.id}>
              <strong>{item.label}</strong>
              <span>{item.detail}</span>
            </div>
          ))}
          {check.warnings.map((item) => (
            <div key={item.id}>
              <strong>{item.label}</strong>
              <span>{item.detail}</span>
            </div>
          ))}
        </div>
        <div className="modal-actions">
          <button className="secondary" type="button" onClick={onCancel} disabled={busy}>{t("common.cancel")}</button>
          <button className="primary" type="button" onClick={onConfirm} disabled={busy || !check.can_prepare}>
            {t("settings.modalConfirm")}
          </button>
        </div>
      </div>
    </div>
  );
}
