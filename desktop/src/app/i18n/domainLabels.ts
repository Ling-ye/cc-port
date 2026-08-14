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

export function pluginDistributionLabel(
  platform: string,
  track: string,
  originType: string,
  t: TFunction,
): string {
  if (platform === "claude-code" && track === "content") {
    return t("plugin.distributionSkillsDirectory");
  }
  if (track === "reference" && originType === "marketplace") {
    return t("plugin.distributionMarketplace");
  }
  if (track === "content") return t("plugin.trackContent");
  if (track === "reference") return t("plugin.trackReference");
  return "-";
}

export function pluginOriginTypeLabel(originType: string, t: TFunction): string {
  const labels: Record<string, string> = {
    marketplace: t("plugin.originMarketplace"),
    npm: t("plugin.originNpm"),
    git: t("plugin.originGit"),
    local: t("plugin.originLocal"),
  };
  return labels[originType] ?? originType;
}

export function pluginScopeLabel(scope: string, t: TFunction): string {
  const labels: Record<string, string> = {
    user: t("plugin.scopeUser"),
    project: t("plugin.scopeProject"),
    local: t("plugin.scopeLocal"),
    managed: t("plugin.scopeManaged"),
  };
  return labels[scope] ?? scope;
}
