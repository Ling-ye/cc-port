import type { TFunction } from "@/app/i18n/core";

export function resourceKindLabel(kind: string, t: TFunction): string {
  switch (kind) {
    case "all":
      return t("kind.all");
    case "skill":
      return t("kind.skill");
    case "instruction":
      return t("kind.instruction");
    case "memory":
      return t("kind.memory");
    case "mcp":
      return t("kind.mcp");
    case "rule":
      return t("kind.rule");
    case "prompt":
      return t("kind.prompt");
    case "plugin":
      return t("kind.plugin");
    default:
      return kind;
  }
}
