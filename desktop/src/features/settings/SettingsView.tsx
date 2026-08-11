import {
  AlertTriangle,
  BadgeCheck,
  Bot,
  CheckCircle2,
  ExternalLink,
  Link2,
  RefreshCcw,
  ShieldCheck,
  TerminalSquare,
  XCircle,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { ccPortAction, openExternalUrl } from "@/api/client";
import { displayError, translateMessage, type TFunction } from "@/app/i18n";
import { useTaskCenter } from "@/app/TaskCenterContext";
import {
  PlatformIdentityLabel,
  platformDisplayName,
  platformOptionLabel,
} from "@/components/PlatformIdentity";
import type {
  ConfigBindRepoResult,
  ConfigSettings,
  AiIntegrationPlan,
  AiIntegrationProfileStatus,
  AiIntegrationResult,
  AiIntegrationStatusResult,
  ApprovalRequest,
  DiagnosticsState,
  DoctorCheck,
  DoctorStatus,
  GitCredentialStatus,
} from "@/types/cc-port";

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
  const [aiStatuses, setAiStatuses] = useState<AiIntegrationProfileStatus[] | null>(null);
  const [approvalRequests, setApprovalRequests] = useState<ApprovalRequest[]>([]);
  const [approvalReview, setApprovalReview] = useState<ApprovalRequest | null>(null);
  const [aiPlan, setAiPlan] = useState<AiIntegrationPlan | null>(null);
  const [aiBusy, setAiBusy] = useState(false);
  const [aiError, setAiError] = useState("");
  const [aiTakeover, setAiTakeover] = useState(false);
  const settingsRequestRef = useRef(0);

  const actionBusy = binding || Boolean(platformSaving) || aiBusy;
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
        ccPortAction<ConfigSettings>("config_get"),
        ccPortAction<GitCredentialStatus>("git_credential_status"),
      ]);
      if (requestId !== settingsRequestRef.current) return;
      applySettings(data);
      setCredentialStatus(status);
      setLastBinding(null);
      setBindError("");
      void loadAiState();
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

  async function loadAiState() {
    setAiError("");
    try {
      const [status, approvals] = await Promise.all([
        ccPortAction<AiIntegrationStatusResult>("ai_integration_status"),
        ccPortAction<{ requests: ApprovalRequest[] }>("approval_requests"),
      ]);
      setAiStatuses(status.profiles);
      setApprovalRequests(approvals.requests);
    } catch (err) {
      setAiError(displayError(err, t));
    }
  }

  async function createAiPlan(
    profileId: string,
    action: "install" | "uninstall",
    overwriteUnmanaged = false,
  ) {
    setAiBusy(true);
    setAiError("");
    try {
      const plan = await ccPortAction<AiIntegrationPlan>("ai_integration_plan", {
        profile_id: profileId,
        action,
        overwrite_unmanaged: overwriteUnmanaged,
      });
      setAiTakeover(false);
      setAiPlan(plan);
      void loadAiState();
    } catch (err) {
      const message = displayError(err, t);
      setAiError(message);
      onError(message);
    } finally {
      setAiBusy(false);
    }
  }

  async function verifyAiProfile(profileId: string) {
    setAiBusy(true);
    setAiError("");
    try {
      const verified = await ccPortAction<AiIntegrationProfileStatus>(
        "ai_integration_verify",
        { profile_id: profileId, verify_transport: true },
      );
      setAiStatuses((current) => current?.map((status) => (
        status.profile_id === profileId ? verified : status
      )) ?? [verified]);
    } catch (err) {
      setAiError(displayError(err, t));
    } finally {
      setAiBusy(false);
    }
  }

  async function applyAiPlan() {
    if (!aiPlan?.approval_id) return;
    setAiBusy(true);
    setAiError("");
    try {
      const result = await runTask({
        kind: "ai-integration",
        title: t("settings.ai.applying"),
        context: aiPlan.profile_id,
        action: () => ccPortAction<AiIntegrationResult>("ai_integration_approve_apply", {
          operation_id: aiPlan.operation_id,
          plan_hash: aiPlan.plan_hash,
          approval_id: aiPlan.approval_id,
        }),
        successMessage: aiPlan.action === "install"
          ? t("settings.ai.successInstall")
          : t("settings.ai.successUninstall"),
        failureMessage: (error) => displayError(error, t),
        retryPolicy: "none",
      });
      if (result.status === "stale-plan" && result.stale_plan) {
        setAiPlan(result.stale_plan);
        setAiError(t("settings.ai.stale"));
      } else if (result.status === "succeeded" || result.status === "unchanged") {
        setAiPlan(null);
        setAiTakeover(false);
        await loadAiState();
        void onChanged();
      } else {
        setAiError(result.message || result.status);
      }
    } catch (err) {
      setAiError(displayError(err, t));
    } finally {
      setAiBusy(false);
    }
  }

  async function decideApproval(request: ApprovalRequest, approve: boolean) {
    setAiBusy(true);
    setAiError("");
    try {
      await ccPortAction(approve ? "approval_approve" : "approval_reject", {
        approval_id: request.approval_id,
        operation_id: request.operation_id,
        plan_hash: request.plan_hash,
        scope_hash: request.scope_hash,
        revision: request.revision,
      });
      setApprovalReview(null);
      await loadAiState();
    } catch (err) {
      setAiError(displayError(err, t));
    } finally {
      setAiBusy(false);
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
        action: () => ccPortAction<ConfigBindRepoResult>("config_bind_repo", {
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
      const next = await ccPortAction<ConfigSettings>("platform_set_enabled", { name, enabled });
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
    (platform) => SIMPLE_PLATFORM_NAME_SET.has(platform.tool_id || platform.name),
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
                    <span className="platform-toggle-identity">
                      <strong>
                        <PlatformIdentityLabel identity={platform} profileId={platform.name} t={t} />
                      </strong>
                      {platform.tool_id && platform.name !== platform.tool_id ? (
                        <small>{platform.name}</small>
                      ) : null}
                      {platform.instructions_path || platform.memories_dir || platform.settings_path ? (
                        <span className="platform-profile-paths">
                          {platform.instructions_path ? (
                            <span>
                              <span>{t("settings.profileInstructions")}</span>
                              <code>{platform.instructions_path}</code>
                            </span>
                          ) : null}
                          {platform.memories_dir ? (
                            <span>
                              <span>{t("settings.profileMemory")}</span>
                              <code>{platform.memories_dir}</code>
                            </span>
                          ) : null}
                          {platform.settings_path ? (
                            <span>
                              <span>{t("settings.profileSettings")}</span>
                              <code>{platform.settings_path}</code>
                            </span>
                          ) : null}
                        </span>
                      ) : null}
                    </span>
                    <span className="platform-toggle-control">
                      {platformSaving === platform.name ? <RefreshCcw className="spin" size={15} /> : null}
                      <input
                        type="checkbox"
                        checked={platform.enabled}
                        disabled={controlsDisabled}
                        onChange={(event) => void setPlatformEnabled(platform.name, event.target.checked)}
                        aria-label={platformOptionLabel(platform, platform.name, t)}
                      />
                    </span>
                  </label>
                </li>
              ))
              : SIMPLE_PLATFORM_NAMES.map((name) => (
                <li className="platform-toggle-item" key={name}>
                  <div className="platform-toggle is-disabled">
                    <strong>{platformDisplayName(undefined, name)}</strong>
                    <span className="platform-toggle-placeholder" aria-hidden="true" />
                  </div>
                </li>
              ))}
          </ul>
          <small className="field-note">{t("settings.toolDirectoryWarning")}</small>
        </section>

        <section className="settings-section ai-integration-section" aria-labelledby="ai-integration-title">
          <div className="simple-section-head">
            <div>
              <h3 id="ai-integration-title"><Bot size={18} />{t("settings.ai.title")}</h3>
              <p>{t("settings.ai.description")}</p>
            </div>
            <button
              className="secondary"
              type="button"
              onClick={() => void loadAiState()}
              disabled={aiBusy || !settingsReady}
              aria-label={t("settings.ai.retry")}
            >
              <RefreshCcw className={aiBusy ? "spin" : ""} size={15} />
              {t("settings.ai.retry")}
            </button>
          </div>
          {aiError ? <div className="repo-bind-error" role="alert">{aiError}</div> : null}
          {aiStatuses === null ? (
            <div className="diagnostics-loading" role="status">
              <RefreshCcw className="spin" size={16} />{t("settings.ai.loading")}
            </div>
          ) : (
            <ul className="ai-integration-list" aria-label={t("settings.ai.title")}>
              {aiStatuses.map((status) => {
                const profile = settings?.config.platforms.find((item) => item.name === status.profile_id);
                const canUninstall = status.managed_actions_available.includes("uninstall");
                const ownershipLabel = status.managed
                  ? t("settings.ai.managed")
                  : status.skill_managed || status.mcp_managed
                  ? t("settings.ai.partiallyManaged")
                  : status.configured
                  ? t("settings.ai.compatible")
                  : "";
                const connectionLabel = !status.configured
                  ? t("settings.ai.notInstalled")
                  : status.transport_status === "verified"
                  ? t("settings.ai.installed")
                  : status.transport_status === "failed"
                  ? t("settings.ai.verificationFailed")
                  : t("settings.ai.configuredUnverified");
                return (
                  <li className="ai-integration-item" key={status.profile_id}>
                    <div>
                      <strong>
                        <PlatformIdentityLabel identity={profile} profileId={status.profile_id} t={t} />
                      </strong>
                      <span className={`connection-pill${status.transport_status === "verified" ? " connected" : ""}`}>
                        {status.transport_status === "verified" ? <BadgeCheck size={14} /> : null}
                        {connectionLabel}
                      </span>
                      {ownershipLabel ? <small>{ownershipLabel}</small> : null}
                      {status.problems.length ? <small>{status.problems.join(" · ")}</small> : null}
                    </div>
                    <div className="ai-integration-actions">
                      {status.configured ? (
                        <button
                          className="secondary"
                          type="button"
                          disabled={aiBusy}
                          onClick={() => void verifyAiProfile(status.profile_id)}
                        >
                          {t("settings.ai.verify")}
                        </button>
                      ) : (
                        <button
                          className="primary"
                          type="button"
                          disabled={aiBusy}
                          onClick={() => void createAiPlan(status.profile_id, "install")}
                        >
                          {t("settings.ai.enable")}
                        </button>
                      )}
                      {canUninstall ? (
                      <button
                        className="secondary"
                        type="button"
                        disabled={aiBusy}
                        onClick={() => void createAiPlan(status.profile_id, "uninstall")}
                      >
                        {t("settings.ai.disable")}
                      </button>
                      ) : null}
                    </div>
                  </li>
                );
              })}
            </ul>
          )}

          <div className="ai-approval-head">
            <strong>{t("settings.ai.pendingApprovals")}</strong>
            <p>{t("settings.ai.pendingApprovalsDescription")}</p>
          </div>
          {approvalRequests.length ? (
            <ul className="ai-approval-list">
              {approvalRequests
                .filter((request) => request.kind !== "ai-integration" || request.approval_id !== aiPlan?.approval_id)
                .map((request) => (
                  <li key={request.approval_id}>
                    <div>
                      <strong>{request.summary}</strong>
                      <small>{t("settings.ai.expires", { value: new Date(request.expires_at).toLocaleString() })}</small>
                    </div>
                    <div className="modal-actions">
                      <button
                        className="secondary"
                        type="button"
                        disabled={aiBusy}
                        onClick={() => void decideApproval(request, false)}
                      >
                        {t("settings.ai.reject")}
                      </button>
                      {request.status === "pending" ? (
                        <button
                          className="primary"
                          type="button"
                          disabled={aiBusy}
                          onClick={() => setApprovalReview(request)}
                        >
                          {t("settings.ai.reviewApproval")}
                        </button>
                      ) : null}
                    </div>
                  </li>
                ))}
            </ul>
          ) : <p className="field-note">{t("settings.ai.noPendingApprovals")}</p>}
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
      {aiPlan ? (
        <AiIntegrationPlanModal
          plan={aiPlan}
          takeover={aiTakeover}
          busy={aiBusy}
          error={aiError}
          t={t}
          onTakeoverChange={setAiTakeover}
          onReplan={() => void createAiPlan(aiPlan.profile_id, aiPlan.action, true)}
          onCancel={() => {
            setAiPlan(null);
            setAiTakeover(false);
            setAiError("");
          }}
          onApply={() => void applyAiPlan()}
        />
      ) : null}
      {approvalReview ? (
        <ApprovalReviewModal
          request={approvalReview}
          busy={aiBusy}
          t={t}
          onCancel={() => setApprovalReview(null)}
          onApprove={() => void decideApproval(approvalReview, true)}
        />
      ) : null}
    </section>
  );
}

