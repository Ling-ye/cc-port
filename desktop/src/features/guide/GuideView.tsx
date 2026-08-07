import { ExternalLink, Github, Star } from "lucide-react";
import { openExternalUrl } from "@/api/client";
import { displayError, type I18nKey, type TFunction } from "@/app/i18n";
import { DescriptionList } from "@/components/DescriptionList";

const PROJECT_URL = "https://github.com/Ling-ye/cc-port";
const PROJECT_REPOSITORY = "Ling-ye/cc-port";

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

export function GuideView({
  t,
  onError,
}: {
  t: TFunction;
  onError: (message: string) => void;
}) {
  async function openProjectRepository() {
    try {
      await openExternalUrl(PROJECT_URL);
    } catch (error) {
      onError(displayError(error, t));
    }
  }

  return (
    <section className="settings-view">
      <div className="panel settings-panel">
        <div className="panel-head">
          <div>
            <h2>{t("guide.title")}</h2>
            <p>{t("guide.description")}</p>
          </div>
        </div>

        <aside className="github-star-card" aria-labelledby="github-star-title">
          <div className="github-star-copy">
            <span className="github-star-mark" aria-hidden="true">
              <Github size={24} />
            </span>
            <div>
              <span className="github-star-repository">{PROJECT_REPOSITORY}</span>
              <h3 id="github-star-title">{t("guide.starTitle")}</h3>
              <p>{t("guide.starDescription")}</p>
            </div>
          </div>
          <button
            className="primary github-star-action"
            type="button"
            onClick={() => void openProjectRepository()}
          >
            <Star size={17} />
            {t("guide.starAction")}
            <ExternalLink size={15} />
          </button>
        </aside>

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
                    onClick={() => void openProjectRepository()}
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
