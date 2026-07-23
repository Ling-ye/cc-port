import { Github } from "lucide-react";
import { type FormEvent, useMemo, useState } from "react";
import { lpmAction } from "@/api/client";
import { displayError, type TFunction } from "@/app/i18n";
import { useTaskCenter } from "@/app/TaskCenterContext";
import { Banner } from "@/components/Banner";
import {
  ResourceAdvancedFields,
  ResourceDialogFrame,
} from "@/features/resources/ResourceDialogFrame";
import type {
  AddResourceResult,
  CollectResourcePayload,
  McpTransport,
  PortableMcpConfig,
  ResourceKind,
} from "@/types/lpm";

interface CollectDraft {
  githubUrl: string;
  name: string;
  kind: ResourceKind;
  mcpTransport: McpTransport;
  mcpCommand: string;
  mcpArgs: string;
  mcpUrl: string;
  mcpEnv: string;
}

const emptyDraft: CollectDraft = {
  githubUrl: "",
  name: "",
  kind: "skill",
  mcpTransport: "stdio",
  mcpCommand: "",
  mcpArgs: "",
  mcpUrl: "",
  mcpEnv: "",
};

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

function buildMcpConfig(draft: CollectDraft, env: Record<string, string>): PortableMcpConfig {
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

export function CollectGithubDialog({
  t,
  onClose,
  onAdded,
}: {
  t: TFunction;
  onClose: () => void;
  onAdded: (resourceKey: string) => Promise<void> | void;
}) {
  const { runTask } = useTaskCenter();
  const [draft, setDraft] = useState<CollectDraft>(emptyDraft);
  const [pushAfterCompletion, setPushAfterCompletion] = useState(true);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState("");
  const dirty = useMemo(() => (
    !pushAfterCompletion
    || Boolean(draft.githubUrl.trim())
    || Boolean(draft.name.trim())
    || draft.kind !== "skill"
    || draft.mcpTransport !== "stdio"
    || Boolean(draft.mcpCommand.trim())
    || Boolean(draft.mcpArgs.trim())
    || Boolean(draft.mcpUrl.trim())
    || Boolean(draft.mcpEnv.trim())
  ), [draft, pushAfterCompletion]);

  function updateDraft(changes: Partial<CollectDraft>) {
    setDraft((current) => ({ ...current, ...changes }));
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    setFormError("");
    let mcpConfig: PortableMcpConfig | undefined;
    if (draft.kind === "mcp") {
      const env = parseMcpEnvironment(draft.mcpEnv);
      if (env === null) {
        setFormError(t("add.mcpEnvInvalid"));
        return;
      }
      mcpConfig = buildMcpConfig(draft, env);
    }
    const payload: CollectResourcePayload = {
      github_url: draft.githubUrl,
      kind: draft.kind,
      name: draft.name,
      push: pushAfterCompletion,
      ...(mcpConfig ? { mcp_config: mcpConfig } : {}),
    };
    setBusy(true);
    try {
      const result = await runTask<AddResourceResult>({
        kind: "resource-collect",
        title: t("add.modeCollect"),
        context: draft.name || draft.githubUrl,
        action: () => lpmAction<AddResourceResult>("collect", payload),
        successMessage: t("add.successCollected"),
        failureMessage: (error) => displayError(error, t),
        retryPolicy: "none",
      });
      await Promise.resolve(onAdded(`${result.entry.kind}:${result.entry.name}`));
    } catch {
      // TaskCenter owns feedback for tracked operations.
    } finally {
      setBusy(false);
    }
  }

  return (
    <ResourceDialogFrame
      dialogId="collect-github-title"
      title={t("add.modeCollect")}
      description={t("add.collectDescription")}
      icon={<Github size={19} />}
      busy={busy}
      dirty={dirty}
      t={t}
      onClose={onClose}
    >
      {(requestClose) => (
        <form onSubmit={submit} className="stack-form add-resource-form">
          <label>
            <span>{t("add.githubUrl")}</span>
            <input
              value={draft.githubUrl}
              onChange={(event) => updateDraft({ githubUrl: event.target.value })}
              autoFocus
              required
            />
          </label>

          {formError ? <Banner tone="danger" text={formError} /> : null}

          <ResourceAdvancedFields
            name={draft.name}
            kind={draft.kind}
            allowAuto={false}
            open={advancedOpen}
            t={t}
            onNameChange={(name) => updateDraft({ name })}
            onKindChange={(kind) => {
              setFormError("");
              if (kind !== "auto") updateDraft({ kind });
            }}
            onOpenChange={setAdvancedOpen}
          />

          {draft.kind === "mcp" ? (
            <fieldset className="add-mcp-config">
              <legend>{t("add.mcpConfig")}</legend>
              <p>{t("add.mcpReferenceHint")}</p>
              <div className="add-mcp-config-grid">
                <label>
                  <span>{t("add.mcpTransport")}</span>
                  <select
                    value={draft.mcpTransport}
                    onChange={(event) => {
                      setFormError("");
                      updateDraft({ mcpTransport: event.target.value as McpTransport });
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
                          setFormError("");
                          updateDraft({ mcpCommand: event.target.value });
                        }}
                        required
                      />
                    </label>
                    <label className="add-mcp-wide-field">
                      <span>{t("add.mcpArgs")}</span>
                      <textarea
                        value={draft.mcpArgs}
                        onChange={(event) => updateDraft({ mcpArgs: event.target.value })}
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
                        setFormError("");
                        updateDraft({ mcpUrl: event.target.value });
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
                      setFormError("");
                      updateDraft({ mcpEnv: event.target.value });
                    }}
                    placeholder={t("add.mcpEnvPlaceholder")}
                  />
                  <small>{t("add.mcpEnvHint")}</small>
                </label>
              </div>
            </fieldset>
          ) : null}

          <label className="add-push-option">
            <input
              type="checkbox"
              checked={pushAfterCompletion}
              onChange={(event) => setPushAfterCompletion(event.target.checked)}
            />
            <span>{t("add.pushAfterCompletion")}</span>
          </label>

          <div className="modal-actions">
            <button className="secondary" type="button" onClick={requestClose} disabled={busy}>
              {t("common.cancel")}
            </button>
            <button className="primary" disabled={busy}>
              {busy ? t("common.working") : t("add.modeCollect")}
            </button>
          </div>
        </form>
      )}
    </ResourceDialogFrame>
  );
}
