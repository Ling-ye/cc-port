import { FolderInput, FolderOpen } from "lucide-react";
import { type FormEvent, useMemo, useState } from "react";
import { lpmAction, selectDirectory } from "@/api/client";
import { displayError, type TFunction } from "@/app/i18n";
import { useTaskCenter } from "@/app/TaskCenterContext";
import { Banner } from "@/components/Banner";
import {
  ResourceAdvancedFields,
  ResourceDialogFrame,
  type AddKind,
} from "@/features/resources/ResourceDialogFrame";
import type { AddResourceResult } from "@/types/lpm";

interface ImportDraft {
  path: string;
  name: string;
  kind: AddKind;
}

const emptyDraft: ImportDraft = {
  path: "",
  name: "",
  kind: "auto",
};

export function ImportLocalDialog({
  t,
  onClose,
  onAdded,
}: {
  t: TFunction;
  onClose: () => void;
  onAdded: (resourceKey: string) => Promise<void> | void;
}) {
  const { runTask } = useTaskCenter();
  const [draft, setDraft] = useState<ImportDraft>(emptyDraft);
  const [pushAfterCompletion, setPushAfterCompletion] = useState(true);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [pathError, setPathError] = useState("");
  const dirty = useMemo(() => (
    !pushAfterCompletion
    || Boolean(draft.path.trim())
    || Boolean(draft.name.trim())
    || draft.kind !== "auto"
  ), [draft, pushAfterCompletion]);

  function updateDraft(changes: Partial<ImportDraft>) {
    setDraft((current) => ({ ...current, ...changes }));
  }

  async function chooseDirectory() {
    setPathError("");
    try {
      const selected = await selectDirectory();
      if (selected) updateDraft({ path: selected });
    } catch (error) {
      setPathError(displayError(error, t));
    }
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    setPathError("");
    setBusy(true);
    try {
      const result = await runTask<AddResourceResult>({
        kind: "resource-import",
        title: t("add.modeImport"),
        context: draft.name || draft.path,
        action: () => lpmAction<AddResourceResult>("upload", {
          path: draft.path,
          kind: draft.kind === "auto" ? undefined : draft.kind,
          name: draft.name,
          no_push: !pushAfterCompletion,
        }),
        successMessage: t("add.successImported"),
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
      dialogId="import-local-title"
      title={t("add.modeImport")}
      description={t("add.importDescription")}
      icon={<FolderInput size={19} />}
      busy={busy}
      dirty={dirty}
      t={t}
      onClose={onClose}
    >
      {(requestClose) => (
        <form onSubmit={submit} className="stack-form add-resource-form">
          <label>
            <span>{t("add.localPath")}</span>
            <div className="path-input-row">
              <input
                value={draft.path}
                onChange={(event) => {
                  setPathError("");
                  updateDraft({ path: event.target.value });
                }}
                autoFocus
                required
              />
              <button className="secondary" type="button" onClick={() => void chooseDirectory()} disabled={busy}>
                <FolderOpen size={16} />{t("add.browse")}
              </button>
            </div>
          </label>

          {pathError ? <Banner tone="danger" text={pathError} /> : null}

          <ResourceAdvancedFields
            name={draft.name}
            kind={draft.kind}
            open={advancedOpen}
            t={t}
            onNameChange={(name) => updateDraft({ name })}
            onKindChange={(kind) => updateDraft({ kind })}
            onOpenChange={setAdvancedOpen}
          />

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
              {busy ? t("common.working") : t("add.modeImport")}
            </button>
          </div>
        </form>
      )}
    </ResourceDialogFrame>
  );
}