function AiIntegrationPlanModal({
  plan,
  takeover,
  busy,
  error,
  t,
  onTakeoverChange,
  onReplan,
  onCancel,
  onApply,
}: {
  plan: AiIntegrationPlan;
  takeover: boolean;
  busy: boolean;
  error: string;
  t: TFunction;
  onTakeoverChange: (value: boolean) => void;
  onReplan: () => void;
  onCancel: () => void;
  onApply: () => void;
}) {
  const unmanaged = plan.blockers.some((item) => item.toLowerCase().includes("unmanaged"));
  const takeoverPlan = plan.overwrite_unmanaged
    || plan.target.skill_status === "replace-unmanaged"
    || plan.target.mcp_status === "replace-unmanaged";
  return (
    <div className="modal-backdrop" role="presentation">
      <div className="modal ai-integration-modal" role="dialog" aria-modal="true" aria-labelledby="ai-plan-title">
        <div className="modal-head">
          <Bot size={20} />
          <h2 id="ai-plan-title">{t("settings.ai.planTitle")}</h2>
        </div>
        <p>{t("settings.ai.planDescription")}</p>
        <dl className="rebind-comparison">
          <div><dt>{t("settings.ai.skillTarget")}</dt><dd><code>{plan.target.skill_path}</code></dd></div>
          <div><dt>{t("settings.ai.mcpTarget")}</dt><dd><code>{plan.target.mcp_config_path}</code></dd></div>
          <div>
            <dt>{t("settings.ai.launchCommand")}</dt>
            <dd><code>{[plan.command, ...plan.command_args].join(" ")}</code></dd>
          </div>
        </dl>
        <div>
          <strong>{t("settings.ai.plannedChanges")}</strong>
          {plan.target.actions.length ? (
            <ul>{plan.target.actions.map((action) => <li key={action}><code>{action}</code></li>)}</ul>
          ) : <p>{t("settings.ai.noChanges")}</p>}
        </div>
        {plan.blockers.length ? (
          <div className="repo-bind-error" role="alert">
            <strong>{t("settings.ai.blockers")}</strong>
            <ul>{plan.blockers.map((blocker) => <li key={blocker}>{blocker}</li>)}</ul>
          </div>
        ) : null}
        {unmanaged && !plan.overwrite_unmanaged ? (
          <label className="ai-takeover-option">
            <input
              type="checkbox"
              checked={takeover}
              disabled={busy}
              onChange={(event) => onTakeoverChange(event.target.checked)}
            />
            <span>{t("settings.ai.allowTakeover")}</span>
          </label>
        ) : null}
        {takeoverPlan ? (
          <div className="repo-bind-error" role="alert">
            <strong>{t("settings.ai.takeoverWarning")}</strong>
            <ul>
              {plan.target.skill_status === "replace-unmanaged"
                ? <li>{t("settings.ai.replaceSkill")}</li>
                : null}
              {plan.target.mcp_status === "replace-unmanaged"
                ? <li>{t("settings.ai.replaceMcp")}</li>
                : null}
            </ul>
            <label className="ai-takeover-option">
              <input
                type="checkbox"
                checked={takeover}
                disabled={busy}
                onChange={(event) => onTakeoverChange(event.target.checked)}
              />
              <span>{t("settings.ai.confirmTakeoverPlan")}</span>
            </label>
          </div>
        ) : null}
        {error ? <div className="repo-bind-error" role="alert">{error}</div> : null}
        <div className="modal-actions">
          <button className="secondary" type="button" onClick={onCancel} disabled={busy}>
            {t("common.cancel")}
          </button>
          {unmanaged && !plan.overwrite_unmanaged ? (
            <button className="primary" type="button" onClick={onReplan} disabled={busy || !takeover}>
              {t("settings.ai.replanTakeover")}
            </button>
          ) : (
            <button
              className="primary"
              type="button"
              onClick={onApply}
              disabled={
                busy
                || plan.blocked
                || !plan.requires_approval
                || !plan.approval_id
                || (takeoverPlan && !takeover)
              }
            >
              {busy
                ? t("settings.ai.applying")
                : plan.action === "install"
                ? t("settings.ai.applyInstall")
                : t("settings.ai.applyUninstall")}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

function ApprovalReviewModal({
  request,
  busy,
  t,
  onCancel,
  onApprove,
}: {
  request: ApprovalRequest;
  busy: boolean;
  t: TFunction;
  onCancel: () => void;
  onApprove: () => void;
}) {
  const [scopeReviewed, setScopeReviewed] = useState(false);

  useEffect(() => {
    setScopeReviewed(false);
  }, [request.approval_id]);

  return (
    <div className="modal-backdrop" role="presentation">
      <div className="modal ai-integration-modal" role="dialog" aria-modal="true" aria-labelledby="approval-review-title">
        <div className="modal-head">
          <ShieldCheck size={20} />
          <h2 id="approval-review-title">{t("settings.ai.approvalTitle")}</h2>
        </div>
        <p>{request.summary}</p>
        <dl className="rebind-comparison">
          <div><dt>{t("settings.ai.approvalKind")}</dt><dd><code>{request.kind}</code></dd></div>
          <div><dt>{t("settings.ai.operationId")}</dt><dd><code>{request.operation_id}</code></dd></div>
          <div><dt>{t("settings.ai.planHash")}</dt><dd><code>{request.plan_hash}</code></dd></div>
          <div><dt>{t("settings.ai.scopeHash")}</dt><dd><code>{request.scope_hash}</code></dd></div>
          {Object.entries(request.metadata).map(([key, value]) => (
            <div key={key}>
              <dt>{key}</dt>
              <dd><code>{formatApprovalValue(value)}</code></dd>
            </div>
          ))}
        </dl>
        <p className="field-note">{t("settings.ai.approvalBinding")}</p>
        <label className="ai-takeover-option">
          <input
            type="checkbox"
            checked={scopeReviewed}
            onChange={(event) => setScopeReviewed(event.target.checked)}
          />
          <span>{t("settings.ai.confirmApprovalScope")}</span>
        </label>
        <div className="modal-actions">
          <button className="secondary" type="button" onClick={onCancel} disabled={busy}>
            {t("common.cancel")}
          </button>
          <button
            className="primary"
            type="button"
            onClick={onApprove}
            disabled={busy || !scopeReviewed}
          >
            {t("settings.ai.approve")}
          </button>
        </div>
      </div>
    </div>
  );
}

function formatApprovalValue(value: unknown): string {
  const rendered = typeof value === "string" ? value : JSON.stringify(value);
  return rendered || "—";
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
    const profileId = check.id.slice("platform:".length);
    return t("settings.diagnostics.check.platform", {
      name: platformOptionLabel(check.profile, profileId, t),
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
