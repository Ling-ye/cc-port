import {
  Activity,
  BookOpenText,
  Database,
  HeartPulse,
  GitCompareArrows,
  History,
  Info,
  MonitorDown,
  PackagePlus,
  SlidersHorizontal,
} from "lucide-react";
import type { I18nKey } from "@/app/i18n";

export type View =
  | "dashboard"
  | "resources"
  | "environment"
  | "version-sync"
  | "operations"
  | "add"
  | "health"
  | "settings"
  | "guide"
  | "about";

export const navItems = [
  { id: "dashboard", labelKey: "nav.dashboard", icon: Activity },
  { id: "resources", labelKey: "nav.resources", icon: Database },
  { id: "environment", labelKey: "nav.environment", icon: MonitorDown },
  { id: "version-sync", labelKey: "nav.versionSync", icon: GitCompareArrows },
  { id: "operations", labelKey: "nav.operations", icon: History },
  { id: "add", labelKey: "nav.add", icon: PackagePlus },
  { id: "health", labelKey: "nav.health", icon: HeartPulse },
  { id: "settings", labelKey: "nav.settings", icon: SlidersHorizontal },
  { id: "guide", labelKey: "nav.guide", icon: BookOpenText },
  { id: "about", labelKey: "nav.about", icon: Info },
] satisfies Array<{ id: View; labelKey: I18nKey; icon: typeof Activity }>;
