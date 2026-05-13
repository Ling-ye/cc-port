import type { TFunction } from "@/app/i18n";
import { DescriptionList } from "@/components/DescriptionList";

const PROJECT_GIT_URL = "https://github.com/Ling-ye/LingyePluginMarketplace.git";

export function AboutView({ t }: { t: TFunction }) {
  return (
    <section className="settings-view">
      <div className="panel settings-panel">
        <div className="panel-head">
          <div>
            <h2>{t("about.title")}</h2>
            <p>{t("about.description")}</p>
          </div>
        </div>

        <div className="settings-sections">
          <div className="settings-section">
            <h3>{t("about.projectInfo")}</h3>
            <DescriptionList
              rows={[
                [t("about.projectName"), "LingyePluginMarketplace / LPM"],
                [t("about.positioning"), t("about.positioningValue")],
                [t("about.developer"), t("about.developerDescription")],
                [t("about.gitAddress"), PROJECT_GIT_URL],
                [t("about.openSourceStatus"), t("about.openSourceStatusValue")],
              ]}
            />
          </div>
        </div>
      </div>
    </section>
  );
}
