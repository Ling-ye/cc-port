import type { TFunction } from "@/app/i18n";
import { DescriptionList } from "@/components/DescriptionList";
import type { PlatformProfile } from "@/types/lpm";

export function PlatformsView({ platforms, t }: { platforms: PlatformProfile[]; t: TFunction }) {
  return (
    <section className="panel">
      <div className="panel-head">
        <h2>{t("platforms.title")}</h2>
      </div>
      <div className="platform-grid">
        {platforms.map((profile) => (
          <div key={profile.name} className="platform-row">
            <div>
              <strong>{profile.name}</strong>
              <span>{profile.enabled ? t("platforms.enabled") : t("platforms.disabled")}</span>
            </div>
            <DescriptionList
              rows={[
                [t("platforms.skills"), profile.skills_dir || "-"],
                [t("platforms.mcp"), profile.mcp_json || "-"],
                [t("platforms.rules"), profile.rules_dir || "-"],
              ]}
            />
          </div>
        ))}
      </div>
    </section>
  );
}
