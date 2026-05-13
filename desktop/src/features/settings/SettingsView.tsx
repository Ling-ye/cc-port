import { AlertTriangle, RefreshCcw, Save } from "lucide-react";
import { useEffect, useState } from "react";
import { lpmAction } from "@/api/client";
import type {
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
  onDone,
  onError,
  onChanged,
}: {
  onDone: (message: string) => void;
  onError: (message: string) => void;
  onChanged: () => Promise<void> | void;
}) {
  const [settings, setSettings] = useState<ConfigSettings | null>(null);
  const [draft, setDraft] = useState<EditableConfig | null>(null);
  const [newToken, setNewToken] = useState("");
  const [clearToken, setClearToken] = useState(false);
  const [busy, setBusy] = useState(false);
  const [pendingSave, setPendingSave] = useState<PendingSave | null>(null);

  async function loadSettings() {
    setBusy(true);
    try {
      const data = await lpmAction<ConfigSettings>("config_get");
      setSettings(data);
      setDraft(data.config);
      setNewToken("");
      setClearToken(false);
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
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

  function buildPayload(prepare = false): SavePayload | null {
    if (!draft) return null;
    const trimmedToken = newToken.trim();
    return {
      draft,
      token_action: clearToken ? "clear" : trimmedToken ? "replace" : "preserve",
      new_token: trimmedToken || undefined,
      prepare_resource_repo: prepare,
    };
  }

  async function saveSettings() {
    const payload = buildPayload(false);
    if (!payload) return;
    setBusy(true);
    try {
      const check = await lpmAction<ConfigCheckResult>("config_check", payload);
      if (check.missing.length > 0) {
        setPendingSave({ check, payload });
        return;
      }
      await commitSave(payload);
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function commitSave(payload: SavePayload) {
    const saved = await lpmAction<ConfigSettings>("config_save", payload);
    setSettings(saved);
    setDraft(saved.config);
    setNewToken("");
    setClearToken(false);
    setPendingSave(null);
    onDone("Configuration saved.");
    await onChanged();
  }

  async function confirmPrepareAndSave() {
    if (!pendingSave) return;
    setBusy(true);
    try {
      await commitSave({ ...pendingSave.payload, prepare_resource_repo: true });
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  if (!draft || !settings) {
    return (
      <section className="panel">
        <div className="panel-head">
          <h2>Settings</h2>
          <button className="secondary" type="button" onClick={loadSettings} disabled={busy}>
            <RefreshCcw size={17} />Refresh
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
            <h2>Settings</h2>
            <p>{settings.path}</p>
          </div>
          <div className="settings-actions">
            <button className="secondary" type="button" onClick={loadSettings} disabled={busy}>
              <RefreshCcw size={17} />Reload
            </button>
            <button className="primary" type="button" onClick={saveSettings} disabled={busy}>
              <Save size={17} />Save
            </button>
          </div>
        </div>

        {settings.env_token_active ? (
          <div className="inline-warning">
            <AlertTriangle size={17} />
            <span>{`Current GitHub token comes from LPM_GITHUB_TOKEN. Saving a token here updates config.toml, but this running session still uses the environment token.`}</span>
          </div>
        ) : null}

        <div className="settings-sections">
          <div className="settings-section">
            <h3>GitHub</h3>
            <div className="stack-form two-column">
              <label>
                <span>Token preview</span>
                <input value={settings.token_preview || "Not configured"} readOnly />
              </label>
              <label>
                <span>New token</span>
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
                <span>Clear stored token</span>
              </label>
              <label className="checkline">
                <input
                  type="checkbox"
                  checked={draft.github.default_private}
                  onChange={(event) => updateGithub("default_private", event.target.checked)}
                />
                <span>New repositories default to private</span>
              </label>
              <label>
                <span>Owner</span>
                <input
                  value={draft.github.owner}
                  onChange={(event) => updateGithub("owner", event.target.value)}
                />
              </label>
              <label>
                <span>Repo prefix</span>
                <input
                  value={draft.github.repo_prefix}
                  onChange={(event) => updateGithub("repo_prefix", event.target.value)}
                />
              </label>
            </div>
          </div>

          <div className="settings-section">
            <h3>Resource Repository</h3>
            <div className="stack-form two-column">
              <label>
                <span>Repo name</span>
                <input
                  value={draft.resources.repo_name}
                  onChange={(event) => updateResources("repo_name", event.target.value)}
                />
              </label>
              <label>
                <span>Branch</span>
                <input
                  value={draft.resources.branch}
                  onChange={(event) => updateResources("branch", event.target.value)}
                />
              </label>
              <label className="span-2">
                <span>Repo URL</span>
                <input
                  value={draft.resources.repo_url}
                  onChange={(event) => updateResources("repo_url", event.target.value)}
                />
              </label>
              <label className="span-2">
                <span>Local path</span>
                <input
                  value={draft.resources.local_path}
                  onChange={(event) => updateResources("local_path", event.target.value)}
                />
              </label>
            </div>
          </div>

          <div className="settings-section">
            <h3>Install</h3>
            <div className="stack-form">
              <label>
                <span>Fallback target</span>
                <input
                  value={draft.install.target}
                  onChange={(event) => updateInstall("target", event.target.value)}
                />
              </label>
            </div>
          </div>

          <div className="settings-section">
            <h3>Platforms</h3>
            <div className="platform-editor-list">
              {draft.platforms.map((platform, index) => (
                <div className="platform-editor" key={platform.name}>
                  <div className="platform-editor-head">
                    <strong>{platform.name}</strong>
                    <label className="checkline">
                      <input
                        type="checkbox"
                        checked={platform.enabled}
                        onChange={(event) => updatePlatform(index, { enabled: event.target.checked })}
                      />
                      <span>Enabled</span>
                    </label>
                  </div>
                  <div className="stack-form three-column">
                    <label>
                      <span>Skills</span>
                      <input
                        value={platform.skills_dir}
                        onChange={(event) => updatePlatform(index, { skills_dir: event.target.value })}
                      />
                    </label>
                    <label>
                      <span>MCP JSON</span>
                      <input
                        value={platform.mcp_json}
                        onChange={(event) => updatePlatform(index, { mcp_json: event.target.value })}
                      />
                    </label>
                    <label>
                      <span>Rules</span>
                      <input
                        value={platform.rules_dir}
                        onChange={(event) => updatePlatform(index, { rules_dir: event.target.value })}
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
          busy={busy}
          onCancel={() => setPendingSave(null)}
          onConfirm={confirmPrepareAndSave}
        />
      ) : null}
    </section>
  );
}

function PrepareModal({
  check,
  busy,
  onCancel,
  onConfirm,
}: {
  check: ConfigCheckResult;
  busy: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  return (
    <div className="modal-backdrop">
      <div className="modal">
        <div className="modal-head">
          <AlertTriangle size={20} />
          <h2>Resource repository needs preparation</h2>
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
          <button className="secondary" type="button" onClick={onCancel} disabled={busy}>Cancel</button>
          <button className="primary" type="button" onClick={onConfirm} disabled={busy || !check.can_prepare}>
            Create / connect and save
          </button>
        </div>
      </div>
    </div>
  );
}
