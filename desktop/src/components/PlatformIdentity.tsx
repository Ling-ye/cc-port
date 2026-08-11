import type { TFunction } from "@/app/i18n";
import type { PlatformIdentity } from "@/types/cc-port";

type PlatformIdentityValue = PlatformIdentity | null | undefined;

const KNOWN_TOOL_NAMES: Record<string, string> = {
  codex: "Codex",
  "claude-code": "Claude Code",
  cursor: "Cursor",
  windsurf: "Windsurf",
  opencode: "opencode",
};

export function platformDisplayName(
  identity: PlatformIdentityValue,
  profileId: string,
): string {
  const explicitName = identity?.display_name?.trim();
  if (explicitName) return explicitName;

  const toolId = identity?.tool_id?.trim() || profileId;
  return KNOWN_TOOL_NAMES[toolId] || toolId || profileId;
}

export function platformEnvironmentLabel(
  identity: PlatformIdentityValue,
  t: TFunction,
): string {
  const kind = identity?.environment_kind?.trim();
  const name = identity?.environment_name?.trim();
  if (!kind) return "";

  switch (kind) {
    case "windows":
      return t("platform.environment.windows");
    case "wsl":
      return name
        ? t("platform.environment.wslNamed", { name })
        : t("platform.environment.wsl");
    case "linux":
      return name
        ? t("platform.environment.linuxNamed", { name })
        : t("platform.environment.linux");
    case "macos":
      return name
        ? t("platform.environment.macosNamed", { name })
        : t("platform.environment.macos");
    case "unknown":
      return name || t("platform.environment.unknown");
    default:
      return name ? `${kind} · ${name}` : kind;
  }
}

export function platformOptionLabel(
  identity: PlatformIdentityValue,
  profileId: string,
  t: TFunction,
): string {
  const displayName = platformDisplayName(identity, profileId);
  const environment = platformEnvironmentLabel(identity, t);
  return environment ? `${displayName} · ${environment}` : displayName;
}

export function PlatformIdentityLabel({
  identity,
  profileId,
  t,
}: {
  identity: PlatformIdentityValue;
  profileId: string;
  t: TFunction;
}) {
  const environment = platformEnvironmentLabel(identity, t);
  return (
    <span className="platform-identity-label">
      <span className="platform-display-name">{platformDisplayName(identity, profileId)}</span>
      {environment ? (
        <span className={`platform-environment-badge environment-${environmentTone(identity)}`}>
          {environment}
        </span>
      ) : null}
    </span>
  );
}

function environmentTone(identity: PlatformIdentityValue): string {
  switch (identity?.environment_kind) {
    case "windows":
    case "wsl":
    case "linux":
    case "macos":
      return identity.environment_kind;
    default:
      return "unknown";
  }
}
