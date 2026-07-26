import {
  AlertTriangle,
  BadgeCheck,
  CheckCircle2,
  ExternalLink,
  Link2,
  RefreshCcw,
  ShieldCheck,
  TerminalSquare,
  XCircle,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { lpmAction, openExternalUrl } from "@/api/client";
import { displayError, translateMessage, type TFunction } from "@/app/i18n";
import { useTaskCenter } from "@/app/TaskCenterContext";
import type {
  ConfigBindRepoResult,
  ConfigSettings,
  DiagnosticsState,
  DoctorCheck,
  DoctorStatus,
  GitCredentialStatus,
} from "@/types/lpm";

const SIMPLE_PLATFORM_NAMES = ["codex", "claude-code", "cursor", "windsurf", "opencode"] as const;
const SIMPLE_PLATFORM_NAME_SET = new Set<string>(SIMPLE_PLATFORM_NAMES);

export function SettingsView({
  t,
  refreshVersion,
  onError,
  onChanged,
  diagnostics,
  onRunDiagnostics,
}: {
  t: TFunction;
  refreshVersion: number;
  onError: (message: string) => void;
  onChanged: () => Promise<void> | void;
  diagnostics: DiagnosticsState;
  onRunDiagnostics: () => Promise<void>;
}) {
  const { runTask } = useTaskCenter();
  const [settings, setSettings] = useState<ConfigSettings | null>(null);
  const [credentialStatus, setCredentialStatus] = useState<GitCredentialStatus | null>(null);
  const [bindUrl, setBindUrl] = useState("");
  const [lastBinding, setLastBinding] = useState<ConfigBindRepoResult["binding"] | null>(null);
  const [bindError, setBindError] = useState("");
  const [pendingRebindUrl, setPendingRebindUrl] = useState("");
  const [loading, setLoading] = useState(false);
  const [binding, setBinding] = useState(false);
  const [platformSaving, setPlatformSaving] = useState("");
  const [diagnosticsOpen, setDiagnosticsOpen] = useState(false);
  const [loadError, setLoadError] = useState("");
  const settingsRequestRef = useRef(0);

  const actionBusy = binding || Boolean(platformSaving);
  const anyBusy = loading || actionBusy;
  const settingsReady = Boolean(settings && credentialStatus && !loadError);
  const controlsDisabled = anyBusy || !settingsReady;

  useEffect(() => () => {
    settingsRequestRef.current += 1;
  }, []);

  function applySettings(data: ConfigSettings, resetInputs = true) {
    setSettings(data);
    if (resetInputs) setBindUrl(data.config.resources.repo_url);
  }

  async function loadSettings() {
    const requestId = settingsRequestRef.current + 1;
    settingsRequestRef.current = requestId;
    setLoading(true);
    setLoadError("");
    try {
      const [data, status] = await Promise.all([
        lpmAction<ConfigSettings>("config_get"),
        lpmAction<GitCredentialStatus>("git_credential_status"),
      ]);
      if (requestId !== settingsRequestRef.current) return;
      applySettings(data);
      setCredentialStatus(status);
      setLastBinding(null);
      setBindError("");
    } catch (err) {
      if (requestId === settingsRequestRef.current) {
        const message = displayError(err, t);
        setLoadError(message);
        onError(message);
      }
    } finally {
      if (requestId === settingsRequestRef.current) setLoading(false);
    }
  }

  useEffect(() => {
    void loadSettings();
  }, [refreshVersion]);

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
        failureMessage: (error) => displayError(error, t),
        retryPolicy: "none",
      });
      applySettings(result.settings);
      setLastBinding(result.binding);
      void onChanged();
    } catch (err) {
      setBindError(displayError(err, t));
    } finally {
      setBinding(false);
    }
  }

  async function openCredentialGuide() {
    if (!credentialStatus?.install_url) return;
    try {
      await openExternalUrl(credentialStatus.install_url);
    } catch (err) {
      onError(displayError(err, t));
    }
  }

  async function setPlatformEnabled(name: string, enabled: boolean) {
    setPlatformSaving(name);
    try {
      const next = await lpmAction<ConfigSettings>("platform_set_enabled", { name, enabled });
      applySettings(next, false);
      void onChanged();
    } catch (err) {
      onError(displayError(err, t));
    } finally {
      setPlatformSaving("");
    }
  }

  const currentUrl = settings?.config.resources.repo_url.trim() || "";
  const currentRepoName = settings?.config.resources.repo_name || "";
  const visiblePlatforms = settings?.config.platforms.filter(
    (platform) => SIMPLE_PLATFORM_NAME_SET.has(platform.name),
  );
  const initialLoading = !settings || !credentialStatus;
  const settingsPending = loading || (initialLoading && !loadError);
  return (
    <section className="settings-view" aria-busy={settingsPending}>
      <div className="panel settings-panel">
        <div className="panel-head">
          <div>
            <h2>{t("settings.title")}</h2>
            <p>{t("settings.simpleDescription")}</p>
          </div>
        </div>

        <section
          className="repo-binding-card"
          aria-labelledby="repo-binding-title"
          aria-busy={settingsPending}
        >
          <div className="repo-binding-head">
            <div>
              <span className="repo-binding-icon"><Link2 size={20} /></span>
              <div>
                <h3 id="repo-binding-title">{t("settings.quickBindTitle")}</h3>
                <p>{t("settings.quickBindDescription")}</p>
              </div>
            </div>
            <span
              className={!loading && !loadError && currentUrl ? "connection-pill connected" : "connection-pill"}
              aria-live="polite"
            >
              {settingsPending ? (
                <><RefreshCcw className="spin" size={15} />{t("settings.loading")}</>
              ) : loadError ? (
                <><AlertTriangle size={15} />{t("settings.loadFailed")}</>
              ) : (
                <>{currentUrl ? <BadgeCheck size={15} /> : null}{currentUrl ? t("settings.bound") : t("settings.unbound")}</>
              )}
            </span>
          </div>

          {loadError ? (
            <SettingsLoadState error={loadError} t={t} onRetry={() => void loadSettings()} />
          ) : credentialStatus ? (
            <CredentialStatusPanel
              status={credentialStatus}
              t={t}
              onOpenGuide={() => void openCredentialGuide()}
            />
          ) : (
            <SettingsLoadState error="" t={t} onRetry={() => void loadSettings()} />
          )}

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
                disabled={controlsDisabled}
              />
            </label>
            <button
              className="primary"
              type="button"
              onClick={requestBind}
              disabled={controlsDisabled || !bindUrl.trim()}
            >
              {binding ? <RefreshCcw className="spin" size={17} /> : <Link2 size={17} />}
              {binding ? t("settings.binding") : t("settings.connectAndVerify")}
            </button>
          </div>

          {bindError ? <div className="repo-bind-error" role="alert">{bindError}</div> : null}
          {currentUrl ? (
            <div className="repo-binding-status" aria-live="polite">
              <div>
                <span>{t("settings.boundRepository")}</span>
                <strong>{currentRepoName}</strong>
                <small>{currentUrl}</small>
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

        <section className="settings-section platform-toggle-section" aria-labelledby="platform-title">
          <div className="simple-section-head">
            <div>
              <h3 id="platform-title">{t("settings.targetTools")}</h3>
              <p>{t("settings.targetToolsDescription")}</p>
            </div>
          </div>
          <ul
            className="platform-toggle-list"
            aria-label={t("settings.targetTools")}
            aria-busy={settingsPending}
          >
            {visiblePlatforms
              ? visiblePlatforms.map((platform) => (
                <li className="platform-toggle-item" key={platform.name}>
                  <label className={`platform-toggle${controlsDisabled ? " is-disabled" : ""}`}>
                    <strong>{platformDisplayName(platform.name)}</strong>
                    <span className="platform-toggle-control">
                      {platformSaving === platform.name ? <RefreshCcw className="spin" size={15} /> : null}
                      <input
                        type="checkbox"
                        checked={platform.enabled}
                        disabled={controlsDisabled}
                        onChange={(event) => void setPlatformEnabled(platform.name, event.target.checked)}
                        aria-label={platformDisplayName(platform.name)}
                      />
                    </span>
                  </label>
                </li>
              ))
              : SIMPLE_PLATFORM_NAMES.map((name) => (
                <li className="platform-toggle-item" key={name}>
                  <div className="platform-toggle is-disabled">
                    <strong>{platformDisplayName(name)}</strong>
                    <span className="platform-toggle-placeholder" aria-hidden="true" />
                  </div>
                </li>
              ))}
          </ul>
          <small className="field-note">{t("settings.toolDirectoryWarning")}</small>
        </section>

        <div className="settings-diagnostics">
          <button
            className="secondary"
            type="button"
            onClick={() => {
              setDiagnosticsOpen(true);
              void onRunDiagnostics();
            }}
            disabled={loading || !settingsReady}
          >
            {diagnostics.phase === "running"
              ? <RefreshCcw className="spin" size={17} />
              : <TerminalSquare size={17} />}
            {t("settings.diagnostics.run")}
          </button>
          <span
            className={`settings-diagnostics-status status-${diagnostics.phase}`}
            role="status"
            aria-live="polite"
          >
            {diagnosticsStatusLabel(diagnostics.phase, t)}
          </span>
        </div>
      </div>

      {pendingRebindUrl ? (
        <RebindModal
          currentUrl={currentUrl}
          nextUrl={pendingRebindUrl}
          busy={binding}
          t={t}
          onCancel={() => setPendingRebindUrl("")}
          onConfirm={() => void bindRepository(pendingRebindUrl)}
        />
      ) : null}
      {diagnosticsOpen ? (
        <DiagnosticsModal
          diagnostics={diagnostics}
          t={t}
          onClose={() => setDiagnosticsOpen(false)}
        />
      ) : null}
    </section>
  );
}

function SettingsLoadState({
  error,
  t,
  onRetry,
}: {
  error: string;
  t: TFunction;
  onRetry: () => void;
}) {
  return (
    <div
      className={`settings-load-state ${error ? "is-error" : "is-loading"}`}
      role={error ? "alert" : "status"}
    >
      {error ? <AlertTriangle size={18} /> : <RefreshCcw className="spin" size={18} />}
      <div>
        <strong>{error ? t("settings.loadFailed") : t("settings.loading")}</strong>
        {error ? <span>{error}</span> : null}
      </div>
      {error ? (
        <button className="secondary" type="button" onClick={onRetry}>
          <RefreshCcw size={15} />{t("common.retry")}
        </button>
      ) : null}
    </div>
  );
}

function CredentialStatusPanel({
  status,
  t,
  onOpenGuide,
}: {
  status: GitCredentialStatus;
  t: TFunction;
  onOpenGuide: () => void;
}) {
  return (
    <div className={`git-credential-status ${status.ready ? "is-ready" : "needs-action"}`} role="status">
      {status.ready ? <CheckCircle2 size={18} /> : <AlertTriangle size={18} />}
      <div>
        <strong>{t(`settings.gitCredentialState.${status.state}`)}</strong>
        <span>{t(`settings.gitCredentialDetail.${status.state}`)}</span>
        {status.ready ? (
          <small>
            {status.git_version || t("settings.gitReady")}
            {" · "}
            {status.gcm_version ? `GCM ${status.gcm_version}` : t("settings.gcmReady")}
          </small>
        ) : null}
      </div>
      {!status.ready ? (
        <button className="secondary" type="button" onClick={onOpenGuide}>
          <ExternalLink size={15} />{t("settings.openCredentialGuide")}
        </button>
      ) : null}
    </div>
  );
}

function DiagnosticsResults({ checks, t }: { checks: DoctorCheck[]; t: TFunction }) {
  const counts = checks.reduce<Record<DoctorStatus, number>>((current, check) => {
    current[doctorStatus(check)] += 1;
    return current;
  }, { ok: 0, warning: 0, error: 0, skipped: 0 });
  const issues = checks.filter((check) => {
    const status = doctorStatus(check);
    return status === "warning" || status === "error";
  });
  const healthy = counts.warning === 0 && counts.error === 0;

  return (
    <div className="diagnostics-results">
      <div className={`diagnostics-summary status-${healthy ? "ok" : counts.error ? "error" : "warning"}`} role="status">
        {healthy ? <CheckCircle2 size={18} /> : counts.error ? <XCircle size={18} /> : <AlertTriangle size={18} />}
        <span>
          {healthy
            ? t("settings.diagnostics.healthy", { ok: counts.ok, skipped: counts.skipped })
            : t("settings.diagnostics.summary", counts)}
        </span>
      </div>
      {issues.length ? (
        <ul className="diagnostics-issues" aria-label={t("settings.diagnostics.issues")}>
          {issues.map((check) => {
            const status = doctorStatus(check);
            return (
              <li className={`diagnostics-issue status-${status}`} key={check.id}>
                {status === "error" ? <XCircle size={18} /> : <AlertTriangle size={18} />}
                <div>
                  <strong>{doctorLabel(check, t)}</strong>
                  <p>{translateMessage(check.detail_ref, t, check.detail)}</p>
                </div>
              </li>
            );
          })}
        </ul>
      ) : null}
    </div>
  );
}

function diagnosticsStatusLabel(phase: DiagnosticsState["phase"], t: TFunction): string {
  switch (phase) {
    case "running":
      return t("settings.diagnostics.statusRunning");
    case "healthy":
      return t("settings.diagnostics.statusHealthy");
    case "issues":
      return t("settings.diagnostics.statusIssues");
    case "failed":
      return t("settings.diagnostics.statusFailed");
    default:
      return t("settings.diagnostics.statusIdle");
  }
}

function DiagnosticsModal({
  diagnostics,
  t,
  onClose,
}: {
  diagnostics: DiagnosticsState;
  t: TFunction;
  onClose: () => void;
}) {
  const loading = diagnostics.phase === "idle" || diagnostics.phase === "running";
  return (
    <div className="modal-backdrop" role="presentation">
      <div
        className="modal diagnostics-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="diagnostics-modal-title"
      >
        <div className="modal-head">
          <TerminalSquare size={20} />
          <h2 id="diagnostics-modal-title">{t("settings.diagnostics.title")}</h2>
        </div>
        <div className="diagnostics-modal-body">
          {loading ? (
            <div className="diagnostics-loading" role="status">
              <RefreshCcw className="spin" size={18} />
              <span>{t("settings.diagnostics.loading")}</span>
            </div>
          ) : diagnostics.phase === "failed" ? (
            <div className="diagnostics-failure" role="alert">
              <AlertTriangle size={18} />
              <span>{t("settings.diagnostics.failure", { detail: diagnostics.error })}</span>
            </div>
          ) : (
            <DiagnosticsResults checks={diagnostics.checks ?? []} t={t} />
          )}
        </div>
        <div className="modal-actions">
          <button className="secondary" type="button" onClick={onClose}>
            {t("common.close")}
          </button>
        </div>
      </div>
    </div>
  );
}

function doctorStatus(check: DoctorCheck): DoctorStatus {
  return check.status ?? (check.ok ? "ok" : "error");
}

function doctorLabel(check: DoctorCheck, t: TFunction): string {
  const labels: Record<string, string> = {
    git: t("settings.diagnostics.check.git"),
    config: t("settings.diagnostics.check.config"),
    resource_repo: t("settings.diagnostics.check.resourceRepo"),
    install_target: t("settings.diagnostics.check.installTarget"),
  };
  if (check.id.startsWith("platform:")) {
    return t("settings.diagnostics.check.platform", {
      name: platformDisplayName(check.id.slice("platform:".length)),
    });
  }
  return labels[check.id] || check.label;
}

function RebindModal({
  currentUrl,
  nextUrl,
  busy,
  t,
  onCancel,
  onConfirm,
}: {
  currentUrl: string;
  nextUrl: string;
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

function platformDisplayName(name: string): string {
  return {
    codex: "Codex",
    "claude-code": "Claude Code",
    cursor: "Cursor",
    windsurf: "Windsurf",
    opencode: "opencode",
  }[name] || name;
}
