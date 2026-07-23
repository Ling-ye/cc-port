import {
  AlertTriangle,
  BadgeCheck,
  CheckCircle2,
  Github,
  Link2,
  RefreshCcw,
  ShieldCheck,
  TerminalSquare,
  Trash2,
  XCircle,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import {
  listenForOAuthDeepLinks,
  lpmAction,
  openExternalUrl,
} from "@/api/client";
import { displayError, translateMessage, type TFunction } from "@/app/i18n";
import { useTaskCenter } from "@/app/TaskCenterContext";
import type {
  ConfigBindRepoResult,
  ConfigSettings,
  DoctorCheck,
  DoctorStatus,
  GithubAuthPollResult,
  GithubAuthStatus,
  GithubWebAuthSession,
} from "@/types/lpm";

const SIMPLE_PLATFORM_NAMES = new Set(["codex", "claude-code", "cursor", "windsurf", "opencode"]);

export function SettingsView({
  t,
  refreshVersion,
  onError,
  onChanged,
}: {
  t: TFunction;
  refreshVersion: number;
  onError: (message: string) => void;
  onChanged: () => Promise<void> | void;
}) {
  const { runTask } = useTaskCenter();
  const [settings, setSettings] = useState<ConfigSettings | null>(null);
  const [auth, setAuth] = useState<GithubAuthStatus | null>(null);
  const [bindUrl, setBindUrl] = useState("");
  const [lastBinding, setLastBinding] = useState<ConfigBindRepoResult["binding"] | null>(null);
  const [bindError, setBindError] = useState("");
  const [pendingRebindUrl, setPendingRebindUrl] = useState("");
  const [authSession, setAuthSession] = useState<GithubWebAuthSession | null>(null);
  const [authRetry, setAuthRetry] = useState(false);
  const [loading, setLoading] = useState(false);
  const [binding, setBinding] = useState(false);
  const [authStarting, setAuthStarting] = useState(false);
  const [platformSaving, setPlatformSaving] = useState("");
  const [diagnosticChecks, setDiagnosticChecks] = useState<DoctorCheck[] | null>(null);
  const [diagnosticsBusy, setDiagnosticsBusy] = useState(false);
  const authSessionRef = useRef<GithubWebAuthSession | null>(null);
  const authPollRef = useRef<(immediate?: boolean) => void>(() => undefined);
  const settingsRequestRef = useRef(0);

  const actionBusy = binding || authStarting || Boolean(platformSaving);
  const anyBusy = loading || actionBusy;

  useEffect(() => {
    authSessionRef.current = authSession;
  }, [authSession]);

  useEffect(() => () => {
    settingsRequestRef.current += 1;
    const sessionId = authSessionRef.current?.session_id;
    if (sessionId) {
      void lpmAction("github_web_auth_cancel", { session_id: sessionId }).catch(() => undefined);
    }
  }, []);

  function applySettings(data: ConfigSettings, resetInputs = true) {
    setSettings(data);
    if (resetInputs) {
      setBindUrl(data.config.resources.repo_url);
    }
  }

  async function loadSettings() {
    const requestId = settingsRequestRef.current + 1;
    settingsRequestRef.current = requestId;
    setLoading(true);
    try {
      const [data, status] = await Promise.all([
        lpmAction<ConfigSettings>("config_get"),
        lpmAction<GithubAuthStatus>("github_auth_status"),
      ]);
      if (requestId !== settingsRequestRef.current) return;
      applySettings(data);
      setAuth(status);
      setLastBinding(null);
      setBindError("");
    } catch (err) {
      if (requestId === settingsRequestRef.current) {
        onError(displayError(err, t));
      }
    } finally {
      if (requestId === settingsRequestRef.current) {
        setLoading(false);
      }
    }
  }

  useEffect(() => {
    void loadSettings();
  }, [refreshVersion]);

  useEffect(() => {
    if (!authSession) {
      authPollRef.current = () => undefined;
      return;
    }
    let stopped = false;
    let timer = 0;
    let polling = false;

    const schedule = (delaySeconds: number) => {
      window.clearTimeout(timer);
      timer = window.setTimeout(() => void poll(false), Math.max(1, delaySeconds) * 1000);
    };

    const poll = async (immediate: boolean) => {
      if (stopped || polling) return;
      polling = true;
      try {
        const result = await lpmAction<GithubAuthPollResult>("github_web_auth_poll", {
          session_id: authSession.session_id,
          immediate,
        });
        if (stopped) return;
        if (result.state === "pending" || result.state === "slow_down") {
          schedule(result.retry_after || authSession.interval);
          return;
        }
        if (result.state === "authorized") {
          setAuthSession(null);
          setAuthRetry(false);
          const status = await lpmAction<GithubAuthStatus>("github_auth_status");
          if (!stopped) setAuth(status);
          void onChanged();
          return;
        }
        setAuthSession(null);
        setAuthRetry(true);
        onError(result.state === "denied" ? t("settings.authDenied") : t("settings.authExpired"));
      } catch (err) {
        if (!stopped) {
          setAuthSession(null);
          setAuthRetry(true);
          void lpmAction("github_web_auth_cancel", {
            session_id: authSession.session_id,
          }).catch(() => undefined);
          onError(displayError(err, t));
        }
      } finally {
        polling = false;
      }
    };

    authPollRef.current = (immediate = false) => void poll(immediate);
    schedule(authSession.interval);
    return () => {
      stopped = true;
      window.clearTimeout(timer);
      authPollRef.current = () => undefined;
    };
  }, [authSession]);

  useEffect(() => {
    let stopped = false;
    let unlisten: (() => void) | undefined;
    void listenForOAuthDeepLinks((urls) => {
      const session = authSessionRef.current;
      if (!session) return;
      if (urls.some((url) => matchesGithubOAuthDeepLink(url, session.session_id))) {
        authPollRef.current(true);
      }
    }).then((dispose) => {
      if (stopped) dispose();
      else unlisten = dispose;
    }).catch((err) => {
      if (!stopped) onError(displayError(err, t));
    });
    return () => {
      stopped = true;
      unlisten?.();
    };
  }, []);

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

  async function startAuth() {
    setAuthStarting(true);
    setAuthRetry(false);
    try {
      const session = await lpmAction<GithubWebAuthSession>("github_web_auth_start", {
        purpose: "standard",
      });
      setAuthSession(session);
      try {
        await openExternalUrl(session.authorization_url);
      } catch (err) {
        onError(t("settings.browserOpenFailed"));
      }
    } catch (err) {
      setAuthRetry(true);
      onError(displayError(err, t));
    } finally {
      setAuthStarting(false);
    }
  }

  async function reopenAuthorization() {
    if (!authSession) return;
    try {
      await openExternalUrl(authSession.authorization_url);
    } catch {
      onError(t("settings.browserOpenFailed"));
    }
  }

  async function cancelAuth() {
    if (!authSession) return;
    const sessionId = authSession.session_id;
    setAuthSession(null);
    try {
      await lpmAction("github_web_auth_cancel", { session_id: sessionId });
    } catch (err) {
      onError(displayError(err, t));
    }
  }

  async function clearAuthorization() {
    if (!window.confirm(t("settings.clearAuthorizationConfirm"))) return;
    try {
      const status = await lpmAction<GithubAuthStatus>("github_token_clear");
      setAuth(status);
      void onChanged();
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

  async function runDiagnostics() {
    setDiagnosticChecks(null);
    setDiagnosticsBusy(true);
    try {
      await runTask({
        kind: "settings-diagnostics",
        title: t("settings.diagnostics.run"),
        action: async () => {
          const result = await lpmAction<{ checks: DoctorCheck[] }>("doctor");
          setDiagnosticChecks(result.checks);
          return result;
        },
        successMessage: (result) => t("settings.diagnostics.completed", { count: result.checks.length }),
        failureMessage: (error) => displayError(error, t),
        retryPolicy: "safe-read",
      });
    } catch {
      // TaskCenter owns feedback for tracked operations.
    } finally {
      setDiagnosticsBusy(false);
    }
  }

  if (!settings || !auth) {
    return (
      <section className="panel">
        <div className="panel-head">
          <h2>{t("settings.title")}</h2>
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
            <p>{t("settings.simpleDescription")}</p>
          </div>
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

        <section className="settings-section github-access-card" aria-labelledby="github-access-title">
          <div className="simple-section-head">
            <div>
              <h3 id="github-access-title"><Github size={18} />{t("settings.githubAccess")}</h3>
              <p>{t("settings.githubAccessDescription")}</p>
            </div>
            <span className={auth.state === "connected" ? "connection-pill connected" : "connection-pill"}>
              {auth.state === "connected" ? <BadgeCheck size={15} /> : null}
              {t(`settings.authState.${auth.state}`)}
            </span>
          </div>

          {!auth.oauth_configured ? (
            <div className="inline-warning"><AlertTriangle size={17} /><span>{t("settings.oauthNotConfigured")}</span></div>
          ) : null}
          {auth.env_override ? (
            <div className="inline-warning"><AlertTriangle size={17} /><span>{t("settings.envTokenOverride")}</span></div>
          ) : null}
          {auth.error ? <div className="repo-bind-error" role="alert">{auth.error}</div> : null}

          <dl className="compact-status-list">
            <div><dt>{t("settings.authorizedAccount")}</dt><dd>{auth.login || t("settings.notConfigured")}</dd></div>
          </dl>

          {authSession ? (
            <div className="oauth-device-panel" role="status" aria-live="polite">
              <span><RefreshCcw className="spin" size={16} />{t("settings.authWaiting")}</span>
              <small>{t("settings.authWaitingHint")}</small>
              <div>
                <button className="secondary" type="button" onClick={() => void reopenAuthorization()}><Github size={16} />{t("settings.reopenGithub")}</button>
                <button className="secondary" type="button" onClick={() => void cancelAuth()}>{t("common.cancel")}</button>
              </div>
            </div>
          ) : (
            <div className="simple-actions">
              <button className="primary" type="button" onClick={() => void startAuth()} disabled={anyBusy || !auth.oauth_configured || auth.env_override}>
                {authStarting ? <RefreshCcw className="spin" size={17} /> : <Github size={17} />}
                {auth.state === "connected"
                  ? t("settings.reauthorizeGithub")
                  : authRetry
                    ? t("settings.retryGithub")
                    : t("settings.connectGithub")}
              </button>
              {auth.can_clear && !auth.env_override ? (
                <button className="danger-ghost" type="button" onClick={() => void clearAuthorization()} disabled={anyBusy}>
                  <Trash2 size={16} />{t("settings.removeLocalAuthorization")}
                </button>
              ) : null}
            </div>
          )}
          {auth.can_clear && !auth.env_override ? (
            <small className="field-note">{t("settings.localAuthorizationOnlyNote")}</small>
          ) : null}
        </section>

        <section className="settings-section platform-toggle-section" aria-labelledby="platform-title">
          <div className="simple-section-head">
            <div>
              <h3 id="platform-title">{t("settings.targetTools")}</h3>
              <p>{t("settings.targetToolsDescription")}</p>
            </div>
          </div>
          <ul className="platform-toggle-list" aria-label={t("settings.targetTools")}>
            {settings.config.platforms.filter((platform) => SIMPLE_PLATFORM_NAMES.has(platform.name)).map((platform) => (
              <li className="platform-toggle-item" key={platform.name}>
                <label className={`platform-toggle${anyBusy ? " is-disabled" : ""}`}>
                  <strong>{platformDisplayName(platform.name)}</strong>
                  <span className="platform-toggle-control">
                    {platformSaving === platform.name ? <RefreshCcw className="spin" size={15} /> : null}
                    <input
                      type="checkbox"
                      checked={platform.enabled}
                      disabled={anyBusy}
                      onChange={(event) => void setPlatformEnabled(platform.name, event.target.checked)}
                      aria-label={platformDisplayName(platform.name)}
                    />
                  </span>
                </label>
              </li>
            ))}
          </ul>
          <small className="field-note">{t("settings.toolDirectoryWarning")}</small>
        </section>

        <details className="settings-diagnostics">
          <summary>
            <span>
              <strong>{t("settings.diagnostics.title")}</strong>
              <small>{t("settings.diagnostics.description")}</small>
            </span>
          </summary>
          <div className="settings-diagnostics-body">
            <button className="secondary" type="button" onClick={() => void runDiagnostics()} disabled={diagnosticsBusy}>
              {diagnosticsBusy ? <RefreshCcw className="spin" size={17} /> : <TerminalSquare size={17} />}
              {diagnosticChecks === null ? t("settings.diagnostics.run") : t("settings.diagnostics.rerun")}
            </button>
            {diagnosticChecks !== null ? <DiagnosticsResults checks={diagnosticChecks} t={t} /> : null}
          </div>
        </details>
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
    </section>
  );
}

export function matchesGithubOAuthDeepLink(value: string, sessionId: string): boolean {
  try {
    const url = new URL(value);
    return url.protocol === "lingye-lpm:"
      && url.hostname === "oauth"
      && url.pathname === "/complete"
      && url.searchParams.get("session_id") === sessionId;
  } catch {
    return false;
  }
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

function doctorStatus(check: DoctorCheck): DoctorStatus {
  return check.status ?? (check.ok ? "ok" : "error");
}

function doctorLabel(check: DoctorCheck, t: TFunction): string {
  const labels: Record<string, string> = {
    git: t("settings.diagnostics.check.git"),
    config: t("settings.diagnostics.check.config"),
    github_token: t("settings.diagnostics.check.githubToken"),
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
