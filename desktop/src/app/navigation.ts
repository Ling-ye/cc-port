import {
  Activity,
  BookOpenText,
  Database,
  HeartPulse,
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
  | "add"
  | "health"
  | "settings"
  | "guide"
  | "about";

export const navItems = [
  { id: "dashboard", labelKey: "nav.dashboard", icon: Activity },
  { id: "resources", labelKey: "nav.resources", icon: Database },
  { id: "environment", labelKey: "nav.environment", icon: MonitorDown },
  { id: "add", labelKey: "nav.add", icon: PackagePlus },
  { id: "health", labelKey: "nav.health", icon: HeartPulse },
  { id: "settings", labelKey: "nav.settings", icon: SlidersHorizontal },
  { id: "guide", labelKey: "nav.guide", icon: BookOpenText },
  { id: "about", labelKey: "nav.about", icon: Info },
] satisfies Array<{ id: View; labelKey: I18nKey; icon: typeof Activity }>;
