import { Download, FolderSearch, GitPullRequest, PackageCheck, UploadCloud, Rocket, ShieldCheck } from "lucide-react";
import { useMemo, useState } from "react";
import { lpmAction } from "@/api/client";
import type { TFunction } from "@/app/i18n";
import { useTaskCenter, type RunTaskInput } from "@/app/TaskCenterContext";
import type { CaptureResult, DeployPlan, EnvDiffPlan, EnvVersionChoice, EnvDiscoveryResult } from "@/types/lpm";

type BusyAction = "" | "discover" | "capture" | "export" | "pull" | "push" | "import" | "plan" | "deploy" | "applyReview";

export function EnvironmentView({
  t,
  onChanged,
}: {
  t: TFunction;
  onChanged: () => Promise<void> | void;
}) {
  const { runTask } = useTaskCenter();
  const [discovery, setDiscovery] = useState<EnvDiscoveryResult | null>(null);
  const [capture, setCapture] = useState<CaptureResult | null>(null);
  const [deployPlan, setDeployPlan] = useState<DeployPlan | null>(null);
  const [diffPlan, setDiffPlan] = useState<EnvDiffPlan | null>(null);
  const [diffChoices, setDiffChoices] = useState<Record<string, EnvVersionChoice>>({});
  const [snapshotPath, setSnapshotPath] = useState("~/lpm-env-snapshot.zip");
  const [busyAction, setBusyAction] = useState<BusyAction>("");

  const detectedTools = useMemo(
    () => discovery?.tools.filter((tool) => tool.detected) ?? [],
    [discovery],
  );
  const resourceRows = useMemo(() => {
    if (capture) {
      return capture.captured.map((item) => ({
        id: item.name,
        name: item.name,
        kind: item.kind,
        detail: item.source,
      }));
    }
    if (discovery) {
      return discovery.resources.map((item) => ({
        id: item.id,
        name: item.name_hint,
        kind: item.kind,
        detail: item.path,
      }));
    }
    return [];
  }, [capture, discovery]);
  const effectiveChoices = useMemo(
    () => ({ ...(diffPlan?.default_choices ?? {}), ...diffChoices }),
    [diffChoices, diffPlan],
  );
  const actionBusy = Boolean(busyAction);

  async function withBusy<T>(action: BusyAction, task: RunTaskInput<T>) {
    setBusyAction(action);
    try {
      await runTask(task);
    } catch {
      // TaskCenter owns feedback for tracked operations.
    } finally {
      setBusyAction("");
    }
  }

  function setReviewPlan(data: EnvDiffPlan) {
    setDiffPlan(data);
    setDiffChoices(data.default_choices ?? {});
  }

  function setChoice(id: string, choice: EnvVersionChoice) {
    setDiffChoices((current) => ({ ...current, [id]: choice }));
  }

  async function runDiscover() {
    await withBusy("discover", {
      kind: "environment-discover",
      title: t("environment.discover"),
      action: async () => {
        const data = await lpmAction<EnvDiscoveryResult>("env_discover");
        setDiscovery(data);
        return data;
      },
      successMessage: (data) => t("environment.discovered", { count: data.resources.length + data.mcp_servers.length }),
      retryPolicy: "safe-read",
    });
  }

  async function runCapture() {
    await withBusy("capture", {
      kind: "environment-capture",
      title: t("environment.capture"),
      action: async () => {
        const data = await lpmAction<CaptureResult>("env_capture");
        setCapture(data);
        await Promise.resolve(onChanged());
        return data;
      },
      successMessage: (data) => t("environment.captured", { count: data.captured.length }),
      retryPolicy: "none",
    });
  }

  async function runExport() {
    await withBusy("export", {
      kind: "environment-export",
      title: t("environment.export"),
      context: snapshotPath,
      action: () => lpmAction<{ path: string }>("env_export", { out: snapshotPath }),
      successMessage: (data) => t("environment.exported", { path: data.path }),
      retryPolicy: "none",
    });
  }

  async function runPushReview() {
    await withBusy("push", {
      kind: "environment-push-review",
      title: t("environment.pushReview"),
      action: async () => {
        const data = await lpmAction<EnvDiffPlan>("env_diff_push");
        setReviewPlan(data);
        return data;
      },
      successMessage: (data) => t("environment.reviewReady", { count: data.items.length }),
      retryPolicy: "safe-read",
    });
  }

  async function runPullReview() {
    await withBusy("pull", {
      kind: "environment-pull-review",
      title: t("environment.pullReview"),
      action: async () => {
        const data = await lpmAction<EnvDiffPlan>("env_diff_pull");
        setReviewPlan(data);
        return data;
      },
      successMessage: (data) => t("environment.reviewReady", { count: data.items.length }),
      retryPolicy: "safe-read",
    });
  }

  async function runImportReview() {
    await withBusy("import", {
      kind: "environment-import-review",
      title: t("environment.importReview"),
      context: snapshotPath,
      action: async () => {
        const data = await lpmAction<EnvDiffPlan>("env_diff_import", { snapshot: snapshotPath });
        setReviewPlan(data);
        return data;
      },
      successMessage: (data) => t("environment.reviewReady", { count: data.items.length }),
      retryPolicy: "safe-read",
    });
  }

  async function runApplyReview() {
    if (!diffPlan) return;
    await withBusy("applyReview", {
      kind: "environment-apply-review",
      title: t("environment.applyReview"),
      context: diffPlan.operation,
      action: async () => {
        const payload = { choices: effectiveChoices, snapshot: snapshotPath };
        const action = diffPlan.operation === "push"
          ? "env_apply_push"
          : diffPlan.operation === "pull"
            ? "env_apply_pull"
            : "env_apply_import";
        const data = await lpmAction<EnvDiffPlan>(action, payload);
        setReviewPlan(data);
        await Promise.resolve(onChanged());
        return data;
      },
      successMessage: (data) => t("environment.reviewApplied", { operation: data.operation }),
      retryPolicy: "none",
    });
  }

  async function runDeployPlan() {
    await withBusy("plan", {
      kind: "environment-deploy-plan",
      title: t("environment.deployDryRun"),
      action: async () => {
        const data = await lpmAction<DeployPlan>("env_deploy_plan");
        setDeployPlan(data);
        return data;
      },
      successMessage: (data) => t("environment.planned", { count: data.items.length }),
      retryPolicy: "safe-read",
    });
  }

  async function runDeploy() {
    await withBusy("deploy", {
      kind: "environment-deploy",
      title: t("environment.deploy"),
      action: async () => {
        const data = await lpmAction<DeployPlan>("env_deploy");
        setDeployPlan(data);
        await Promise.resolve(onChanged());
        return data;
      },
      successMessage: (data) => t("environment.deployed", { count: data.items.length }),
      retryPolicy: "none",
    });
  }

  return (
    <div className="environment-view">
      <section className="panel environment-panel">
        <div className="panel-head">
          <div>
            <h2>{t("environment.title")}</h2>
            <p>{t("environment.description")}</p>
          </div>
        </div>

        <div className="environment-actions">
          <button className="primary" onClick={() => void runDiscover()} disabled={actionBusy}>
            <FolderSearch size={17} />{t("environment.discover")}
          </button>
          <button className="secondary" onClick={() => void runCapture()} disabled={actionBusy}>
            <PackageCheck size={17} />{t("environment.capture")}
          </button>
          <button className="secondary" onClick={() => void runPullReview()} disabled={actionBusy}>
            <GitPullRequest size={17} />{t("environment.pullReview")}
          </button>
          <button className="secondary" onClick={() => void runPushReview()} disabled={actionBusy}>
            <UploadCloud size={17} />{t("environment.pushReview")}
          </button>
          <button className="secondary" onClick={() => void runDeployPlan()} disabled={actionBusy}>
            <ShieldCheck size={17} />{t("environment.deployDryRun")}
          </button>
          <button className="primary" onClick={() => void runDeploy()} disabled={actionBusy}>
            <Rocket size={17} />{t("environment.deploy")}
          </button>
        </div>

        <div className="stack-form environment-export-row">
          <label>
            <span>{t("environment.exportPath")}</span>
            <div className="environment-export-control">
              <input value={snapshotPath} onChange={(event) => setSnapshotPath(event.target.value)} />
              <button className="secondary" onClick={() => void runExport()} disabled={actionBusy || !snapshotPath.trim()}>
                <Download size={17} />{t("environment.export")}
              </button>
              <button className="secondary" onClick={() => void runImportReview()} disabled={actionBusy || !snapshotPath.trim()}>
                <GitPullRequest size={17} />{t("environment.importReview")}
              </button>
            </div>
          </label>
        </div>

        <div className="environment-metrics">
          <div className="metric">
            <span>{t("environment.detectedTools")}</span>
            <strong>{detectedTools.length}</strong>
          </div>
          <div className="metric">
            <span>{t("environment.foundResources")}</span>
            <strong>{discovery?.resources.length ?? 0}</strong>
          </div>
          <div className="metric">
            <span>{t("environment.foundMcp")}</span>
            <strong>{discovery?.mcp_servers.length ?? 0}</strong>
          </div>
          <div className="metric">
            <span>{t("environment.secretPlaceholders")}</span>
            <strong>{capture?.secrets.length ?? deployPlan?.missing_secrets.length ?? diffPlan?.secret_findings.length ?? 0}</strong>
          </div>
        </div>
      </section>

      {diffPlan ? (
        <section className="panel environment-panel environment-review">
          <div className="panel-head">
            <div>
              <h2>{t("environment.review")}</h2>
              <p>{t("environment.reviewSummary", { operation: diffPlan.operation, count: diffPlan.items.length })}</p>
            </div>
            <button className="primary" onClick={() => void runApplyReview()} disabled={actionBusy || diffPlan.blocked}>
              <PackageCheck size={17} />{t("environment.applyReview")}
            </button>
          </div>

          {diffPlan.blocked ? (
            <div className="environment-secret-block">
              <strong>{t("environment.secretBlocked")}</strong>
              {diffPlan.secret_findings.map((finding) => (
                <small key={`${finding.path}-${finding.reason}`}>{finding.path} - {finding.reason}</small>
              ))}
            </div>
          ) : null}

          <div className="environment-list">
            {diffPlan.items.map((item) => (
              <div key={item.id} className={`environment-review-row action-${item.status}`}>
                <div className="environment-review-main">
                  <strong>{item.name}</strong>
                  <span>{item.group} / {item.kind} / {item.status}</span>
                  <small>{item.local_path || "-"}</small>
                  <small>{item.incoming_path || "-"}</small>
                </div>
                <div className="environment-choice-group">
                  <label>
                    <input
                      type="radio"
                      name={item.id}
                      checked={(effectiveChoices[item.id] ?? item.default_choice) === "local"}
                      onChange={() => setChoice(item.id, "local")}
                    />
                    {t("environment.choiceLocal")}
                  </label>
                  <label>
                    <input
                      type="radio"
                      name={item.id}
                      checked={(effectiveChoices[item.id] ?? item.default_choice) === "incoming"}
                      onChange={() => setChoice(item.id, "incoming")}
                    />
                    {t("environment.choiceIncoming")}
                  </label>
                </div>
                {item.preview ? <pre className="environment-preview">{item.preview.split("\n").slice(0, 60).join("\n")}</pre> : null}
              </div>
            ))}
          </div>
        </section>
      ) : null}

      <section className="panel environment-panel">
        <div className="panel-head">
          <div>
            <h2>{t("environment.tools")}</h2>
            <p>{discovery ? t("environment.discovered", { count: discovery.resources.length + discovery.mcp_servers.length }) : t("environment.noData")}</p>
          </div>
        </div>
        <div className="environment-list">
          {detectedTools.map((tool) => (
            <div key={tool.id} className="environment-row">
              <strong>{tool.name}</strong>
              <span>{tool.confidence}</span>
              <small>{tool.root_path}</small>
            </div>
          ))}
          {discovery && detectedTools.length === 0 ? <p className="environment-empty">{t("environment.noData")}</p> : null}
        </div>
      </section>

      <section className="panel environment-panel">
        <div className="panel-head">
          <div>
            <h2>{t("environment.resources")}</h2>
            <p>{capture ? t("environment.captured", { count: capture.captured.length }) : t("environment.noData")}</p>
          </div>
        </div>
        <div className="environment-list">
          {resourceRows.slice(0, 40).map((item) => (
            <div key={item.id} className="environment-row">
              <strong>{item.name}</strong>
              <span>{item.kind}</span>
              <small>{item.detail}</small>
            </div>
          ))}
          {!capture && !discovery ? <p className="environment-empty">{t("environment.noData")}</p> : null}
        </div>
      </section>

      <section className="panel environment-panel">
        <div className="panel-head">
          <div>
            <h2>{t("environment.plan")}</h2>
            <p>{deployPlan ? t("environment.planned", { count: deployPlan.items.length }) : t("environment.noPlan")}</p>
          </div>
        </div>
        {deployPlan?.operation_id ? (
          <dl className="description-list environment-operation-meta">
            <div>
              <dt>{t("environment.operationId")}</dt>
              <dd>{deployPlan.operation_id}</dd>
            </div>
            <div>
              <dt>{t("environment.operationStatus")}</dt>
              <dd>{deployPlan.status}</dd>
            </div>
            {deployPlan.backup_root ? (
              <div>
                <dt>{t("environment.backupRoot")}</dt>
                <dd>{deployPlan.backup_root}</dd>
              </div>
            ) : null}
          </dl>
        ) : null}
        <div className="environment-list">
          {deployPlan?.items.map((item) => (
            <div key={`${item.name}-${item.platform}-${item.target_path}`} className={`environment-row action-${item.action}`}>
              <strong>{item.name}</strong>
              <span>{item.action}</span>
              <small>{item.target_path || item.reason || "-"}</small>
            </div>
          ))}
          {deployPlan?.missing_secrets.map((item) => (
            <div key={`${item.resource}-${item.name}`} className="environment-row action-conflict">
              <strong>{item.name}</strong>
              <span>{item.tool}</span>
              <small>{item.resource}</small>
            </div>
          ))}
          {!deployPlan ? <p className="environment-empty">{t("environment.noPlan")}</p> : null}
        </div>
      </section>
    </div>
  );
}
