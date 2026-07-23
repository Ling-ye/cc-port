import { X } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { resourceKindLabel, type TFunction } from "@/app/i18n";
import type { ResourceKind } from "@/types/lpm";

export type AddKind = "auto" | ResourceKind;

const resourceKinds: ResourceKind[] = ["skill", "mcp", "rule", "prompt", "plugin"];

export function ResourceDialogFrame({
  dialogId,
  title,
  description,
  icon,
  busy,
  dirty,
  t,
  onClose,
  children,
}: {
  dialogId: string;
  title: string;
  description: string;
  icon: ReactNode;
  busy: boolean;
  dirty: boolean;
  t: TFunction;
  onClose: () => void;
  children: (requestClose: () => void) => ReactNode;
}) {
  const [discardOpen, setDiscardOpen] = useState(false);

  function requestClose() {
    if (busy) return;
    if (dirty) setDiscardOpen(true);
    else onClose();
  }

  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key !== "Escape" || busy) return;
      if (discardOpen) setDiscardOpen(false);
      else if (dirty) setDiscardOpen(true);
      else onClose();
    }
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [busy, dirty, discardOpen, onClose]);

  return (
    <>
      <div
        className="modal-backdrop"
        role="presentation"
        onMouseDown={(event) => {
          if (event.target === event.currentTarget) requestClose();
        }}
      >
        <div className="modal add-resource-modal" role="dialog" aria-modal="true" aria-labelledby={dialogId}>
          <div className="modal-head">
            {icon}
            <h2 id={dialogId}>{title}</h2>
            <button
              className="icon-button modal-close"
              type="button"
              onClick={requestClose}
              aria-label={t("common.close")}
              disabled={busy}
            >
              <X size={17} />
            </button>
          </div>
          <p className="add-resource-description">{description}</p>
          {children(requestClose)}
        </div>
      </div>

      {discardOpen ? (
        <div
          className="modal-backdrop add-discard-backdrop"
          role="presentation"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) setDiscardOpen(false);
          }}
        >
          <div
            className="modal add-discard-modal"
            role="alertdialog"
            aria-modal="true"
            aria-labelledby="discard-add-title"
            aria-describedby="discard-add-description"
          >
            <div className="modal-head danger-head">
              <X size={19} />
              <h2 id="discard-add-title">{t("add.discardTitle")}</h2>
            </div>
            <p id="discard-add-description">{t("add.discardDescription")}</p>
            <div className="modal-actions">
              <button className="secondary" type="button" onClick={() => setDiscardOpen(false)}>
                {t("common.cancel")}
              </button>
              <button className="danger" type="button" onClick={onClose}>{t("add.discard")}</button>
            </div>
          </div>
        </div>
      ) : null}
    </>
  );
}

export function ResourceAdvancedFields({
  name,
  kind,
  allowAuto = true,
  open,
  t,
  onNameChange,
  onKindChange,
  onOpenChange,
}: {
  name: string;
  kind: AddKind;
  allowAuto?: boolean;
  open: boolean;
  t: TFunction;
  onNameChange: (value: string) => void;
  onKindChange: (value: AddKind) => void;
  onOpenChange: (value: boolean) => void;
}) {
  return (
    <details
      className="add-resource-advanced"
      open={open}
      onToggle={(event) => onOpenChange(event.currentTarget.open)}
    >
      <summary>{t("add.advanced")}</summary>
      <div className="add-resource-advanced-fields">
        <label>
          <span>{t("add.resourceName")}</span>
          <input
            value={name}
            onChange={(event) => onNameChange(event.target.value)}
            placeholder={t("add.inferPlaceholder")}
          />
        </label>
        <label>
          <span>{t("add.type")}</span>
          <select value={kind} onChange={(event) => onKindChange(event.target.value as AddKind)}>
            {allowAuto ? <option value="auto">{t("kind.auto")}</option> : null}
            {resourceKinds.map((item) => (
              <option key={item} value={item}>{resourceKindLabel(item, t)}</option>
            ))}
          </select>
        </label>
      </div>
    </details>
  );
}
