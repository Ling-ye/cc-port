import { DescriptionList } from "@/components/DescriptionList";
import type { PlatformProfile } from "@/types/lpm";

export function PlatformsView({ platforms }: { platforms: PlatformProfile[] }) {
  return (
    <section className="panel">
      <div className="panel-head">
        <h2>Platform config</h2>
      </div>
      <div className="platform-grid">
        {platforms.map((profile) => (
          <div key={profile.name} className="platform-row">
            <div>
              <strong>{profile.name}</strong>
              <span>{profile.enabled ? "enabled" : "disabled"}</span>
            </div>
            <DescriptionList
              rows={[
                ["Skills", profile.skills_dir || "-"],
                ["MCP", profile.mcp_json || "-"],
                ["Rules", profile.rules_dir || "-"],
              ]}
            />
          </div>
        ))}
      </div>
    </section>
  );
}

