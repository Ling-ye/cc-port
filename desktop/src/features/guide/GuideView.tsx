import { ExternalLink } from "lucide-react";
import { openExternalUrl } from "@/api/client";
import type { I18nKey, TFunction } from "@/app/i18n";
import { DescriptionList } from "@/components/DescriptionList";

const PROJECT_URL = "https://github.com/Ling-ye/cc-port";

const resourceRows = [
  ["guide.resourceSkill", "guide.resourceSkillDescription"],
  ["guide.resourceMcp", "guide.resourceMcpDescription"],
  ["guide.resourceRule", "guide.resourceRuleDescription"],
  ["guide.resourcePrompt", "guide.resourcePromptDescription"],
  ["guide.resourcePlugin", "guide.resourcePluginDescription"],
] satisfies Array<[I18nKey, I18nKey]>;

const featureRows = [
  ["guide.featureResources", "guide.featureResourcesDescription"],
  ["guide.featureSettings", "guide.featureSettingsDescription"],
  ["guide.featureTopbar", "guide.featureTopbarDescription"],
] satisfies Array<[I18nKey, I18nKey]>;

export function GuideView({ t }: { t: TFunction }) {
  return (
    <section className="settings-view">
      <div className="panel settings-panel">
        <div className="panel-head">
          <div>
            <h2>{t("guide.title")}</h2>
            <p>{t("guide.description")}</p>
          </div>
        </div>

        <div className="settings-sections">
          <div className="settings-section">
            <h3>{t("guide.resourcesTitle")}</h3>
            <DescriptionList rows={resourceRows.map(([label, value]) => [t(label), t(value)])} />
          </div>

          <div className="settings-section">
            <h3>{t("guide.desktopTitle")}</h3>
            <DescriptionList rows={featureRows.map(([label, value]) => [t(label), t(value)])} />
          </div>

          <div className="settings-section">
            <h3>{t("guide.projectInfo")}</h3>
            <DescriptionList
              rows={[
                [t("guide.projectName"), "cc-port / CC Port"],
                [t("guide.positioning"), t("guide.positioningValue")],
                [t("guide.developer"), t("guide.developerDescription")],
                [
                  t("guide.gitAddress"),
                  <button
                    className="external-text-link"
                    type="button"
                    onClick={() => void openExternalUrl(PROJECT_URL)}
                  >
                    {PROJECT_URL}
                    <ExternalLink size={14} />
                  </button>,
                ],
                [t("guide.openSourceStatus"), t("guide.openSourceStatusValue")],
              ]}
            />
          </div>
        </div>
      </div>
    </section>
  );
}
