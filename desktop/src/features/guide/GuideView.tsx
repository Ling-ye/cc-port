import type { I18nKey, TFunction } from "@/app/i18n";
import { DescriptionList } from "@/components/DescriptionList";

const resourceRows = [
  ["guide.resourceSkill", "guide.resourceSkillDescription"],
  ["guide.resourceMcp", "guide.resourceMcpDescription"],
  ["guide.resourceRule", "guide.resourceRuleDescription"],
  ["guide.resourcePrompt", "guide.resourcePromptDescription"],
  ["guide.resourcePlugin", "guide.resourcePluginDescription"],
] satisfies Array<[I18nKey, I18nKey]>;

const featureRows = [
  ["guide.featureOverview", "guide.featureOverviewDescription"],
  ["guide.featureResources", "guide.featureResourcesDescription"],
  ["guide.featureAdd", "guide.featureAddDescription"],
  ["guide.featureHealth", "guide.featureHealthDescription"],
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
        </div>
      </div>
    </section>
  );
}
