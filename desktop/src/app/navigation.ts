import {
  Activity,
  BookOpenText,
  Database,
  FolderSync,
  HeartPulse,
  Info,
  PackagePlus,
  Settings,
  SlidersHorizontal,
} from "lucide-react";
import type { I18nKey } from "@/app/i18n";

export type View =
  | "dashboard"
  | "resources"
  | "add"
  | "sync"
  | "health"
  | "platforms"
  | "settings"
  | "guide"
  | "about";

export const navItems = [
  { id: "dashboard", labelKey: "nav.dashboard", icon: Activity },
  { id: "resources", labelKey: "nav.resources", icon: Database },
  { id: "add", labelKey: "nav.add", icon: PackagePlus },
  { id: "sync", labelKey: "nav.sync", icon: FolderSync },
  { id: "health", labelKey: "nav.health", icon: HeartPulse },
  { id: "platforms", labelKey: "nav.platforms", icon: Settings },
  { id: "settings", labelKey: "nav.settings", icon: SlidersHorizontal },
  { id: "guide", labelKey: "nav.guide", icon: BookOpenText },
  { id: "about", labelKey: "nav.about", icon: Info },
] satisfies Array<{ id: View; labelKey: I18nKey; icon: typeof Activity }>;
